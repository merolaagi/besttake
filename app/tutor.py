"""Tutor mode: designs original first-principles lessons, using the best YouTube explanations as source material."""
import re

from . import llm
from . import youtube as yt

LEVELS = {
    "beginner": "a complete beginner",
    "intermediate": "someone with some background who wants depth",
    "advanced": "an experienced practitioner filling gaps",
}
STAGES = {"orient", "reduce", "derive", "connect", "prove"}
BEAT_KINDS = {"orient", "reduce", "ask", "explain", "derive", "connect", "prove", "compress"}
NODE_KINDS = {"client", "service", "database", "cache", "queue", "storage", "external", "concept", "object", "process",
              "start", "end", "step", "decision", "io"}
OPS = {"add", "link", "unlink", "remove", "flow", "highlight", "fail", "recover", "copies", "rename", "note"}

SCENE_LANGUAGE = """SCENE LANGUAGE for a beat's "visual" (BestTake draws and animates it; you never output images):
1) Diagram. One diagram persists through the whole lesson and each beat changes it, so the learner watches it build up.
   {"type": "diagram", "ops": [
     {"op": "add", "id": "users", "label": "Users", "kind": "client"},
     {"op": "add", "id": "app", "label": "App server", "kind": "service"},
     {"op": "link", "from": "users", "to": "app", "label": "requests"},
     {"op": "flow", "path": ["users", "app"]},              animated traffic along existing links, this beat only
     {"op": "highlight", "id": "app", "tone": "warn"},      tone: warn | good | focus, this beat only
     {"op": "fail", "id": "app"}, {"op": "recover", "id": "app"},
     {"op": "copies", "id": "app", "count": 3},             show 3 identical instances
     {"op": "rename", "id": "app", "label": "Web server"},
     {"op": "unlink", "from": "users", "to": "app"}, {"op": "remove", "id": "app"},
     {"op": "note", "text": "short caption shown on the diagram"}]}
   kinds: client, service, database, cache, queue, storage, external, concept, object, process.
   For non-software topics use "concept", "object" and "process" boxes joined by labeled arrows.
   Keep ids short, lowercase, no spaces. Link only ids that were added earlier. At most 12 boxes in the whole lesson.
   Optional "direction": "right" (default) or "down". Optional "scene": a name for a separate drawing
   (default "main"); beats with the same scene keep building the same picture.
   FLOWCHARTS use a diagram with "scene": "flow", "direction": "down" and kinds start, step, decision, io, end;
   label decision branches with "yes" / "no", and highlight the step being discussed with tone "focus".
2) Sequence diagram (who talks to whom, in order). It also persists and grows beat by beat:
   {"type": "sequence", "ops": [{"op": "actor", "id": "browser", "label": "Browser"},
     {"op": "msg", "from": "browser", "to": "lb", "label": "GET /abc"},
     {"op": "msg", "from": "lb", "to": "browser", "label": "302 redirect", "reply": true},
     {"op": "note", "text": "..."}]}
3) Chart: {"type": "chart", "kind": "line" | "bar", "title": "...", "x_label": "...", "y_label": "...",
   "series": [{"name": "...", "points": [[x, y], ...]}], "mark": {"x": number, "label": "..."}, "trace": true}
   "trace": true animates a dot moving along the first line.
4) Table: {"type": "table", "columns": ["..."], "rows": [["..."]], "highlight": row_index}
5) Code: {"type": "code", "lang": "python", "code": "...", "highlight": [line numbers]}
6) null: keep showing the previous visual.
Mix types: architecture diagrams for structure, flowcharts for decisions and algorithms, sequence diagrams for
request/response over time, charts for anything quantitative (load, latency, cost, growth)."""


# ---------------- Planner ----------------

