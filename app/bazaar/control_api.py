"""Serves the Control Tower page. Pure presentation edge — the page itself
calls the same public JSON APIs agents use; no new business logic here."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from bazaar.control_page import PAGE

router = APIRouter(tags=["control"])


@router.get("/control", response_class=HTMLResponse, include_in_schema=False)
def control() -> HTMLResponse:
    return HTMLResponse(PAGE)
