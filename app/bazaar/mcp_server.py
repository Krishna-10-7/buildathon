"""MCP surface: the merchant as a tool-calling target for ANY external AI buyer.

Same rule as every other edge — thin. Tools delegate to the very functions
the REST edges use (catalog feed, order creation, order lookup), so MCP and
REST can never drift apart. Write bounds come from the caller-side mandate
envelope (mandates.py); the policy engine still guards merchant actions.

Transport: Streamable HTTP, stateless + JSON mode (research/03), mounted
into the core app so one uvicorn worker serves everything.
"""

import json

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http import TransportSecuritySettings

from bazaar.catalog_api import catalog as _catalog_feed
from bazaar.config import settings
from bazaar.orders import OrderIn, create_order, get_order

server = MCPServer(
    name="chai-bazaar",
    title="Chai Bazaar Store",
    version="0.1.0",
    instructions=(
        "Indian D2C chai store. Read tools need no setup. To buy: "
        "create_order with items=[{sku, qty}] (optionally mandate_id if you "
        "hold a spending envelope from your principal), then complete "
        "payment with the returned checkout parameters. Amounts are integer "
        "paise (100 paise = INR 1). Test mode: no real money moves."
    ),
)


def _err(exc: Exception) -> dict:
    detail = getattr(exc, "detail", str(exc))
    return {"error": detail}


@server.tool(
    name="search_catalog",
    description="List purchasable products with live prices (paise) and "
                "stock. Optional case-insensitive query filters by title, "
                "description or tags.",
)
async def search_catalog(query: str = "") -> dict:
    feed = _catalog_feed()
    products = feed["products"]
    if query:
        q = query.lower()
        products = [
            p for p in products
            if q in p["title"].lower()
            or q in (p.get("description") or "").lower()
            or q in (p.get("tags") or "").lower()
        ]
    return {"products": products, "bundles": feed.get("bundles", [])}


@server.tool(
    name="get_product",
    description="One product by sku: full description, price in paise, live "
                "stock availability.",
)
async def get_product(sku: str) -> dict:
    for p in _catalog_feed()["products"]:
        if p["sku"] == sku:
            return p
    return {"error": f"unknown sku: {sku}"}


@server.tool(
    name="create_order",
    description="Create an order and reserve stock. Server-side pricing: "
                "the returned amount_paise is authoritative; a basket whose "
                "items exactly match an active bundle is priced at the "
                "bundle price (savings_paise says what you saved). Returns "
                "checkout parameters for Razorpay Standard Checkout. "
                "Optional mandate_id enforces the buyer's signed spending "
                "envelope.",
)
async def create_order_tool(buyer_session_id: str,
                            items: list[dict],
                            mandate_id: str | None = None) -> dict:
    try:
        parsed = [{"sku": i["sku"], "qty": int(i.get("qty", 1))} for i in items]
        body = OrderIn(buyer_session_id=buyer_session_id, items=parsed,
                       channel="mcp", mandate_id=mandate_id)
        return await create_order(body)
    except HTTPException as exc:
        return _err(exc)
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"bad items payload: {exc}"}


@server.tool(
    name="get_order_status",
    description="Poll an order: lifecycle status (created -> paid | failed), "
                "line items, and per-attempt payment records.",
)
async def get_order_status(order_id: str) -> dict:
    try:
        return await get_order(order_id)
    except HTTPException as exc:
        return _err(exc)


@server.tool(
    name="shop_policies",
    description="Shipping, returns, delivery timelines and store policies.",
)
async def shop_policies() -> dict:
    return {
        "currency": "INR",
        "shipping": "3-5 business days across India; free above Rs 499.",
        "returns": "7-day no-questions returns on unopened physical goods.",
        "digital": "Video courses deliver by email within minutes.",
        "subscription": "Chai Club renews monthly; cancel anytime.",
        "test_mode_notice": "This storefront runs on Razorpay TEST keys; "
                            "payments are simulated end-to-end.",
    }


def build_mcp_app():
    """Starlette sub-app for the /mcp mount. DNS-rebinding guard stays on;
    the allowlist covers every Host the deployment answers to."""
    hosts = ["localhost", "127.0.0.1", "testserver"]
    public = settings.public_base_url.split("://")[-1].strip("/")
    if public:
        hosts.append(public)
    return server.streamable_http_app(
        json_response=True, stateless_http=True, streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
        ),
    )


def mcp_session_manager():
    """Lifespan hook: the transport's task group must be running before any
    /mcp request reaches the mount (mounted sub-apps get no lifespan of
    their own). Reaches one level into the SDK because it owns that object;
    wrapped here so main.py stays clean."""
    return server._lowlevel_server._session_manager