def plan(course: dict, protocol_text: str, provider: str, n: int, user_lessons: list | None, learner: str = "") -> dict:
    fixed = ""
    if user_lessons:
        titles = [l["title"] for m in user_lessons for l in m["lessons"]]
        fixed = ("The learner supplied these lessons. Keep them, in this order, and fill in every field:\n- "
                 + "\n- ".join(titles) + "\n")
        n = len(titles)
    prompt = f"""Topic: {course['topic']}
Learner: {LEVELS.get(course.get('level') or 'beginner')}
Learner's goal: {course.get('goal') or 'not stated'}
{('Learner profile: ' + learner) if learner else ''}
Number of lessons: {n}
{fixed}
Design the course with the protocol. Return:
{{"title": "course title",
  "summary": "one or two sentences on what the learner will be able to do",
  "meaning": "break the topic's words down into plain meaning, 2-4 sentences",
  "purpose": "why this field exists, 1-2 sentences",
  "primitives": [{{"name": "...", "idea": "one sentence"}}],
  "questions": ["the reusable questions every problem in this topic answers"],
  "running_example": {{"name": "...", "description": "a tiny example that will grow through the course"}},
  "modules": [{{"title": "...", "lessons": [{{
      "title": "...",
      "stage": "orient | reduce | derive | connect | prove",
      "pressure": "the new requirement put on the running example",
      "problem": "what breaks because of that requirement",
      "property": "the property we now need",
      "mechanism": "the concept that gives that property (empty for orient lessons)",
      "concepts": ["3-5 ideas the lesson must cover"],
      "queries": ["2 YouTube search queries for the best existing explanations"]}}]}}]}}

Rules: 4-8 primitives and 4-7 questions. The first module orients and reduces (meaning, purpose, primitives).
Middle modules: every lesson is one new pressure on the running example that forces exactly one mechanism.
The last module proves understanding by transferring the rules to new situations. Use exactly {n} lessons."""
    return llm.ask_json(prompt, _system(protocol_text), provider=provider, max_tokens=7000)


def _system(protocol_text: str) -> str:
    return ("You are a first-principles tutor. Follow this tutor protocol exactly:\n\n" + protocol_text +
            "\n\nNever hand the learner facts to memorize when they could derive them.")


# ---------------- Lesson designer ----------------

