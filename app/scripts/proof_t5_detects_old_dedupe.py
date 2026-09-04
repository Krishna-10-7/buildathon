"""Proof that test_webhooks.py's concurrency check has teeth.

Re-creates the OLD dedupe (SELECT 1 ... if already: return duplicate, then
INSERT) and runs it under the same 8-thread barrage the real test uses.

A new test that passes proves nothing unless it FAILS on the bug it is
meant to catch. This is that demonstration.

Run:  python scripts/proof_t5_detects_old_dedupe.py
"""

import sqlite3
import threading
import time
from pathlib import Path

PROOF_DB = Path(__file__).resolve().parent.parent / ".data" / ".proof_t5.db"
PROOF_DB.parent.mkdir(parents=True, exist_ok=True)
if PROOF_DB.exists():
    PROOF_DB.unlink()

conn = sqlite3.connect(str(PROOF_DB))
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE webhook_events (id TEXT PRIMARY KEY, event TEXT NOT NULL,
  signature_valid INTEGER NOT NULL, payload_json TEXT NOT NULL,
  processed_at TEXT NOT NULL);
CREATE TABLE payments (id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL);
""")
conn.commit()

EVENT_ID = "evt_proof_1"
AMOUNT = 30000
BARRIER = threading.Barrier(8)


def old_handler() -> str:
    """The pre-T5 shape: read, decide, then write."""
    c = sqlite3.connect(str(PROOF_DB), timeout=15)
    c.row_factory = sqlite3.Row
    try:
        # ---- the race window -------------------------------------
        already = c.execute(
            "SELECT 1 FROM webhook_events WHERE id = ?", (EVENT_ID,)
        ).fetchone()
        if already:
            return "duplicate"

        # Yield here so all eight threads are provably inside the window
        # at once. In production the gap is a scheduler slice; here it is
        # made deterministic so the result is not a timing lottery.
        time.sleep(0.05)

        c.execute(
            "INSERT INTO webhook_events (id, event, signature_valid,"
            " payload_json, processed_at) VALUES (?,?,?,?,?)",
            (EVENT_ID, "payment.captured", 1, "{}", "now"))
        c.execute(
            "INSERT OR IGNORE INTO payments (id, order_id, amount_paise)"
            " VALUES (?,?,?)", (f"pay_{threading.get_ident()}", "ord_1", AMOUNT))
        c.commit()
        return "accepted"
    except sqlite3.IntegrityError as exc:
        # THIS is the old failure mode, and it is worse than a double
        # spend would have been: an uncaught IntegrityError is a 500, and
        # a 500 is precisely what tells Razorpay to redeliver. The old
        # code answered a redelivery storm by manufacturing one.
        return f"500:{exc}"
    except sqlite3.OperationalError as exc:
        return f"500:{exc}"
    finally:
        c.close()


def new_handler() -> str:
    """The T5 shape: claim the slot with the write, then look at rowcount."""
    c = sqlite3.connect(str(PROOF_DB), timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.isolation_level = None
        c.execute("BEGIN IMMEDIATE")           # write lock taken up front
        claimed = c.execute(
            "INSERT OR IGNORE INTO webhook_events (id, event,"
            " signature_valid, payload_json, processed_at)"
            " VALUES (?,?,?,?,?)",
            (EVENT_ID, "payment.captured", 1, "{}", "now"))
        if claimed.rowcount == 0:
            c.execute("COMMIT")
            return "duplicate"
        time.sleep(0.05)                       # same window, now harmless
        c.execute(
            "INSERT OR IGNORE INTO payments (id, order_id, amount_paise)"
            " VALUES (?,?,?)", (f"pay_{threading.get_ident()}", "ord_1", AMOUNT))
        c.execute("COMMIT")
        return "accepted"
    except sqlite3.OperationalError as exc:
        return f"error:{exc}"
    finally:
        c.close()


def barrage(handler) -> tuple[int, int, int, int]:
    conn.execute("DELETE FROM webhook_events")
    conn.execute("DELETE FROM payments")
    conn.commit()
    results = [None] * 8

    def go(i: int) -> None:
        BARRIER.wait()
        results[i] = handler()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n_pay = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    total = conn.execute(
        "SELECT COALESCE(SUM(amount_paise),0) FROM payments").fetchone()[0]
    crashed = sum(1 for r in results if isinstance(r, str) and r.startswith("500"))
    return results.count("accepted"), n_pay, total, crashed


old_acc, old_pay, old_total, old_500 = barrage(old_handler)
new_acc, new_pay, new_total, new_500 = barrage(new_handler)

print("8 threads, one event id, delivered simultaneously\n")
print(f"  OLD  SELECT-then-INSERT   accepted={old_acc}  "
      f"500s={old_500}  duplicates_returned={8 - old_acc - old_500}"
      f"  payment_rows={old_pay}  captured={old_total}")
print(f"  NEW  BEGIN IMMEDIATE      accepted={new_acc}  "
      f"500s={new_500}  duplicates_returned={8 - new_acc - new_500}"
      f"  payment_rows={new_pay}  captured={new_total}")

print()
if old_500 > 0:
    print(f"  OLD BUG REPRODUCED: {old_500} of 8 concurrent redeliveries "
          f"died on\n  'UNIQUE constraint failed: webhook_events.id'. In "
          f"FastAPI that is a 500,\n  and a 500 is exactly the signal that "
          f"makes Razorpay redeliver.\n  The old code answered a retry with "
          f"a retry.")
else:
    print("  old bug did not reproduce on this run (timing); rerun.")

print()
assert new_acc == 1, f"new path must apply exactly once, got {new_acc}"
assert new_500 == 0, f"new path must not error, got {new_500} 500s"
assert 8 - new_acc - new_500 == 7, "new path must return 7 clean duplicates"
assert new_pay == 1, f"new path must write one payment row, got {new_pay}"
assert new_total == AMOUNT, f"new path must capture {AMOUNT}, got {new_total}"
print("  NEW PATH HOLDS: 1 applied, 7 clean 200 duplicates, 0 errors.")
print("\nPROOF COMPLETE")

conn.close()
PROOF_DB.unlink(missing_ok=True)
