"""Scoring and lesson building without any AI model.

Everything here is computed from the transcript, comments, chapters and storyboard frames.
It is rougher than a model judge (it cannot check correctness), but it is free and instant.
"""
import math
import re

STOP = set("""the a an and or of to in on for with is are be this that how what why when your you from into by as at it its
using use vs versus explained explain explaining tutorial introduction intro part basics basic guide learn learning
course lecture video complete full beginners beginner simple simply easy understanding understand overview about
do does can will we our they them their there here one two three""".split())

CUES = ["for example", "for instance", "imagine", "let's say", "let us say", "suppose", "because", "intuition",
        "think of", "in other words", "the key idea", "step by step", "let's see", "notice", "trade-off", "tradeoff",
        "the reason", "what happens", "why does", "why do", "the problem is", "the idea is"]

VISUAL_CUES = ["diagram", "as you can see", "animation", "draw", "graph", "look at", "on the screen", "picture",
               "visualize", "this box", "this arrow", "highlighted"]

POS = [re.compile(p, re.I) for p in [
    r"finally (understood|understand|get it|got it|makes? sense)", r"best explanation", r"clearest",
    r"(so|crystal|very|super) clear", r"better than (my )?(professor|prof|teacher|lecturer|course|class|university|college|textbook)",
    r"(makes|made) (so much |total |perfect )?sense", r"\bclick(ed|s)\b", r"well explained|explained (so|really|very|it) well",
    r"(great|amazing|excellent|brilliant|awesome) (explanation|video|visuals?)", r"clear(ed)? (up )?my (doubt|confusion)",
    r"(got|landed|cracked|passed) (the|an|my) (job|offer|interview|exam)", r"understood (it|everything)", r"aha moment",
    r"never understood .* until"]]
NEG = [re.compile(p, re.I) for p in [
    r"\bwrong\b", r"\bincorrect\b", r"\bmistakes?\b", r"\bconfus(ing|ed)\b", r"misleading", r"not (true|correct|accurate)",
    r"\berror\b", r"doesn'?t make sense", r"too fast", r"clickbait", r"waste of time", r"\boutdated\b"]]


def clamp(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, x))