def design(course: dict, plan_data: dict, lesson: dict, meta: dict, prior: list, sources: list, frame_notes: list,
           images: list, protocol_text: str, provider: str, frames: list | None = None, learner: str = "") -> dict:
    local = provider == "ollama"
    budget_a, budget_b = (7000, 3000) if local else (16000, 8000)
    src_txt = []
    for i, s in enumerate(sources[:2]):
        tr = s.get("transcript") or []
        excerpt = yt.transcript_text(tr, budget_a if i == 0 else budget_b, parts=6) if tr else "(no transcript)"
        src_txt.append(f"SOURCE {'AB'[i]}: “{s.get('title')}” by {s.get('channel')}\n{excerpt}")
    notes = dict(frame_notes or [])
    frame_lines = []
    for i, f in enumerate(frames or []):
        desc = notes.get(f["t"], "")
        frame_lines.append(f"[{i}] at {yt.fmt_ts(f['t'])} ({f.get('kind', 'diagram')})"
                           + (f" shows: {desc}" if desc else "") + (f" | narration: {f.get('text', '')[:220]}" if f.get("text") else ""))
    frames_txt = ""
    if frame_lines:
        frames_txt = ("SOURCE FRAMES: key visuals extracted from SOURCE A, in the order the video builds the idea"
                      + (" (the attached images are these frames, same order)" if images else "") + ":\n" + "\n".join(frame_lines)
                      + "\nAttach a frame to a beat with \"frame\": index so the learner sees the original next to your visual."
                      + " Follow the source's order: frames on later beats have higher indexes. Your own visual must add what the"
                      + " frame lacks (motion, flow, failure, numbers, a decision path); never just redraw it.")
    prims = "; ".join(f"{p.get('name')}: {p.get('idea')}" for p in plan_data.get("primitives") or [])
    ex = plan_data.get("running_example") or {}
    prior_txt = "\n".join(f"- {p['title']}: {p.get('mechanism') or ''}" for p in prior[-8:]) or "(this is the first lesson)"
    rules_so_far = "\n".join(f"- {r}" for r in (plan_data.get("rules_so_far") or [])[-12:]) or "(none yet)"
    prompt = f"""Course: {course.get('title') or course['topic']}
Learner: {LEVELS.get(course.get('level') or 'beginner')}
{('Learner profile (use it for CONNECT beats and examples): ' + learner) if learner else ''}
Primitives: {prims or 'n/a'}
Reusable questions: {'; '.join(plan_data.get('questions') or []) or 'n/a'}
Running example: {ex.get('name', '')} - {ex.get('description', '')}
Already learned:
{prior_txt}
Rules the learner has compressed so far:
{rules_so_far}

THIS LESSON: {lesson['title']}  (protocol stage: {meta.get('stage') or 'derive'})
Pressure: {meta.get('pressure') or '-'}
Problem: {meta.get('problem') or '-'}
Property needed: {meta.get('property') or '-'}
Mechanism: {meta.get('mechanism') or '-'}
Must cover: {', '.join(meta.get('concepts') or []) or '-'}

Source material (the best explanations found on YouTube; learn from them, then teach in your own words):
{chr(10).join(src_txt) or '(no sources found; teach from first principles)'}
{frames_txt}

{SCENE_LANGUAGE}

Design the lesson as 8-14 beats. Return:
{{"goal": "after this lesson you can ...",
  "beats": [{{
     "kind": "orient | reduce | ask | explain | derive | connect | prove | compress",
     "text": "what the tutor says, 1-4 sentences, markdown allowed",
     "question": "only for ask/prove beats: the question the learner answers before moving on",
     "hint": "only for ask/prove beats",
     "answer": "only for ask/prove beats: the idea the learner should reach",
     "missing_primitive": {{"name": "only for ask/prove beats: the earlier idea a stuck learner is missing", "explain": "2-3 sentences rebuilding it"}},
     "visual": {{...scene language...}} or null,
     "frame": index of a SOURCE FRAME to show with this beat, or null,
     "source": {{"video": "A" or "B", "t": seconds}} or null
  }}],
  "rules": ["2-4 generative rules that compress this lesson"],
  "challenge": {{"question": "apply the rules to an unfamiliar situation", "hint": "...", "answer": "..."}}}}

Rules for the beats:
- Open by connecting to the running example and what the learner already knows.
- Before revealing a mechanism, add an "ask" beat that lets the learner derive it from the problem.
- Follow requirement -> problem -> property -> mechanism. Build the diagram up gradually: 1-4 ops per beat.
- Use at least two visual types (for example an architecture diagram plus a chart, or a flowchart plus a sequence diagram), and show at least one flow, failure or trace when it makes the idea concrete.
- Most beats should change the visual, so the picture builds up in step with the explanation.
- Use "source" to point to the moment in a video that explains a beat best.
- Finish with a "compress" beat."""
    return llm.ask_json(prompt, _system(protocol_text), provider=provider, images=images, max_tokens=8000)


def caption_frames(paths_and_times: list) -> list:
    out = []
    for t, path in paths_and_times[:6]:
        try:
            r = llm.ask_json('Describe the diagram or visual in this frame for someone who cannot see it: the parts, '
                             'arrows, labels and what it shows. Return {"caption": "..."}',
                             "You describe educational video frames precisely and briefly.",
                             provider="ollama", images=[path], max_tokens=250, vision=True)
            cap = str(r.get("caption") or "").strip()
            if cap:
                out.append((t, cap[:400]))
        except llm.ProviderUnavailable:
            raise
        except Exception:
            continue
    return out


# ---------------- Answer checker ----------------

