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

from bazaar import logging_setup, mandates
from bazaar.audit import append
from bazaar.db import connect
from bazaar.rzp import verify_webhook_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging_setup.log_for("webhooks")


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

    # One connection for the whole request, owned by ONE finally. The two
    # branches below used to open their own and never close either, which
    # leaks a file handle per webhook — and a leaked handle on Windows
    # pins the database file so it cannot be replaced or rotated.
    conn = connect()
    try:
        # Authenticate FIRST: unsigned input must never reach the payment
        # tables or claim a slot in the dedupe table.
        if not verify_webhook_signature(raw, signature):
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            # Recorded, not dropped: an unsigned POST at a payment endpoint
            # is an attempted forgery, and the only record that matters is
            # the one the attacker cannot delete — the hash chain.
            #
            # Nothing is deduped on here on purpose: an unauthenticated
            # caller must not be able to claim an event id and pre-block
            # the real one from ever being applied.
            append(
                conn,
                actor="razorpay:webhook",
                action_type="webhook.rejected_invalid_signature",
                payload={"event": payload.get("event", "unknown"),
                         "body_sha256": hashlib.sha256(raw).hexdigest()},
                correlation_id=hashlib.sha256(raw).hexdigest(),
            )
            conn.commit()
            # Hashed, not logged verbatim: an attacker can put anything in
            # a request body, and this line is the one a human reads first.
            log.warning("webhook signature rejected: body_sha256=%s event=%s",
                        hashlib.sha256(raw).hexdigest()[:16],
                        payload.get("event", "unknown"))
            raise HTTPException(status_code=400, detail="invalid signature")

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        event = payload.get("event", "unknown")
        event_id = header_event_id or hashlib.sha256(raw).hexdigest()

        # BEGIN IMMEDIATE takes the write lock up front. The dedupe below
        # used to be SELECT-then-INSERT, which is a check-then-act race:
        # two redeliveries of the same event arriving together both see
        # "not seen before" and BOTH apply the payment. Razorpay retries
        # on any non-2xx and can deliver twice on a timeout, so this is a
        # realistic arrival pattern, not a theoretical one.
        #
        # isolation_level=None turns off the sqlite3 module's implicit
        # BEGIN (which it only issues on INSERT/UPDATE/DELETE, too late to
        # protect the read) so the transaction boundary is explicit.
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")

        claimed = conn.execute(
            "INSERT OR IGNORE INTO webhook_events"
            " (id, event, signature_valid, payload_json, processed_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (event_id, event, raw.decode(errors="replace"), _now()),
        )
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        rp_order_id = entity.get("order_id")
        order_cid: str | None = None

        if claimed.rowcount == 0:
            # A real, validly-signed event we have already applied. Still
            # 200 — a non-2xx tells Razorpay to keep retrying, so refusing
            # a duplicate would guarantee more duplicates. It IS recorded
            # though: a replay is a fact about the system, and a replayed
            # capture the ledger did not apply must be visible in the same
            # chain as the one it did.
            append(
                conn, actor="razorpay:webhook",
                action_type="webhook.duplicate",
                payload={"event_id": event_id, "event": event,
                         "rp_order_id": rp_order_id,
                         "rp_payment_id": entity.get("id"),
                         "note": "validly signed replay; not re-applied"},
                correlation_id=event_id,
            )
            conn.commit()
            log.info("duplicate webhook ignored: event_id=%s event=%s",
                     event_id, event)
            return {"status": "duplicate", "event": event}

        if event in ("payment.captured", "order.paid") and rp_order_id:
            order = conn.execute(
                "SELECT id, status, mandate_id, correlation_id,"
                " mandate_reserved_paise FROM orders"
                " WHERE rp_order_id = ?",
                (rp_order_id,),
            ).fetchone()
            if order:
                order_cid = order["correlation_id"]
                attempt_no = 1 + conn.execute(
                    "SELECT COUNT(*) FROM payments WHERE order_id = ?",
                    (order["id"],)
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
                    # The budget was already HELD at order creation by
                    # mandates.reserve(), so capture only converts the hold
                    # into spend — drawing down again here would double-count.
                    # The one exception is a legacy row created before
                    # reserve() existed: it holds nothing, so fall back to
                    # the old behaviour rather than silently under-counting.
                    held = order["mandate_reserved_paise"] or 0
                    if held == 0:
                        mandates.draw_down(conn, order["mandate_id"],
                                           int(entity.get("amount") or 0))
                conn.execute(
                    "UPDATE orders SET status = 'paid', updated_at = ? "
                    "WHERE id = ? AND status IN ('created','attempting')",
                    (_now(), order["id"]),
                )
                append(
                    conn, actor="razorpay:webhook", action_type=event,
                    payload={"rp_payment_id": entity.get("id"),
                             "rp_order_id": rp_order_id,
                             "amount_paise": entity.get("amount"),
                             "method": entity.get("method")},
                    correlation_id=order["correlation_id"],
                )
        elif event == "payment.failed" and rp_order_id:
            order = conn.execute(
                "SELECT id, correlation_id, mandate_id, mandate_reserved_paise"
                " FROM orders WHERE rp_order_id = ?",
                (rp_order_id,),
            ).fetchone()
            if order:
                order_cid = order["correlation_id"]
                attempt_no = 1 + conn.execute(
                    "SELECT COUNT(*) FROM payments WHERE order_id = ?",
                    (order["id"],)
                ).fetchone()[0]
                conn.execute(
                    """INSERT OR IGNORE INTO payments
                       (id, order_id, attempt_no, rp_payment_id, method, amount_paise,
                        status, error_code, error_desc, idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?)""",
                    (
                        _row_id(order["id"], entity.get("id")),
                        order["id"], attempt_no, entity.get("id"),
                        entity.get("method"),
                        entity.get("amount"), entity.get("error_code"),
                        entity.get("error_description"),
                        f"{order['id']}:{attempt_no}", _now(),
                    ),
                )
                failed = conn.execute(
                    "UPDATE orders SET status = 'failed', updated_at = ? "
                    "WHERE id = ? AND status IN ('created','attempting')",
                    (_now(), order["id"]),
                )
                # Only give the hold back if we actually moved the order
                # into 'failed'. A late failure notice for an order already
                # marked paid must not refund budget that was really spent.
                if failed.rowcount == 1 and order["mandate_id"]:
                    held = order["mandate_reserved_paise"] or 0
                    if held > 0:
                        mandates.release(conn, order["mandate_id"], held)
                append(
                    conn, actor="razorpay:webhook", action_type=event,
                    payload={"rp_payment_id": entity.get("id"),
                             "rp_order_id": rp_order_id,
                             "error_code": entity.get("error_code"),
                             "error_description": entity.get("error_description")},
                    correlation_id=order["correlation_id"],
                )
        else:
            append(
                conn, actor="razorpay:webhook", action_type=event,
                payload={"note": "unhandled/orphan event",
                         "rp_order_id": rp_order_id},
                correlation_id=event_id,
            )

        conn.commit()
        # Bound to the ORDER's id, not the event's: the question you ask at
        # 02:00 is "what happened to this order", not "what did this
        # delivery do". One id therefore has to cover both the buyer's
        # request and Razorpay's callback for it.
        with logging_setup.bind(order_cid or event_id):
            log.info("webhook applied: event_id=%s event=%s order=%s "
                     "payment=%s amount=%sp", event_id, event,
                     rp_order_id or "-", entity.get("id") or "-",
                     entity.get("amount") or 0)
        return {"status": "accepted", "event": event}
    finally:
        # Every request opens its own connection. Without this the handler
        # leaks one per webhook, and SQLite connections are not free on a
        # 1 GB box. Closing with an uncommitted transaction rolls it back,
        # which is the right outcome if anything above raised.
        conn.close()
