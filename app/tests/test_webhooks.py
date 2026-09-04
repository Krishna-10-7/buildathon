"""Webhook idempotency and replay protection.

Converted from scripts/test_webhooks.py.

T5. A payment webhook is the one input we do not control: Razorpay
retries on any non-2xx, and a timeout on our side can look like a failure
to them, so the SAME event legitimately arrives more than once. Applied
twice, a `payment.captured` would append a second audit row for one real
payment, move money twice, and quietly corrupt every number the README
publishes.

What must hold:
  1. Signature is HMAC-SHA256 over the RAW body, compared with
     `compare_digest` (not `==`, which leaks length and short-circuits),
     verified BEFORE anything is written.
  2. Dedupe on event id.
  3. A replay returns 200 — a non-2xx tells Razorpay to retry, so
     refusing a duplicate manufactures more duplicates.
  4. A replay is RECORDED as `webhook.duplicate`. Silence is worse than
     the replay: a judge reading the ledger must be able to see that
     something tried to pay twice and that it did not take.
  5. No second payment row, no second draw-down, no second audit row.
"""

import hashlib
import hmac
import json
import threading

import pytest

from bazaar import audit, mandates
from bazaar.config import settings
from tests.conftest import ok

SECRET = "whsec_test_secret_for_idempotency_suite"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "rzp_webhook_secret", SECRET)


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def captured(order_id: str, payment_id: str, amount: int) -> bytes:
    return json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id,
            "amount": amount, "method": "upi",
        }}},
    }).encode()


def failed(order_id: str, payment_id: str, amount: int,
           code: str = "GATEWAY") -> bytes:
    return json.dumps({
        "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": amount,
            "error_code": code,
        }}},
    }).encode()


def post(client, body: bytes, event_id: str | None = None,
         signature: str | None = None):
    headers = {"Content-Type": "application/json",
               "X-Razorpay-Signature":
                   signature if signature is not None else sign(body)}
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post("/webhooks/razorpay", content=body, headers=headers)


