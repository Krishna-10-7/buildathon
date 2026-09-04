"""Proof that the T1 regression test actually detects the old bug.

A new test that passes is worthless unless it fails on the code it was
written for. So: re-create the ORIGINAL envelope.py behaviour — swap the
process-global settings.db_path for the duration of the run — and run the
exact same concurrent-sampling assertion. If the assertion is any good it
must fail here.

This file is a demonstration, not a test. It is not part of the suite and
it is not collected by pytest.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar import db, envelope  # noqa: E402
from bazaar.config import settings  # noqa: E402

merchant_before = db.connect().execute(
    "SELECT COUNT(*) FROM audit_log").fetchone()[0]
demo_before = envelope.demo_audit_count()
print(f"merchant={merchant_before}  demo={demo_before}")


def run_sequence_old_way(buyer_ref: str) -> None:
    """The original implementation, verbatim in spirit:

    redirect the global, run, restore. _LOCK serialised envelope runs but
    did nothing for anybody else in the process — which is the bug.
    """
    original = settings.db_path
    settings.db_path = envelope._DEMO_PATH
    try:
        envelope.run_sequence(buyer_ref=buyer_ref)
    finally:
        settings.db_path = original


observed: list[int] = []
stop = threading.Event()


def sampler() -> None:
    while not stop.is_set():
        conn = db.connect()
        try:
            observed.append(conn.execute(
                "SELECT COUNT(*) FROM audit_log").fetchone()[0])
        finally:
            conn.close()
        time.sleep(0.001)


t = threading.Thread(target=sampler, daemon=True)
t.start()
try:
    run_sequence_old_way("proof-buyer")
finally:
    stop.set()
    t.join(timeout=5)

leaked = sorted({n for n in observed if n != merchant_before})
print(f"samples={len(observed)}  distinct sizes seen={leaked[:6]}")

detected = bool(leaked)
print()
if detected:
    print("PASS (for the proof): the assertion FIRES on the old code.")
    print("  -> a concurrent read saw ledger sizes that are not the")
    print(f"     merchant's {merchant_before}. The test has teeth.")
else:
    print("PROBLEM: the assertion did NOT fire on the old code.")
    print("  -> the new test is vacuous and would not have caught this.")
    sys.exit(1)

# and confirm the fixed path is clean under the same sampler
observed.clear()
stop.clear()
t2 = threading.Thread(target=sampler, daemon=True)
t2.start()
try:
    envelope.run_sequence(buyer_ref="proof-buyer")
finally:
    stop.set()
    t2.join(timeout=5)
leaked2 = sorted({n for n in observed if n != merchant_before})
print()
print(f"fixed path: samples={len(observed)} leaked={leaked2[:6]}")
print("PASS: fixed code is clean under the identical probe."
      if not leaked2 else "FAIL: fixed code still leaks.")
sys.exit(0 if not leaked2 else 1)