def check(protocol_text: str, provider: str, question: str, expected: str, missing: dict, answer: str) -> dict:
    prompt = f"""Question the learner was asked: {question}
The idea they should reach: {expected}
The earlier primitive a stuck learner is usually missing: {missing.get('name', '')} - {missing.get('explain', '')}
Learner's answer: {answer}

Judge the reasoning, not the wording. Return:
{{"verdict": "got_it | partly | not_yet",
  "feedback": "2-4 sentences. Say what is right first. If something is missing, do not re-explain the answer: name the missing primitive, rebuild it briefly, and end with one small question that lets them try again.",
  "missing_primitive": "name of the missing primitive, or empty"}}"""
    r = llm.ask_json(prompt, _system(protocol_text), provider=provider, max_tokens=600)
    verdict = r.get("verdict") if r.get("verdict") in ("got_it", "partly", "not_yet") else "partly"
    return {"verdict": verdict, "feedback": str(r.get("feedback") or "")[:1200],
            "missing_primitive": str(r.get("missing_primitive") or "")[:120]}


# ---------------- Sanitizers ----------------

def _s(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _id(v):
    return re.sub(r"[^a-z0-9_-]", "", str(v or "").lower().replace(" ", "_"))[:24]


def _num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) < 1e12 else None
    except Exception:
        return None


SEQ_KNOWN: dict = {}


def clean_visual(v, known: set):
    if not isinstance(v, dict):
        return None
    t = v.get("type")
    if t == "diagram":
        ops = []
        for o in (v.get("ops") or [])[:12]:
            if not isinstance(o, dict) or o.get("op") not in OPS:
                continue
            op = o["op"]
            if op == "add":
                i = _id(o.get("id"))
                if not i or (i not in known and len(known) >= 14):
                    continue
                known.add(i)
                ops.append({"op": "add", "id": i, "label": _s(o.get("label") or i, 40),
                            "kind": o.get("kind") if o.get("kind") in NODE_KINDS else "concept"})
            elif op in ("link", "unlink"):
                a, b = _id(o.get("from")), _id(o.get("to"))
                if a in known and b in known and a != b:
                    ops.append({"op": op, "from": a, "to": b, "label": _s(o.get("label"), 30)})
            elif op == "flow":
                path = [_id(x) for x in (o.get("path") or [o.get("from"), o.get("to")]) if _id(x) in known][:8]
                if len(path) >= 2:
                    ops.append({"op": "flow", "path": path})
            elif op in ("highlight", "fail", "recover", "remove"):
                i = _id(o.get("id"))
                if i in known:
                    d = {"op": op, "id": i}
                    if op == "highlight":
                        d["tone"] = o.get("tone") if o.get("tone") in ("warn", "good", "focus") else "focus"
                    ops.append(d)
            elif op == "copies":
                i = _id(o.get("id"))
                c = _num(o.get("count"))
                if i in known and c:
                    ops.append({"op": "copies", "id": i, "count": int(max(1, min(9, c)))})
            elif op == "rename":
                i = _id(o.get("id"))
                if i in known:
                    ops.append({"op": "rename", "id": i, "label": _s(o.get("label"), 40)})
            elif op == "note":
                if o.get("text"):
                    ops.append({"op": "note", "text": _s(o.get("text"), 120)})
        out = {"type": "diagram", "ops": ops}
        if v.get("direction") in ("right", "down"):
            out["direction"] = v["direction"]
        scene = _id(v.get("scene"))
        if not scene and any(o.get("kind") in ("start", "end", "step", "decision", "io") for o in ops if o["op"] == "add"):
            scene = "flow"
        if scene and scene != "main":
            out["scene"] = scene
            if scene == "flow" and "direction" not in out:
                out["direction"] = "down"
        return out if ops else None
    if t == "sequence":
        seq_known = SEQ_KNOWN.setdefault(id(known), set())
        ops = []
        for o in (v.get("ops") or [])[:12]:
            if not isinstance(o, dict):
                continue
            if o.get("op") == "actor":
                i = _id(o.get("id"))
                if i and (i in seq_known or len(seq_known) < 7):
                    seq_known.add(i)
                    ops.append({"op": "actor", "id": i, "label": _s(o.get("label") or i, 30)})
            elif o.get("op") == "msg":
                a, b = _id(o.get("from")), _id(o.get("to"))
                if a in seq_known and b in seq_known:
                    ops.append({"op": "msg", "from": a, "to": b, "label": _s(o.get("label"), 50), "reply": bool(o.get("reply"))})
            elif o.get("op") == "note" and o.get("text"):
                ops.append({"op": "note", "text": _s(o.get("text"), 120)})
        return {"type": "sequence", "ops": ops} if ops else None
    if t == "chart":
        series = []
        for sr in (v.get("series") or [])[:4]:
            pts = []
            for p in (sr.get("points") or [])[:80] if isinstance(sr, dict) else []:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    x, y = _num(p[0]), _num(p[1])
                    if x is not None and y is not None:
                        pts.append([x, y])
            if len(pts) >= 2:
                series.append({"name": _s(sr.get("name"), 30), "points": sorted(pts)})
        if not series:
            return None
        mark = v.get("mark") if isinstance(v.get("mark"), dict) else None
        mx = _num(mark.get("x")) if mark else None
        return {"type": "chart", "kind": "bar" if v.get("kind") == "bar" else "line", "title": _s(v.get("title"), 80),
                "trace": bool(v.get("trace")),
                "x_label": _s(v.get("x_label"), 40), "y_label": _s(v.get("y_label"), 40), "series": series,
                "mark": {"x": mx, "label": _s(mark.get("label"), 40)} if mx is not None else None}
    if t == "table":
        cols = [_s(c, 40) for c in (v.get("columns") or [])][:6]
        rows = [[_s(c, 80) for c in r][:len(cols) or 6] for r in (v.get("rows") or [])[:10] if isinstance(r, list)]
        if not cols or not rows:
            return None
        hl = _num(v.get("highlight"))
        return {"type": "table", "columns": cols, "rows": rows,
                "highlight": int(hl) if hl is not None and 0 <= hl < len(rows) else None}
    if t == "code":
        code = "\n".join(str(v.get("code") or "").splitlines()[:40])
        if not code.strip():
            return None
        hl = [int(x) for x in (v.get("highlight") or []) if _num(x) is not None][:10]
        return {"type": "code", "lang": _s(v.get("lang"), 20), "code": code[:4000], "highlight": hl}
    return None


