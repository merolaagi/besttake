import base64
import json
import re
import threading
from pathlib import Path

from .config import ANTHROPIC_API_KEY, MODEL_SMART

_client = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            if not ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to .env and restart BestTake.")
            import anthropic
            _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=4)
    return _client


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


def image_block(path: str):
    data = Path(path).read_bytes()
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.b64encode(data).decode()}}


def ask_json(prompt: str, system: str, model: str | None = None, images: list | None = None,
             max_tokens: int = 4000, attempts: int = 2):
    content = [image_block(p) for p in (images or [])]
    content.append({"type": "text", "text": prompt})
    sys_prompt = system + "\n\nRespond with a single JSON value only. No prose before or after it, no markdown fences."
    last_err = None
    for _ in range(attempts):
        msg = client().messages.create(
            model=model or MODEL_SMART,
            max_tokens=max_tokens,
            system=sys_prompt,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        try:
            return extract_json(text)
        except Exception as e:
            last_err = e
            content[-1] = {"type": "text",
                           "text": prompt + "\n\nYour previous reply was not valid JSON. Return valid JSON only."}
    raise RuntimeError(f"The model did not return valid JSON ({last_err}).")
