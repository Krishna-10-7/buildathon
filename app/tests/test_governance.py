"""Governance core: pure policy rules + full proposal lifecycle.

Converted from scripts/test_governance.py.

`policy.py` is deliberately PURE — no database, no network — so the rule
half of this module needs no fixtures at all. The lifecycle half does,
because an agent proposal is a durable object that a human approves.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bazaar import policy
from bazaar.audit import verify
from bazaar.proposals import refresh_expired_discounts
from tests.conftest import ok


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


# ---- pure policy engine ---------------------------------------------------

def test_discount_clamped_to_max():
    d = policy.evaluate("apply_discount",
                        {"sku": "chai-250g", "percent_off": 40, "days": 7}, CTX)
    ok("discount clamped to max",
       d.status == "clamp" and d.final_params["percent_off"] == 15
       and d.final_params["days"] == 3, str(d))


def test_clamp_recorded_rule_ids():
    d = policy.evaluate("apply_discount",
                        {"sku": "chai-250g", "percent_off": 40, "days": 7}, CTX)
    ok("clamp recorded rule ids",
       "POL-DISC-001" in d.rule_ids and "POL-DISC-003" in d.rule_ids)


def test_price_floored_at_cost_plus_5pct():
    """The clamp is not a suggestion. An agent asking to sell below cost
    gets the floor, not a refusal — the proposal stays useful."""
    d = policy.evaluate("apply_discount",
                        {"sku": "thin-margin", "percent_off": 15}, CTX)
    ok("price floored at cost+5%",
       d.final_params["new_price_paise"] == 9975
       and "POL-PRICE-001" in d.rule_ids, str(d))


def test_unknown_sku_denied():
    d = policy.evaluate("apply_discount",
                        {"sku": "ghost", "percent_off": 10}, CTX)
    ok("unknown sku denied", d.status == "deny")


def test_concurrent_discount_denied():
    d = policy.evaluate("apply_discount",
                        {"sku": "already-discounted", "percent_off": 10}, CTX)
    ok("concurrent discount denied",
       d.status == "deny" and "POL-DISC-004" in d.rule_ids)


def test_bundle_price_clamped_into_profitable_range():
    d = policy.evaluate("create_bundle",
                        {"skus": ["chai-250g", "kulhad"], "price_paise": 5000},
                        CTX)
    lo = (27000 * 105) // 100
    ok("bundle price clamped into profitable range",
       d.status == "clamp" and lo <= d.final_params["price_paise"] < 44800,
       str(d))


def test_bundle_with_dead_component_denied():
    d = policy.evaluate("create_bundle",
                        {"skus": ["thin-margin", "ghost"], "price_paise": 1},
                        CTX)
    ok("bundle with dead component denied", d.status == "deny")


def test_low_risk_action_allowed_and_auto_safe():
    d = policy.evaluate("restock_alert", {"sku": "chai-250g"}, CTX)
    ok("low-risk action allowed + auto-safe",
       d.status == "allow" and not d.needs_approval)


def test_unknown_action_denied():
    """Default-deny: an action the policy has never heard of is refused,
    rather than waved through because no rule mentioned it."""
    d = policy.evaluate("self_destruct_everything", {}, CTX)
    ok("unknown action denied", d.status == "deny")


# ---- lifecycle over HTTP ---------------------------------------------------

@pytest.fixture
def catalog(conn):
    conn.execute(
        """INSERT INTO products (sku, title, description, price_paise,
           cost_paise, stock, category) VALUES ('masala-chai-250g',
           'Masala Chai 250g', 'test', 24900, 15000, 50, 'chai')""")
    conn.commit()
    return conn


def propose(client, **kw):
    body = {"actor": "growth-agent-v0", "action_type": "apply_discount",
            "params": {"sku": "masala-chai-250g", "percent_off": 40,
                       "days": 30}}
    body.update(kw)
    return client.post("/governance/proposals", json=body)


def test_agent_proposal_lands_pending_review(client, catalog):
    r = propose(client)
    body = r.json()
    ok("agent proposal lands pending_review",
       r.status_code == 200 and body["status"] == "pending_review"
       and body["decision"]["final_params"]["percent_off"] <= 15,
       str(body)[:200])


def test_execute_before_approval_blocked(client, catalog):
    pid = propose(client).json()["proposal_id"]
    r = client.post(f"/governance/proposals/{pid}/execute")
    ok("execute before approval blocked", r.status_code == 409,
       str(r.status_code))


def test_human_approves(client, catalog):
    pid = propose(client).json()["proposal_id"]
    r = client.post(f"/governance/proposals/{pid}/decide",
                    json={"decided_by": "merchant-owner", "approved": True})
    ok("human approves",
       r.status_code == 200 and r.json()["status"] == "approved")


def test_execute_after_approval_works(client, catalog):
    pid = propose(client).json()["proposal_id"]
    client.post(f"/governance/proposals/{pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    r = client.post(f"/governance/proposals/{pid}/execute")
    ok("execute after approval works",
       r.status_code == 200 and r.json()["executed"], str(r.json())[:160])


def test_catalog_shows_discounted_price_with_base_intact(client, catalog):
    pid = propose(client).json()["proposal_id"]
    client.post(f"/governance/proposals/{pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{pid}/execute")
    row = catalog.execute(
        "SELECT price_paise, base_price_paise, discount_until"
        " FROM products WHERE sku='masala-chai-250g'").fetchone()
    ok("catalog shows discounted price with base intact",
       row["base_price_paise"] > row["price_paise"]
       and row["discount_until"] is not None, str(tuple(row)))


def test_second_discount_denied_terminal(client, catalog):
    """A policy-denied proposal is terminal — it never reaches the review
    queue, so a human is never asked to rubber-stamp something the policy
    has already ruled out."""
    pid = propose(client).json()["proposal_id"]
    client.post(f"/governance/proposals/{pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{pid}/execute")
    r = propose(client, params={"sku": "masala-chai-250g",
                                "percent_off": 50, "days": 1})
    ok("second discount denied by concurrent rule, terminal",
       r.json()["decision"]["status"] == "deny"
       and r.json()["status"] == "rejected", str(r.json())[:200])


def test_low_risk_proposal_auto_executed(client, catalog):
    r = propose(client, action_type="restock_alert",
                params={"sku": "masala-chai-250g"})
    ok("low-risk proposal auto-executed",
       r.json()["status"] == "auto_executed"
       and r.json().get("execution", {}).get("executed"), str(r.json())[:160])


def test_expired_discount_reverts_to_base_price(client, catalog):
    pid = propose(client).json()["proposal_id"]
    client.post(f"/governance/proposals/{pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{pid}/execute")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    catalog.execute(
        "UPDATE products SET discount_until=? WHERE sku='masala-chai-250g'",
        (past,))
    catalog.commit()
    n = refresh_expired_discounts()
    row = catalog.execute(
        "SELECT price_paise, base_price_paise, discount_until"
        " FROM products WHERE sku='masala-chai-250g'").fetchone()
    ok("expired discount reverts to base price", n == 1
       and row["base_price_paise"] is None and row["discount_until"] is None)


def test_audit_chain_intact_after_governance_ops(client, catalog):
    propose(client)
    good, count, bad = verify(catalog)
    ok("audit chain intact after governance ops", good and bad is None,
       f"n={count} bad={bad}")
