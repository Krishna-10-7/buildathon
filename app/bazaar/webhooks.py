"""Razorpay webhook receiver.

Contract (docs): verify X-Razorpay-Signature = HMAC-SHA256(raw_body, webhook_secret),
respond 2XX within 5s, dedupe on X-Razorpay-Event-Id.
Invalid signatures are logged and rejected with 400; valid events are applied
idempotently and appended to the hash-chained audit ledger.
"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from bazaar import mandates
from bazaar.audit import append
from bazaar.config import settings
from bazaar.db import connect
from bazaar.rzp import verify_webhook_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _row_id(order_id: str, rp_payment_id: str | None) -> str:
    """Collision-proof per-(order,payment) row id; stable across redeliveries."""
    return "pay_" + hashlib.sha256(
        f"{order_id}:{rp_payment_id or ''}".encode()
    ).hexdigest()[:16]


@router.post("/razorpay")
async def razorpay(request: Request) -> dict:
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    header_event_id = request.headers.get("X-Razorpay-Event-Id")

    # Authenticate FIRST: unsigned input must never touch state or dedupe tables.
    if not verify_webhook_signature(raw, signature):
        conn = connect()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        append(
            conn,
            actor="razorpay:webhook",
            action_type="webhook.rejected_invalid_signature",
            payload={"event": payload.get("event", "unknown"),
                     "body_sha256": hashlib.sha256(raw).hexdigest()},
            correlation_id=hashlib.sha256(raw).hexdigest(),
        )
        conn.commit()
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    event = payload.get("event", "unknown")
    event_id = header_event_id or hashlib.sha256(raw).hexdigest()

    conn = connect()
    already = conn.execute(
        "SELECT 1 FROM webhook_events WHERE id = ?", (event_id,)
    ).fetchone()
    if already:
        return {"status": "duplicate", "event": event}

    conn.execute(
        "INSERT INTO webhook_events (id, event, signature_valid, payload_json, processed_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (event_id, event, 1, raw.decode(errors="replace"), _now()),
    )

    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    rp_order_id = entity.get("order_id")

    if event in ("payment.captured", "order.paid") and rp_order_id:
        order = conn.execute(
            "SELECT id, status, mandate_id, correlation_id FROM orders"
            " WHERE rp_order_id = ?",
            (rp_order_id,),
        ).fetchone()
        if order:
            attempt_no = 1 + conn.execute(
                "SELECT COUNT(*) FROM payments WHERE order_id = ?", (order["id"],)
            ).fetchone()[0]
            pay_cur = conn.execute(
                """INSERT OR IGNORE INTO payments
                   (id, order_id, attempt_no, rp_payment_id, method, amount_paise,
                    status, error_code, error_desc, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'captured', NULL, NULL, ?, ?)""",
                (
                    _row_id(order["id"], entity.get("id")),
                    order["id"], attempt_no, entity.get("id"),
                    entity.get("method"), entity.get("amount"),
                    f"{order['id']}:{attempt_no}", _now(),
                ),
            )
            if pay_cur.rowcount == 1 and order["mandate_id"]:
                # First time THIS payment is recorded -> draw the envelope
                # down once. Failed attempts never reach this branch.
                mandates.draw_down(conn, order["mandate_id"],
                                   int(entity.get("amount") or 0))
            conn.execute(
                "UPDATE orders SET status = 'paid', updated_at = ? "
                "WHERE id = ? AND status IN ('created','attempting')",
                (_now(), order["id"]),
            )
            append(
                conn, actor="razorpay:webhook", action_type=event,
                payload={"rp_payment_id": entity.get("id"), "rp_order_id": rp_order_id,
                         "amount_paise": entity.get("amount"), "method": entity.get("method")},
                correlation_id=order["correlation_id"],
            )
    elif event == "payment.failed" and rp_order_id:
        order = conn.execute(
            "SELECT id, correlation_id FROM orders WHERE rp_order_id = ?",
            (rp_order_id,),
        ).fetchone()
        if order:
            attempt_no = 1 + conn.execute(
                "SELECT COUNT(*) FROM payments WHERE order_id = ?", (order["id"],)
            ).fetchone()[0]
            conn.execute(
                """INSERT OR IGNORE INTO payments
                   (id, order_id, attempt_no, rp_payment_id, method, amount_paise,
                    status, error_code, error_desc, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?)""",
                (
                    _row_id(order["id"], entity.get("id")),
                    order["id"], attempt_no, entity.get("id"), entity.get("method"),
                    entity.get("amount"), entity.get("error_code"),
                    entity.get("error_description"), f"{order['id']}:{attempt_no}", _now(),
                ),
            )
            conn.execute(
                "UPDATE orders SET status = 'failed', updated_at = ? "
                "WHERE id = ? AND status IN ('created','attempting')",
                (_now(), order["id"]),
            )
            append(
                conn, actor="razorpay:webhook", action_type=event,
                payload={"rp_payment_id": entity.get("id"), "rp_order_id": rp_order_id,
                         "error_code": entity.get("error_code"),
                         "error_description": entity.get("error_description")},
                correlation_id=order["correlation_id"],
            )
    else:
        append(
            conn, actor="razorpay:webhook", action_type=event,
            payload={"note": "unhandled/orphan event", "rp_order_id": rp_order_id},
            correlation_id=event_id,
        )

    conn.commit()
    return {"status": "accepted", "event": event}
