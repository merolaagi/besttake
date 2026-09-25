import json
import re
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import CACHE_TTL, COMMENTS_PER_VIDEO, COOKIES_FROM_BROWSER, FRAMES_DIR
from .db import db

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

BOT_HINT = ("YouTube asked for a sign-in check. Set YTDLP_COOKIES_FROM_BROWSER=chrome (or safari/firefox) "
            "in .env, make sure you are signed in to YouTube in that browser, and restart.")


class YouTubeError(RuntimeError):
    pass


def _opts(extra: dict | None = None) -> dict:
    o = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "ignore_no_formats_error": True,
    }
    if COOKIES_FROM_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    if extra:
        o.update(extra)
    return o


def _run(url: str, opts: dict, download: bool = False) -> dict:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download) or {}
    except DownloadError as e:
        msg = str(e)
        if "not a bot" in msg or "Sign in to confirm" in msg:
            raise YouTubeError(BOT_HINT) from e
        raise YouTubeError(re.sub(r"\x1b\[[0-9;]*m", "", msg)[:300]) from e


def search(query: str, n: int) -> list[dict]:
    info = _run(f"ytsearch{n}:{query}", _opts({"extract_flat": "in_playlist"}))
    out = []
    for pos, e in enumerate(info.get("entries") or []):
        if not e or not e.get("id") or "/shorts/" in (e.get("url") or ""):
            continue
        out.append({
            "id": e["id"],
            "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or "",
            "duration": e.get("duration"),
            "view_count": e.get("view_count"),
            "position": pos,
        })
    return out


def _cache_get(vid: str):
    with db() as c:
        row = c.execute("SELECT data, fetched_at FROM video_cache WHERE video_id=?", (vid,)).fetchone()
    if row and time.time() - row["fetched_at"] < CACHE_TTL:
        return json.loads(row["data"])
    return None


def _cache_put(vid: str, data: dict):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO video_cache(video_id, data, fetched_at) VALUES(?,?,?)",
                  (vid, json.dumps(data), time.time()))


def _http_get(url: str, limit: int = 3_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit)


def _base(info: dict, vid: str) -> dict:
    return {
        "id": vid,
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "channel_id": info.get("channel_id"),
        "channel_follower_count": info.get("channel_follower_count"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "description": (info.get("description") or "")[:2000],
        "chapters": [
            {"start": int(ch.get("start_time") or 0), "end": int(ch.get("end_time") or 0),
             "title": ch.get("title") or ""}
            for ch in (info.get("chapters") or [])
        ],
        "tags": (info.get("tags") or [])[:20],
        "thumbnail": info.get("thumbnail"),
    }


def fetch_video(vid: str, deep: bool = False) -> dict:
    cached = _cache_get(vid)
    if cached and (cached.get("deep") or not deep):
        return cached
    url = f"https://www.youtube.com/watch?v={vid}"
    if not deep:
        info = _run(url, _opts())
        data = _base(info, vid)
        data["deep"] = False
        _cache_put(vid, data)
        return data

    with tempfile.TemporaryDirectory() as tmp:
        opts = _opts({
            "getcomments": True,
            "extractor_args": {"youtube": {"max_comments": [str(COMMENTS_PER_VIDEO)],
                                           "comment_sort": ["top"]}},
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "en"],
            "subtitlesformat": "json3/vtt/best",
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
        })
        info = _run(url, opts, download=True)
        data = _base(info, vid)
        data["comments"] = [
            {"text": (c.get("text") or "")[:500], "likes": c.get("like_count") or 0}
            for c in (info.get("comments") or [])[:COMMENTS_PER_VIDEO]
            if c.get("parent", "root") == "root"
        ]
        data["transcript"] = _read_subs(Path(tmp))
    data["frames"] = _storyboard_frames(info, vid)
    data["deep"] = True
    _cache_put(vid, data)
    return data


def _read_subs(folder: Path) -> list:
    files = [p for p in folder.iterdir() if p.suffix in (".json3", ".vtt")]

    def pref(p: Path):
        lang = p.name.split(".")[-2] if p.name.count(".") >= 2 else ""
        order = ["en", "en-orig", "en-US", "en-GB"]
        return (order.index(lang) if lang in order else 10, 0 if p.suffix == ".json3" else 1)

    for p in sorted(files, key=pref):
        try:
            raw = p.read_text(errors="ignore")
            entries = parse_json3(raw) if p.suffix == ".json3" else parse_vtt(raw)
            if entries:
                return chunk(entries)
        except Exception:
            continue
    return []


def parse_json3(raw: str) -> list:
    d = json.loads(raw)
    out = []
    for ev in d.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if text:
            out.append((ev.get("tStartMs", 0) / 1000.0, text))
    return out


_TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->")


def parse_vtt(raw: str) -> list:
    out, cur, prev = [], None, None
    for line in raw.splitlines():
        m = _TS.match(line.strip())
        if m:
            h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
            cur = h * 3600 + mi * 60 + s
            continue
        if cur is None or not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        t = re.sub(r"<[^>]+>", "", line).strip()
        if t and t != prev:
            out.append((cur, t))
            prev = t
    return out


def chunk(entries: list, window: float = 20.0) -> list:
    out, start, buf = [], None, []
    for t, text in entries:
        if start is None:
            start = t
        if t - start >= window and buf:
            out.append([int(start), " ".join(buf)])
            start, buf = t, []
        buf.append(text)
    if buf:
        out.append([int(start or 0), " ".join(buf)])
    return out


def _storyboard_frames(info: dict, vid: str) -> list:
    boards = [f for f in (info.get("formats") or [])
              if (f.get("format_note") == "storyboard" or str(f.get("format_id", "")).startswith("sb"))
              and f.get("fragments")]
    if not boards:
        return []
    best = max(boards, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
    frags = best["fragments"]
    picks = sorted({min(len(frags) - 1, int(len(frags) * r)) for r in (0.15, 0.5, 0.85)})
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, idx in enumerate(picks):
        url = frags[idx].get("url")
        if not url:
            continue
        try:
            data = _http_get(url, 1_500_000)
            if len(data) < 2000:
                continue
            p = FRAMES_DIR / f"{vid}_{i}.jpg"
            p.write_bytes(data)
            paths.append(str(p))
        except Exception:
            continue
    return paths


def fmt_ts(sec) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def transcript_text(transcript: list, max_chars: int, parts: int = 6) -> str:
    lines = [f"[{fmt_ts(s)}] {t}" for s, t in transcript]
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full
    n, budget, out = len(lines), max_chars // parts, []
    for i in range(parts):
        j, size, buf = int(i * n / parts), 0, []
        while j < n and size < budget:
            buf.append(lines[j])
            size += len(lines[j]) + 1
            j += 1
        out.append("\n".join(buf))
    return "\n[…]\n".join(out)
