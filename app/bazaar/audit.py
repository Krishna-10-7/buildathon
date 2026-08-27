"""Hash-chained, tamper-evident audit ledger (research/04 section 3).

Each record: self_hash = sha256(prev_hash | ts | actor | action_type | payload).
verify() replays the whole chain and reports the first break.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _digest(prev_hash: str, ts: str, actor: str, action_type: str, body: str) -> str:
    return hashlib.sha256(
        f"{prev_hash}|{ts}|{actor}|{action_type}|{body}".encode()
    ).hexdigest()


def append(
    conn: sqlite3.Connection,
    actor: str,
    action_type: str,
    payload: dict,
    correlation_id: str,
) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    row = conn.execute(
        "SELECT self_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row[0] if row else "GENESIS"
    body = canonical(payload)
    self_hash = _digest(prev_hash, ts, actor, action_type, body)
    conn.execute(
        """INSERT INTO audit_log
           (ts_utc, actor, action_type, payload, prev_hash, self_hash, correlation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ts, actor, action_type, body, prev_hash, self_hash, correlation_id),
    )
    return self_hash


def verify(conn: sqlite3.Connection) -> tuple[bool, int, int | None]:
    """Returns (chain_ok, records_checked, first_bad_seq_or_None)."""
    prev = "GENESIS"
    n = 0
    for row in conn.execute(
        """SELECT seq, ts_utc, actor, action_type, payload, prev_hash, self_hash
           FROM audit_log ORDER BY seq"""
    ):
        if row["prev_hash"] != prev:
            return False, n, row["seq"]
        expected = _digest(
            row["prev_hash"], row["ts_utc"], row["actor"], row["action_type"], row["payload"]
        )
        if expected != row["self_hash"]:
            return False, n, row["seq"]
        prev = row["self_hash"]
        n += 1
    return True, n, None
