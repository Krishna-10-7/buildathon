"""Order creation & lookup.

Prices are ALWAYS computed server-side from the catalog — client-sent amounts
are never trusted. Stock is reserved atomically at creation
(UPDATE ... WHERE stock >= qty guards the two-buyers-one-unit race).
"""

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bazaar import logging_setup, mandates
from bazaar.audit import append
from bazaar.bundles import skus_of as bundles_skus
from bazaar.config import settings
from bazaar.db import connect
from bazaar.proposals import refresh_expired_discounts
from bazaar.rzp import create_order as rp_create_order

router = APIRouter(prefix="/orders", tags=["orders"])
log = logging_setup.log_for("orders")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class OrderItem(BaseModel):
    sku: str
    qty: int = Field(ge=1, le=10)


class OrderIn(BaseModel):
    buyer_session_id: str
    items: list[OrderItem] = Field(min_length=1, max_length=20)
    channel: str = Field(default="mcp", pattern="^(mcp|acp|chat|x402)$")
    mandate_id: str | None = None


def _bundle_for(basket: Counter, bundles: list[dict]) -> dict | None:
    """Pure pricing rule: the cheapest active bundle whose sku multiset
    EXACTLY equals the basket. Partial baskets and supersets pay line sum —
    a bundle is a deal you opt into by buying exactly it."""
    for b in sorted(bundles, key=lambda x: x["price_paise"]):
        if Counter(bundles_skus(b["skus_json"])) == basket:
            return b
    return None


def expire_stale_orders(max_age_minutes: int = 30) -> int:
    """Abandoned checkouts (buyer walked away, risk challenge, driver crash)
    release their stock reservation. Same lazy pattern as discount expiry:
    no scheduler, swept before any new order prices against stock. Only
    'created' orders expire — 'failed' may still be retried by the buyer in
    the open checkout session."""
    conn = connect()
    try:
        cutoff = (datetime.now(timezone.utc) -
                  timedelta(minutes=max_age_minutes)).isoformat(
                      timespec="milliseconds")
        stale = conn.execute(
            "SELECT id, items_json, mandate_id, mandate_reserved_paise"
            " FROM orders"
            " WHERE status = 'created' AND created_at < ?", (cutoff,)
        ).fetchall()
        if not stale:
            return 0
        released_mandate_paise = 0
        for row in stale:
            for line in json.loads(row["items_json"]):
                conn.execute(
                    "UPDATE products SET stock = stock + ? WHERE sku = ?",
                    (line["qty"], line["sku"]))
            # An abandoned checkout must give its budget hold back, or a
            # buyer who walks away silently loses spending authority.
            if row["mandate_id"] and (row["mandate_reserved_paise"] or 0) > 0:
                mandates.release(conn, row["mandate_id"],
                                 row["mandate_reserved_paise"])
                released_mandate_paise += row["mandate_reserved_paise"]
            conn.execute(
                "UPDATE orders SET status = 'expired', updated_at = ?"
                " WHERE id = ?", (_now(), row["id"]))
        append(conn, actor="system", action_type="order.expired_released",
               payload={"orders": [r["id"] for r in stale],
                        "released_stock": sorted(
                            {ln["sku"] for r in stale
                             for ln in json.loads(r["items_json"])}),
                        "released_mandate_paise": released_mandate_paise},
               correlation_id=f"order-sweep-{_now()}")
        conn.commit()
        return len(stale)
    finally:
        conn.close()


