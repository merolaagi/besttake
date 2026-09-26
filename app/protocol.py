"""The tutor protocol: the method every tutor-mode lesson follows. Versioned per user so it can evolve."""
import time

from .db import db

DEFAULT = """Tutor Protocol v0.1

Hypothesis: Learning = Reduce -> Understand rules -> Reconstruct complexity.

1. ORIENT - Establish meaning and purpose before terminology. Break the topic's words down. Ask: what is this, and why does it exist?
2. REDUCE - Break the subject down until we reach primitives. Ask: what are the smallest ideas underneath this?
3. DERIVE - Build concepts from primitives instead of presenting facts to memorize. Ask: could we have discovered this ourselves?
4. CONNECT - Attach every new idea to something already understood. Ask: what does this resemble that I already know?
5. PROVE - Make the learner reconstruct or apply the idea in an unfamiliar situation. Ask: can I regenerate it without being told?

Core loop: Unknown topic -> Meaning -> Purpose -> Primitives -> Rules -> Derivation -> Connection -> Challenge -> Compression.
At the end of a section, compress many facts into a few generative rules.

Central rule: This requirement creates this problem. Therefore I need this property. What mechanism gives me that property?
(requirement -> problem -> property -> mechanism). Concepts must emerge because they are needed.

Use one tiny running example and keep increasing its requirements, so every concept appears as the answer to a new pressure.

Critical rule: When the learner doesn't understand something, don't immediately add another explanation. Find which earlier primitive is missing, and rebuild from there.

Treat this protocol as an experiment, not doctrine. Every failure is information for the next version."""


def active(user_id: int) -> dict:
    with db() as c:
        row = c.execute("SELECT id, version, text, created_at FROM protocols WHERE user_id=? ORDER BY version DESC LIMIT 1",
                        (user_id,)).fetchone()
    if row:
        return dict(row)
    return {"id": None, "version": 1, "text": DEFAULT, "created_at": None}


def save(user_id: int, text: str) -> dict:
    cur = active(user_id)
    version = (cur["version"] + 1) if cur["id"] else 2
    if not cur["id"]:
        with db() as c:
            c.execute("INSERT INTO protocols(user_id, version, text, created_at) VALUES(?,?,?,?)",
                      (user_id, 1, DEFAULT, time.time()))
    with db() as c:
        c.execute("INSERT INTO protocols(user_id, version, text, created_at) VALUES(?,?,?,?)",
                  (user_id, version, text.strip()[:20000], time.time()))
    return active(user_id)


def history(user_id: int) -> list:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, version, created_at, substr(text, 1, 120) AS preview FROM protocols WHERE user_id=? ORDER BY version DESC",
            (user_id,))]


def add_note(user_id: int, lesson_id: int | None, text: str):
    with db() as c:
        c.execute("INSERT INTO protocol_notes(user_id, lesson_id, text, created_at) VALUES(?,?,?,?)",
                  (user_id, lesson_id, text.strip()[:2000], time.time()))


def notes(user_id: int) -> list:
    with db() as c:
        return [dict(r) for r in c.execute("""
          SELECT n.id, n.text, n.created_at, l.title AS lesson_title, c.title AS course_title
          FROM protocol_notes n LEFT JOIN lessons l ON l.id=n.lesson_id LEFT JOIN courses c ON c.id=l.course_id
          WHERE n.user_id=? ORDER BY n.id DESC LIMIT 100""", (user_id,))]
