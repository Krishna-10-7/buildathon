"""Thin async Razorpay API client — Basic auth, test mode."""

import base64
import hashlib
import hmac

import httpx

from bazaar.config import settings

BASE_URL = "https://api.razorpay.com/v1"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _auth_header() -> dict[str, str]:
    token = base64.b64encode(
        f"{settings.rzp_key_id}:{settings.rzp_key_secret}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


class RazorpayError(RuntimeError):
    pass


def _check(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise RazorpayError(f"razorpay {resp.status_code}: {resp.text[:300]}")


async def create_order(amount_paise: int, receipt: str, notes: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{BASE_URL}/orders",
            headers=_auth_header(),
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt[:40],
                "notes": notes or {},
            },
        )
        _check(resp)
        return resp.json()


async def fetch_payment(payment_id: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{BASE_URL}/payments/{payment_id}", headers=_auth_header()
        )
        _check(resp)
        return resp.json()


async def fetch_order_payments(rp_order_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{BASE_URL}/orders/{rp_order_id}/payments", headers=_auth_header()
        )
        _check(resp)
        return resp.json().get("items", [])


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    if not settings.rzp_webhook_secret or not signature:
        return False
    expected = hmac.new(
        settings.rzp_webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
