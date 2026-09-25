import json
import threading
from pathlib import Path
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import heuristics, llm, media, scoring, templates
from . import youtube as yt
from .config import COURSE_WORKERS, DEEP_CANDIDATES, SEARCH_RESULTS
from .db import db, log

DEPTHS = {"quick": 6, "standard": 12, "deep": 20}
LEVELS = {
    "beginner": "a complete beginner",
    "intermediate": "someone with some background who wants depth",
    "advanced": "an experienced practitioner filling gaps",
}
ENGINE_NAMES = {"none": "Basic (no AI)", "ollama": "Local model", "anthropic": "Claude"}

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


def _fatal(e: Exception) -> bool:
    return isinstance(e, llm.ProviderUnavailable) or "sign-in check" in str(e)


def insert_plan(course_id: int, modules: list, limit: int = 30) -> int:
    rows = []
    for mi, m in enumerate(modules or []):
        for li, l in enumerate(m.get("lessons") or []):
            if not l.get("title") or len(rows) >= limit:
                continue
            queries = [q for q in (l.get("queries") or []) if isinstance(q, str) and q.strip()][:2]
            if not queries:
                queries = [f"{l['title']} explained"]
            concepts = [str(x) for x in (l.get("concepts") or []) if str(x).strip()] or [l["title"]]
            rows.append((course_id, mi, m.get("title") or f"Part {mi + 1}", li, l["title"][:160],
                         json.dumps(concepts[:8]), json.dumps(queries)))
    with db() as c:
        c.executemany(
            "INSERT INTO lessons(course_id,module_idx,module_title,idx,title,concepts,queries,status) "
            "VALUES(?,?,?,?,?,?,?,'pending')", rows)
    return len(rows)


def run_course(cid: int):
    with db() as c:
        course = dict(c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone())
        has_lessons = c.execute("SELECT COUNT(*) FROM lessons WHERE course_id=?", (cid,)).fetchone()[0]
    provider = course.get("provider") or "none"
    if not has_lessons:
        if provider == "none":
            t = templates.match_template(course["topic"])
            if not t:
                raise RuntimeError("Basic mode needs a lesson list. Delete this course and add lessons, or pick a template.")
            n = insert_plan(cid, templates.parse_lessons(t["text"], course["topic"]))
            log(cid, f"Using the built-in “{t['label']}” plan ({n} lessons)")
        else:
            _set_course(cid, status="planning")
            log(cid, f"Planning a zero-to-hero path with {ENGINE_NAMES[provider]}")
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
            if _fatal(e):
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
    data = llm.ask_json(prompt, PLANNER_SYSTEM, provider=course.get("provider") or "anthropic", max_tokens=6000)
    ctx = heuristics.topic_context(course["topic"])
    ctx_kw = set(heuristics.keywords(ctx))
    for m in data.get("modules") or []:
        for l in m.get("lessons") or []:
            qs = [q for q in (l.get("queries") or []) if isinstance(q, str) and q.strip()]
            l["queries"] = [q if ctx_kw <= set(heuristics.keywords(q)) else f"{q} {ctx}" for q in qs] or \
                templates.lesson_queries(l.get("title") or "", ctx)
    count = insert_plan(course["id"], data.get("modules") or [], limit=n + 2)
    if not count:
        raise RuntimeError("The planner returned no lessons. Try rephrasing the topic, or paste a lesson list.")
    _set_course(course["id"], title=data.get("title") or course["topic"], summary=data.get("summary") or "")
    log(course["id"], f"Planned {count} lessons")


