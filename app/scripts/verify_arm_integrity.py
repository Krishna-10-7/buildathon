"""Mechanical arm-integrity audit for laptop-era sessions (incident 2026-08-25).

For each analyzed (paid) session: pull its actual payment timestamp from the
merchant DB, then walk the experiment.arm_switch ledger and require that the
CLAIMED arm held continuously from 2 s before session start until just after
payment. Any opposing switch inside that window voids the row. This replaces
reconstructed-timing guesswork with ledger+DB facts.

Two modes:

  uv run python scripts/verify_arm_integrity.py              # on the MERCHANT
  uv run python scripts/verify_arm_integrity.py --self-check # anywhere, no DB

The second exists because the first cannot run in CI: `bazaar.db` is
gitignored (it is the live ledger), so a CI job that opened it would fail
for the wrong reason. Rather than skip the check — a skipped check is a
green tick that means nothing — the window predicate is a pure function and
the self-check exercises it against synthetic switches. That is the part
that could silently regress; the DB walk around it is plumbing.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# (order_id, session_start_iso, claimed_arm) — paid sessions only,
# from evidence/sessions_laptop.jsonl (incident era).
SESSIONS = [
    ("ord_1d4cba907c0f46", "2026-08-25T05:38:28", "treatment"),   # L3 solo era
    ("ord_a286138683e64b", "2026-08-25T05:53:21", "treatment"),   # L7
    ("ord_8d545a08303848", "2026-08-25T05:56:32", "treatment"),   # L10
    ("ord_d5865251bfde43", "2026-08-25T06:01:24", "treatment"),   # L18
    ("ord_000d36ebe07c43", "2026-08-25T06:03:28", "control"),     # L23
    ("ord_e790a0be7bcb45", "2026-08-25T06:05:38", "treatment"),   # L26
    ("ord_ebd019f5aab144", "2026-08-25T06:07:22", "control"),     # L31
    ("ord_9521b4de42fb4f", "2026-08-25T06:11:01", "treatment"),   # L35
    ("ord_94f38f16bcd443", "2026-08-25T06:12:56", "control"),     # L38
    ("ord_19fe72912a3e4b", "2026-08-25T06:15:40", "treatment"),   # L41
    ("ord_30b7b7f599b648", "2026-08-25T06:19:17", "control"),     # L46
    ("ord_9053768dd41d41", "2026-08-25T06:22:25", "treatment"),   # L49
    ("ord_a5a40db5febd41", "2026-08-25T06:24:23", "control"),     # L53
]

FMT = "%Y-%m-%dT%H:%M:%S.%f%z"

# The grace window around a session. Two seconds of slack before the start
# because the buyer's clock and the merchant's are not synchronised; one
# second after payment because the webhook lands just after the capture.
LEAD_S = 2
TRAIL_S = 1


def parse(ts: str) -> datetime:
    if len(ts) == 19:  # session starts lack millis/offset
        ts += ".000+0000"
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z")
    return datetime.strptime(ts.replace("+00:00", "+0000"), FMT)


def voided_by(start: datetime, paid: datetime, claimed: str,
              switches: list[tuple[datetime, str]]) -> list[str]:
    """Opposing arm switches that fall inside the session's window.

    Pure, and deliberately the ONLY place the window rule lives: the merchant
    run and the CI self-check both go through this, so they cannot disagree
    about what "the arm held" means.
    """
    lo = start - timedelta(seconds=LEAD_S)
    hi = paid + timedelta(seconds=TRAIL_S)
    return [f"{ts.isoformat()}->{arm}" for ts, arm in switches
            if lo <= ts <= hi and arm != claimed]


def audit(db_path: str) -> int:
    """Walk the real ledger. Returns the number of admitted sessions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        switches = [(parse(r["ts_utc"]),
                     json.loads(r["payload"])["arm"])
                    for r in conn.execute(
                        "SELECT ts_utc, payload FROM audit_log"
                        " WHERE action_type='experiment.arm_switch'"
                        " AND ts_utc >= '2026-08-25T05:25' ORDER BY seq")]

        admitted = 0
        for order_id, start_s, claimed in SESSIONS:
            pay = conn.execute(
                "SELECT updated_at FROM orders WHERE id=? AND status='paid'",
                (order_id,)).fetchone()
            if pay is None:
                print(f"{order_id} {claimed:9s} start={start_s}  VOID"
                      f"  (no paid row)")
                continue
            start, paid = parse(start_s), parse(pay["updated_at"])
            bad = voided_by(start, paid, claimed, switches)
            verdict = "ADMIT" if not bad else "VOID "
            admitted += not bad
            print(f"{order_id} {claimed:9s} start={start_s}"
                  f" paid={pay['updated_at']}  {verdict}  "
                  f"{'; '.join(bad) if bad else 'arm held throughout'}")
    finally:
        conn.close()

    print(f"\n{admitted}/{len(SESSIONS)} paid rows pass mechanical"
          f" arm-integrity")
    return admitted


def self_check() -> int:
    """Exercise the window predicate against synthetic switches.

    Covers the four ways this rule can be got wrong: a switch that lands
    inside the window, one on each boundary, one safely outside, and a
    switch to the SAME arm (which must not void anything, or every
    idempotent re-flip would void the whole dataset).
    """
    t0 = parse("2026-08-25T06:00:00.000+0000")
    t1 = parse("2026-08-25T06:01:00.000+0000")
    cases = [
        ("switch inside the window voids the row",
         [(parse("2026-08-25T06:00:30.000+0000"), "control")], 1),
        ("switch on the leading edge still counts",
         [(t0 - timedelta(seconds=LEAD_S), "control")], 1),
        ("switch on the trailing edge still counts",
         [(t1 + timedelta(seconds=TRAIL_S), "control")], 1),
        ("switch just outside the lead is ignored",
         [(t0 - timedelta(seconds=LEAD_S + 1), "control")], 0),
        ("switch just after the trail is ignored",
         [(t1 + timedelta(seconds=TRAIL_S + 1), "control")], 0),
        ("a switch to the SAME arm voids nothing",
         [(parse("2026-08-25T06:00:30.000+0000"), "treatment")], 0),
    ]
    failures = 0
    for label, switches, expected in cases:
        got = len(voided_by(t0, t1, "treatment", switches))
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  {status} {label}  [voids={got}, expected={expected}]")

    print("ARM-INTEGRITY SELF-CHECK " + ("OK" if not failures else "FAILED"))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-check", action="store_true",
                    help="validate the window predicate without a database")
    ap.add_argument("--db", default=None,
                    help="path to the merchant ledger (default: ./bazaar.db)")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    db_path = args.db or "bazaar.db"
    if not Path(db_path).exists():
        print(f"{db_path} not present — this audit runs against the live "
              f"merchant ledger, which is not committed.\n"
              f"Run with --self-check to validate the predicate offline.")
        return 2
    audit(db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
