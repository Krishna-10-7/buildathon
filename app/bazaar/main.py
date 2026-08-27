from contextlib import asynccontextmanager

from fastapi import FastAPI

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