def build_lesson(course: dict, lesson: dict):
    cid, lid = course["id"], lesson["id"]
    provider = course.get("provider") or "none"
    concepts = json.loads(lesson["concepts"] or "[]")
    queries = json.loads(lesson["queries"] or "[]")
    ctx = heuristics.topic_context(course["topic"])
    title = lesson["title"]

    def rel_of(m):
        return heuristics.relevance(m, title, concepts, ctx)

    _set_lesson(lid, status="searching", error=None)
    log(cid, f"Searching YouTube for “{title}” ({', '.join(queries)})")
    found: dict[str, dict] = {}
    for q in queries:
        for pos, r in enumerate(yt.search(q, SEARCH_RESULTS)):
            d = r.get("duration")
            if d and (d < 150 or d > 4 * 3600):
                continue
            if r["id"] not in found:
                r["position"] = pos
                r["title_rel"] = rel_of({"title": r.get("title")})
                found[r["id"]] = r
    if not found:
        raise RuntimeError("No usable videos found for this lesson.")

    ranked = sorted(found.values(), key=lambda r: -(0.7 * r["title_rel"] + 0.3 * (1 - r["position"] / 12)))
    pool = [r for r in ranked if r["title_rel"] >= 0.2][:10] or ranked[:6]
    with ThreadPoolExecutor(max_workers=4) as ex:
        metas = list(ex.map(lambda r: _safe(yt.fetch_video, r["id"], False), pool))
    shortlist = []
    for r, m in zip(pool, metas):
        if not m:
            continue
        sig, _ = scoring.audience(m)
        rel = rel_of(m)
        shortlist.append((scoring.prelim(sig, r["position"], rel), rel, r, m))
    shortlist.sort(key=lambda x: x[0], reverse=True)
    on_topic = [x for x in shortlist if x[1] >= 0.3]
    dropped = len(shortlist) - len(on_topic)
    shortlist = (on_topic if len(on_topic) >= 2 else shortlist)[:DEEP_CANDIDATES]
    if not shortlist:
        raise RuntimeError("Could not read details for any candidate video.")
    if dropped:
        log(cid, f"Dropped {dropped} off-topic candidate(s) for “{title}”")

    _set_lesson(lid, status="judging")
    log(cid, f"Judging {len(shortlist)} candidates for “{title}” ({ENGINE_NAMES[provider]})")
    vision_ok = provider == "anthropic" or (provider == "ollama" and llm.ollama_status().get("vision"))

    def evaluate(item):
        _, _rel, r, _m = item
        meta = _safe(yt.fetch_video, r["id"], True) or _m
        sig, raw = scoring.audience(meta)
        rel = rel_of(meta)
        raw["relevance"] = rel
        if provider == "none":
            judge = heuristics.judge(meta, concepts, title)
        else:
            try:
                judge = judge_video(course, lesson, concepts, meta, provider, vision_ok)
            except Exception as e:
                if _fatal(e):
                    raise
                judge = heuristics.judge(meta, concepts, title)
                judge["verdict"] = "The AI judge failed on this video, so it was scored without AI. " + judge["verdict"]
        score, contrib = scoring.combine(sig, judge, course.get("profile") or "balanced", rel)
        return {"meta": meta, "signals": sig, "raw": raw, "judge": judge, "score": score, "contrib": contrib, "rel": rel}

    with ThreadPoolExecutor(max_workers=1 if provider == "ollama" else 3) as ex:
        results = list(ex.map(evaluate, shortlist))
    results.sort(key=lambda x: x["score"], reverse=True)

    with db() as c:
        c.execute("DELETE FROM candidates WHERE lesson_id=?", (lid,))
        for rank, res in enumerate(results, 1):
            m = res["meta"]
            judge_public = {k: v for k, v in res["judge"].items() if k != "marks"}
            c.execute(
                "INSERT INTO candidates(lesson_id,video_id,title,channel,url,duration,meta,signals,judge,score,"
                "contributions,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (lid, m["id"], m.get("title"), m.get("channel"), f"https://www.youtube.com/watch?v={m['id']}",
                 m.get("duration"), json.dumps(_public_meta(m)), json.dumps(res["raw"]),
                 json.dumps(judge_public), res["score"], json.dumps(res["contrib"]), rank))

    winner = results[0]
    log(cid, f"Picked “{winner['meta'].get('title', '')[:70]}” ({winner['score']}) for “{title}”")

    _set_lesson(lid, status="extracting", winner_video=winner["meta"]["id"])
    steps, source, extract_error = [], winner, ""
    for cand in [r for r in results if r["rel"] >= 0.3][:2] or results[:1]:
        try:
            st = media.extract(cand["meta"], concepts, title, log=lambda msg: log(cid, msg))
        except Exception as e:
            if _fatal(e):
                raise
            extract_error = str(e)[:300]
            log(cid, f"Visual extraction failed for “{cand['meta'].get('title', '')[:50]}”: {extract_error[:150]}")
            continue
        if len(st) > len(steps):
            steps, source = st, cand
        if len(st) >= 3:
            break
    if source is not winner and steps:
        log(cid, f"Using visuals from runner-up “{source['meta'].get('title', '')[:60]}” (the pick is mostly talking)")

    _set_lesson(lid, status="writing")
    sm = source["meta"]
    marks = heuristics.coverage(sm, concepts, title)[1]
    base = {
        "mode": "basic",
        "intro": "",
        "steps": [dict(s, title=s.get("title") or f"Step {i + 1}") for i, s in enumerate(steps)],
        "source": {"id": sm["id"], "title": sm.get("title"), "channel": sm.get("channel"),
                   "url": f"https://www.youtube.com/watch?v={sm['id']}", "rank": results.index(source) + 1},
        "concept_marks": marks,
        "key_ideas": [], "diagram": "", "worked_example": "", "pitfalls": [], "quiz": [],
        "check_yourself": "Without notes, explain " + ", ".join([m["concept"] for m in marks][:3] or [title])
                          + " in your own words. Step back through the build-up for any part you stumble on.",
        "extract_error": "" if steps else (extract_error or "No diagram or animation moments were found in the top videos."),
    }
    if provider != "none":
        try:
            ai = write_buildup(course, lesson, concepts, sm, steps, provider)
            base.update(ai)
            base["mode"] = "ai"
        except Exception as e:
            if _fatal(e):
                raise
            log(cid, f"Notes for “{title}” fell back to basic mode: {str(e)[:120]}")
    _set_lesson(lid, status="ready", content=json.dumps(base))


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

