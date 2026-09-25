import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v != "":
            os.environ.setdefault(k, v)


_load_env()

VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "dev"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_SMART = os.getenv("BESTTAKE_MODEL", "claude-sonnet-5")
MODEL_FAST = os.getenv("BESTTAKE_MODEL_FAST", "claude-haiku-4-5-20251001")
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "besttake.db"
FRAMES_DIR = DATA_DIR / "frames"
PORT = int(os.getenv("PORT", "47823"))
FREE_COURSE_LIMIT = int(os.getenv("FREE_COURSE_LIMIT", "3"))
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "1") == "1"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
SEARCH_RESULTS = int(os.getenv("SEARCH_RESULTS", "12"))
DEEP_CANDIDATES = int(os.getenv("DEEP_CANDIDATES", "5"))
COMMENTS_PER_VIDEO = int(os.getenv("COMMENTS_PER_VIDEO", "80"))
COURSE_WORKERS = int(os.getenv("COURSE_WORKERS", "2"))
COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
CACHE_TTL = 7 * 86400
AI_PROVIDER = os.getenv("AI_PROVIDER", "none").strip().lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
