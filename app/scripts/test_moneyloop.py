"""End-to-end money-loop simulation WITHOUT a browser:

  1. create order (hits the REAL Razorpay test API)
  2. deliver a correctly-signed payment.captured webhook -> order flips to paid
  3. replay the same webhook -> must be a deduped no-op
  4. deliver a BAD-signature webhook -> must be rejected 400
  5. verify hash-chain integrity of the audit ledger

Used as a permanent regression test and as the demo fallback path.
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from bazaar.audit import verify  # noqa: E402
from bazaar.config import settings  # noqa: E402
from bazaar.db import connect  # noqa: E402
from bazaar.main import app  # noqa: E402


def webhook_request(client: TestClient, payload: dict, signature: str):
    raw = json.dumps(payload).encode()
    return client.post(
        "/webhooks/razorpay",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
    )


def main() -> None:
    with TestClient(app) as client:  # context manager runs startup (schema heal)
        _run(client)


def _run(client: TestClient) -> None:

    # -- 1. real order creation -------------------------------------------------
    r = client.post(
        "/orders",
        json={
            "buyer_session_id": "sim-runner-1",
            "items": [
                {"sku": "masala-chai-250g", "qty": 1},
                {"sku": "kulhad-set-6", "qty": 1},
            ],
        },
    )
    print("1. POST /orders ->", r.status_code)
    assert r.status_code == 200, r.text
    order = r.json()
    print("   order:", order["order_id"], "| rp:", order["rp_order_id"],
          "| total:", order["amount_paise"], "paise")

    # -- 2. signed capture webhook ----------------------------------------------
    # Fresh payment id per run — mirrors reality (Razorpay ids never repeat).
    import uuid
    entity = {
        "id": f"pay_SIM{uuid.uuid4().hex[:12]}", "entity": "payment",
        "amount": order["amount_paise"], "currency": "INR",
        "status": "captured", "order_id": order["rp_order_id"],
        "method": "card", "captured": True,
    }
    payload = {"event": "payment.captured",
               "payload": {"payment": {"entity": entity}}}
    good_sig = hmac.new(settings.rzp_webhook_secret.encode(),
                        json.dumps(payload).encode(), hashlib.sha256).hexdigest()

    r2 = webhook_request(client, payload, good_sig)
    print("2. valid webhook ->", r2.status_code, r2.json())
    assert r2.status_code == 200

    # -- 3. replay = duplicate ----------------------------------------------------
    r3 = webhook_request(client, payload, good_sig)
    print("3. replayed webhook ->", r3.status_code, r3.json())
    assert r3.json().get("status") == "duplicate"

    # -- 4. forged signature rejected ---------------------------------------------
    r4 = webhook_request(client, payload, "deadbeef" * 8)
    print("4. forged webhook ->", r4.status_code)
    assert r4.status_code == 400

    # -- 5. final state + ledger ---------------------------------------------------
    got = client.get(f"/orders/{order['order_id']}").json()
    print("5. order status:", got["status"],
          "| payments:", len(got["payments"]),
          "| method:", got["payments"][0]["method"] if got["payments"] else None)
    assert got["status"] == "paid"
    assert len(got["payments"]) == 1  # replay did NOT double-insert

    ok, n, bad_seq = verify(connect())
    print(f"   audit chain: ok={ok} records={n} first_bad_seq={bad_seq}")
    assert ok

    print("\nMONEY LOOP SIMULATION PASSED")


if __name__ == "__main__":
    main()
