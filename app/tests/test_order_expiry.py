"""Order expiry sweep: abandoned checkouts release reserved stock.

Converted from scripts/test_order_expiry.py. The script shared one
module-level connection; each test now gets its own store and its own
seeded orders, so the sweep under test is the only thing that varies.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from bazaar.audit import verify
from bazaar.orders import expire_stale_orders
from tests.conftest import ok


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


@pytest.fixture
def shop(conn):
    """One product with 10 in stock, plus one order in each state.

    'old_created' (45 min, created) is the only row the sweep should
    touch; the other three exist to prove it does not over-reach.
    """
    conn.execute(
        "INSERT INTO products (sku, title, price_paise, cost_paise, stock,"
        " category) VALUES ('chai', 'Chai', 24900, 15000, 10, 'tea')")

    def seed(oid: str, status: str, age_minutes: int) -> None:
        created = iso(datetime.now(timezone.utc)
                      - timedelta(minutes=age_minutes))
        conn.execute(
            """INSERT INTO orders (id, buyer_session_id, channel, items_json,
               total_paise, status, correlation_id, created_at, updated_at)
               VALUES (?, 't', 'chat', ?, 24900, ?, 'c', ?, ?)""",
            (oid, json.dumps([{"sku": "chai", "qty": 2}]), status,
             created, created))

    seed("old_created", "created", 45)
    seed("fresh_created", "created", 5)
    seed("old_paid", "paid", 45)
    seed("old_failed", "failed", 45)
    conn.commit()
    return conn


def test_exactly_the_stale_created_order_expires(shop):
    n = expire_stale_orders(max_age_minutes=30)
    ok("exactly the stale created order expires", n == 1, f"n={n}")


def test_expired_order_released_its_reservation(shop):
    expire_stale_orders(max_age_minutes=30)
    row = shop.execute(
        "SELECT status FROM orders WHERE id='old_created'").fetchone()
    stock = shop.execute(
        "SELECT stock FROM products WHERE sku='chai'").fetchone()["stock"]
    # 10 left in stock + the 2 this order was holding = 12.
    ok("expired order released its reservation",
       row["status"] == "expired" and stock == 12,
       f"status={row['status']} stock={stock}")


def test_recent_checkout_untouched(shop):
    expire_stale_orders(max_age_minutes=30)
    row = shop.execute(
        "SELECT status FROM orders WHERE id='fresh_created'").fetchone()
    ok("recent checkout untouched", row["status"] == "created", row["status"])


def test_paid_history_untouched(shop):
    """A paid order is money that moved. No sweep may rewrite it."""
    expire_stale_orders(max_age_minutes=30)
    row = shop.execute(
        "SELECT status FROM orders WHERE id='old_paid'").fetchone()
    ok("paid history untouched", row["status"] == "paid", row["status"])


def test_failed_order_kept_for_buyer_retry(shop):
    expire_stale_orders(max_age_minutes=30)
    row = shop.execute(
        "SELECT status FROM orders WHERE id='old_failed'").fetchone()
    ok("failed order kept for buyer retry", row["status"] == "failed",
       row["status"])


def test_sweep_is_idempotent(shop):
    expire_stale_orders(max_age_minutes=30)
    ok("sweep is idempotent",
       expire_stale_orders(max_age_minutes=30) == 0)


def test_release_audited_with_order_ids_and_skus(shop):
    expire_stale_orders(max_age_minutes=30)
    rec = shop.execute(
        "SELECT payload FROM audit_log"
        " WHERE action_type = 'order.expired_released'").fetchone()
    payload = json.loads(rec["payload"])
    ok("release audited with order ids and skus",
       payload["orders"] == ["old_created"]
       and payload["released_stock"] == ["chai"], str(payload))


def test_audit_chain_intact(shop):
    expire_stale_orders(max_age_minutes=30)
    good, count, bad = verify(shop)
    ok("audit chain intact", good and bad is None, f"n={count} bad={bad}")
