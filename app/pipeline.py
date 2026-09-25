import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import llm, scoring
from . import youtube as yt
from .config import COURSE_WORKERS, DEEP_CANDIDATES, SEARCH_RESULTS
from .db import db, log

DEPTHS = {"quick": 6, "standard": 12, "deep": 20}
LEVELS = {
    "beginner": "a complete beginner",
    "intermediate": "someone with some background who wants depth",
    "advanced": "an experienced practitioner filling gaps",
}

_executor = ThreadPoolExecutor(max_workers=max(1, COURSE_WORKERS))
_running: set[int] = set()
_lock = threading.Lock()


def enqueue(course_id: int):
    with _lock:
        if course_id in _running:
            return
        _running.add(course_id)
    _set_course(course_id, status="queued", error=None)
    _executor.submit(_run, course_id)


def is_running(course_id: int) -> bool:
    with _lock:
        return course_id in _running


def _run(course_id: int):
    try:
        run_course(course_id)
    except Exception as e:
        traceback.print_exc()
        _set_course(course_id, status="failed", error=str(e)[:500])
        log(course_id, f"Stopped: {e}")
    finally:
        with _lock:
            _running.discard(course_id)


def _set_course(cid, **fields):
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE courses SET {cols} WHERE id=?", (*fields.values(), cid))


def _set_lesson(lid, **fields):
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE lessons SET {cols} WHERE id=?", (*fields.values(), lid))


def run_course(cid: int):
    with db() as c:
        course = dict(c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone())
        has_lessons = c.execute("SELECT COUNT(*) FROM lessons WHERE course_id=?", (cid,)).fetchone()[0]
    if not has_lessons:
        _set_course(cid, status="planning")
        log(cid, "Planning a zero-to-hero path")
        plan_curriculum(course)
        with db() as c:
            course = dict(c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone())

    _set_course(cid, status="building")
    with db() as c:
        lessons = [dict(r) for r in c.execute(
            "SELECT * FROM lessons WHERE course_id=? AND status!='ready' ORDER BY module_idx, idx", (cid,))]
    for lesson in lessons:
        try:
            build_lesson(course, lesson)
        except Exception as e:
            traceback.print_exc()
            _set_lesson(lesson["id"], status="failed", error=str(e)[:500])
            log(cid, f"“{lesson['title']}” failed: {str(e)[:200]}")
            if "ANTHROPIC_API_KEY" in str(e) or "sign-in check" in str(e):
                raise

    with db() as c:
        left = c.execute("SELECT COUNT(*) FROM lessons WHERE course_id=? AND status!='ready'", (cid,)).fetchone()[0]
    _set_course(cid, status="ready" if not left else "partial")
    log(cid, "Course ready" if not left else f"Finished with {left} lesson(s) needing a retry")


PLANNER_SYSTEM = """You design zero-to-hero learning paths that will be taught with the best existing YouTube explanations.
Each lesson must be a single concept that one focused video (or one section of a longer video) can teach well."""


