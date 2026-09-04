"""Bundle pricing + A/B arm switch (the PREREGISTRATION.md prerequisites).

Converted from scripts/test_bundles_and_arms.py.

The arm switch is what defines treatment, so it is driven through the
REAL governance lifecycle here: a proposal is made, clamped by policy,
approved by a human, executed — and only then replayed by the arm flip.
An arm that is defined anywhere else is not the arm the measurement ran.

Each test starts from a clean store and replays the whole sequence, so a
failure points at the property that broke rather than at whichever test
happened to run second.
"""

import json
from datetime import datetime, timezone

import pytest

from bazaar import bundles as bnd
from bazaar.audit import verify
from bazaar.experiment import current_state, set_arm
from tests.conftest import ok

CHAI, KULHAD, TOWEL = ("masala-chai-250g", "kulhad-set", "tea-towel")
BUNDLE_SKUS = [CHAI, KULHAD]


@pytest.fixture
def shop(conn):
    conn.executescript(f"""
        INSERT INTO products (sku, title, description, price_paise,
             cost_paise, stock, category) VALUES
             ('{CHAI}',   'Masala Chai 250g', 'test', 24900, 15000, 50, 'tea'),
             ('{KULHAD}', 'Kulhad Set',       'test', 19900, 12000, 30, 'ware'),
             ('{TOWEL}',  'Tea Towel',        'test',  9900,  6000, 40, 'ware');
        INSERT INTO bundles (id, skus_json, price_paise, active, created_at)
             VALUES ('bnd_test', '{json.dumps(BUNDLE_SKUS)}', 38000, 1,
                     datetime('now'));
    """)
    conn.commit()
    return conn


def order(client, items):
    return client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat", "items": items})


def product_row(conn, sku: str):
    return conn.execute(
        "SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()


def bundle_active(conn, bid: str) -> bool:
    row = conn.execute(
        "SELECT active FROM bundles WHERE id = ?", (bid,)).fetchone()
    return bool(row and row["active"])


# ---- A. bundle pricing over the public order edge -------------------------

def test_exact_match_basket_priced_at_bundle_price(client, shop):
    body = order(client, [{"sku": CHAI, "qty": 1},
                          {"sku": KULHAD, "qty": 1}]).json()
    ok("exact-match basket priced at bundle price",
       body["amount_paise"] == 38000 and body["bundle_id"] == "bnd_test",
       str(body)[:200])


def test_savings_reported(client, shop):
    body = order(client, [{"sku": CHAI, "qty": 1},
                          {"sku": KULHAD, "qty": 1}]).json()
    ok("savings reported",
       body["savings_paise"] == 24900 + 19900 - 38000,
       str(body["savings_paise"]))


def test_stock_reserved_per_line_on_bundle_order(client, shop):
    order(client, [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1}])
    ok("stock reserved per line on bundle order",
       product_row(shop, CHAI)["stock"] == 49
       and product_row(shop, KULHAD)["stock"] == 29)


def test_partial_basket_pays_line_sum(client, shop):
    body = order(client, [{"sku": CHAI, "qty": 1}]).json()
    ok("partial basket pays line sum",
       body["amount_paise"] == 24900 and body["bundle_id"] is None,
       str(body)[:160])


def test_superset_basket_is_not_a_bundle_deal(client, shop):
    """A superset is a different basket. Discounting it at the bundle
    price would be giving away the extra item."""
    body = order(client, [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1},
                          {"sku": TOWEL, "qty": 1}]).json()
    ok("superset basket is NOT a bundle deal",
       body["amount_paise"] == 54700 and body["bundle_id"] is None,
       str(body)[:160])


def test_wrong_quantities_dont_match_the_multiset(client, shop):
    body = order(client, [{"sku": CHAI, "qty": 2}]).json()
    ok("wrong quantities don't match the multiset",
       body["amount_paise"] == 49800 and body["bundle_id"] is None,
       str(body)[:160])


def test_inactive_bundle_ignored(client, shop):
    shop.execute("UPDATE bundles SET active = 0 WHERE id = 'bnd_test'")
    shop.commit()
    r = order(client, [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1}])
    ok("inactive bundle ignored", r.json()["amount_paise"] == 44800,
       str(r.json())[:160])


def test_bundle_never_costs_more_than_the_parts(client, shop):
    """The last line of defence on pricing: even an approved bundle whose
    stored price exceeds the sum of its parts must not be charged at a
    premium. A 'deal' that costs more than the items is a bug the buyer
    pays for."""
    shop.execute("UPDATE bundles SET active = 1, price_paise = 999999"
                 " WHERE id = 'bnd_test'")
    shop.commit()
    body = order(client, [{"sku": CHAI, "qty": 1},
                          {"sku": KULHAD, "qty": 1}]).json()
    ok("bundle never costs MORE than the parts",
       body["amount_paise"] == 44800 and body["bundle_id"] is None,
       str(body)[:160])


# ---- B. arm switch ---------------------------------------------------------

