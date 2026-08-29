#!/usr/bin/env python3
"""
probe_s2s_payment.py — can we complete a Razorpay test payment WITHOUT
opening a browser?

WHY THIS EXISTS
---------------
Every browser checkout burns reputation on the Razorpay test key. The
Checkout JS injects hCaptcha, the risk engine is stateful, and after
~15 automated checkouts the key starts challenging everything — which is
why the key has to be rotated by hand every day.

Razorpay also exposes a pure server-to-server payment path:

    POST /v1/payments/create/json     create the payment (no Checkout JS)
    POST /v1/payments/{id}/otp/submit submit OTP if the flow asks for one
    POST /v1/payments/{id}/capture    capture it

No browser => no Checkout JS => no hCaptcha => no velocity flag.
This script simply finds out whether that path works on our test keys,
and with which test instrument. It decides; it does not assume.

VERIFIED RESULT — 2026-08-29, on this account:
    POST /v1/payments/create/json is NOT ENABLED.
    Every attempt (UPI collect and UPI intent) returns:
        400 BAD_REQUEST_ERROR
        "The requested URL was not found on the server."
        metadata: {order_id: order_...}
    The order itself creates fine, so auth and the key are good — the
    endpoint is gated behind PCI-DSS / server-to-server enablement that
    this test account does not have.

    So on this account the browser Checkout is unavoidable for live
    payments. The key-rotation problem therefore has to be solved by
    NOT NEEDING LIVE PAYMENTS (decouple the demo — see
    research/10-key-rotation-and-risk-escalation.md), plus pacing and
    automation. Keep this script: re-run it if the account ever gets
    server-to-server enabled, because that would end the problem.

It creates only small test-mode orders (₹1-₹5). No live keys are used.
Nothing it does affects the frozen measurement dataset or the audit
ledger — it talks to Razorpay directly, not to our own service.

USAGE
-----
    uv run python scripts/probe_s2s_payment.py
    uv run python scripts/probe_s2s_payment.py --vpa success@razorpay

Exit code 0 if at least one path reaches a captured/authorized payment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

from bazaar.config import settings  # noqa: E402

BASE = "https://api.razorpay.com/v1"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Candidate test VPAs. Razorpay publishes test UPI IDs; which one is
# accepted varies by account/era, so we simply try them in order and
# report what actually happened rather than hard-coding a guess.
CANDIDATE_VPAS = [
    "success@razorpay",
    "test@razorpay",
    "9999999999@razorpay",
    "success@razorpay.com",
    "8779690905@ptsbi",
    "gauravkumar@exampleupi",
]

# OTPs test mode commonly accepts.
CANDIDATE_OTPS = ["1234", "123456", "0000", "1111"]


def _auth() -> dict[str, str]:
    import base64

    token = base64.b64encode(
        f"{settings.rzp_key_id}:{settings.rzp_key_secret}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


def _key_fingerprint() -> str:
    """Show which key we're on without leaking the whole id."""
    kid = settings.rzp_key_id or ""
    if len(kid) < 12:
        return "(unset)"
    return f"{kid[:8]}…{kid[-4:]}"


async def create_order(client: httpx.AsyncClient, paise: int, receipt: str) -> dict:
    resp = await client.post(
        f"{BASE}/orders",
        headers=_auth(),
        json={
            "amount": paise,
            "currency": "INR",
            "receipt": receipt[:40],
            "notes": {"probe": "s2s-payment", "channel": "probe"},
        },
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"create order failed {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def attempt_upi_collect(
    client: httpx.AsyncClient, order: dict, vpa: str
) -> tuple[str, dict | None]:
    """Try UPI collect. Returns (verdict, payment_json)."""
    body = {
        "amount": order["amount"],
        "currency": "INR",
        "order_id": order["id"],
        "method": "upi",
        "upi": {"flow": "collect", "expiry_time": "6", "vpa": vpa},
        "contact": "8422496779",
        "email": "probe@bazaar.test",
    }
    resp = await client.post(f"{BASE}/payments/create/json", headers=_auth(), json=body)
    if resp.status_code >= 400:
        return f"rejected ({resp.status_code}: {resp.text[:160]})", None

    pay = resp.json()
    pay_id = pay.get("id", "?")
    status = pay.get("status", "?")
    return f"accepted -> payment {pay_id} status={status}", pay