VISION_SYSTEM = """You rate how visually a video teaches, from storyboard contact sheets (grids of small frames sampled across the video).
0-3 mostly talking head or static text; 4-6 slides or static diagrams; 7-10 diagrams that build up step by step or animations with clear labels."""


def judge_video(course: dict, lesson: dict, concepts: list, meta: dict, provider: str, vision_ok: bool) -> dict:
    local = provider == "ollama"
    tr = meta.get("transcript") or []
    transcript = yt.transcript_text(tr, 8000 if local else 14000) if tr else "(no transcript available)"
    comments = sorted(meta.get("comments") or [], key=lambda c: c.get("likes", 0), reverse=True)[:25 if local else 40]
    comments_txt = "\n".join(f"- ({c.get('likes', 0)} likes) {c['text'][:300]}" for c in comments) or "(no comments)"
    chapters = "\n".join(f"{yt.fmt_ts(ch['start'])} {ch['title']}" for ch in meta.get("chapters") or []) or "(none)"
    frames = meta.get("frames") or []
    inline_frames = frames if (provider == "anthropic" and frames) else []
    frames_note = (f"The {len(frames)} images are storyboard contact sheets: grids of small frames sampled "
                   f"from early, middle and late in the video." if inline_frames
                   else "No frames are attached; score visual from transcript cues, near 5 if unclear. It may be rescored separately.")
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
    judge = llm.ask_json(prompt, JUDGE_SYSTEM, provider=provider, images=inline_frames, max_tokens=1200)

    if provider == "ollama":
        if frames and vision_ok:
            try:
                v = llm.ask_json('Return {"visual": 0-10, "visual_notes": "one sentence"}', VISION_SYSTEM,
                                 provider="ollama", images=frames, max_tokens=200, vision=True)
                judge["visual"], judge["visual_notes"] = v.get("visual", judge.get("visual")), v.get("visual_notes", "")
            except Exception as e:
                if _fatal(e):
                    raise
                judge["visual"], judge["visual_notes"] = heuristics.visual(meta)
        else:
            judge["visual"], judge["visual_notes"] = heuristics.visual(meta)
    return judge


WRITER_SYSTEM = """You turn the visuals and narration extracted from the best YouTube explanation of a concept into a
step-by-step build-up lesson. Each step shows one extracted frame or animation. Write in your own words:
never copy more than a few words of narration. Each step should build on the previous one, like a teacher
adding to a whiteboard."""


