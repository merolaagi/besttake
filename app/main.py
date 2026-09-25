import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, llm, pipeline, scoring, templates
from . import db as dbm
from .config import (AI_PROVIDER, ALLOW_SIGNUP, ANTHROPIC_API_KEY, COOKIE_SECURE, FREE_COURSE_LIMIT, MODEL_SMART,
                     OLLAMA_MODEL, ROOT, VERSION)
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
        cur = c.execute("INSERT INTO users(email,name,pw_hash,plan,created_at) VALUES(?,?,?,?,?)",
                        (email, (body.name or "").strip()[:80], auth.hash_pw(body.password),
                         "pro" if first else "free", time.time()))
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
    return [
        {"id": "none", "label": "Basic, no AI", "available": True,
         "note": "Free and instant. Scores transcript coverage, comments, visuals and audience numbers. You give the lesson list."},
        {"id": "ollama", "label": f"Local model ({OLLAMA_MODEL})", "available": bool(st["running"] and st["text"]),
         "note": st["note"]},
        {"id": "anthropic", "label": f"Claude ({MODEL_SMART})", "available": bool(ANTHROPIC_API_KEY),
         "note": "Best judging and full lesson notes." if ANTHROPIC_API_KEY
         else "Add ANTHROPIC_API_KEY to .env and redeploy to enable."},
    ]


def default_engine(eng: list[dict]) -> str:
    ok = {e["id"] for e in eng if e["available"]}
    return AI_PROVIDER if AI_PROVIDER in ok else "none"


@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    with db() as c:
        used = c.execute("SELECT COUNT(*) FROM courses WHERE user_id=?", (user["id"],)).fetchone()[0]
    eng = engines()
    return {
        "user": user,
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
    if engine == "none" and not plan:
        raise HTTPException(400, "Basic mode needs a lesson list. Add one lesson per line, or pick a template.")
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
            "INSERT INTO courses(user_id,topic,level,goal,depth,profile,provider,title,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], topic[:200], level, (body.goal or "").strip()[:500], depth, profile, engine, topic[:200],
             "queued", now, now))
        cid = cur.lastrowid
    if plan:
        n = pipeline.insert_plan(cid, plan)
        dbm.log(cid, f"Using your list of {n} lessons")
    dbm.log(cid, f"Queued, ranking with {pipeline.ENGINE_NAMES[engine]}")
    pipeline.enqueue(cid)
    return {"id": cid}


@app.get("/api/courses")
def list_courses(user=Depends(auth.current_user)):
    with db() as c:
        rows = c.execute("""
          SELECT c.id, c.topic, c.title, c.status, c.depth, c.profile, c.provider, c.created_at,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id) AS total,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id AND l.status='ready') AS ready,
            (SELECT COUNT(*) FROM progress p JOIN lessons l ON l.id=p.lesson_id
               WHERE l.course_id=c.id AND p.user_id=? AND p.completed=1) AS done
          FROM courses c WHERE c.user_id=? ORDER BY c.created_at DESC""", (user["id"], user["id"])).fetchall()
    return {"courses": [dict(r) for r in rows]}


def _outline(c, cid: int, uid: int) -> list:
    rows = c.execute("""
      SELECT l.id, l.module_idx, l.module_title, l.idx, l.title, l.status, l.error,
        cd.title AS video_title, cd.channel AS video_channel, cd.score AS video_score,
        COALESCE(p.completed, 0) AS completed
      FROM lessons l
      LEFT JOIN candidates cd ON cd.lesson_id=l.id AND cd.rank=1
      LEFT JOIN progress p ON p.lesson_id=l.id AND p.user_id=?
      WHERE l.course_id=? ORDER BY l.module_idx, l.idx""", (uid, cid)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/courses/{cid}")
def get_course(cid: int, user=Depends(auth.current_user)):
    with db() as c:
        course = _own_course(c, cid, user["id"])
        lessons = _outline(c, cid, user["id"])
        events = [dict(r) for r in c.execute(
            "SELECT ts, msg FROM events WHERE course_id=? ORDER BY id DESC LIMIT 40", (cid,))][::-1]
    course["running"] = pipeline.is_running(cid)
    return {"course": course, "lessons": lessons, "events": events}


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
    return {
        "lesson": lesson,
        "course": {"id": course["id"], "title": course["title"], "topic": course["topic"],
                   "status": course["status"], "profile": course["profile"],
                   "provider": course.get("provider") or "none"},
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