@router.post("")
async def create_order(body: OrderIn) -> dict:
    refresh_expired_discounts()  # lazy price reversion; no scheduler needed
    expire_stale_orders()        # lazy stock release for abandoned checkouts
    conn = connect()
    total = 0
    lines: list[dict] = []
    order_id = "ord_" + uuid.uuid4().hex[:14]
    # Adopt the request's id rather than minting a second one. If the log
    # line and the ledger row carried different ids, "show me everything
    # about this order" would mean searching two systems and joining them
    # by hand — which is exactly the failure this is meant to remove.
    # A direct caller (a script, a test) has no request id, so it gets a
    # fresh one and its log lines carry that instead.
    correlation_id = logging_setup.current()
    if correlation_id == logging_setup.NO_CID:
        correlation_id = uuid.uuid4().hex

    line_categories: list[str] = []
    basket: Counter = Counter()
    mandate_row = None
    try:
        for item in body.items:
            basket[item.sku] += item.qty
            row = conn.execute(
                "SELECT sku, title, price_paise, active, category"
                " FROM products WHERE sku = ?",
                (item.sku,),
            ).fetchone()
            if not row or not row["active"]:
                raise HTTPException(status_code=404, detail=f"unknown sku: {item.sku}")
            cur = conn.execute(
                "UPDATE products SET stock = stock - ? WHERE sku = ? AND stock >= ?",
                (item.qty, item.sku, item.qty),
            )
            if cur.rowcount != 1:
                raise HTTPException(
                    status_code=409, detail=f"insufficient stock: {item.sku}"
                )
            total += row["price_paise"] * item.qty
            line_categories.append(row["category"])
            lines.append(
                {"sku": row["sku"], "title": row["title"], "qty": item.qty,
                 "unit_price_paise": row["price_paise"]}
            )

        # Bundle pricing, still server-side: an exact-match basket buys the
        # bundle at its approved price — never MORE than the line sum.
        list_total = total
        bundle_row = _bundle_for(
            basket,
            [dict(r) for r in conn.execute(
                "SELECT id, skus_json, price_paise FROM bundles WHERE active = 1")],
        )
        if bundle_row and bundle_row["price_paise"] < list_total:
            total = bundle_row["price_paise"]
            bundle_id = bundle_row["id"]
        else:
            bundle_id = None

        # Buyer's signed spending authority — enforced BEFORE the gateway
        # call; a refused order never reaches Razorpay.
        #
        # reserve() rather than check(): the check and the spend must be
        # one step, or two concurrent orders can each read the same
        # remaining budget and both pass. The reservation is held inside
        # this transaction, so the stock decrement and the budget hold
        # commit together or not at all — and a failure below rolls both
        # back without an explicit release.
        if body.mandate_id:
            mandate_row, verdict = mandates.reserve(
                conn, body.mandate_id, total, line_categories)
            if not verdict.allowed:
                conn.rollback()
                log.warning("mandate refused order: mandate=%s total=%dp "
                            "reasons=%s", body.mandate_id, total,
                            verdict.reasons)
                deny_conn = connect()
                try:
                    append(deny_conn, actor="api", action_type="order.mandate_denied",
                           payload={"mandate_id": body.mandate_id,
                                    "total_paise": total,
                                    "reasons": verdict.reasons},
                           correlation_id=correlation_id)
                    deny_conn.commit()
                finally:
                    deny_conn.close()
                raise HTTPException(
                    status_code=403,
                    detail={"code": "mandate_denied",
                            "reasons": verdict.reasons},
                )

        now = _now()
        conn.execute(
            """INSERT INTO orders
               (id, buyer_session_id, channel, items_json, total_paise, status,
                mandate_id, bundle_id, correlation_id, created_at, updated_at,
                mandate_reserved_paise)
               VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?)""",
            (order_id, body.buyer_session_id, body.channel,
             json.dumps(lines), total, body.mandate_id, bundle_id,
             correlation_id, now, now,
             total if body.mandate_id else 0),
        )

        rp = await rp_create_order(
            total, receipt=order_id, notes={"order_id": order_id}
        )
        conn.execute(
            "UPDATE orders SET rp_order_id = ?, updated_at = ? WHERE id = ?",
            (rp["id"], _now(), order_id),
        )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:  # Razorpay/transport failure -> order stays uncommitted
        conn.rollback()
        log.error("gateway error creating order %s: %s", order_id, exc)
        raise HTTPException(
            status_code=502, detail=f"payment gateway error: {exc}"
        ) from exc

    log.info("order created: order=%s rp=%s total=%dp skus=%d mandate=%s",
             order_id, rp["id"], total, len(lines), body.mandate_id or "-")
    append(
        conn, actor="api", action_type="order.create",
        payload={"order_id": order_id, "rp_order_id": rp["id"],
                 "total_paise": total, "items": lines, "channel": body.channel,
                 "mandate_id": body.mandate_id, "bundle_id": bundle_id,
                 "list_total_paise": list_total},
        correlation_id=correlation_id,
    )
    conn.commit()
    conn.close()

    return {
        "order_id": order_id,
        "rp_order_id": rp["id"],
        "amount_paise": total,
        "currency": "INR",
        "status": "created",
        "bundle_id": bundle_id,
        "savings_paise": max(0, list_total - total),
        "correlation_id": correlation_id,
        "checkout": {
            "key_id": settings.rzp_key_id,
            "rp_order_id": rp["id"],
            "amount_paise": total,
        },
    }


@router.get("")
async def list_orders(limit: int = 20) -> dict:
    """Newest-first feed for the Control Tower; no buyer PII beyond session id."""
    limit = max(1, min(limit, 100))
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, buyer_session_id, channel, total_paise, status,
                      attempt_no, correlation_id, created_at, updated_at
               FROM orders ORDER BY created_at DESC LIMIT ?""", (limit,),
        ).fetchall()
        return {"orders": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/{order_id}")
async def get_order(order_id: str) -> dict:
    conn = connect()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    payments = conn.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY attempt_no", (order_id,)
    ).fetchall()
    conn.close()
    return {
        **{k: order[k] for k in order.keys()},
        "items": json.loads(order["items_json"]),
        "payments": [dict(p) for p in payments],
    }
