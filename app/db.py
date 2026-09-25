import sqlite3
import time
from contextlib import contextmanager

from .config import DATA_DIR, DB_PATH, FRAMES_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, pw_hash TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free', created_at REAL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL, expires_at REAL);
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, topic TEXT NOT NULL, level TEXT, goal TEXT,
  depth TEXT, profile TEXT, title TEXT, summary TEXT, status TEXT NOT NULL DEFAULT 'queued',
  error TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS lessons(
  id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, module_idx INTEGER, module_title TEXT,
  idx INTEGER, title TEXT, concepts TEXT, queries TEXT, status TEXT NOT NULL DEFAULT 'pending',
  winner_video TEXT, content TEXT, error TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY, lesson_id INTEGER NOT NULL, video_id TEXT, title TEXT, channel TEXT,
  url TEXT, duration INTEGER, meta TEXT, signals TEXT, judge TEXT, score REAL,
  contributions TEXT, rank INTEGER);
CREATE TABLE IF NOT EXISTS video_cache(video_id TEXT PRIMARY KEY, data TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS progress(
  user_id INTEGER, lesson_id INTEGER, completed INTEGER DEFAULT 0, quiz_score REAL,
  updated_at REAL, PRIMARY KEY(user_id, lesson_id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, course_id INTEGER, ts REAL, msg TEXT);
CREATE INDEX IF NOT EXISTS ix_lessons_course ON lessons(course_id);
CREATE INDEX IF NOT EXISTS ix_cand_lesson ON candidates(lesson_id);
CREATE INDEX IF NOT EXISTS ix_events_course ON events(course_id);
CREATE INDEX IF NOT EXISTS ix_courses_user ON courses(user_id);
"""


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


@contextmanager
def db():
    c = connect()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)


def log(course_id, msg):
    with db() as c:
        c.execute("INSERT INTO events(course_id, ts, msg) VALUES(?,?,?)", (course_id, time.time(), msg))
