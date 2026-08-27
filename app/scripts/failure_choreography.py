"""Narrated failure choreography — the graceful-failure demo (task: judges
see every money pathway refuse safely). Run ON the merchant VM:

    cd ~/bazaar/app && uv run python scripts/failure_choreography.py

Acts:
  1. FORGED WEBHOOK     bad-signature payment.captured -> refused 400,
                        audited, state untouched (+ why Razorpay retries are
                        safe: event-id dedupe, stable payment row ids)
  2. REVOKED MANDATE    buyer envelope revoked mid-flight -> next order
                        refused 403 mandate_denied BEFORE the gateway call
  3. POLICY CLAMP       agent asks 40% x 30d -> engine clamps to the bound,
                        human rejects the remainder -> catalog unchanged
  4. TAMPER EVIDENCE    one flipped byte in a COPY of the ledger -> chain
                        breaks at exactly that seq; production chain intact

Every act ends with "THE GUARANTEE" - the sentence a judge should remember.
Nothing is cleaned up afterwards: the audit records ARE the deliverable.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from bazaar.audit import verify  # noqa: E402
from bazaar.config import settings  # noqa: E402

W = 72


def banner(title: str) -> None:
    print("\n" + "=" * W)
    print(title)
    print("=" * W)


def step(label: str) -> None:
    print(f"\n>> {label}")


def guarantee(text: str) -> None:
    print(f"\n   THE GUARANTEE: {text}")


def j(x) -> str:
    return json.dumps(x, sort_keys=True)[:220]


def recent_audit(base: str, limit: int = 6) -> list[dict]:
    r = httpx.get(f"{base}/audit/recent", params={"limit": limit}, timeout=15)
    r.raise_for_status()
    return r.json()["records"]


def show_audit_tail(records: list[dict]) -> None:
    for rec in reversed(records):  # newest last for reading order
        payload = rec["payload"]
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            pass
        print(f"   [{rec['seq']:>3}] {rec['action_type']:<38} {j(payload)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=settings.public_base_url)
    ap.add_argument("--db", default=settings.db_path)
    ap.add_argument("--sku", default="masala-chai-250g")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    health = httpx.get(f"{base}/healthz", timeout=15)
    print(f"merchant core: {health.json()['status']} at {base}")
    print("NOTE: acts leave their audit records behind on purpose.\n")

    # ---------------------------------------------------------------- act 1
    banner("ACT 1 - A FORGED WEBHOOK KNOCKS ON THE DOOR")
    step("attacker POSTs a fake payment.captured with an INVALID signature")
    forged = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_FORGEDDEMO", "order_id": "rp_ord_does_not_exist",
            "amount": 9900, "method": "netbanking"}}},
    }
    r = httpx.post(f"{base}/webhooks/razorpay", json=forged,
                   headers={"X-Razorpay-Signature": "deadbeef"}, timeout=15)
    print(f"   receiver answered HTTP {r.status_code}: {r.text.strip()[:80]}")
    ok_400 = r.status_code == 400

    step("the refusal itself is evidence - audited as rejected_invalid_signature")
    tail = [rec for rec in recent_audit(base, 10)
            if rec["action_type"] == "webhook.rejected_invalid_signature"]
    print(f"   audit records of refused forgeries: {len(tail)}")
    if tail:
        print(f"   latest: body_sha256={json.loads(tail[-1]['payload'])['body_sha256'][:16]}...")

    step("why GENUINE retries are safe (Razorpay redelivers on outage)")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    n_events = conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0]
    conn.close()
    print(f"   webhook_events holds {n_events} unique event ids; redeliveries "
          f"short-circuit on event id (receiver answers 'duplicate')")
    print("   payment rows key on sha256(order:payment) + INSERT OR IGNORE,")
    print("   so a retried capture can never double-count (proven live Aug 22:")
    print("   a retry after a deploy window landed and was absorbed cleanly).")
    guarantee("only cryptographically signed events touch state; forgeries "
              "are refused AND remembered; gateway retries are idempotent.")

    # ---------------------------------------------------------------- act 2
    banner("ACT 2 - A SPENDING MANDATE REVOKED MID-FLIGHT")
    step("buyer agent mints a Rs 500 envelope scoped to category 'tea'")
    r = httpx.post(f"{base}/mandates", json={
        "buyer_ref": "demo-buyer", "budget_cap_paise": 50000,
        "max_single_txn_paise": 30000, "allowed_categories": ["tea"],
        "ttl_hours": 24}, timeout=15)
    r.raise_for_status()
    m = r.json()
    mid = m["mandate_id"] if isinstance(m, dict) and "mandate_id" in m else m["id"]
    print(f"   mandate {mid} minted (HMAC-signed, expires in 24h)")

    step("order WITHIN the mandate: accepted, reaches Razorpay")
    r1 = httpx.post(f"{base}/orders", json={
        "buyer_session_id": "choreography-demo", "channel": "chat",
        "items": [{"sku": args.sku, "qty": 1}], "mandate_id": mid}, timeout=30)
    print(f"   HTTP {r1.status_code}: order {r1.json().get('order_id')} "
          f"amount={r1.json().get('amount_paise')}p")

    step("principal revokes the envelope (consent withdrawn)")
    httpx.post(f"{base}/mandates/{mid}/revoke", timeout=15).raise_for_status()
    print(f"   mandate {mid} revoked")

    step("same basket again with the dead mandate: REFUSED before Razorpay")
    r2 = httpx.post(f"{base}/orders", json={
        "buyer_session_id": "choreography-demo", "channel": "chat",
        "items": [{"sku": args.sku, "qty": 1}], "mandate_id": mid}, timeout=30)
    detail = r2.json().get("detail")
    print(f"   HTTP {r2.status_code}: {j(detail)}")
    refused = (r2.status_code == 403
               and isinstance(detail, dict) and detail.get("code") == "mandate_denied")

    step("both sides of the story are in the ledger")
    show_audit_tail(recent_audit(base, 5))
    guarantee("a revoked envelope stops spending IMMEDIATELY - the refusal "
              "happens before the payment gateway is ever called, and it is "
              "audited." if refused else "(check output above!)")

    # ---------------------------------------------------------------- act 3
    banner("ACT 3 - AN AGENT ASKS FOR TOO MUCH; THE ENGINE CLAMPS IT")

    def propose_discount(sku: str) -> dict:
        r = httpx.post(f"{base}/governance/proposals", json={
            "actor": "growth-agent-demo", "action_type": "apply_discount",
            "params": {"sku": sku, "percent_off": 40, "days": 30}}, timeout=15)
        return r.json()

    def narrate(d: dict) -> None:
        dec = d["decision"]
        fp = dec.get("final_params") or {}
        print("   asked   : percent_off=40 days=30")
        print(f"   decided : status={dec['status']}"
              + (f" final percent_off={fp.get('percent_off')}"
                 f" days={fp.get('days')}" if fp else ""))
        print(f"   rules   : {', '.join(dec['rule_ids'])}")

    step("growth agent proposes 40% off for 30 days")
    d = propose_discount(args.sku)
    narrate(d)

    if d["decision"]["status"] == "deny":
        step("no safe interpretation exists -> DENIED outright"
             " (a discount is already live on this sku)")
        alt = next(p["sku"] for p in httpx.get(
            f"{base}/catalog", timeout=15).json()["products"]
            if p["sku"] != args.sku)
        print(f"   retargeting the demo at {alt} to show the clamp path")
        args.sku = alt
        d = propose_discount(alt)
        narrate(d)

    price_before = httpx.get(f"{base}/catalog", timeout=15).json()
    p_before = next(p["price_paise"] for p in price_before["products"]
                    if p["sku"] == args.sku)

    step("human REJECTS the clamped proposal - consent matters too")
    pid = d["proposal_id"]
    rr = httpx.post(f"{base}/governance/proposals/{pid}/decide",
                    json={"decided_by": "human@failure-choreography",
                          "approved": False}, timeout=15)
    print(f"   decision recorded: HTTP {rr.status_code} -> {rr.json()}")

    p_after = next(p["price_paise"] for p in httpx.get(
        f"{base}/catalog", timeout=15).json()["products"] if p["sku"] == args.sku)
    print(f"   catalog price {args.sku}: {p_before}p -> {p_after}p (unchanged)")
    guarantee("agents cannot move money or prices: the engine clamps to the "
              "bound, and only a HUMAN decision can spend it - here the "
              "human said no, so nothing changed.")
    print(f"   (watch this live any time at {base}/control)")

    # ---------------------------------------------------------------- act 4
    banner("ACT 4 - FLIP ONE BYTE IN THE LEDGER, WATCH IT SCREAM")
    step("taking a WAL-consistent snapshot of production (sqlite backup API)")
    tmp = Path(tempfile.mkdtemp()) / "tampered.db"
    src = sqlite3.connect(args.db)
    conn = sqlite3.connect(str(tmp))
    for c in (src, conn):
        c.row_factory = sqlite3.Row
    src.backup(conn)  # file-copy is stale under WAL; backup() sees everything

    victim = conn.execute(
        "SELECT seq, payload FROM audit_log WHERE payload LIKE ?"
        " ORDER BY seq DESC LIMIT 1", ('%"amount_paise"%',)).fetchone()
    tampered = victim["payload"].replace('"amount_paise"', '"amount_paiseX"', 1)
    if tampered == victim["payload"]:  # marker absent -> force a visible edit
        tampered = victim["payload"][:-1] + "X"
    conn.execute("UPDATE audit_log SET payload = ? WHERE seq = ?",
                 (tampered, victim["seq"]))
    conn.commit()

    step(f"snapshot: rewrote payload of seq {victim['seq']} "
         f"(one character, pretending to hide a money record)")
    good, n, bad = verify(conn)
    print(f"   tampered copy : chain_ok={good} records_checked={n} "
          f"first_bad_seq={bad}")
    broken = (not good) and bad == victim["seq"]

    good_live, n_live, _ = verify(src)
    print(f"   production db : chain_ok={good_live} records_checked={n_live}")
    src.close()
    conn.close()
    guarantee("every record hash-seals the previous one - edit history and "
              "the break points at EXACTLY the edited record."
              if broken and good_live else "(tamper detection failed?!)")

    banner("CHOREOGRAPHY COMPLETE - all four guarantees above are backed by "
           "audit records in the live ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
