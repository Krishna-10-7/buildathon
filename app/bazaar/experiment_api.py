"""HTTP edge for the A/B arm switch. Thin: parse, authorize, delegate.

The buyer fleet (VM2) must flip storefront arms between sessions without
shell access to this host, so the toggle is exposed here behind a shared-
secret token (same signing key family as mandates — never hardcoded).
Every accepted flip is audited by experiment.set_arm itself; rejects are
cheap and leave no state.
"""

import hashlib
import hmac as hmac_mod

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from bazaar import mandates
from bazaar.experiment import ARMS, current_state, set_arm

router = APIRouter(prefix="/experiment", tags=["experiment"])


def _token() -> str:
    return hashlib.sha256(
        f"experiment-v1:{mandates._signing_key()}".encode()).hexdigest()


class ArmIn(BaseModel):
    arm: str


@router.post("/arm")
def switch_arm(body: ArmIn, x_experiment_token: str = Header(default="")) -> dict:
    if not hmac_mod.compare_digest(x_experiment_token, _token()):
        raise HTTPException(status_code=403, detail="invalid experiment token")
    try:
        return set_arm(body.arm)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/state")
def arm_state(x_experiment_token: str = Header(default="")) -> dict:
    """Read-only view; same token because discount internals leak through it."""
    if not hmac_mod.compare_digest(x_experiment_token, _token()):
        raise HTTPException(status_code=403, detail="invalid experiment token")
    return current_state()
