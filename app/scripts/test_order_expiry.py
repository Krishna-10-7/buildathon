"""Order expiry sweep tests: abandoned checkouts release reserved stock.

Isolated temp DB (DB_PATH env before imports). Exit code 0 = all green.

  uv run python scripts/test_order_expiry.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from bazaar.audit import verify  # noqa: E402
from bazaar.db import SCHEMA, connect  # noqa: E402
from bazaar.orders import expire_stale_orders  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


conn = connect()
conn.executescript(SCHEMA)
conn.execute(
    "INSERT INTO products (sku, title, price_paise, cost_paise, stock,"
    " category) VALUES ('chai', 'Chai', 24900, 15000, 10, 'tea')")


def seed_order(oid: str, status: str, age_minutes: int) -> None:
    created = iso(datetime.now(timezone.utc) - timedelta(minutes=age_minutes))
    conn.execute(
        """INSERT INTO orders (id, buyer_session_id, channel, items_json,
           total_paise, status, correlation_id, created_at, updated_at)
           VALUES (?, 't', 'chat', ?, 24900, ?, 'c', ?, ?)""",
        (oid, json.dumps([{"sku": "chai", "qty": 2}]), status,
         created, created))


seed_order("old_created", "created", age_minutes=45)
seed_order("fresh_created", "created", age_minutes=5)
seed_order("old_paid", "paid", age_minutes=45)
seed_order("old_failed", "failed", age_minutes=45)
conn.commit()
conn.close()

n = expire_stale_orders(max_age_minutes=30)
ok("exactly the stale created order expires", n == 1, f"n={n}")

row = connect().execute(
    "SELECT status FROM orders WHERE id='old_created'").fetchone()
stock = connect().execute(
    "SELECT stock FROM products WHERE sku='chai'").fetchone()["stock"]
ok("expired order released its reservation",
   row["status"] == "expired" and stock == 12, f"status={row['status']} stock={stock}")

statuses = dict(connect().execute(
    "SELECT id, status FROM orders").fetchall())
ok("recent checkout untouched", statuses["fresh_created"] == "created")
ok("paid history untouched", statuses["old_paid"] == "paid")
ok("failed order kept for buyer retry", statuses["old_failed"] == "failed")

ok("sweep is idempotent", expire_stale_orders(max_age_minutes=30) == 0)

rec = connect().execute(
    "SELECT payload FROM audit_log WHERE action_type = 'order.expired_released'"
).fetchone()
payload = json.loads(rec["payload"])
ok("release audited with order ids and skus",
   payload["orders"] == ["old_created"] and payload["released_stock"] == ["chai"],
   str(payload))

good, count, bad = verify(connect())
ok("audit chain intact", good and bad is None, f"n={count} bad={bad}")

print(f"\nORDER EXPIRY: {PASS} CHECKS PASSED")