@pytest.fixture
def governed(client, shop):
    """Run the real lifecycle: discount + bundle, clamped, approved,
    executed. Returns what the ledger says treatment should look like."""
    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "apply_discount",
        "params": {"sku": CHAI, "percent_off": 40, "days": 30}}).json()
    disc_pid = r["proposal_id"]
    expected_price = r["decision"]["final_params"]["new_price_paise"]
    expected_days = r["decision"]["final_params"]["days"]
    client.post(f"/governance/proposals/{disc_pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{disc_pid}/execute")

    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "create_bundle",
        "params": {"skus": BUNDLE_SKUS, "price_paise": 40000}}).json()
    bnd_pid = r["proposal_id"]
    client.post(f"/governance/proposals/{bnd_pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{bnd_pid}/execute")
    return {"expected_price": expected_price, "expected_days": expected_days,
            "gov_bid": bnd.bundle_id(BUNDLE_SKUS), "proposal": r}


def test_discount_executed_precondition(governed, shop):
    row = product_row(shop, CHAI)
    ok("discount executed (precondition)",
       row["discount_until"] is not None
       and row["price_paise"] == governed["expected_price"],
       str(dict(row))[:200])


def test_governed_bundle_within_profitable_band_accepted(governed):
    ok("governed bundle within profitable band accepted",
       governed["proposal"]["decision"]["status"] in ("allow", "clamp"),
       str(governed["proposal"])[:200])


def test_governed_bundle_live_precondition(governed, shop):
    ok("governed bundle live (precondition)",
       bundle_active(shop, governed["gov_bid"]))


def test_control_reverts_price_to_base(governed, shop):
    set_arm("control")
    row = product_row(shop, CHAI)
    ok("control reverts price to base",
       row["price_paise"] == 24900 and row["base_price_paise"] is None
       and row["discount_until"] is None, str(dict(row))[:200])


def test_control_deactivates_all_bundles(governed, shop):
    set_arm("control")
    ok("control deactivates ALL bundles",
       not bundle_active(shop, governed["gov_bid"])
       and not bundle_active(shop, "bnd_test"))


def test_control_switch_audited_with_reverted_skus(governed, shop):
    res = set_arm("control")
    ok("control switch audited with reverted skus", res["reverted"] == [CHAI],
       str(res))


def test_state_reads_as_control(governed, shop):
    set_arm("control")
    ok("state reads as control", current_state()["looks_like"] == "control")


def test_treatment_replays_the_executed_discount_from_the_ledger(governed,
                                                                 shop):
    set_arm("treatment")
    row = product_row(shop, CHAI)
    ok("treatment replays the EXECUTED discount from the ledger",
       row["price_paise"] == governed["expected_price"]
       and row["base_price_paise"] == 24900, str(dict(row))[:200])


def test_treatment_window_reextended_by_the_replayed_days(governed, shop):
    set_arm("treatment")
    row = product_row(shop, CHAI)
    until = datetime.fromisoformat(row["discount_until"])
    ok("treatment window re-extended by the replayed days",
       until > datetime.now(timezone.utc)
       and (until - datetime.now(timezone.utc)).days + 1
       == governed["expected_days"],
       f"{row['discount_until']} vs {governed['expected_days']}d")


def test_treatment_reactivates_only_ledger_bundles(governed, shop):
    """`bnd_test` was inserted by hand, not through governance, so the
    treatment arm must not resurrect it. Treatment is what the LEDGER
    says it is — not whatever happens to be sitting in the table."""
    set_arm("treatment")
    ok("treatment reactivates only LEDGER bundles (manual bnd_test stays off)",
       bundle_active(shop, governed["gov_bid"])
       and not bundle_active(shop, "bnd_test"))


def test_treatment_toggle_idempotent_base_never_compounds(governed, shop):
    """Discounting an already-discounted price would compound silently.
    The base is stored once and the discounted price is always derived
    from it, never from itself."""
    set_arm("treatment")
    res2 = set_arm("treatment")
    row = product_row(shop, CHAI)
    ok("treatment toggle idempotent, base never compounds",
       row["price_paise"] == governed["expected_price"]
       and row["base_price_paise"] == 24900
       and res2["reverted"] == [CHAI], str(res2))


def test_second_control_returns_clean_state(governed, shop):
    set_arm("treatment")
    set_arm("control")
    ok("second control returns clean state",
       current_state()["looks_like"] == "control"
       and product_row(shop, CHAI)["price_paise"] == 24900)


def test_bad_arm_rejected(governed, shop):
    with pytest.raises(ValueError):
        set_arm("placebo")
    ok("bad arm rejected", True)


def test_every_switch_audited(governed, shop):
    set_arm("control")
    set_arm("treatment")
    set_arm("treatment")
    set_arm("control")
    n = shop.execute(
        "SELECT COUNT(*) FROM audit_log"
        " WHERE action_type = 'experiment.arm_switch'").fetchone()[0]
    # Four switches. `placebo` raises before anything is written, so it
    # contributes nothing -- a rejected switch is not a switch.
    ok("every switch audited", n == 4, f"n={n}")


def test_audit_chain_intact_through_it_all(governed, shop):
    set_arm("control")
    set_arm("treatment")
    good, count, bad = verify(shop)
    ok("audit chain intact through it all", good and bad is None,
       f"n={count} bad={bad}")
