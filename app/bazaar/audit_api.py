"""Read-only HTTP edge over the audit ledger for the Control Tower.

Exposes recent records and a live chain verdict computed by audit.verify().
No writes exist here by design — the only writer is audit.append().
"""

from fastapi import APIRouter

from bazaar.audit import verify
from bazaar.db import connect

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/recent")
def recent(limit: int = 40) -> dict:
    limit = max(1, min(limit, 200))
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT seq, ts_utc, actor, action_type, payload,
                      prev_hash, self_hash, correlation_id
               FROM audit_log ORDER BY seq DESC LIMIT ?""", (limit,),
        ).fetchall()
        chain_ok, checked, bad_seq = verify(conn)
        return {
            "chain_ok": chain_ok,
            "records_checked": checked,
            "first_bad_seq": bad_seq,
            "records": [dict(r) for r in reversed(rows)],  # oldest first
        }
    finally:
        conn.close()
