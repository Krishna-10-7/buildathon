"""HTTP edge for buyer mandates. Thin: parse, delegate, map errors."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bazaar import mandates

router = APIRouter(prefix="/mandates", tags=["mandates"])


class MandateIn(BaseModel):
    buyer_ref: str = Field(min_length=1, max_length=64)
    budget_cap_paise: int = Field(gt=0)
    max_single_txn_paise: int = Field(gt=0)
    allowed_categories: list[str] = Field(default_factory=list)
    ttl_hours: float = Field(default=24.0, gt=0)


@router.post("")
def create_mandate(body: MandateIn) -> dict:
    try:
        return mandates.create(body.buyer_ref, body.budget_cap_paise,
                               body.max_single_txn_paise,
                               body.allowed_categories, body.ttl_hours)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{mandate_id}")
def get_mandate(mandate_id: str) -> dict:
    row = mandates.get(mandate_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"unknown mandate {mandate_id}")
    return row


@router.post("/{mandate_id}/revoke")
def revoke_mandate(mandate_id: str) -> dict:
    try:
        return mandates.revoke(mandate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