def plan_curriculum(course: dict):
    n = DEPTHS.get(course.get("depth") or "standard", 12)
    prompt = f"""Topic: {course['topic']}
Learner: {LEVELS.get(course.get('level') or 'beginner')}
Learner's goal: {course.get('goal') or 'not stated'}
Number of lessons: {n}

Build an ordered path from first principles to advanced material and real-world application.
Group lessons into 3-6 modules. Every lesson needs:
- "title": short and specific
- "concepts": 3-6 concrete ideas the lesson must cover (used to check whether a video covers the lesson)
- "queries": 2 YouTube search queries phrased the way good explainer videos are titled; no channel names

Return:
{{"title": "course title", "summary": "one or two sentences on what the learner will be able to do",
  "modules": [{{"title": "...", "lessons": [{{"title": "...", "concepts": ["..."], "queries": ["...", "..."]}}]}}]}}

Use exactly {n} lessons in total."""
    data = llm.ask_json(prompt, PLANNER_SYSTEM, max_tokens=6000)
    rows = []
    for mi, m in enumerate(data.get("modules") or []):
        for li, l in enumerate(m.get("lessons") or []):
            if not l.get("title"):
                continue
            queries = [q for q in (l.get("queries") or []) if isinstance(q, str) and q.strip()][:2]
            if not queries:
                queries = [f"{l['title']} explained"]
            rows.append((course["id"], mi, m.get("title") or f"Part {mi + 1}", li, l["title"],
                         json.dumps(l.get("concepts") or []), json.dumps(queries)))
    if not rows:
        raise RuntimeError("The planner returned no lessons. Try rephrasing the topic.")
    rows = rows[: n + 2]
    with db() as c:
        c.executemany(
            "INSERT INTO lessons(course_id,module_idx,module_title,idx,title,concepts,queries,status) "
            "VALUES(?,?,?,?,?,?,?,'pending')", rows)
    _set_course(course["id"], title=data.get("title") or course["topic"], summary=data.get("summary") or "")
    log(course["id"], f"Planned {len(rows)} lessons")


def build_lesson(course: dict, lesson: dict):
    cid, lid = course["id"], lesson["id"]
    concepts = json.loads(lesson["concepts"] or "[]")
    queries = json.loads(lesson["queries"] or "[]")

    _set_lesson(lid, status="searching", error=None)
    log(cid, f"Searching YouTube for “{lesson['title']}”")
    found: dict[str, dict] = {}
    for q in queries:
        for pos, r in enumerate(yt.search(q, SEARCH_RESULTS)):
            d = r.get("duration")
            if d and (d < 150 or d > 4 * 3600):
                continue
            if r["id"] not in found:
                r["position"] = pos
                found[r["id"]] = r
    if not found:
        raise RuntimeError("No usable videos found for this lesson.")

    pool = sorted(found.values(), key=lambda r: r["position"])[:10]
    with ThreadPoolExecutor(max_workers=4) as ex:
        metas = list(ex.map(lambda r: _safe(yt.fetch_video, r["id"], False), pool))
    shortlist = []
    for r, m in zip(pool, metas):
        if not m:
            continue
        sig, raw = scoring.audience(m)
        shortlist.append((scoring.prelim(sig, r["position"]), r, m))
    shortlist.sort(key=lambda x: x[0], reverse=True)
    shortlist = shortlist[:DEEP_CANDIDATES]
    if not shortlist:
        raise RuntimeError("Could not read details for any candidate video.")

    _set_lesson(lid, status="judging")
    log(cid, f"Judging {len(shortlist)} candidates for “{lesson['title']}”")

    def evaluate(item):
        _, r, _m = item
        meta = _safe(yt.fetch_video, r["id"], True) or _m
        sig, raw = scoring.audience(meta)
        try:
            judge = judge_video(course, lesson, concepts, meta)
        except Exception as e:
            if "ANTHROPIC_API_KEY" in str(e):
                raise
            judge = {"verdict": f"Could not be judged: {str(e)[:120]}", "judge_error": True,
                     "teaching": 3, "correctness": 3, "coverage": 3, "visual": 3, "comment_evidence": 3}
        score, contrib = scoring.combine(sig, judge, course.get("profile") or "balanced")
        return {"meta": meta, "signals": sig, "raw": raw, "judge": judge, "score": score, "contrib": contrib}

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(evaluate, shortlist))
    results.sort(key=lambda x: x["score"], reverse=True)

    with db() as c:
        c.execute("DELETE FROM candidates WHERE lesson_id=?", (lid,))
        for rank, res in enumerate(results, 1):
            m = res["meta"]
            c.execute(
                "INSERT INTO candidates(lesson_id,video_id,title,channel,url,duration,meta,signals,judge,score,"
                "contributions,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (lid, m["id"], m.get("title"), m.get("channel"), f"https://www.youtube.com/watch?v={m['id']}",
                 m.get("duration"), json.dumps(_public_meta(m)), json.dumps(res["raw"]),
                 json.dumps(res["judge"]), res["score"], json.dumps(res["contrib"]), rank))

    winner = results[0]
    runner = results[1] if len(results) > 1 else None
    log(cid, f"Picked “{winner['meta'].get('title', '')[:70]}” ({winner['score']}) for “{lesson['title']}”")

    _set_lesson(lid, status="writing", winner_video=winner["meta"]["id"])
    content = write_lesson(course, lesson, concepts, winner, runner)
    _set_lesson(lid, status="ready", content=json.dumps(content))