def stem(w: str) -> str:
    for suf in ("ing", "ies", "es", "s", "ed"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#]*", (text or "").lower())
    return [stem(w) for w in words if len(w) > 2 and w not in STOP]


def concept_terms(concepts: list, title: str) -> list[tuple[str, list[str]]]:
    out = []
    for c in (concepts or [title]):
        ks = list(dict.fromkeys(keywords(c)))
        if ks:
            out.append((c, ks))
    if not out:
        ks = list(dict.fromkeys(keywords(title)))
        if ks:
            out.append((title, ks))
    return out


def _chunks(meta: dict) -> list[tuple[int, set]]:
    tr = meta.get("transcript") or []
    return [(int(s), set(keywords(t))) for s, t in tr]


def coverage(meta: dict, concepts: list, title: str) -> tuple[float, list[dict]]:
    terms = concept_terms(concepts, title)
    if not terms:
        return 5.0, []
    chunks = _chunks(meta)
    if not chunks:
        blob = set(keywords(" ".join([meta.get("title") or "", meta.get("description") or "",
                                      " ".join(ch["title"] for ch in meta.get("chapters") or [])])))
        chunks = [(None, blob)]
    full = set().union(*(c[1] for c in chunks))
    marks, hit = [], 0
    for concept, ks in terms:
        need = max(1, math.ceil(0.6 * len(ks)))
        covered = sum(k in full for k in ks) >= need
        start = None
        if covered:
            hit += 1
            for s, words in chunks:
                if s is not None and sum(k in words for k in ks) >= min(need, 2):
                    start = s
                    break
        marks.append({"concept": concept, "start": start, "covered": covered})
    score = 10 * hit / len(terms)
    if not meta.get("transcript"):
        score = min(score, 6.0)
    return round(score, 1), marks


def teaching(meta: dict) -> float:
    tr = meta.get("transcript") or []
    if not tr:
        return 5.0
    text = " ".join(t for _, t in tr).lower()
    words = max(1, len(text.split()))
    cue_rate = sum(text.count(c) for c in CUES) / words * 1000
    cue_score = min(1.0, cue_rate / 12)
    minutes = max(1.0, (meta.get("duration") or 60) / 60)
    wpm = words / minutes
    if 110 <= wpm <= 175:
        pace = 1.0
    elif wpm < 110:
        pace = max(0.3, wpm / 110)
    else:
        pace = max(0.3, 1 - (wpm - 175) / 120)
    chapters = 1.0 if meta.get("chapters") else 0.0
    return round(clamp(10 * (0.55 * cue_score + 0.3 * pace + 0.15 * chapters)), 1)


def comments(meta: dict) -> tuple[float, float, str]:
    cs = meta.get("comments") or []
    if not cs:
        return 5.0, 5.5, "No comments were available."
    pos = neg = 0.0
    npos = nneg = 0
    for c in cs:
        text = c.get("text") or ""
        w = 1 + math.log10(1 + (c.get("likes") or 0))
        if any(p.search(text) for p in POS):
            pos += w
            npos += 1
        if any(p.search(text) for p in NEG):
            neg += w
            nneg += 1
    evidence = clamp(5 + 5 * (pos - 1.3 * neg) / (pos + neg + 4))
    correctness = clamp(6 + 2 * (pos - 2 * neg) / (pos + neg + 4), 2, 8)
    note = f"{npos} of {len(cs)} top comments say it made the idea click"
    note += f"; {nneg} raise problems." if nneg else "."
    return round(evidence, 1), round(correctness, 1), note


def visual(meta: dict) -> tuple[float, str]:
    frames = meta.get("frames") or []
    grid = meta.get("frame_grid") or None
    text = " ".join(t for _, t in (meta.get("transcript") or [])).lower()
    cue_bonus = min(1.0, sum(text.count(c) for c in VISUAL_CUES) / 15)
    if not frames:
        return round(4.5 + cue_bonus, 1), "No frames were available, so visuals were estimated from the narration."
    try:
        flat, dyn, n = _frame_stats(frames, grid)
    except Exception:
        return round(4.5 + cue_bonus, 1), "Frames could not be analyzed."
    if n == 0:
        return round(4.5 + cue_bonus, 1), "Frames were blank."
    score = 10 * (0.6 * min(1.0, max(0.0, (flat - 0.3) / 0.5)) + 0.3 * dyn) + cue_bonus
    kind = ("mostly diagrams or drawn visuals" if flat > 0.6 else
            "a mix of drawn visuals and camera footage" if flat > 0.42 else "mostly camera footage")
    motion = "that change often" if dyn > 0.5 else "that change occasionally" if dyn > 0.2 else "that rarely change"
    return round(clamp(score), 1), f"Sampled frames look like {kind} {motion}."


def _frame_stats(paths: list, grid) -> tuple[float, float, int]:
    from PIL import Image, ImageStat
    tiles = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        W, H = img.size
        rows, cols = (grid if grid else (1, 1))
        rows, cols = max(1, int(rows)), max(1, int(cols))
        tw, th = W // cols, H // rows
        for r in range(rows):
            for c in range(cols):
                t = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th)).resize((64, 36))
                if max(ImageStat.Stat(t.convert("L")).stddev) < 4:
                    continue
                tiles.append(t)
    if not tiles:
        return 0.0, 0.0, 0
    flats = []
    for t in tiles:
        q = t.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
        counts = sorted((cnt for cnt, _ in q.getcolors(64 * 64) or []), reverse=True)
        total = sum(counts) or 1
        flats.append(sum(counts[:3]) / total)
    diffs = []
    for a, b in zip(tiles, tiles[1:]):
        la, lb = list(a.convert("L").getdata()), list(b.convert("L").getdata())
        diffs.append(sum(abs(x - y) for x, y in zip(la, lb)) / (len(la) * 255))
    dyn = (sum(1 for d in diffs if 0.03 <= d <= 0.35) / len(diffs)) if diffs else 0.0
    return sum(flats) / len(flats), dyn, len(tiles)


