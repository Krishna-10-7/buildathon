import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from bazaar import logging_setup
from bazaar.agents_api import router as agents_router
from bazaar.audit_api import router as audit_router
from bazaar.catalog_api import router as catalog_router
from bazaar.config import settings
from bazaar.control_api import router as control_router
from bazaar.db import SCHEMA, connect, db_ready, migrate
from bazaar.experiment_api import router as experiment_router
from bazaar.governance_api import router as governance_router
from bazaar.mandates_api import router as mandates_router
from bazaar.mcp_server import build_mcp_app, mcp_session_manager
from bazaar.orders import router as orders_router
from bazaar.webhooks import router as webhooks_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Idempotent schema heal on boot + MCP transport task group."""
    logging_setup.configure()
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    async with mcp_session_manager().run():
        yield


app = FastAPI(title="Bazaar merchant core", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    """One id per request, echoed back on the response.

    An incoming X-Correlation-Id is honoured so a caller that already has
    one (an AI buyer's agent, or an upstream retry) gets a single id
    spanning both sides of the call. Otherwise we mint one.

    The response header is the point: the id has to reach the caller, or
    "paste me the correlation id from the response" is not a request you
    can make of someone.
    """
    incoming = request.headers.get(logging_setup.CORRELATION_HEADER)
    # Don't trust a caller-supplied id to be short or safe to log.
    cid = (incoming or "").strip()[:64] or uuid.uuid4().hex
    with logging_setup.bind(cid):
        response = await call_next(request)
    response.headers[logging_setup.CORRELATION_HEADER] = cid
    return response


app.include_router(orders_router)
app.include_router(webhooks_router)
app.include_router(governance_router)
app.include_router(mandates_router)
app.include_router(agents_router)
app.include_router(catalog_router)
app.include_router(audit_router)
app.include_router(control_router)
app.include_router(experiment_router)

# MCP surface for external AI buyers (Streamable HTTP, stateless JSON).
app.mount("/mcp", build_mcp_app())


@app.get("/.well-known/bazaar-mandate-key")
def wellknown_mandate_key() -> dict:
    """Publish the key an external buyer needs to verify a mandate.

    Standard well-known location so an agent can find it without being
    told: fetch /.well-known/bazaar-mandate-key, verify the envelope, and
    only then present it. It also publishes the canonical field order,
    because a key without the byte layout it signs is not verifiable.

    In hmac mode it returns no key at all — see the note it serves instead.
    """
    from bazaar import mandates

    return mandates.public_key_document()


@app.get("/healthz")
def healthz() -> dict:
    ok, detail = db_ready()
    return {
        "status": "ok" if ok else "degraded",
        "db": detail,
        "razorpay_configured": bool(settings.rzp_key_id and settings.rzp_key_secret),
        "webhook_secret_set": bool(settings.rzp_webhook_secret),
        "llm_provider": settings.llm_provider,
        "env": settings.env,
    }