def clean_lesson(data: dict, sources: list, frames: list | None = None) -> dict:
    known: set = set()
    SEQ_KNOWN.pop(id(known), None)
    frames = frames or []
    beats = []
    for b in (data.get("beats") or [])[:16]:
        if not isinstance(b, dict):
            continue
        kind = b.get("kind") if b.get("kind") in BEAT_KINDS else "explain"
        beat = {"kind": kind, "text": _s(b.get("text"), 900), "visual": clean_visual(b.get("visual"), known)}
        if kind in ("ask", "prove") and b.get("question"):
            mp = b.get("missing_primitive") if isinstance(b.get("missing_primitive"), dict) else {}
            beat.update(question=_s(b.get("question"), 400), hint=_s(b.get("hint"), 400),
                        answer=_s(b.get("answer"), 700),
                        missing_primitive={"name": _s(mp.get("name"), 80), "explain": _s(mp.get("explain"), 600)})
        elif kind in ("ask", "prove"):
            beat["kind"] = "explain"
        src = b.get("source") if isinstance(b.get("source"), dict) else None
        if src and src.get("video") in ("A", "B"):
            idx = "AB".index(src["video"])
            t = _num(src.get("t"))
            if idx < len(sources) and t is not None:
                beat["source"] = {"idx": idx, "t": int(max(0, t))}
        fr = _num(b.get("frame"))
        if fr is not None and 0 <= int(fr) < len(frames):
            beat["frame"] = int(fr)
        if beat["text"] or beat.get("question") or beat["visual"]:
            beats.append(beat)
    SEQ_KNOWN.pop(id(known), None)
    if frames and beats and not any("frame" in b for b in beats):
        teach = [i for i, b in enumerate(beats) if b["kind"] not in ("compress",)]
        for j, bi in enumerate(teach):
            beats[bi]["frame"] = round(j * (len(frames) - 1) / max(1, len(teach) - 1))
    ch = data.get("challenge") if isinstance(data.get("challenge"), dict) else {}
    return {
        "type": "tutor",
        "goal": _s(data.get("goal"), 300),
        "beats": beats,
        "rules": [_s(r, 200) for r in (data.get("rules") or []) if str(r).strip()][:5],
        "challenge": ({"question": _s(ch.get("question"), 500), "hint": _s(ch.get("hint"), 400),
                       "answer": _s(ch.get("answer"), 800)} if ch.get("question") else None),
        "sources": [{"id": s["id"], "title": s.get("title"), "channel": s.get("channel"),
                     "url": f"https://www.youtube.com/watch?v={s['id']}"} for s in sources[:2]],
        "frames": [{"t": f["t"], "start": f.get("start", f["t"]), "image": f["image"], "clip": f.get("clip"),
                    "kind": f.get("kind", "diagram")} for f in frames],
    }


