import argparse

from . import db as dbm
from .db import db


def main():
    p = argparse.ArgumentParser(prog="python -m app.cli", description="BestTake admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="list accounts")
    sp = sub.add_parser("set-plan", help="set an account's plan")
    sp.add_argument("email")
    sp.add_argument("plan", choices=["free", "pro"])
    a = p.parse_args()
    dbm.init()
    with db() as c:
        if a.cmd == "users":
            for r in c.execute("SELECT u.email, u.plan, (SELECT COUNT(*) FROM courses WHERE user_id=u.id) n "
                               "FROM users u ORDER BY u.id"):
                print(f"{r['email']:40} {r['plan']:5} {r['n']} courses")
        else:
            n = c.execute("UPDATE users SET plan=? WHERE email=?", (a.plan, a.email.lower())).rowcount
            print("Updated." if n else "No account with that email.")


if __name__ == "__main__":
    main()