def _safe(fn, *a):
    try:
        return fn(*a)
    except yt.YouTubeError as e:
        if "sign-in check" in str(e):
            raise
        return None
    except Exception:
        return None


def _public_meta(m: dict) -> dict:
    return {k: m.get(k) for k in ("title", "channel", "duration", "upload_date", "thumbnail", "chapters",
                                  "view_count", "like_count", "comment_count", "channel_follower_count")}


JUDGE_SYSTEM = """You are the jury that picks the single best YouTube explanation for one lesson. Score each criterion 0-10.

teaching: builds intuition before formalism, motivates the why, clear progression, good examples, no filler.
correctness: technically accurate, no misleading simplifications; deduct for errors you can spot.
coverage: how many of the lesson's required concepts it actually explains (not just mentions).
visual: 0-3 mostly talking head or static text; 4-6 slides or static diagrams; 7-10 diagrams that build up step by step or animations with clear labels. Judge from the storyboard frames when present.
comment_evidence: 8-10 many specific comments saying it finally made the idea click or beat courses; 5 only generic praise or few comments; 0-3 comments point out errors or confusion.

Be a tough, fair judge. Popularity is scored elsewhere; ignore it here."""


def judge_video(course: dict, lesson: dict, concepts: list, meta: dict) -> dict:
    tr = meta.get("transcript") or []
    transcript = yt.transcript_text(tr, 14000) if tr else "(no transcript available)"
    comments = sorted(meta.get("comments") or [], key=lambda c: c.get("likes", 0), reverse=True)[:40]
    comments_txt = "\n".join(f"- ({c.get('likes', 0)} likes) {c['text'][:300]}" for c in comments) or "(no comments)"
    chapters = "\n".join(f"{yt.fmt_ts(ch['start'])} {ch['title']}" for ch in meta.get("chapters") or []) or "(none)"
    frames = meta.get("frames") or []
    frames_note = (f"The {len(frames)} images are storyboard contact sheets: grids of small frames sampled "
                   f"from early, middle and late in the video." if frames
                   else "No frames are available; estimate the visual score from transcript cues and keep it near 5 if unclear.")
    prompt = f"""Course: {course['topic']} (learner: {LEVELS.get(course.get('level') or 'beginner')})
Lesson: {lesson['title']}
Required concepts: {', '.join(concepts) or 'n/a'}

Video: {meta.get('title')} — {meta.get('channel')} — {yt.fmt_ts(meta.get('duration'))}
Chapters:
{chapters}

{frames_note}

Transcript excerpts:
{transcript}

Top comments:
{comments_txt}

Return:
{{"teaching": 0-10, "correctness": 0-10, "coverage": 0-10, "visual": 0-10, "comment_evidence": 0-10,
  "visual_notes": "one sentence on how it looks", "comment_notes": "one sentence on what viewers say",
  "strengths": ["up to 3 short points"], "weaknesses": ["up to 3 short points"],
  "verdict": "one sentence on why a learner should or should not use this video for this lesson"}}"""
    return llm.ask_json(prompt, JUDGE_SYSTEM, images=frames, max_tokens=1200)


WRITER_SYSTEM = """You turn the best YouTube explanation of a concept into a short, rigorous lesson.
Teach in your own words from what the video explains; never quote more than a few words of the transcript.
The video is the main teacher; your notes frame it, reinforce it and check understanding."""