def judge(meta: dict, concepts: list, title: str) -> dict:
    cov, marks = coverage(meta, concepts, title)
    teach = teaching(meta)
    evidence, correct, cnote = comments(meta)
    vis, vnote = visual(meta)
    covered = sum(1 for m in marks if m["covered"])
    verdict = (f"Covers {covered} of {len(marks)} lesson concepts"
               + ("" if meta.get("transcript") else " (judged from title and description only)") + ". " + cnote)
    strengths, weaknesses = [], []
    (strengths if cov >= 7 else weaknesses).append(
        "Covers most of the lesson" if cov >= 7 else "Misses some of the lesson's concepts")
    (strengths if vis >= 6.5 else weaknesses).append(
        "Strong visual explanation" if vis >= 6.5 else "Not very visual")
    (strengths if teach >= 6.5 else weaknesses).append(
        "Explains with examples and reasons" if teach >= 6.5 else "Few examples or explanations of why")
    return {"teaching": teach, "correctness": correct, "coverage": cov, "visual": vis, "comment_evidence": evidence,
            "verdict": verdict, "visual_notes": vnote, "comment_notes": "",
            "strengths": strengths, "weaknesses": weaknesses, "marks": marks, "basic": True}


def watch_segments(meta: dict, concepts: list, title: str) -> list[dict]:
    duration = int(meta.get("duration") or 0)
    terms = [k for _, ks in concept_terms(concepts, title) for k in ks]
    tr = meta.get("transcript") or []
    full = [{"start": 0, "end": duration, "label": "Full video"}]
    if not tr or not terms:
        chs = [ch for ch in meta.get("chapters") or [] if any(k in set(keywords(ch["title"])) for k in terms)]
        if chs:
            return [{"start": ch["start"], "end": ch["end"] or duration, "label": ch["title"][:120]} for ch in chs[:4]]
        return full
    scored = [(int(s), sum(k in set(keywords(t)) for k in terms)) for s, t in tr]
    hot = [s for s, h in scored if h >= 1]
    if not hot:
        return full
    sc = dict(scored)
    segs, cur = [], None
    for s in hot:
        if cur and s - cur[1] <= 45:
            cur[1] = s + 20
            cur[2] += sc[s]
        else:
            if cur:
                segs.append(cur)
            cur = [max(0, s - 15), s + 20, sc[s]]
    segs.append(cur)
    segs = [sg for sg in segs if sg[1] - sg[0] >= 60] or sorted(segs, key=lambda x: -x[2])[:1]
    segs = sorted(sorted(segs, key=lambda x: -x[2])[:3], key=lambda x: x[0])
    if duration and sum(e - s for s, e, _ in segs) > 0.7 * duration:
        return full
    out = []
    for s, e, _ in segs:
        e = min(e, duration) if duration else e
        label = next((ch["title"] for ch in meta.get("chapters") or [] if ch["start"] <= s < (ch["end"] or 10 ** 9)),
                     "Most relevant part")
        out.append({"start": int(s), "end": int(e), "label": label[:120]})
    return out or full


def basic_lesson(meta: dict, concepts: list, title: str, judge_result: dict, runner: dict | None) -> dict:
    marks = judge_result.get("marks") or coverage(meta, concepts, title)[1]
    names = [m["concept"] for m in marks][:3] or [title]
    return {
        "mode": "basic",
        "hook": "",
        "watch": watch_segments(meta, concepts, title),
        "concept_marks": marks,
        "chapters": meta.get("chapters") or [],
        "key_ideas": [],
        "diagram": "",
        "worked_example": "",
        "pitfalls": [],
        "quiz": [],
        "check_yourself": f"Without notes, explain {', '.join(names)} in your own words. Rewatch any part you stumble on.",
        "video": {"id": meta["id"], "title": meta.get("title"), "channel": meta.get("channel"),
                  "duration": int(meta.get("duration") or 0)},
        "runner_up": runner,
    }