@pytest.fixture
def order_row(conn):
    """Seed one order in `created` state and return its row."""
    def _make(oid, rp_oid, total, mandate_id=None, reserved=0):
        conn.execute(
            "INSERT INTO orders (id, rp_order_id, buyer_session_id, channel,"
            " items_json, total_paise, status, attempt_no, mandate_id,"
            " bundle_id, mandate_reserved_paise, correlation_id,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, rp_oid, "sess_test", "mcp", "[]", total, "created", 0,
             mandate_id, None, reserved, f"corr-{oid}",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        conn.commit()
        return oid
    return _make


# -- 1. signature ------------------------------------------------------------

def test_valid_signature_is_accepted(client):
    r = post(client, captured("order_test_rp1", "pay_test_1", 45000), "evt_1")
    ok("valid signature is accepted", r.status_code == 200, r.text)


def test_no_signature_at_all_is_rejected(client):
    r = post(client, captured("order_test_rp1", "pay_test_1", 45000),
             "evt_2", "")
    ok("no signature at all is rejected", r.status_code == 400)


def test_wrong_secret_is_rejected(client):
    body = captured("order_test_rp1", "pay_test_1", 45000)
    r = post(client, body, "evt_3", sign(b"something else"))
    ok("wrong secret is rejected", r.status_code == 400)


def test_tampered_body_fails_under_the_original_signature(client):
    """The signature must cover the RAW body.

    Verifying a re-serialised dict instead would let a payload that
    parses the same but differs bytewise (whitespace, key order, unicode
    escapes) through under the original's signature.
    """
    body = captured("order_test_rp1", "pay_test_1", 45000)
    tampered = body.replace(b'"amount": 45000', b'"amount": 999999')
    r = post(client, tampered, "evt_4", sign(body))
    ok("tampered body fails under the original signature",
       r.status_code == 400)


def test_a_rejected_request_claims_no_event_id(client, conn):
    """An unauthenticated caller must not be able to claim an event id
    and pre-block the real one from ever being applied."""
    post(client, captured("order_test_rp1", "pay_test_1", 45000), "evt_2", "")
    n = conn.execute(
        "SELECT COUNT(*) FROM webhook_events WHERE id = 'evt_2'").fetchone()[0]
    ok("a rejected request claims no event id", n == 0, f"{n} rows")


# -- 2. first delivery --------------------------------------------------------

def test_first_delivery_is_accepted(client, order_row):
    order_row("ord_test_1", "order_test_rp1", 45000)
    r = post(client, captured("order_test_rp1", "pay_test_1", 45000),
             "evt_sig_1")
    ok("first delivery is accepted", r.status_code == 200, r.text)
    ok("first delivery reports accepted", r.json()["status"] == "accepted")


def test_order_is_marked_paid(client, order_row, conn):
    order_row("ord_test_1", "order_test_rp1", 45000)
    post(client, captured("order_test_rp1", "pay_test_1", 45000), "evt_sig_1")
    status = conn.execute(
        "SELECT status FROM orders WHERE id = 'ord_test_1'").fetchone()[0]
    ok("order is marked paid", status == "paid", status)


def test_one_payment_row_was_written(client, order_row, conn):
    order_row("ord_test_1", "order_test_rp1", 45000)
    post(client, captured("order_test_rp1", "pay_test_1", 45000), "evt_sig_1")
    n = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    ok("one payment row was written", n == 1, f"{n}")


# -- 3. replay -----------------------------------------------------------------

@pytest.fixture
def delivered(client, order_row):
    order_row("ord_test_1", "order_test_rp1", 45000)
    body = captured("order_test_rp1", "pay_test_1", 45000)
    post(client, body, "evt_sig_1")
    return body


def test_replay_returns_200(client, delivered):
    r = post(client, delivered, "evt_sig_1")
    ok("replay returns 200 (never 4xx — that triggers a retry storm)",
       r.status_code == 200, str(r.status_code))
    ok("replay reports duplicate", r.json()["status"] == "duplicate", r.text)


def test_replay_did_not_append_a_second_payment_row(client, delivered, conn):
    post(client, delivered, "evt_sig_1")
    n = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    ok("replay did NOT append a second payment row", n == 1, f"{n}")


def test_replay_did_not_double_the_captured_amount(client, delivered, conn):
    post(client, delivered, "evt_sig_1")
    total = conn.execute("SELECT SUM(amount_paise) FROM payments").fetchone()[0]
    ok("replay did NOT double the captured amount", total == 45000,
       f"{total}")


def test_replay_is_recorded_as_webhook_duplicate(client, delivered, conn):
    post(client, delivered, "evt_sig_1")
    actions = [r[0] for r in conn.execute(
        "SELECT action_type FROM audit_log ORDER BY seq")]
    ok("replay is recorded as webhook.duplicate",
       "webhook.duplicate" in actions, str(actions))
    ok("the capture event itself was not appended twice",
       actions.count("payment.captured") == 1,
       f"payment.captured x{actions.count('payment.captured')}")


def test_chain_still_verifies_after_the_replay(client, delivered, conn):
    post(client, delivered, "evt_sig_1")
    chain_ok, records, first_bad = audit.verify(conn)
    ok("chain still verifies after the replay", chain_ok is True,
       f"records={records} first_bad={first_bad}")


def test_every_replay_is_recorded_not_just_the_first(client, delivered, conn):
    post(client, delivered, "evt_sig_1")
    post(client, delivered, "evt_sig_1")
    n_dup = conn.execute(
        "SELECT COUNT(*) FROM audit_log"
        " WHERE action_type = 'webhook.duplicate'").fetchone()[0]
    n_pay = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    # Each replay is its own fact; collapsing them would hide a storm.
    ok("every replay is recorded, not just the first", n_dup == 2, f"{n_dup}")
    ok("three deliveries still mean one payment", n_pay == 1, f"{n_pay}")


def test_a_genuinely_different_event_is_not_swallowed(client, delivered):
    """Dedupe is on event id. A real second capture must still apply."""
    r = post(client, captured("order_test_rp1", "pay_test_2", 45000),
             "evt_sig_2")
    ok("a genuinely different event is not swallowed",
       r.json()["status"] == "accepted", r.text)


# -- 4. concurrent replays -----------------------------------------------------

def test_eight_simultaneous_deliveries_exactly_one_is_applied(client,
                                                              order_row, conn):
    """The regression test for the old SELECT-then-INSERT dedupe.

    Eight threads deliver one event at once; exactly one may apply it and
    the rest must be clean duplicates. Under the old shape they collided
    on the primary key and returned 500s — and a 500 is precisely what
    makes Razorpay redeliver.
    """
    order_row("ord_test_2", "order_test_rp2", 30000)
    body = captured("order_test_rp2", "pay_race_1", 30000)
    barrier = threading.Barrier(8)
    outcome = [None] * 8

    def deliver(i: int) -> None:
        barrier.wait()
        try:
            outcome[i] = post(client, body, "evt_race_1").json()["status"]
        except Exception as exc:  # noqa: BLE001
            outcome[i] = f"error:{type(exc).__name__}"

    threads = [threading.Thread(target=deliver, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    accepted = outcome.count("accepted")
    duplicates = outcome.count("duplicate")
    ok("8 simultaneous deliveries: exactly one is applied", accepted == 1,
       f"accepted={accepted} duplicate={duplicates} -> {outcome}")
    ok("the other 7 were detected as duplicates", duplicates == 7,
       str(outcome))
    ok("the race produced exactly one payment row",
       conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1)
    chain_ok, records, _bad = audit.verify(conn)
    ok("chain verifies after the race", chain_ok is True, f"records={records}")


# -- 5. no header -> body hash ---------------------------------------------------

def test_headerless_replay_is_still_deduped(client, order_row):
    """Razorpay always sends Event-Id, but if it is missing the fallback
    is the body hash, which still dedupes a byte-identical replay rather
    than treating every headerless delivery as new."""
    order_row("ord_test_3", "order_test_rp3", 10000)
    body = captured("order_test_rp3", "pay_nohdr_1", 10000)
    ok("headerless first delivery is accepted",
       post(client, body).json()["status"] == "accepted")
    ok("headerless replay is still deduped",
       post(client, body).json()["status"] == "duplicate")


# -- 6. the hold, and the money bug hiding in idempotency ------------------------

@pytest.fixture
def held_order(conn):
    """An order whose budget is genuinely held by mandates.reserve().

    Seeding the column by hand would leave the mandate untouched and make
    every assertion below vacuous.
    """
    def _make(oid="ord_test_4", rp_oid="order_test_rp4", total=30000):
        mandate = mandates.create(conn=conn, buyer_ref="buyer-t",
                                  budget_cap_paise=100_000,
                                  max_single_txn_paise=50_000,
                                  allowed_categories=["tea"], ttl_hours=1.0)
        _row, verdict = mandates.reserve(conn, mandate["id"], total, ["tea"])
        assert verdict.allowed, verdict.reasons
        conn.execute(
            "INSERT INTO orders (id, rp_order_id, buyer_session_id, channel,"
            " items_json, total_paise, status, attempt_no, mandate_id,"
            " bundle_id, mandate_reserved_paise, correlation_id,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, rp_oid, "sess_test", "mcp", "[]", total, "created", 0,
             mandate["id"], None, total, f"corr-{oid}",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        conn.commit()
        return mandate, oid
    return _make


def spent_of(conn, mandate_id: str) -> int:
    return conn.execute("SELECT spent_paise FROM mandates WHERE id = ?",
                        (mandate_id,)).fetchone()[0]


def test_order_creation_holds_the_budget(held_order, conn):
    mandate, _oid = held_order()
    ok("order creation holds the budget", spent_of(conn, mandate["id"]) == 30000,
       f"spent={spent_of(conn, mandate['id'])}")


def test_a_late_failure_does_not_refund_a_captured_payment(held_order, conn,
                                                           client):
    """The money bug hiding inside idempotency.

    A failure notice arriving for an order already marked paid must not
    hand back budget that was really spent.
    """
    mandate, oid = held_order()
    post(client, captured("order_test_rp4", "pay_hold_1", 30000), "evt_hold_1")
    post(client, failed("order_test_rp4", "pay_hold_1", 30000),
         "evt_hold_fail")
    ok("a late failure does not refund a captured payment",
       spent_of(conn, mandate["id"]) == 30000,
       f"spent={spent_of(conn, mandate['id'])}")
    ok("the paid order stays paid",
       conn.execute("SELECT status FROM orders WHERE id = ?",
                    (oid,)).fetchone()[0] == "paid")


def test_a_genuine_failure_releases_the_hold(held_order, conn, client):
    mandate, oid = held_order(oid="ord_test_5", rp_oid="order_test_rp5")
    post(client, failed("order_test_rp5", "pay_hold_2", 30000),
         "evt_hold_fail2")
    ok("a genuine failure releases the hold",
       spent_of(conn, mandate["id"]) == 0,
       f"spent={spent_of(conn, mandate['id'])}")
    ok("the failed order is marked failed",
       conn.execute("SELECT status FROM orders WHERE id = ?",
                    (oid,)).fetchone()[0] == "failed")


def test_replaying_a_failure_does_not_invent_budget(held_order, conn, client):
    """Releasing the same hold twice would produce negative spend —
    which is budget the envelope could then spend again."""
    mandate, _oid = held_order(oid="ord_test_5", rp_oid="order_test_rp5")
    body = failed("order_test_rp5", "pay_hold_2", 30000)
    post(client, body, "evt_hold_fail2")
    post(client, body, "evt_hold_fail2")
    ok("replaying a failure does not invent budget",
       spent_of(conn, mandate["id"]) == 0,
       f"spent={spent_of(conn, mandate['id'])}")
    chain_ok, records, first_bad = audit.verify(conn)
    ok("chain verifies at the end of the suite", chain_ok is True,
       f"records={records} first_bad={first_bad}")
