"""Buyer consent envelopes: bounded spending authority for agentic orders.

An AP2-flavored mandate is a signed envelope saying WHAT an agent may spend,
on WHICH categories, UNTIL when. The merchant enforces it at order creation:
a presented mandate must match its signature and satisfy every bound, or the
order is refused before Razorpay is ever involved. Spend draws down only when
payment captures — failed attempts never consume budget.

Signature = HMAC-SHA256 over canonical fields. The signing key derives from
MANDATE_SECRET (or, in dev, from the gateway secret) so envelopes are
tamper-evident without new required env vars.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bazaar import audit
from bazaar.config import settings
from bazaar.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _signing_key() -> bytes:
    base = settings.mandate_secret or f"{settings.rzp_key_secret}:mandates-v1"
    return hashlib.sha256(base.encode()).digest()


def _canonical(buyer_ref: str, budget_cap_paise: int, max_single_txn_paise: int,
               allowed_categories: list[str], expires_at: str) -> str:
    return json.dumps(
        {"buyer_ref": buyer_ref, "budget_cap_paise": budget_cap_paise,
         "max_single_txn_paise": max_single_txn_paise,
         "allowed_categories": sorted(allowed_categories),
         "expires_at": expires_at},
        sort_keys=True, separators=(",", ":"),
    )


def _sign(body: str) -> str:
    return hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str]


def create(buyer_ref: str, budget_cap_paise: int, max_single_txn_paise: int,
           allowed_categories: list[str] | None = None,
           ttl_hours: float = 24.0) -> dict:
    if budget_cap_paise <= 0 or max_single_txn_paise <= 0:
        raise ValueError("caps must be positive paise amounts")
    if max_single_txn_paise > budget_cap_paise:
        raise ValueError("single-txn cap cannot exceed budget cap")
    expires_at = (datetime.now(timezone.utc) +
                  timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    cats = sorted(set(allowed_categories or []))
    body = _canonical(buyer_ref, budget_cap_paise, max_single_txn_paise,
                      cats, expires_at)
    mid = "mnt_" + hmac.new(_signing_key(),
                            f"{buyer_ref}:{body}:{_now()}".encode(),
                            hashlib.sha256).hexdigest()[:12]
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO mandates
               (id, buyer_ref, budget_cap_paise, spent_paise,
                max_single_txn_paise, allowed_categories_json, expires_at,
                revoked_at, signature, created_at)
               VALUES (?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)""",
            (mid, buyer_ref, budget_cap_paise, max_single_txn_paise,
             json.dumps(cats), expires_at, _sign(body), _now()),
        )
        audit.append(conn, actor=f"buyer:{buyer_ref}", action_type="mandate.created",
                     payload={"mandate_id": mid, "budget_cap_paise": budget_cap_paise,
                              "max_single_txn_paise": max_single_txn_paise,
                              "allowed_categories": cats, "expires_at": expires_at},
                     correlation_id=mid)
        conn.commit()
        return get(mid)
    finally:
        conn.close()


def get(mandate_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM mandates WHERE id = ?",
                           (mandate_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def revoke(mandate_id: str) -> dict:
    row = get(mandate_id)
    if not row:
        raise LookupError(f"unknown mandate {mandate_id}")
    if row["revoked_at"]:
        return row  # idempotent
    conn = connect()
    try:
        conn.execute("UPDATE mandates SET revoked_at = ? WHERE id = ?",
                     (_now(), mandate_id))
        audit.append(conn, actor=f"buyer:{row['buyer_ref']}",
                     action_type="mandate.revoked",
                     payload={"mandate_id": mandate_id},
                     correlation_id=mandate_id)
        conn.commit()
    finally:
        conn.close()
    return get(mandate_id)


def check(mandate_id: str, total_paise: int,
          categories: list[str]) -> tuple[dict | None, Verdict]:
    """Verify signature + every bound against the stored envelope.
    Pure: no state mutation."""
    row = get(mandate_id)
    if not row:
        return None, Verdict(False, ["unknown mandate"])
    reasons: list[str] = []

    body = _canonical(row["buyer_ref"], row["budget_cap_paise"],
                      row["max_single_txn_paise"],
                      json.loads(row["allowed_categories_json"]),
                      row["expires_at"])
    if not hmac.compare_digest(_sign(body), row["signature"]):
        reasons.append("mandate signature mismatch — envelope altered")
    if row["revoked_at"]:
        reasons.append("mandate revoked")
    if row["expires_at"] <= _now():
        reasons.append("mandate expired")
    if total_paise > row["max_single_txn_paise"]:
        reasons.append(f"single txn {total_paise}p exceeds cap "
                       f"{row['max_single_txn_paise']}p")
    if row["spent_paise"] + total_paise > row["budget_cap_paise"]:
        reasons.append(f"budget exhausted: spent {row['spent_paise']}p + "
                       f"{total_paise}p > cap {row['budget_cap_paise']}p")
    allowed = json.loads(row["allowed_categories_json"])
    if allowed:
        bad = sorted({c for c in categories if c and c not in allowed})
        if bad:
            reasons.append(f"categories outside mandate: {','.join(bad)}")
    return row, Verdict(not reasons, reasons)


def draw_down(conn, mandate_id: str, amount_paise: int) -> None:
    """Add captured spend to the envelope. Runs INSIDE the webhook's
    transaction; idempotency comes from the payments-row insert guard."""
    conn.execute("UPDATE mandates SET spent_paise = spent_paise + ? WHERE id = ?",
                 (amount_paise, mandate_id))