async def submit_otp(client: httpx.AsyncClient, pay_id: str, otp: str) -> tuple[bool, str]:
    resp = await client.post(
        f"{BASE}/payments/{pay_id}/otp/submit", headers=_auth(), json={"otp": otp}
    )
    if resp.status_code >= 400:
        return False, f"{resp.status_code}: {resp.text[:160]}"
    return True, f"otp {otp} accepted -> {resp.json().get('status', '?')}"


async def capture(client: httpx.AsyncClient, pay_id: str, paise: int) -> tuple[bool, str]:
    resp = await client.post(
        f"{BASE}/payments/{pay_id}/capture",
        headers=_auth(),
        json={"amount": paise, "currency": "INR"},
    )
    if resp.status_code >= 400:
        return False, f"{resp.status_code}: {resp.text[:200]}"
    return True, f"captured -> {resp.json().get('status', '?')}"


async def fetch_payment(client: httpx.AsyncClient, pay_id: str) -> dict | None:
    resp = await client.get(f"{BASE}/payments/{pay_id}", headers=_auth())
    return resp.json() if resp.status_code < 400 else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vpa", action="append", default=[], help="try this VPA first")
    ap.add_argument("--paise", type=int, default=500, help="order amount (default ₹5)")
    args = ap.parse_args()

    if not settings.rzp_key_id or not settings.rzp_key_secret:
        print("ERROR: RZP_KEY_ID / RZP_KEY_SECRET not set. Is app/.env present?")
        return 2

    vpases = list(args.vpa) + CANDIDATE_VPAS

    print("=" * 68)
    print("  SERVER-TO-SERVER PAYMENT PROBE — Razorpay TEST mode")
    print("=" * 68)
    print(f"  key        : {_key_fingerprint()}")
    print(f"  order size : ₹{args.paise / 100:.2f}")
    print()
    print("  If any line below reaches 'captured', the daily key rotation")
    print("  is no longer necessary — the browser can be dropped entirely.")
    print()

    wins: list[str] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for vpa in vpases:
            print(f"--- UPI collect via {vpa} " + "-" * (40 - len(vpa)))
            try:
                order = await create_order(client, args.paise, f"probe-{abs(hash(vpa)) % 10**8}")
            except Exception as exc:  # noqa: BLE001
                print(f"    order create failed: {exc}")
                continue
            print(f"    order  : {order['id']}  ₹{order['amount'] / 100:.2f}")

            verdict, pay = await attempt_upi_collect(client, order, vpa)
            print(f"    create : {verdict}")
            if not pay:
                continue

            pay_id = pay["id"]
            status = pay.get("status")

            # If it needs an OTP, try the usual test OTPs.
            if status in {"pending", "authorized"} or pay.get("otp_required"):
                print(f"    status says '{status}' — trying OTP submission")
                for otp in CANDIDATE_OTPS:
                    ok, msg = await submit_otp(client, pay_id, otp)
                    print(f"      otp {otp:<8} {msg}")
                    if ok:
                        break

            # Refresh state, then try to capture.
            pay = await fetch_payment(client, pay_id) or pay
            status = pay.get("status")
            print(f"    after otp: status={status}")

            if status in {"authorized", "pending"}:
                ok, msg = await capture(client, pay_id, args.paise)
                print(f"    capture: {msg}")
                if ok:
                    status = "captured"

            if status == "captured":
                print(f"    ✅ WORKS — {vpa} reached CAPTURED with no browser")
                wins.append(vpa)
            else:
                print(f"    ✗ ended at status={status}")

            print()

    print("=" * 68)
    if wins:
        print(f"  RESULT: {len(wins)} path(s) reached a captured payment.")
        print(f"  Working VPA(s): {', '.join(wins)}")
        print()
        print("  Next step: wire this into orders.py as a second payment rail,")
        print("  keep the browser rail for the already-frozen evidence, and the")
        print("  daily key rotation stops being necessary.")
        return 0

    print("  RESULT: no server-to-server path reached a captured payment.")
    print()
    print("  That is itself a useful answer — it means Razorpay test mode")
    print("  requires the Checkout surface on this account, and the fix has")
    print("  to come from pacing + demo decoupling instead. See")
    print("  research/10-key-rotation-and-risk-escalation.md")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