def write_lesson(course: dict, lesson: dict, concepts: list, winner: dict, runner: dict | None) -> dict:
    m = winner["meta"]
    tr = m.get("transcript") or []
    duration = int(m.get("duration") or 0)
    transcript = yt.transcript_text(tr, 40000, parts=10) if tr else "(no transcript available)"
    chapters = "\n".join(f"{yt.fmt_ts(ch['start'])} {ch['title']}" for ch in m.get("chapters") or []) or "(none)"
    prompt = f"""Course: {course.get('title') or course['topic']}
Learner: {LEVELS.get(course.get('level') or 'beginner')}
Lesson: {lesson['title']}
Must cover: {', '.join(concepts) or 'n/a'}

Chosen video: {m.get('title')} by {m.get('channel')} ({yt.fmt_ts(duration)} long, {duration} seconds)
Chapters:
{chapters}

Timestamped transcript:
{transcript}

Return:
{{"hook": "1-2 sentences on why this matters",
  "watch": [{{"start": seconds, "end": seconds, "label": "what this part shows"}}],
  "key_ideas": [{{"title": "...", "body": "2-5 sentences, markdown allowed (**bold**, `code`, - lists)"}}],
  "diagram": "a Mermaid flowchart (graph TD or graph LR) that captures the core structure, or empty string",
  "worked_example": "a short concrete example in markdown (code fences allowed)",
  "pitfalls": ["common mistakes or misconceptions"],
  "quiz": [{{"q": "...", "options": ["a", "b", "c", "d"], "answer": 0, "why": "..."}}],
  "check_yourself": "one question to answer out loud to prove understanding"}}

Rules:
- "watch": 1-4 segments of THIS video that teach this lesson, using timestamps that exist in the transcript, within 0-{duration}. If the whole video is on-topic, use one segment for all of it.
- 3-6 key ideas, 3-5 quiz questions that test understanding, not recall of trivia.
- Mermaid: node ids without spaces, every label in double quotes, no parentheses outside quotes."""
    data = llm.ask_json(prompt, WRITER_SYSTEM, max_tokens=5000)
    return _clean_lesson(data, winner, runner, duration)


def _clean_lesson(data: dict, winner: dict, runner: dict | None, duration: int) -> dict:
    watch = []
    for w in data.get("watch") or []:
        try:
            s = max(0, int(float(w.get("start", 0))))
            e = int(float(w.get("end", duration or s + 600)))
            if duration:
                e = min(e, duration)
            if e - s >= 10:
                watch.append({"start": s, "end": e, "label": str(w.get("label") or "Watch")[:160]})
        except Exception:
            continue
    if not watch:
        watch = [{"start": 0, "end": duration or 0, "label": "Full video"}]
    quiz = []
    for q in data.get("quiz") or []:
        opts = [str(o) for o in (q.get("options") or [])][:6]
        try:
            ans = int(q.get("answer", 0))
        except Exception:
            ans = 0
        if q.get("q") and len(opts) >= 2 and 0 <= ans < len(opts):
            quiz.append({"q": str(q["q"]), "options": opts, "answer": ans, "why": str(q.get("why") or "")})
    wm = winner["meta"]
    return {
        "hook": str(data.get("hook") or ""),
        "watch": watch,
        "key_ideas": [{"title": str(k.get("title") or ""), "body": str(k.get("body") or "")}
                      for k in (data.get("key_ideas") or []) if isinstance(k, dict)][:6],
        "diagram": str(data.get("diagram") or "").strip(),
        "worked_example": str(data.get("worked_example") or ""),
        "pitfalls": [str(p) for p in (data.get("pitfalls") or [])][:6],
        "quiz": quiz[:5],
        "check_yourself": str(data.get("check_yourself") or ""),
        "video": {"id": wm["id"], "title": wm.get("title"), "channel": wm.get("channel"), "duration": duration},
        "runner_up": ({"id": runner["meta"]["id"], "title": runner["meta"].get("title"),
                       "channel": runner["meta"].get("channel")} if runner else None),
    }
