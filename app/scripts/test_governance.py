"""Governance core tests: pure policy rules + full proposal lifecycle.

Runs against an ISOLATED temp database (DB_PATH env set before imports), so
dev/prod data is never touched. Exit code 0 = all green.

  uv run python scripts/test_governance.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from bazaar import policy  # noqa: E402
from bazaar.audit import verify  # noqa: E402
from bazaar.db import connect  # noqa: E402
from bazaar.main import app  # noqa: E402
from bazaar.proposals import refresh_expired_discounts  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


def prod(price: int, cost: int, **kw) -> dict:
    return {"price_paise": price, "cost_paise": cost,
            "active": True, "discounted": False, **kw}


CTX = {
    "products": {
        "chai-250g": prod(24900, 15000),
        "kulhad": prod(19900, 12000),
        "thin-margin": prod(10000, 9500),
        "already-discounted": prod(24900, 15000, discounted=True),
    }
}

# ---- pure policy engine -----------------------------------------------------
d = policy.evaluate("apply_discount", {"sku": "chai-250g", "percent_off": 40, "days": 7}, CTX)
ok("discount clamped to max", d.status == "clamp" and d.final_params["percent_off"] == 15
   and d.final_params["days"] == 3, str(d))
ok("clamp recorded rule ids", "POL-DISC-001" in d.rule_ids and "POL-DISC-003" in d.rule_ids)

d = policy.evaluate("apply_discount", {"sku": "thin-margin", "percent_off": 15}, CTX)
ok("price floored at cost+5%", d.final_params["new_price_paise"] == 9975
   and "POL-PRICE-001" in d.rule_ids, str(d))

d = policy.evaluate("apply_discount", {"sku": "ghost", "percent_off": 10}, CTX)
ok("unknown sku denied", d.status == "deny")

d = policy.evaluate("apply_discount", {"sku": "already-discounted", "percent_off": 10}, CTX)
ok("concurrent discount denied", d.status == "deny" and "POL-DISC-004" in d.rule_ids)

d = policy.evaluate("create_bundle", {"skus": ["chai-250g", "kulhad"],
                                      "price_paise": 5000}, CTX)
lo = (27000 * 105) // 100
ok("bundle price clamped into profitable range",
   d.status == "clamp" and lo <= d.final_params["price_paise"] < 44800, str(d))

d = policy.evaluate("create_bundle", {"skus": ["thin-margin", "ghost"], "price_paise": 1}, CTX)
ok("bundle with dead component denied", d.status == "deny")

d = policy.evaluate("restock_alert", {"sku": "chai-250g"}, CTX)
ok("low-risk action allowed + auto-safe",
   d.status == "allow" and not d.needs_approval)

d = policy.evaluate("self_destruct_everything", {}, CTX)
ok("unknown action denied", d.status == "deny")

# ---- lifecycle over HTTP edge ------------------------------------------------
with TestClient(app) as client:  # startup hook builds schema on the temp db
    conn = connect()
    conn.execute(
        """INSERT INTO products (sku, title, description, price_paise, cost_paise,
           stock, category) VALUES ('masala-chai-250g', 'Masala Chai 250g', 'test',
           24900, 15000, 50, 'chai')""")
    conn.commit()
    conn.close()

    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "apply_discount",
        "params": {"sku": "masala-chai-250g", "percent_off": 40, "days": 30}})
    body = r.json()
    ok("agent proposal lands pending_review", r.status_code == 200
       and body["status"] == "pending_review"
       and body["decision"]["final_params"]["percent_off"] <= 15, str(body)[:200])
    pid = body["proposal_id"]

    asked_40_got = body["decision"]["final_params"]["percent_off"]

    r = client.post(f"/governance/proposals/{pid}/execute")
    ok("execute before approval blocked", r.status_code == 409)

    r = client.post(f"/governance/proposals/{pid}/decide",
                    json={"decided_by": "merchant-owner", "approved": True})
    ok("human approves", r.status_code == 200 and r.json()["status"] == "approved")

    r = client.post(f"/governance/proposals/{pid}/execute")
    ok("execute after approval works", r.status_code == 200
       and r.json()["executed"], str(r.json())[:160])

    from bazaar.db import connect as c2
    row = c2().execute("SELECT price_paise, base_price_paise, discount_until"
                       " FROM products WHERE sku='masala-chai-250g'").fetchone()
    ok("catalog shows discounted price with base intact",
       row["base_price_paise"] > row["price_paise"] and row["discount_until"] is not None,
       str(tuple(row)))

    # policy-deny path: terminal, never queued for review
    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "apply_discount",
        "params": {"sku": "masala-chai-250g", "percent_off": 50, "days": 1}})
    pid2 = r.json()["proposal_id"]
    ok("second discount denied by concurrent rule, terminal",
       r.json()["decision"]["status"] == "deny"
       and r.json()["status"] == "rejected", str(r.json())[:200])

    # low-risk auto-execution path
    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "restock_alert",
        "params": {"sku": "masala-chai-250g"}})
    ok("low-risk proposal auto-executed",
       r.json()["status"] == "auto_executed"
       and r.json().get("execution", {}).get("executed"), str(r.json())[:160])

    # expiry reversion (simulate window passed)
    conn = connect()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE products SET discount_until=? WHERE sku='masala-chai-250g'", (past,))
    conn.commit(); conn.close()
    n = refresh_expired_discounts()
    row = connect().execute("SELECT price_paise, base_price_paise, discount_until"
                            " FROM products WHERE sku='masala-chai-250g'").fetchone()
    ok("expired discount reverts to base price", n == 1
       and row["base_price_paise"] is None and row["discount_until"] is None)

    good, count, bad = verify(connect())
    ok("audit chain intact after governance ops", good and bad is None,
       f"n={count} bad={bad}")

print(f"\nGOVERNANCE CORE: {PASS} CHECKS PASSED")