# ---------------- Custom animation program (strong models) ----------------

ANIM_SYSTEM = """You are a world-class explainer who builds animated teaching visuals, like the best 3Blue1Brown or
ByteByteGo scenes. You write one self-contained HTML document that animates a lesson step by step."""

CSP = ("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; "
       "style-src 'unsafe-inline'; img-src data: blob:; font-src data:\">")


def animation(course: dict, lesson: dict, content: dict, provider: str) -> str:
    beats = []
    for i, b in enumerate(content["beats"]):
        v = b.get("visual") or {}
        summary = v.get("type", "keep previous")
        if v.get("type") == "diagram":
            summary += ": " + ", ".join(f"{o['op']} {o.get('label') or o.get('id') or ''}".strip() for o in v["ops"])[:300]
        elif v.get("type") in ("chart", "table", "sequence"):
            summary += ": " + (v.get("title") or ", ".join(o.get("label", "") for o in v.get("ops", [])))[:200]
        beats.append(f"{i}. [{b['kind']}] {b['text'][:300]}" + (f" | QUESTION: {b['question'][:200]}" if b.get("question") else "")
                     + f" | planned visual: {summary}")
    prompt = f"""Lesson: {lesson['title']} (course: {course.get('title') or course['topic']})
Goal: {content.get('goal', '')}

The lesson plays as these beats, in order:
{chr(10).join(beats)}

Write ONE self-contained HTML document that visualizes this lesson and advances in sync with the beats.
Requirements:
- Define window.btStep = function (k) {{ ... }} that shows the state for beat k (0-based). It must be cumulative and
  reversible: btStep(5) after btStep(8) shows beat 5's state. Animate every change smoothly (CSS transitions,
  requestAnimationFrame or SVG animation).
- Add: window.addEventListener("message", e => {{ if (e.data && e.data.type === "bt-step") window.btStep(e.data.step); }});
  and call window.btStep(0) on load.
- Combine the right forms for each beat: animated architecture diagrams with traffic moving along arrows,
  flowcharts that light up the current path, graphs whose curves draw and whose markers move, counters and
  meters that change. Every beat must change something visible and point the eye at the new part.
- Before a QUESTION beat, do not reveal the answer visually; show the problem (for example the overloaded server).
- One 16:9 scene scaled with an SVG viewBox or a canvas that fits 100% width and height, white background,
  ink #1A1C22, accent #2E47D1, highlight #FFE14D, good #3F8F6B, bad #B3261E, font-family system-ui, sans-serif,
  labels at least 14px in scene units at 960x540.
- Inline CSS and JS only: no external files, fonts, images or network requests. Under 45 KB.
Output only the HTML document, starting with <!doctype html>."""
    raw = llm.ask_text(prompt, ANIM_SYSTEM, provider=provider, max_tokens=16000)
    m = re.search(r"<!doctype html.*</html>", raw, re.S | re.I)
    html = m.group(0) if m else ""
    if "btStep" not in html or len(html) > 120_000:
        return ""
    if re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(r"(<head[^>]*>)", r"\1" + CSP, html, count=1, flags=re.I)
    elif re.search(r"<html[^>]*>", html, re.I):
        html = re.sub(r"(<html[^>]*>)", r"\1<head>" + CSP + "</head>", html, count=1, flags=re.I)
    else:
        return ""
    return html
