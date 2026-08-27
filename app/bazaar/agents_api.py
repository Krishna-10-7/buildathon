"""HTTP edge for agent operations. Thin: parse, delegate, map errors.

The cycle endpoint is the demo trigger: one POST = one governed strategy
cycle. The snapshot endpoint is read-only and feeds the Control Tower later.
"""

from fastapi import APIRouter

from bazaar.agents import growth
from bazaar.llm import LLMError

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/snapshot")
def snapshot() -> dict:
    return growth.build_snapshot()


@router.post("/growth/cycle")
async def growth_cycle() -> dict:
    try:
        return await growth.run_cycle()
    except LLMError as exc:
        return {"error": "llm_unavailable", "detail": str(exc)[:300]}
    except ValueError as exc:  # unparsable plan — audited as a skipped cycle
        return {"error": "invalid_plan", "detail": str(exc)[:200]}
