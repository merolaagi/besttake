"""Turns a YouTube video into a visual build-up: key diagram frames, short animation clips, aligned narration.

Method:
1. Download the video (video only, <=720p). Videos over 45 minutes: only the relevant ranges.
2. Sample 1 frame per second inside the focus ranges and measure each frame: how "drawn" it looks
   (few dominant colors = diagram, slide, graph, code) and how much it differs from the previous frame.
3. Split into steps at hard cuts, and inside long continuous animations whenever enough has changed.
   A step's picture is its LAST frame, because diagrams and animations build up: the last frame is the complete state.
4. Keep drawn-looking steps, drop talking heads and near-duplicates, and cut a short looping clip for steps
   that animate. Each step gets the narration spoken while it was on screen.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import heuristics as H
from . import youtube as yt
from .config import DATA_DIR

MEDIA_DIR = DATA_DIR / "media"
MAX_STEPS = 14
CUT = 0.12
CUT_CHANGED = 0.55
ACTIVE = 0.002
BUILD = 0.035
DRAWN = 0.42
DUP = 0.008


class MediaError(RuntimeError):
    pass


def _run(args: list, timeout: int = 900):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise MediaError((r.stderr or "ffmpeg failed").strip().splitlines()[-1][:200])


def focus_ranges(meta: dict, concepts: list, title: str) -> list[tuple[int, int]]:
    duration = int(meta.get("duration") or 0)
    segs = H.watch_segments(meta, concepts, title)
    ranges = []
    for sg in segs:
        s, e = max(0, sg["start"] - 20), sg["end"] or duration
        if duration:
            e = min(e, duration)
        if e > s:
            ranges.append([s, e])
    if not ranges:
        ranges = [[0, duration or 600]]
    ranges.sort()
    merged = [ranges[0]]
    for s, e in ranges[1:]:
        if s <= merged[-1][1] + 10:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, total = [], 0
    for s, e in merged:
        take = min(e - s, 25 * 60 - total)
        if take <= 0:
            break
        out.append((int(s), int(s + take)))
        total += take
    return out


def _sample(ff: str, src: Path, local_start: float, dur: float, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    _run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{local_start:.2f}", "-i", str(src), "-t", f"{dur:.2f}",
          "-vf", "fps=1,scale=160:-2", "-q:v", "6", str(outdir / "%06d.jpg")])
    return sorted(outdir.glob("*.jpg"))


def _features(path: Path) -> dict:
    from PIL import Image, ImageStat
    full = Image.open(path).convert("RGB")
    small = full.resize((64, 36))
    q = small.quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    counts = sorted((c for c, _ in (q.getcolors(4096) or [])), reverse=True)
    g = full.resize((128, 72)).convert("L")
    return {"g": g, "std": ImageStat.Stat(g).stddev[0], "flat": sum(counts[:3]) / (sum(counts) or 1)}


def _diff(a, b) -> tuple[float, float]:
    """(mean absolute difference 0..1, fraction of pixels that clearly changed)."""
    from PIL import ImageChops
    h = ImageChops.difference(a, b).histogram()
    n = sum(h) or 1
    return sum(i * c for i, c in enumerate(h)) / (n * 255), sum(h[26:]) / n


def _segment(frames: list[dict]) -> list[dict]:
    """frames: [{t, src, local, f}] in time order. Returns steps with start, end, rep index, activity."""
    steps, cur, acc = [], None, 0.0
    for i, fr in enumerate(frames):
        prev = frames[i - 1] if i else None
        gap = prev is None or fr["t"] - prev["t"] > 1.5 or fr["src"] != prev["src"]
        mean, d = (1.0, 1.0) if gap else _diff(prev["f"]["g"], fr["f"]["g"])
        if cur is None or mean > CUT or d > CUT_CHANGED:
            if cur:
                steps.append(cur)
            cur, acc = {"start": fr["t"], "end": fr["t"] + 1, "rep": i, "active": 0, "n": 1}, 0.0
            continue
        cur["end"], cur["rep"], cur["n"] = fr["t"] + 1, i, cur["n"] + 1
        if d > ACTIVE:
            cur["active"] += 1
        acc += d
        if acc >= BUILD and fr["t"] - cur["start"] >= 6:
            steps.append(cur)
            cur, acc = {"start": fr["t"] + 1, "end": fr["t"] + 1, "rep": i, "active": 0, "n": 0}, 0.0
    if cur:
        steps.append(cur)
    return [s for s in steps if s["n"] >= 2]


def _clean(text: str) -> str:
    t = re.sub(r"\[(music|applause|laughter|__)\]", " ", text or "", flags=re.I)
    t = re.sub(r"\b(um+|uh+|erm|uhm|hmm)\b,?\s*", "", t, flags=re.I)
    t = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:1].upper() + t[1:]) if t else ""


def _narration(transcript: list, start: float, end: float, max_words: int = 90) -> str:
    parts = [txt for s, txt in transcript if start - 4 <= s < end]
    words = _clean(" ".join(parts)).split()
    if len(words) > max_words:
        words = words[:max_words] + ["…"]
    return " ".join(words)


def extract(meta: dict, concepts: list, title: str, log=None) -> list[dict]:
    ff = yt.ffmpeg_path()
    if not ff:
        raise MediaError("ffmpeg isn't installed. Rerun the one-liner; setup installs it with Homebrew.")
    vid = meta["id"]
    duration = int(meta.get("duration") or 0)
    focus = focus_ranges(meta, concepts, title)
    key = json.dumps(focus)
    outdir = MEDIA_DIR / vid
    manifest = outdir / "steps.json"
    if manifest.exists():
        try:
            saved = json.loads(manifest.read_text())
            if saved.get("focus") == key:
                return saved["steps"]
        except Exception:
            pass
    outdir.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="bt_"))
    try:
        if log:
            log(f"Downloading “{(meta.get('title') or '')[:60]}” to extract visuals")
        long_video = duration > 45 * 60
        files = yt.download_video(vid, work, ranges=focus if long_video else None)

        frames = []
        for k, (s, e) in enumerate(focus):
            if long_video:
                match = [f for f in files if f[1] == s]
                if not match:
                    continue
                src, offset = match[0]
            else:
                src, offset = files[0]
            local = max(0.0, s - offset)
            for j, p in enumerate(_sample(ff, src, local, e - s, work / f"f{k}")):
                frames.append({"t": s + j, "src": str(src), "local": local + j, "f": _features(p)})
        if not frames:
            raise MediaError("No frames could be read from the video.")

        steps = _segment(frames)
        kept = []
        for st in steps:
            f = frames[st["rep"]]["f"]
            if f["std"] < 6 or f["flat"] < DRAWN:
                continue
            if kept and _diff(frames[kept[-1]["rep"]]["f"]["g"], f["g"])[1] < DUP:
                kept[-1].update(end=st["end"], rep=st["rep"], active=kept[-1]["active"] + st["active"],
                                n=kept[-1]["n"] + st["n"])
                continue
            kept.append(dict(st))

        tr = meta.get("transcript") or []
        terms = [k for _, ks in H.concept_terms(concepts, title) for k in ks]
        for i, st in enumerate(kept):
            nxt = kept[i + 1]["start"] if i + 1 < len(kept) else st["end"] + 30
            st["text"] = _narration(tr, st["start"], min(nxt, st["start"] + 90))
            words = set(H.keywords(st["text"]))
            st["score"] = 1 + sum(k in words for k in terms) + min(3, (st["end"] - st["start"]) / 20)
        if len(kept) > MAX_STEPS:
            best = sorted(range(len(kept)), key=lambda i: -kept[i]["score"])[:MAX_STEPS]
            kept = [kept[i] for i in sorted(best)]

        out = []
        for st in kept:
            fr = frames[st["rep"]]
            t = fr["t"]
            img = outdir / f"{int(t * 10)}.jpg"
            if not img.exists():
                _run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{fr['local'] + 0.5:.2f}", "-i", fr["src"],
                      "-frames:v", "1", "-q:v", "3", "-y", str(img)])
            clip_url = None
            span = st["end"] - st["start"]
            animated = st["active"] >= 3 and st["active"] / max(1, st["n"]) >= 0.25 and span >= 4
            if animated:
                s0 = fr["local"] - (t - st["start"])
                dur, speed = float(span), max(1.0, span / 10)
                if speed > 3:
                    s0, dur, speed = s0 + span - 30, 30.0, 3.0
                clip = outdir / f"{int(st['start'] * 10)}_{int(st['end'] * 10)}.mp4"
                if not clip.exists():
                    try:
                        _run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, s0):.2f}", "-i", fr["src"],
                              "-t", f"{dur:.2f}", "-an", "-vf",
                              f"setpts=PTS/{speed:.3f},scale='min(960,iw)':-2,fps=24", "-c:v", "libx264",
                              "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                              "-y", str(clip)])
                    except MediaError:
                        clip = None
                clip_url = f"/media/{vid}/{clip.name}" if clip and clip.exists() else None
            chapter = next((ch["title"] for ch in meta.get("chapters") or []
                            if ch["start"] <= t < (ch["end"] or 10 ** 9)), "")
            words = set(H.keywords(st["text"]))
            concept = next((c for c, ks in H.concept_terms(concepts, title) if any(k in words for k in ks)), "")
            out.append({"t": int(t), "start": int(st["start"]), "end": int(st["end"]),
                        "image": f"/media/{vid}/{img.name}", "clip": clip_url,
                        "kind": "animation" if clip_url else "diagram",
                        "title": concept or chapter or "", "text": st["text"]})
        manifest.write_text(json.dumps({"focus": key, "steps": out}))
        if log:
            log(f"Extracted {len(out)} visual steps ({sum(1 for s in out if s['clip'])} animated)")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)
