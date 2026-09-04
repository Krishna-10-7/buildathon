"""End-to-end money loop, with the gateway stubbed.

Converted from scripts/test_moneyloop.py.

  1. create order (Razorpay gateway STUBBED — no network, no test-key
     velocity spent, no hCaptcha)
  2. a correctly-signed payment.captured webhook flips the order to paid
  3. replaying the same webhook is a deduped no-op
  4. a bad-signature webhook is rejected 400
  5. the hash chain still verifies

The original script hit the REAL Razorpay test API. That is valuable as a
manual smoke test and useless as a committed test: it needs a key that
rotates roughly daily (KEY-ROTATION-CHECKLIST.md), it consumes the shared
test-key velocity, and it cannot run in CI at all. So the committed
version stubs the one outbound call and keeps every other step real —
the signature check, the dedupe, the state machine and the ledger are all
production code.

The live variant is still scripts/test_moneyloop.py.
"""

import hashlib
import hmac
import json
import uuid

import pytest

from bazaar.audit import verify
from bazaar.config import settings
from tests.conftest import ok

SECRET = "whsec_moneyloop_test_secret"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "rzp_webhook_secret", SECRET)


@pytest.fixture
def gateway(monkeypatch):
    """Stub only the outbound Razorpay order creation."""
    from bazaar import orders as orders_mod

    async def fake_rp(amount, receipt=None, notes=None):
        return {"id": f"order_SIM{uuid.uuid4().hex[:12]}",
                "amount": amount, "currency": "INR", "status": "created"}

    monkeypatch.setattr(orders_mod, "rp_create_order", fake_rp)


@pytest.fixture
def catalog(conn):
    conn.executemany(
        "INSERT INTO products (sku, title, description, price_paise,"
        " cost_paise, stock, category, kind) VALUES (?,?,?,?,?,?,?,'physical')",
        [("masala-chai-250g", "Masala Chai 250g", "everyday blend",
          24900, 15000, 50, "tea"),
         ("kulhad-set-6", "Kulhad Set of 6", "clay cups",
          19900, 12000, 30, "ware")])
    conn.commit()
    return conn


def webhook(client, payload: dict, signature: str):
    return client.post(
        "/webhooks/razorpay", content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Razorpay-Signature": signature})


def sign(payload: dict) -> str:
    return hmac.new(SECRET.encode(), json.dumps(payload).encode(),
                    hashlib.sha256).hexdigest()


@pytest.fixture
def paid_order(client, catalog, gateway):
    """One order, created and captured through the real webhook path."""
    r = client.post("/orders", json={
        "buyer_session_id": "sim-runner-1",
        "items": [{"sku": "masala-chai-250g", "qty": 1},
                  {"sku": "kulhad-set-6", "qty": 1}]})
    assert r.status_code == 200, r.text
    order = r.json()
    entity = {"id": f"pay_SIM{uuid.uuid4().hex[:12]}", "entity": "payment",
              "amount": order["amount_paise"], "currency": "INR",
              "status": "captured", "order_id": order["rp_order_id"],
              "method": "card", "captured": True}
    payload = {"event": "payment.captured",
               "payload": {"payment": {"entity": entity}}}
    r2 = webhook(client, payload, sign(payload))
    assert r2.status_code == 200, r2.text
    return {"order": order, "payload": payload, "signature": sign(payload)}


def test_order_creation_succeeds(paid_order):
    ok("order creation returns 200 with a real rp_order_id",
       paid_order["order"]["rp_order_id"].startswith("order_"),
       str(paid_order["order"])[:200])


def test_valid_webhook_is_accepted(client, catalog, gateway):
    """The capture that flips a real order to paid, over the real
    signature check."""
    r = client.post("/orders", json={
        "buyer_session_id": "sim-runner-1",
        "items": [{"sku": "masala-chai-250g", "qty": 1}]})
    order = r.json()
    payload = {"event": "payment.captured",
               "payload": {"payment": {"entity": {
                   "id": f"pay_SIM{uuid.uuid4().hex[:12]}",
                   "order_id": order["rp_order_id"],
                   "amount": order["amount_paise"], "method": "card"}}}}
    r2 = webhook(client, payload, sign(payload))
    ok("valid webhook accepted", r2.status_code == 200, r2.text)
    ok("valid webhook reports accepted", r2.json()["status"] == "accepted",
       r2.text)


def test_replayed_webhook_is_a_duplicate(client, paid_order):
    r3 = webhook(client, paid_order["payload"], paid_order["signature"])
    ok("replayed webhook is a duplicate", r3.json().get("status") == "duplicate",
       r3.text)


def test_forged_signature_rejected(client, paid_order):
    r4 = webhook(client, paid_order["payload"], "deadbeef" * 8)
    ok("forged webhook rejected", r4.status_code == 400, str(r4.status_code))


def test_replay_did_not_double_insert(client, paid_order):
    webhook(client, paid_order["payload"], paid_order["signature"])
    got = client.get(f"/orders/{paid_order['order']['order_id']}").json()
    ok("order is paid", got["status"] == "paid", got["status"])
    ok("replay did NOT double-insert the payment",
       len(got["payments"]) == 1, f"{len(got['payments'])} payment rows")


def test_audit_chain_verifies(client, paid_order, conn):
    ok_ok, n, bad_seq = verify(conn)
    ok("audit chain verifies end to end", ok_ok,
       f"records={n} first_bad_seq={bad_seq}")
