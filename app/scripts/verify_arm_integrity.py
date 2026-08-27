"""Mechanical arm-integrity audit for laptop-era sessions (incident 2026-08-25).

For each analyzed (paid) session: pull its actual payment timestamp from the
merchant DB, then walk the experiment.arm_switch ledger and require that the
CLAIMED arm held continuously from 2 s before session start until just after
payment. Any opposing switch inside that window voids the row. This replaces
reconstructed-timing guesswork with ledger+DB facts.

Run on the MERCHANT (VM1): uv run python scripts/verify_arm_integrity.py
"""

import json
import sqlite3
from datetime import datetime, timedelta

# (order_id, session_start_iso, claimed_arm) — paid sessions only,
# from artifacts/sessions_laptop.jsonl (incident era).
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


def parse(ts: str) -> datetime:
    if len(ts) == 19:  # session starts lack millis/offset
        ts += ".000+0000"
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z")
    return datetime.strptime(ts.replace("+00:00", "+0000"), FMT)


conn = sqlite3.connect("bazaar.db")
conn.row_factory = sqlite3.Row

switches = [(parse(r["ts_utc"]), json.loads(r["payload"])["arm"])
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
        print(f"{order_id} {claimed:9s} start={start_s}  VOID  (no paid row)")
        continue
    start, paid = parse(start_s), parse(pay["updated_at"])
    bad = [f"{ts.isoformat()}->{arm}" for ts, arm in switches
           if start - timedelta(seconds=2) <= ts <= paid + timedelta(seconds=1)
           and arm != claimed]
    verdict = "ADMIT" if not bad else "VOID "
    admitted += not bad
    print(f"{order_id} {claimed:9s} start={start_s} paid={pay['updated_at']}"
          f"  {verdict}  {'; '.join(bad) if bad else 'arm held throughout'}")

print(f"\n{admitted}/{len(SESSIONS)} paid rows pass mechanical arm-integrity")
