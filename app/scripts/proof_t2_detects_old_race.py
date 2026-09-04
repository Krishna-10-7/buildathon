"""Proof that reserve() fixes a race that check()+draw_down() actually has.

A concurrency test whose outcome depends on winning a timing lottery is a
flake waiting to happen, so this does not rely on luck: two barriers force
the exact interleaving. Every thread reads the budget, THEN every thread
writes. That is precisely the check-then-act window, opened by hand.

Demonstration, not part of the suite.
"""

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "proof.db")

from bazaar import db, mandates  # noqa: E402

CAP = 100_000
EACH = 60_000       # two of these exceed CAP
THREADS = 10


def setup_db() -> sqlite3.Connection:
    c = db.connect()
    c.executescript(db.SCHEMA)
    db.migrate(c)
    c.commit()
    return c


def old_way(mandate_id: str) -> int:
    """check() then draw_down(), with the window forced open between them."""
    read_barrier = threading.Barrier(THREADS)
    write_barrier = threading.Barrier(THREADS)

    def worker() -> None:
        conn = db.connect()
        row, verdict = mandates.check(mandate_id, EACH, ["tea"])
        read_barrier.wait()      # everyone has now read spent=0
        if verdict.allowed:
            write_barrier.wait()
            conn2 = db.connect()
            try:
                mandates.draw_down(conn2, mandate_id, EACH)
                conn2.commit()
            finally:
                conn2.close()
        else:
            write_barrier.wait()
        conn.close()

    ts = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    c = db.connect()
    n = c.execute("SELECT spent_paise FROM mandates WHERE id = ?",
                  (mandate_id,)).fetchone()[0]
    c.close()
    return n


def new_way(mandate_id: str) -> int:
    start = threading.Barrier(THREADS)

    def worker() -> None:
        conn = db.connect()
        try:
            start.wait()
            _row, verdict = mandates.reserve(conn, mandate_id, EACH, ["tea"])
            if verdict.allowed:
                conn.commit()
        finally:
            conn.close()

    ts = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    c = db.connect()
    n = c.execute("SELECT spent_paise FROM mandates WHERE id = ?",
                  (mandate_id,)).fetchone()[0]
    c.close()
    return n


conn = setup_db()
old_env = mandates.create("old-buyer", CAP, CAP, ["tea"], ttl_hours=1.0, conn=conn)
new_env = mandates.create("new-buyer", CAP, CAP, ["tea"], ttl_hours=1.0, conn=conn)
conn.commit()
conn.close()

old_spent = old_way(old_env["id"])
new_spent = new_way(new_env["id"])

print(f"budget cap        {CAP}")
print(f"each reservation  {EACH}   (only one fits)")
print(f"threads           {THREADS}")
print()
print(f"check()+draw_down -> spent {old_spent}   "
      f"{'OVER CAP  <-- the bug' if old_spent > CAP else 'within cap'}")
print(f"reserve()         -> spent {new_spent}   "
      f"{'OVER CAP' if new_spent > CAP else 'within cap'}")

ok_old = old_spent > CAP      # the old path must be shown to overrun
ok_new = new_spent <= CAP     # the new path must hold the cap
print()
if ok_old:
    print("PASS: the old path demonstrably breaks the cap. The bug is real.")
else:
    print("NOTE: the forced window did not overrun this run.")
if ok_new:
    print("PASS: reserve() holds the cap under the identical attack.")
else:
    print("FAIL: reserve() broke the cap.")
sys.exit(0 if ok_new else 1)
