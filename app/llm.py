import base64
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import settings
from .config import OLLAMA_NUM_CTX

_clients: dict = {}
_lock = threading.Lock()


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


def client():
    key = settings.get("ANTHROPIC_API_KEY")
    if not key:
        raise ProviderUnavailable("No Anthropic API key. Add one in Settings, or pick another ranking engine.")
    with _lock:
        if key not in _clients:
            import anthropic
            _clients.clear()
            _clients[key] = anthropic.Anthropic(api_key=key, max_retries=4)
        return _clients[key]


def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found")
    start = min(starts)
    end = max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[start:end + 1])


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def ollama_status() -> dict:
    OLLAMA_URL, OLLAMA_MODEL, OLLAMA_VISION_MODEL = (settings.get("OLLAMA_URL").rstrip("/"), settings.get("OLLAMA_MODEL"),
                                                     settings.get("OLLAMA_VISION_MODEL"))
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
    except Exception:
        return {"running": False, "text": False, "vision": False,
                "note": "Ollama isn't running. Choose Local model as the default engine in Settings and rerun the one-liner to install it."}

    def has(model):
        return model in names or (":" not in model and f"{model}:latest" in names)

    text, vision = has(OLLAMA_MODEL), has(OLLAMA_VISION_MODEL)
    if not text:
        note = f"Model {OLLAMA_MODEL} isn't downloaded. Choose Local model as the default engine in Settings and rerun the one-liner to pull it."
    elif not vision:
        note = f"Visual scoring uses frame analysis until {OLLAMA_VISION_MODEL} is downloaded."
    else:
        note = f"Runs on this Mac with {OLLAMA_MODEL} and {OLLAMA_VISION_MODEL}."
    return {"running": True, "text": text, "vision": vision, "note": note}


def _ollama_chat(model: str, system: str, prompt: str, images: list | None, max_tokens: int) -> str:
    OLLAMA_URL = settings.get("OLLAMA_URL").rstrip("/")
    user = {"role": "user", "content": prompt}
    if images:
        user["images"] = [_b64(p) for p in images]
    body = {
        "model": model, "stream": False, "format": "json",
        "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.2, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, user],
    }
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read())["message"]["content"]
    except urllib.error.URLError as e:
        raise ProviderUnavailable(f"Ollama isn't reachable at {OLLAMA_URL} ({e}).") from e


def _anthropic_chat(model: str | None, system: str, prompt: str, images: list | None, max_tokens: int) -> str:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(p)}}
               for p in (images or [])]
    content.append({"type": "text", "text": prompt})
    msg = client().messages.create(model=model or settings.get("BESTTAKE_MODEL"), max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": content}])
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")


def ask_json(prompt: str, system: str, provider: str = "anthropic", model: str | None = None,
             images: list | None = None, max_tokens: int = 4000, attempts: int = 2, vision: bool = False):
    if provider not in ("anthropic", "ollama"):
        raise ProviderUnavailable(f"No AI model is configured for the '{provider}' engine.")
    sys_prompt = system + "\n\nRespond with a single JSON value only. No prose before or after it, no markdown fences."
    last_err, p = None, prompt
    for _ in range(attempts):
        if provider == "ollama":
            text = _ollama_chat(model or settings.get("OLLAMA_VISION_MODEL" if vision else "OLLAMA_MODEL"), sys_prompt, p, images,
                                max_tokens)
        else:
            text = _anthropic_chat(model, sys_prompt, p, images, max_tokens)
        try:
            return extract_json(text)
        except Exception as e:
            last_err = e
            p = prompt + "\n\nYour previous reply was not valid JSON. Return valid JSON only."
    raise ProviderError(f"The model did not return valid JSON ({last_err}).")


def test_anthropic() -> str:
    msg = client().messages.create(model=settings.get("BESTTAKE_MODEL"), max_tokens=5,
                                   messages=[{"role": "user", "content": "Reply with OK."}])
    return f"Connected. {settings.get('BESTTAKE_MODEL')} replied."
