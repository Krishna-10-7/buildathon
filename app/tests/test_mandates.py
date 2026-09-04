"""Mandates: signed envelopes enforced at order creation, spend drawn down
on capture, revoke/expiry honoured, tamper detected.

Converted from scripts/test_mandates.py. Every check is unchanged; the
sections became test functions so a failure is reported by name.
"""

import json

import pytest

from bazaar import mandates
from bazaar.audit import verify
from bazaar.db import connect
from tests.conftest import ok


def seed(sku: str, price: int, category: str) -> None:
    c = connect()
    try:
        c.execute(
            "INSERT OR REPLACE INTO products (sku, title, description,"
            " price_paise, cost_paise, stock, category, kind)"
            " VALUES (?, ?, '', ?, ?, 50, ?, 'physical')",
            (sku, sku, price, price // 2, category))
        c.commit()
    finally:
        c.close()


@pytest.fixture
def seeded():
    seed("grocery-chai", 20000, "grocery")
    seed("gift-hamper", 40000, "gifting")


def make_mandate(client, **kw):
    body = {"buyer_ref": "agent-A9", "budget_cap_paise": 50000,
            "max_single_txn_paise": 30000,
            "allowed_categories": ["grocery"], "ttl_hours": 24}
    body.update(kw)
    return client.post("/mandates", json=body)


def order(client, mandate_id: str, sku: str, qty: int = 1,
          session: str = "sess"):
    return client.post("/orders", json={
        "buyer_session_id": session,
        "items": [{"sku": sku, "qty": qty}],
        "channel": "mcp", "mandate_id": mandate_id,
    })


# ---- creation & signature ------------------------------------------------

def test_mandate_created(client, seeded):
    m = make_mandate(client)
    ok("mandate created", m.status_code == 200, m.text)


def test_mandate_has_signature(client, seeded):
    m = make_mandate(client).json()
    ok("has signature", len(m["signature"]) == 64 and m["spent_paise"] == 0)


# ---- happy path -----------------------------------------------------------

def test_order_within_mandate_accepted(client, seeded):
    m = make_mandate(client).json()
    r = order(client, m["id"], "grocery-chai")
    ok("order within mandate accepted", r.status_code == 200, r.text)

    # The original script's version of this check was
    #     r.json().get("mandate_id") == m["id"] if "mandate_id" in ... else True
    # which passes vacuously, because POST /orders does not echo the
    # mandate id. Kept honest instead: assert the binding in the one place
    # it actually has to exist -- the stored order row. A mandate that is
    # checked and then not recorded is no mandate at all.
    c = connect()
    try:
        row = c.execute("SELECT mandate_id, mandate_reserved_paise FROM orders"
                        " WHERE id = ?", (r.json()["order_id"],)).fetchone()
    finally:
        c.close()
    ok("order is bound to the mandate",
       row is not None and row["mandate_id"] == m["id"],
       f"row={dict(row) if row else None}")
    ok("order creation holds the budget",
       row is not None and row["mandate_reserved_paise"] == 20000,
       f"reserved={row['mandate_reserved_paise'] if row else None}")


# ---- single-txn cap --------------------------------------------------------

def test_over_single_txn_cap_refused(client, seeded):
    m = make_mandate(client).json()
    r = order(client, m["id"], "grocery-chai", qty=2)     # 40000 > 30000 cap
    ok("over single-txn cap refused", r.status_code == 403, r.text)
    ok("refusal names the bound",
       "single txn" in json.dumps(r.json()["detail"]))


# ---- category bound --------------------------------------------------------

def test_out_of_category_refused(client, seeded):
    m = make_mandate(client).json()
    r = order(client, m["id"], "gift-hamper")             # gifting ∉ [grocery]
    ok("out-of-category refused", r.status_code == 403, r.text)
    ok("refusal names category",
       "outside mandate" in json.dumps(r.json()["detail"]))


# ---- unknown mandate -------------------------------------------------------

def test_unknown_mandate_refused(client, seeded):
    make_mandate(client)
    r = order(client, "mnt_nope", "grocery-chai")
    ok("unknown mandate refused", r.status_code == 403, r.text)


# ---- tamper: edit the envelope behind the signature -------------------------

def test_tampered_envelope_detected(client, seeded):
    """Raise the cap behind the signature's back; the seal must break.

    This is the whole reason the envelope is signed: without it, anything
    with database access could rewrite the buyer's own limit.
    """
    m = make_mandate(client).json()
    c = connect()
    try:
        c.execute("UPDATE mandates SET budget_cap_paise = 999999 WHERE id = ?",
                  (m["id"],))
        c.commit()
    finally:
        c.close()
    _row, v = mandates.check(m["id"], 20000, ["grocery"])
    ok("tampered envelope detected",
       not v.allowed and any("signature" in x for x in v.reasons), str(v))


# ---- spend draw-down --------------------------------------------------------

def test_budget_exhaustion_enforced(client, seeded):
    m = make_mandate(client).json()
    c = connect()
    try:
        mandates.draw_down(c, m["id"], 45000)
        c.commit()
    finally:
        c.close()
    _row, v = mandates.check(m["id"], 20000, ["grocery"])
    ok("budget exhaustion enforced",
       not v.allowed and any("budget" in x for x in v.reasons), str(v))


# ---- revoke ------------------------------------------------------------------

def test_revoke_ok_and_idempotent(client, seeded):
    m = make_mandate(client).json()
    rv = client.post(f"/mandates/{m['id']}/revoke")
    ok("revoke ok", rv.status_code == 200 and rv.json()["revoked_at"], rv.text)
    rv2 = client.post(f"/mandates/{m['id']}/revoke")
    ok("revoke idempotent",
       rv2.status_code == 200
       and rv2.json()["revoked_at"] == rv.json()["revoked_at"])


def test_revoked_mandate_refused(client, seeded):
    m = make_mandate(client).json()
    client.post(f"/mandates/{m['id']}/revoke")
    r = order(client, m["id"], "grocery-chai", session="sess-m5")
    ok("revoked mandate refused", r.status_code == 403
       and "revoked" in json.dumps(r.json()["detail"]))


# ---- expiry -------------------------------------------------------------------

def test_expired_mandate_refused(client, seeded):
    mx = mandates.create("agent-B1", 50000, 30000, ["grocery"], ttl_hours=-1)
    _row, v = mandates.check(mx["id"], 20000, ["grocery"])
    ok("expired mandate refused",
       not v.allowed and any("expired" in x for x in v.reasons), str(v))


# ---- audit ---------------------------------------------------------------------

def test_denials_audited(client, seeded):
    m = make_mandate(client).json()
    order(client, m["id"], "grocery-chai", qty=2)            # single-txn
    order(client, m["id"], "gift-hamper", session="s2")      # category
    order(client, "mnt_nope", "grocery-chai", session="s3")  # unknown
    c = connect()
    try:
        n = c.execute(
            "SELECT COUNT(*) FROM audit_log"
            " WHERE action_type = 'order.mandate_denied'").fetchone()[0]
    finally:
        c.close()
    ok("denials audited", n >= 3, f"n={n}")


def test_audit_chain_intact(client, seeded):
    m = make_mandate(client).json()
    order(client, m["id"], "grocery-chai")
    c = connect()
    try:
        chain_ok, checked, _bad = verify(c)
    finally:
        c.close()
    ok("audit chain intact", chain_ok, f"checked={checked}")
