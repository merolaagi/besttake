import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
import re

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, llm, media, pipeline, protocol, scoring, settings, templates, tutor
from . import db as dbm
from . import youtube as yt
from .config import ALLOW_SIGNUP, COOKIE_SECURE, FREE_COURSE_LIMIT, ROOT, VERSION
from .db import db

ACTIVE = ("queued", "planning", "building")


@asynccontextmanager
async def lifespan(app):
    dbm.init()
    now = time.time()
    with db() as c:
        c.execute("UPDATE courses SET status='interrupted', updated_at=? WHERE status IN ('queued','planning','building')", (now,))
        c.execute("UPDATE lessons SET status='pending' WHERE status IN ('searching','judging','writing')")
    yield


app = FastAPI(title="BestTake", version=VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text().replace("{{VERSION}}", VERSION)


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


class SignupIn(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


def _login_response(resp: Response, user_id: int):
    tok = auth.new_session(user_id)
    resp.set_cookie(auth.COOKIE, tok, httponly=True, samesite="lax", secure=COOKIE_SECURE,
                    max_age=auth.SESSION_DAYS * 86400)


@app.post("/api/signup")
def signup(body: SignupIn, resp: Response):
    if not ALLOW_SIGNUP:
        raise HTTPException(403, "New accounts are closed right now.")
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password) < 8:
        raise HTTPException(400, "Use a password with at least 8 characters.")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = c.execute("INSERT INTO users(email,name,pw_hash,plan,is_owner,created_at) VALUES(?,?,?,?,?,?)",
                        (email, (body.name or "").strip()[:80], auth.hash_pw(body.password),
                         "pro" if first else "free", 1 if first else 0, time.time()))
        uid = cur.lastrowid
    _login_response(resp, uid)
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginIn, resp: Response):
    with db() as c:
        row = c.execute("SELECT id, pw_hash FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
    if not row or not auth.check_pw(body.password, row["pw_hash"]):
        raise HTTPException(401, "That email and password don't match.")
    _login_response(resp, row["id"])
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    tok = request.cookies.get(auth.COOKIE)
    if tok:
        auth.end_session(tok)
    resp.delete_cookie(auth.COOKIE)
    return {"ok": True}


def engines() -> list[dict]:
    st = llm.ollama_status()
    key = settings.get("ANTHROPIC_API_KEY")
    return [
        {"id": "none", "label": "Basic, no AI", "available": True,
         "note": "Free. Extracts the visuals and narration; ranks by relevance, coverage, comments, visuals and audience numbers."},
        {"id": "ollama", "label": f"Local model ({settings.get('OLLAMA_MODEL')})",
         "available": bool(st["running"] and st["text"]), "note": st["note"]},
        {"id": "anthropic", "label": f"Claude ({settings.get('BESTTAKE_MODEL')})", "available": bool(key),
         "note": "Best judging, and writes each build-up step in plain words, plus a quiz." if key
         else "Add an Anthropic API key in Settings to enable."},
    ]


def default_engine(eng: list[dict]) -> str:
    ok = {e["id"] for e in eng if e["available"]}
    want = settings.get("AI_PROVIDER")
    return want if want in ok else "none"


def owner_only(user=Depends(auth.current_user)):
    with db() as c:
        row = c.execute("SELECT is_owner FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or not row["is_owner"]:
        raise HTTPException(403, "Only the owner account can change settings.")
    return user


@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    with db() as c:
        used = c.execute("SELECT COUNT(*) FROM courses WHERE user_id=?", (user["id"],)).fetchone()[0]
    eng = engines()
    with db() as c:
        owner = bool(c.execute("SELECT is_owner FROM users WHERE id=?", (user["id"],)).fetchone()["is_owner"])
    return {
        "user": dict(user, is_owner=owner),
        "usage": {"courses": used, "limit": None if user["plan"] != "free" else FREE_COURSE_LIMIT},
        "version": VERSION,
        "engines": eng,
        "default_engine": default_engine(eng),
        "profiles": scoring.PROFILE_LABELS,
        "criteria": scoring.CRITERIA,
    }


@app.get("/api/templates")
def list_templates(user=Depends(auth.current_user)):
    return {"templates": templates.TEMPLATES}


class CourseIn(BaseModel):
    topic: str
    level: str = "beginner"
    depth: str = "standard"
    profile: str = "balanced"
    goal: str | None = None
    engine: str = "none"
    lessons: str | None = None


def _own_course(c, cid: int, uid: int):
    row = c.execute("SELECT * FROM courses WHERE id=? AND user_id=?", (cid, uid)).fetchone()
    if not row:
        raise HTTPException(404, "Course not found.")
    return dict(row)


@app.post("/api/courses")
def create_course(body: CourseIn, user=Depends(auth.current_user)):
    topic = body.topic.strip()
    if len(topic) < 3:
        raise HTTPException(400, "Describe the topic in a few words.")
    eng = {e["id"]: e for e in engines()}
    engine = body.engine if body.engine in eng else "none"
    if not eng[engine]["available"]:
        raise HTTPException(400, f"{eng[engine]['label']} isn't available: {eng[engine]['note']}")
    plan = templates.parse_lessons(body.lessons or "", topic)
    mode = "video" if engine == "none" else "tutor"
    if engine == "none" and not plan:
        t = templates.match_template(topic)
        if not t:
            raise HTTPException(400, "Basic mode needs a lesson list for this topic. Add one lesson per line, or pick a template.")
        plan = templates.parse_lessons(t["text"], topic)
    level = body.level if body.level in pipeline.LEVELS else "beginner"
    depth = body.depth if body.depth in pipeline.DEPTHS else "standard"
    profile = body.profile if body.profile in scoring.PROFILES else "balanced"
    with db() as c:
        if user["plan"] == "free":
            used = c.execute("SELECT COUNT(*) FROM courses WHERE user_id=?", (user["id"],)).fetchone()[0]
            if used >= FREE_COURSE_LIMIT:
                raise HTTPException(402, f"The free plan includes {FREE_COURSE_LIMIT} courses. Delete one to build another, or upgrade to Pro.")
            active = c.execute(f"SELECT COUNT(*) FROM courses WHERE user_id=? AND status IN {ACTIVE}",
                               (user["id"],)).fetchone()[0]
            if active:
                raise HTTPException(409, "Your other course is still building. Start this one when it finishes.")
        now = time.time()
        cur = c.execute(
            "INSERT INTO courses(user_id,topic,level,goal,depth,profile,provider,mode,plan,title,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], topic[:200], level, (body.goal or "").strip()[:500], depth, profile, engine, mode,
             json.dumps({"user_lessons": plan}) if (mode == "tutor" and plan) else None, topic[:200],
             "queued", now, now))
        cid = cur.lastrowid
    if plan and mode == "video":
        n = pipeline.insert_plan(cid, plan)
        dbm.log(cid, f"Using your list of {n} lessons")
    dbm.log(cid, f"Queued in {'tutor' if mode == 'tutor' else 'video build-up'} mode with {pipeline.ENGINE_NAMES[engine]}")
    pipeline.enqueue(cid)
    return {"id": cid}


@app.get("/api/courses")
def list_courses(user=Depends(auth.current_user)):
    with db() as c:
        rows = c.execute("""
          SELECT c.id, c.topic, c.title, c.status, c.depth, c.profile, c.provider, c.mode, c.created_at,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id) AS total,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id AND l.status='ready') AS ready,
            (SELECT COUNT(*) FROM progress p JOIN lessons l ON l.id=p.lesson_id
               WHERE l.course_id=c.id AND p.user_id=? AND p.completed=1) AS done
          FROM courses c WHERE c.user_id=? ORDER BY c.created_at DESC""", (user["id"], user["id"])).fetchall()
    return {"courses": [dict(r) for r in rows]}


def _outline(c, cid: int, uid: int) -> list:
    rows = c.execute("""
      SELECT l.id, l.module_idx, l.module_title, l.idx, l.title, l.status, l.error, l.meta,
        cd.title AS video_title, cd.channel AS video_channel, cd.score AS video_score,
        COALESCE(p.completed, 0) AS completed
      FROM lessons l
      LEFT JOIN candidates cd ON cd.lesson_id=l.id AND cd.rank=1
      LEFT JOIN progress p ON p.lesson_id=l.id AND p.user_id=?
      WHERE l.course_id=? ORDER BY l.module_idx, l.idx""", (uid, cid)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["stage"] = (json.loads(d.pop("meta") or "{}")).get("stage") or ""
        out.append(d)
    return out


@app.get("/api/courses/{cid}")
def get_course(cid: int, user=Depends(auth.current_user)):
    with db() as c:
        course = _own_course(c, cid, user["id"])
        lessons = _outline(c, cid, user["id"])
        events = [dict(r) for r in c.execute(
            "SELECT ts, msg FROM events WHERE course_id=? ORDER BY id DESC LIMIT 40", (cid,))][::-1]
        rules, seen = [], set()
        for r in c.execute("SELECT id, title, content FROM lessons WHERE course_id=? AND status='ready' ORDER BY module_idx, idx", (cid,)):
            for rule in (json.loads(r["content"] or "{}").get("rules") or []):
                if rule.strip().lower() not in seen:
                    seen.add(rule.strip().lower())
                    rules.append({"rule": rule, "lesson": r["title"], "lesson_id": r["id"]})
        gaps = [dict(r) for r in c.execute("""
          SELECT a.missing, COUNT(*) AS times, MAX(a.lesson_id) AS lesson_id FROM answers a JOIN lessons l ON l.id=a.lesson_id
          WHERE l.course_id=? AND a.user_id=? AND a.missing!='' AND a.verdict!='got_it'
          GROUP BY lower(a.missing) ORDER BY times DESC LIMIT 12""", (cid, user["id"]))]
    course["running"] = pipeline.is_running(cid)
    course["plan"] = json.loads(course.get("plan") or "{}")
    course["plan"].pop("user_lessons", None)
    return {"course": course, "lessons": lessons, "events": events, "rulebook": rules, "gaps": gaps}


@app.post("/api/courses/{cid}/resume")
def resume_course(cid: int, user=Depends(auth.current_user)):
    with db() as c:
        course = _own_course(c, cid, user["id"])
        if pipeline.is_running(cid):
            return {"ok": True}
        c.execute("UPDATE lessons SET status='pending', error=NULL WHERE course_id=? AND status!='ready'", (cid,))
    dbm.log(cid, "Resuming")
    pipeline.enqueue(course["id"])
    return {"ok": True}


@app.delete("/api/courses/{cid}")
def delete_course(cid: int, user=Depends(auth.current_user)):
    with db() as c:
        _own_course(c, cid, user["id"])
        if pipeline.is_running(cid):
            raise HTTPException(409, "This course is still building. Delete it when the build stops.")
        ids = [r[0] for r in c.execute("SELECT id FROM lessons WHERE course_id=?", (cid,))]
        for lid in ids:
            c.execute("DELETE FROM candidates WHERE lesson_id=?", (lid,))
            c.execute("DELETE FROM progress WHERE lesson_id=?", (lid,))
        c.execute("DELETE FROM lessons WHERE course_id=?", (cid,))
        c.execute("DELETE FROM events WHERE course_id=?", (cid,))
        c.execute("DELETE FROM courses WHERE id=?", (cid,))
    return {"ok": True}


def _own_lesson(c, lid: int, uid: int):
    row = c.execute("SELECT l.* FROM lessons l JOIN courses c ON c.id=l.course_id WHERE l.id=? AND c.user_id=?",
                    (lid, uid)).fetchone()
    if not row:
        raise HTTPException(404, "Lesson not found.")
    return dict(row)


@app.get("/api/lessons/{lid}")
def get_lesson(lid: int, user=Depends(auth.current_user)):
    with db() as c:
        lesson = _own_lesson(c, lid, user["id"])
        course = _own_course(c, lesson["course_id"], user["id"])
        outline = _outline(c, course["id"], user["id"])
        cands = [dict(r) for r in c.execute("SELECT * FROM candidates WHERE lesson_id=? ORDER BY rank", (lid,))]
        prog = c.execute("SELECT completed, quiz_score FROM progress WHERE user_id=? AND lesson_id=?",
                         (user["id"], lid)).fetchone()
    for cd in cands:
        for k in ("meta", "signals", "judge", "contributions"):
            cd[k] = json.loads(cd[k] or "{}")
    ids = [l["id"] for l in outline]
    i = ids.index(lid)
    lesson["concepts"] = json.loads(lesson["concepts"] or "[]")
    lesson["queries"] = json.loads(lesson["queries"] or "[]")
    lesson["content"] = json.loads(lesson["content"]) if lesson["content"] else None
    lesson["meta"] = json.loads(lesson.get("meta") or "{}")
    return {
        "lesson": lesson,
        "course": {"id": course["id"], "title": course["title"], "topic": course["topic"],
                   "status": course["status"], "profile": course["profile"],
                   "provider": course.get("provider") or "none", "mode": course.get("mode") or "video",
                   "engine_available": any(e["id"] == (course.get("provider") or "none") and e["available"] for e in engines())},
        "outline": outline,
        "candidates": cands,
        "weights": scoring.PROFILES.get(course["profile"] or "balanced"),
        "prev": ids[i - 1] if i > 0 else None,
        "next": ids[i + 1] if i + 1 < len(ids) else None,
        "position": i + 1,
        "total": len(ids),
        "progress": dict(prog) if prog else {"completed": 0, "quiz_score": None},
    }


class ProgressIn(BaseModel):
    completed: bool | None = None
    quiz_score: float | None = None


@app.post("/api/lessons/{lid}/progress")
def set_progress(lid: int, body: ProgressIn, user=Depends(auth.current_user)):
    with db() as c:
        _own_lesson(c, lid, user["id"])
        c.execute("INSERT OR IGNORE INTO progress(user_id,lesson_id,completed,updated_at) VALUES(?,?,0,?)",
                  (user["id"], lid, time.time()))
        if body.completed is not None:
            c.execute("UPDATE progress SET completed=?, updated_at=? WHERE user_id=? AND lesson_id=?",
                      (1 if body.completed else 0, time.time(), user["id"], lid))
        if body.quiz_score is not None:
            c.execute("UPDATE progress SET quiz_score=?, updated_at=? WHERE user_id=? AND lesson_id=?",
                      (max(0.0, min(1.0, body.quiz_score)), time.time(), user["id"], lid))
    return {"ok": True}


@app.post("/api/lessons/{lid}/rebuild")
def rebuild_lesson(lid: int, user=Depends(auth.current_user)):
    with db() as c:
        lesson = _own_lesson(c, lid, user["id"])
        if pipeline.is_running(lesson["course_id"]):
            raise HTTPException(409, "The course is still building. Try again when it finishes.")
        c.execute("DELETE FROM candidates WHERE lesson_id=?", (lid,))
        c.execute("UPDATE lessons SET status='pending', content=NULL, winner_video=NULL, error=NULL WHERE id=?", (lid,))
    dbm.log(lesson["course_id"], f"Rebuilding “{lesson['title']}”")
    pipeline.enqueue(lesson["course_id"])
    return {"ok": True}


class SettingsIn(BaseModel):
    values: dict = {}
    clear: list = []


@app.get("/api/settings")
def get_settings(user=Depends(owner_only)):
    return {"settings": settings.public(), "ffmpeg": bool(yt.ffmpeg_path())}


@app.post("/api/settings")
def save_settings(body: SettingsIn, user=Depends(owner_only)):
    if body.values.get("AI_PROVIDER") not in (None, "none", "ollama", "anthropic"):
        raise HTTPException(400, "Unknown engine.")
    settings.save(body.values, body.clear)
    return {"settings": settings.public()}


class TestIn(BaseModel):
    target: str


@app.post("/api/settings/test")
def test_settings(body: TestIn, user=Depends(owner_only)):
    try:
        if body.target == "anthropic":
            return {"ok": True, "message": llm.test_anthropic()}
        if body.target == "ollama":
            st = llm.ollama_status()
            return {"ok": bool(st["running"] and st["text"]), "message": st["note"]}
        if body.target == "youtube":
            key = settings.get("YOUTUBE_API_KEY")
            if not key:
                return {"ok": False, "message": "No YouTube API key saved."}
            n = len(yt.api_search("system design caching", 3, key))
            return {"ok": n > 0, "message": f"Connected. The test search returned {n} videos."}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
    raise HTTPException(400, "Unknown test.")


_MEDIA_NAME = re.compile(r"^[0-9_]+\.(jpg|mp4)$")


@app.get("/media/{vid}/{name}")
def media_file(vid: str, name: str, user=Depends(auth.current_user)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid) or not _MEDIA_NAME.match(name):
        raise HTTPException(404, "Not found.")
    p = media.MEDIA_DIR / vid / name
    if not p.exists():
        raise HTTPException(404, "Not found.")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


class CheckIn(BaseModel):
    beat: int | None = None
    challenge: bool = False
    answer: str


@app.post("/api/lessons/{lid}/check")
def check_answer(lid: int, body: CheckIn, user=Depends(auth.current_user)):
    answer = body.answer.strip()
    if len(answer) < 2:
        raise HTTPException(400, "Write your answer first.")
    with db() as c:
        lesson = _own_lesson(c, lid, user["id"])
        course = _own_course(c, lesson["course_id"], user["id"])
    content = json.loads(lesson["content"] or "{}")
    if body.challenge:
        item, beat_no = content.get("challenge") or {}, -1
    else:
        beats = content.get("beats") or []
        if body.beat is None or not 0 <= body.beat < len(beats):
            raise HTTPException(400, "Unknown question.")
        item, beat_no = beats[body.beat], body.beat
    if not item.get("question"):
        raise HTTPException(400, "This step has no question.")
    provider = course.get("provider") or "none"
    if provider == "none":
        raise HTTPException(400, "Answer checking needs a local model or Claude. Compare with the idea instead.")
    try:
        r = tutor.check(protocol.active(user["id"])["text"], provider, item["question"], item.get("answer", ""),
                        item.get("missing_primitive") or {}, answer[:3000])
    except llm.ProviderError as e:
        raise HTTPException(503, f"The checker isn't available: {e}")
    with db() as c:
        c.execute("INSERT INTO answers(user_id,lesson_id,beat,answer,verdict,feedback,missing,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (user["id"], lid, beat_no, answer[:3000], r["verdict"], r["feedback"], r["missing_primitive"], time.time()))
    return r


class SelfIn(BaseModel):
    beat: int | None = None
    challenge: bool = False
    got_it: bool


@app.post("/api/lessons/{lid}/self")
def self_assess(lid: int, body: SelfIn, user=Depends(auth.current_user)):
    with db() as c:
        lesson = _own_lesson(c, lid, user["id"])
    content = json.loads(lesson["content"] or "{}")
    item = (content.get("challenge") or {}) if body.challenge else \
        ((content.get("beats") or [])[body.beat] if body.beat is not None and 0 <= body.beat < len(content.get("beats") or []) else {})
    missing = "" if body.got_it else ((item.get("missing_primitive") or {}).get("name") or "")
    with db() as c:
        c.execute("INSERT INTO answers(user_id,lesson_id,beat,answer,verdict,feedback,missing,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (user["id"], lid, -1 if body.challenge else body.beat, "(self-assessed)",
                   "got_it" if body.got_it else "not_yet", "", missing, time.time()))
    return {"ok": True}


class GapIn(BaseModel):
    primitive: str
    after_lesson_id: int | None = None


@app.post("/api/courses/{cid}/gap")
def gap_lesson(cid: int, body: GapIn, user=Depends(auth.current_user)):
    name = body.primitive.strip()[:80]
    if not name:
        raise HTTPException(400, "Name the missing idea.")
    with db() as c:
        course = _own_course(c, cid, user["id"])
        if course.get("mode") != "tutor":
            raise HTTPException(400, "Gap lessons need a tutor-mode course.")
        if pipeline.is_running(cid):
            raise HTTPException(409, "The course is still building. Try again when it finishes.")
        after = None
        if body.after_lesson_id:
            after = c.execute("SELECT module_idx, idx, module_title FROM lessons WHERE id=? AND course_id=?",
                              (body.after_lesson_id, cid)).fetchone()
        if not after:
            after = c.execute("SELECT module_idx, idx, module_title FROM lessons WHERE course_id=? ORDER BY module_idx, idx LIMIT 1",
                              (cid,)).fetchone()
        ctx = pipeline.heuristics.topic_context(course["topic"])
        meta = {"stage": "reduce", "pressure": "", "problem": f"The learner is missing “{name}”",
                "property": "", "mechanism": name}
        c.execute("INSERT INTO lessons(course_id,module_idx,module_title,idx,title,concepts,queries,meta,status) "
                  "VALUES(?,?,?,?,?,?,?,?,'pending')",
                  (cid, after["module_idx"], after["module_title"], (after["idx"] or 0) + 0.5, f"Missing piece: {name}",
                   json.dumps([name]), json.dumps(templates.lesson_queries(name, ctx)), json.dumps(meta)))
    dbm.log(cid, f"Adding a lesson for the missing piece “{name}”")
    pipeline.enqueue(cid)
    return {"ok": True}


@app.get("/api/protocol")
def get_protocol(user=Depends(auth.current_user)):
    return {"active": protocol.active(user["id"]), "history": protocol.history(user["id"]),
            "notes": protocol.notes(user["id"])}


class ProtocolIn(BaseModel):
    text: str


@app.post("/api/protocol")
def save_protocol(body: ProtocolIn, user=Depends(auth.current_user)):
    if len(body.text.strip()) < 40:
        raise HTTPException(400, "The protocol is too short.")
    return {"active": protocol.save(user["id"], body.text)}


class NoteIn(BaseModel):
    text: str
    lesson_id: int | None = None


@app.post("/api/protocol/notes")
def add_protocol_note(body: NoteIn, user=Depends(auth.current_user)):
    if len(body.text.strip()) < 3:
        raise HTTPException(400, "Write a note first.")
    protocol.add_note(user["id"], body.lesson_id, body.text)
    return {"ok": True}
