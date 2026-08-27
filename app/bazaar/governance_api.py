"""HTTP edge for the governance core. Thin by design: parse, delegate to
bazaar.proposals, map errors. No business rules live here."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bazaar import proposals

router = APIRouter(prefix="/governance", tags=["governance"])


class ProposalIn(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    action_type: str = Field(min_length=1, max_length=32)
    params: dict = Field(default_factory=dict)
    correlation_id: str | None = None


class DecisionIn(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    approved: bool


@router.post("/proposals")
def create_proposal(body: ProposalIn) -> dict:
    try:
        return proposals.propose(body.actor, body.action_type, body.params,
                                 body.correlation_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/proposals")
def list_proposals(status: str | None = None, limit: int = 50) -> dict:
    return {"proposals": proposals.list_proposals(status, limit)}


@router.post("/proposals/{proposal_id}/decide")
def decide_proposal(proposal_id: str, body: DecisionIn) -> dict:
    try:
        return proposals.decide(proposal_id, body.decided_by, body.approved)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/proposals/{proposal_id}/execute")
def execute_proposal(proposal_id: str) -> dict:
    try:
        return proposals.execute(proposal_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