def write_buildup(course: dict, lesson: dict, concepts: list, meta: dict, steps: list, provider: str) -> dict:
    local = provider == "ollama"
    listing = "\n".join(f"[{i}] at {yt.fmt_ts(s['t'])} ({s['kind']}): {s['text'][:500] or '(no narration)'}"
                        for i, s in enumerate(steps)) or "(no visual steps were extracted)"
    tr = meta.get("transcript") or []
    transcript = yt.transcript_text(tr, 6000 if local else 16000, parts=6) if tr else "(no transcript)"
    images = []
    if provider == "anthropic" and steps:
        images = _thumbs([media.MEDIA_DIR / meta["id"] / Path(s["image"]).name for s in steps[:12]])
    prompt = f"""Course: {course.get('title') or course['topic']}
Learner: {LEVELS.get(course.get('level') or 'beginner')}
Lesson: {lesson['title']}
Must cover: {', '.join(concepts) or 'n/a'}
Source video: {meta.get('title')} by {meta.get('channel')}

Extracted visual steps (index, time, kind, narration spoken while it was on screen):
{listing}
{"The images attached are those steps' frames, in order." if images else ""}

Transcript excerpts for context:
{transcript}

Return:
{{"intro": "2 sentences: what we're building up to and why it matters",
  "steps": [{{"id": step index, "title": "3-6 words", "explain": "2-4 sentences explaining what this visual shows and what it adds to the previous step"}}],
  "key_ideas": [{{"title": "...", "body": "2-4 sentences, markdown allowed"}}],
  "diagram": "a Mermaid flowchart (graph TD or graph LR) of the final structure, or empty string",
  "worked_example": "a short concrete example in markdown",
  "pitfalls": ["common mistakes"],
  "quiz": [{{"q": "...", "options": ["a", "b", "c", "d"], "answer": 0, "why": "..."}}],
  "check_yourself": "one question to answer out loud"}}

Rules: keep steps in time order; drop steps that are off-topic (intros, sponsors, outros) by leaving them out; 3-5 quiz questions that test understanding.
Mermaid: node ids without spaces, every label in double quotes."""
    data = llm.ask_json(prompt, WRITER_SYSTEM, provider=provider, images=images, max_tokens=5000)
    out_steps = []
    for item in data.get("steps") or []:
        try:
            i = int(item.get("id"))
        except Exception:
            continue
        if 0 <= i < len(steps) and not any(o["_i"] == i for o in out_steps):
            out_steps.append(dict(steps[i], _i=i, title=str(item.get("title") or steps[i].get("title") or ""),
                                  explain=str(item.get("explain") or "")))
    if steps and len(out_steps) < max(2, len(steps) // 3):
        out_steps = [dict(s, _i=i) for i, s in enumerate(steps)]
    out_steps.sort(key=lambda s: s["t"])
    for s in out_steps:
        s.pop("_i", None)
    quiz = []
    for q in data.get("quiz") or []:
        if not isinstance(q, dict):
            continue
        opts = [str(o) for o in (q.get("options") or [])][:6]
        try:
            ans = int(q.get("answer", 0))
        except Exception:
            ans = 0
        if q.get("q") and len(opts) >= 2 and 0 <= ans < len(opts):
            quiz.append({"q": str(q["q"]), "options": opts, "answer": ans, "why": str(q.get("why") or "")})
    result = {
        "intro": str(data.get("intro") or ""),
        "key_ideas": [{"title": str(k.get("title") or ""), "body": str(k.get("body") or "")}
                      for k in (data.get("key_ideas") or []) if isinstance(k, dict)][:6],
        "diagram": str(data.get("diagram") or "").strip(),
        "worked_example": str(data.get("worked_example") or ""),
        "pitfalls": [str(p) for p in (data.get("pitfalls") or [])][:6],
        "quiz": quiz[:5],
    }
    if out_steps:
        result["steps"] = out_steps
    if data.get("check_yourself"):
        result["check_yourself"] = str(data["check_yourself"])
    return result


def _thumbs(paths: list) -> list[str]:
    from PIL import Image
    out = []
    for p in paths:
        try:
            if not p.exists():
                continue
            t = p.with_name(p.stem + "_ai.jpg")
            if not t.exists():
                im = Image.open(p).convert("RGB")
                im.thumbnail((640, 360))
                im.save(t, quality=80)
            out.append(str(t))
        except Exception:
            continue
    return out
