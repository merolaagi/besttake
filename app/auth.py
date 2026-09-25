import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

from .db import db

SESSION_DAYS = 30
COOKIE = "bt_session"


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return f"pbkdf2$240000${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def new_session(user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                  (tok, user_id, now, now + SESSION_DAYS * 86400))
    return tok


def end_session(tok: str):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (tok,))


def current_user(request: Request) -> dict:
    tok = request.cookies.get(COOKIE)
    if tok:
        with db() as c:
            row = c.execute(
                "SELECT u.id, u.email, u.name, u.plan FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token=? AND s.expires_at>?", (tok, time.time())).fetchone()
        if row:
            return dict(row)
    raise HTTPException(401, "Sign in to continue.")
