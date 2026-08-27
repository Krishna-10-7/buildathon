"""Bundle pricing + A/B arm switch tests (the PREREGISTRATION.md prerequisites).

Isolated temp DB (DB_PATH env before imports). Exit code 0 = all green.

  uv run python scripts/test_bundles_and_arms.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from bazaar import bundles as bnd  # noqa: E402
from bazaar.audit import verify  # noqa: E402
from bazaar.db import connect  # noqa: E402
from bazaar.experiment import current_state, set_arm  # noqa: E402
from bazaar.main import app  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


CHAI, KULHAD, TOWEL = ("masala-chai-250g", "kulhad-set", "tea-towel")
BUNDLE_SKUS = [CHAI, KULHAD]


def product_row(sku: str):
    return connect().execute(
        "SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()


def bundle_active(bid: str) -> bool:
    row = connect().execute(
        "SELECT active FROM bundles WHERE id = ?", (bid,)).fetchone()
    return bool(row and row["active"])


with TestClient(app) as client:
    conn = connect()
    conn.executescript(f"""
        INSERT INTO products (sku, title, description, price_paise, cost_paise,
             stock, category) VALUES
             ('{CHAI}',   'Masala Chai 250g', 'test', 24900, 15000, 50, 'tea'),
             ('{KULHAD}', 'Kulhad Set',       'test', 19900, 12000, 30, 'ware'),
             ('{TOWEL}',  'Tea Towel',        'test',  9900,  6000, 40, 'ware');
        INSERT INTO bundles (id, skus_json, price_paise, active, created_at)
             VALUES ('bnd_test', '{json.dumps(BUNDLE_SKUS)}', 38000, 1,
                     datetime('now'));
    """)
    conn.commit()
    conn.close()

    # ---- A. bundle pricing over the public order edge ------------------------
    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1}]})
    body = r.json()
    ok("exact-match basket priced at bundle price",
       body["amount_paise"] == 38000 and body["bundle_id"] == "bnd_test",
       str(body)[:200])
    ok("savings reported", body["savings_paise"] == 24900 + 19900 - 38000,
       str(body["savings_paise"]))
    ok("stock reserved per line on bundle order",
       product_row(CHAI)["stock"] == 49 and product_row(KULHAD)["stock"] == 29)

    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 1}]})
    body = r.json()
    ok("partial basket pays line sum", body["amount_paise"] == 24900
       and body["bundle_id"] is None, str(body)[:160])

    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1},
                  {"sku": TOWEL, "qty": 1}]})
    body = r.json()
    ok("superset basket is NOT a bundle deal", body["amount_paise"] == 54700
       and body["bundle_id"] is None, str(body)[:160])

    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 2}]})
    body = r.json()
    ok("wrong quantities don't match the multiset",
       body["amount_paise"] == 49800 and body["bundle_id"] is None,
       str(body)[:160])

    conn = connect()
    conn.execute("UPDATE bundles SET active = 0 WHERE id = 'bnd_test'")
    conn.commit()
    conn.close()
    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1}]})
    ok("inactive bundle ignored", r.json()["amount_paise"] == 44800,
       str(r.json())[:160])

    conn = connect()
    conn.execute("UPDATE bundles SET active = 1, price_paise = 999999"
                 " WHERE id = 'bnd_test'")
    conn.commit()
    conn.close()
    r = client.post("/orders", json={
        "buyer_session_id": "t", "channel": "chat",
        "items": [{"sku": CHAI, "qty": 1}, {"sku": KULHAD, "qty": 1}]})
    body = r.json()
    ok("bundle never costs MORE than the parts", body["amount_paise"] == 44800
       and body["bundle_id"] is None, str(body)[:160])

    # ---- B. arm switch --------------------------------------------------------
    # A governed discount + a governed bundle through the REAL lifecycle, so
    # the ledger defines treatment exactly as production would.
    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "apply_discount",
        "params": {"sku": CHAI, "percent_off": 40, "days": 30}})
    disc_pid = r.json()["proposal_id"]
    expected_price = r.json()["decision"]["final_params"]["new_price_paise"]
    expected_days = r.json()["decision"]["final_params"]["days"]
    client.post(f"/governance/proposals/{disc_pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{disc_pid}/execute")
    ok("discount executed (precondition)",
       product_row(CHAI)["discount_until"] is not None
       and product_row(CHAI)["price_paise"] == expected_price)

    r = client.post("/governance/proposals", json={
        "actor": "growth-agent-v0", "action_type": "create_bundle",
        "params": {"skus": BUNDLE_SKUS, "price_paise": 40000}})
    ok("governed bundle within profitable band accepted",
       r.json()["decision"]["status"] in ("allow", "clamp"), str(r.json())[:200])
    bnd_pid = r.json()["proposal_id"]
    client.post(f"/governance/proposals/{bnd_pid}/decide",
                json={"decided_by": "merchant-owner", "approved": True})
    client.post(f"/governance/proposals/{bnd_pid}/execute")
    gov_bid = bnd.bundle_id(BUNDLE_SKUS)
    ok("governed bundle live (precondition)", bundle_active(gov_bid))

    res = set_arm("control")
    row = product_row(CHAI)
    ok("control reverts price to base",
       row["price_paise"] == 24900 and row["base_price_paise"] is None
       and row["discount_until"] is None, str(dict(row))[:200])
    ok("control deactivates ALL bundles",
       not bundle_active(gov_bid) and not bundle_active("bnd_test"))
    ok("control switch audited with reverted skus", res["reverted"] == [CHAI],
       str(res))
    ok("state reads as control", current_state()["looks_like"] == "control")

    res = set_arm("treatment")
    row = product_row(CHAI)
    until = datetime.fromisoformat(row["discount_until"])
    ok("treatment replays the EXECUTED discount from the ledger",
       row["price_paise"] == expected_price
       and row["base_price_paise"] == 24900, str(dict(row))[:200])
    ok("treatment window re-extended by the replayed days",
       until > datetime.now(timezone.utc)
       and (until - datetime.now(timezone.utc)).days + 1 == expected_days,
       f"{row['discount_until']} vs {expected_days}d")
    ok("treatment reactivates only LEDGER bundles (manual bnd_test stays off)",
       bundle_active(gov_bid) and not bundle_active("bnd_test"), str(res))

    res2 = set_arm("treatment")
    row = product_row(CHAI)
    ok("treatment toggle idempotent, base never compounds",
       row["price_paise"] == expected_price
       and row["base_price_paise"] == 24900
       and res2["reverted"] == [CHAI], str(res2))

    set_arm("control")
    ok("second control returns clean state",
       current_state()["looks_like"] == "control"
       and product_row(CHAI)["price_paise"] == 24900)

    try:
        set_arm("placebo")
        ok("bad arm rejected", False)
    except ValueError:
        ok("bad arm rejected", True)

    n_switches = connect().execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_type = 'experiment.arm_switch'"
    ).fetchone()[0]
    ok("every switch audited", n_switches == 4, f"n={n_switches}")

    good, count, bad = verify(connect())
    ok("audit chain intact through it all", good and bad is None,
       f"n={count} bad={bad}")

print(f"\nBUNDLES+ARMS: {PASS} CHECKS PASSED")
