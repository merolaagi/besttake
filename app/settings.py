"""Runtime settings. Values saved in the app (Settings page) win over .env, which wins over defaults."""
import os
import time

from .db import db

DEFAULTS = {
    "AI_PROVIDER": "none",
    "ANTHROPIC_API_KEY": "",
    "BESTTAKE_MODEL": "claude-sonnet-5",
    "YOUTUBE_API_KEY": "",
    "OLLAMA_URL": "http://127.0.0.1:11434",
    "OLLAMA_MODEL": "qwen2.5:14b",
    "OLLAMA_VISION_MODEL": "qwen2.5vl:7b",
}
SECRETS = {"ANTHROPIC_API_KEY", "YOUTUBE_API_KEY"}


def _stored(key: str):
    try:
        with db() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row and row["value"] else None
    except Exception:
        return None


def get(key: str) -> str:
    return (_stored(key) or os.getenv(key) or DEFAULTS.get(key, "")).strip()


def source(key: str) -> str:
    if _stored(key):
        return "app"
    if os.getenv(key):
        return "env"
    return "default"


def save(values: dict, clear: list | None = None):
    now = time.time()
    with db() as c:
        for k, v in (values or {}).items():
            if k not in DEFAULTS or v is None:
                continue
            v = str(v).strip()
            if k in SECRETS and not v:
                continue
            c.execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES(?,?,?)", (k, v, now))
        for k in clear or []:
            if k in DEFAULTS:
                c.execute("DELETE FROM settings WHERE key=?", (k,))


def public() -> dict:
    out = {}
    for k in DEFAULTS:
        v = get(k)
        if k in SECRETS:
            out[k] = {"set": bool(v), "hint": ("••••" + v[-4:]) if len(v) > 8 else ("set" if v else ""),
                      "source": source(k) if v else "none"}
        else:
            out[k] = {"value": v, "source": source(k)}
    return out
