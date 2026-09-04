"""MCP surface: tools/list -> tools/call against the mounted /mcp app,
using the wire protocol (JSON-RPC over HTTP POST).

Converted from scripts/test_mcp.py.

This is the agent-facing half of the product: an AI buyer that reaches us
through MCP must hit exactly the same policy code as a human on the
checkout page, so the tool surface is exercised over the real wire
rather than by calling the functions directly.
"""

import json

import pytest

from tests.conftest import ok

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def rpc(client, body: dict) -> dict:
    """Stateless JSON mode (research/03): no initialize handshake; every
    request carries the protocol envelope in params._meta plus the
    2026-07-28 method/name routing headers."""
    params = dict(body.get("params") or {})
    params["_meta"] = META
    body = {**body, "params": params}
    headers = {"Accept": "application/json",
               "MCP-Protocol-Version": "2026-07-28",
               "Mcp-Method": body["method"]}
    if body["method"] == "tools/call":
        headers["Mcp-Name"] = body["params"]["name"]
    r = client.post("/mcp", json=body, headers=headers)
    return {"code": r.status_code, "body": r.json()}


@pytest.fixture
def stocked(conn):
    conn.execute(
        "INSERT OR REPLACE INTO products (sku, title, description,"
        " price_paise, cost_paise, stock, category, kind) VALUES"
        " ('masala-chai-250g', 'Masala Chai', 'spiced', 24900, 15000, 40,"
        "  'tea', 'physical')")
    conn.commit()
    return conn


def test_five_tools_exposed(client, stocked):
    tools = rpc(client, {"jsonrpc": "2.0", "id": 2,
                         "method": "tools/list", "params": {}})
    names = sorted(t["name"] for t in tools["body"]["result"]["tools"])
    ok("five tools exposed", names == [
        "create_order", "get_order_status", "get_product",
        "search_catalog", "shop_policies"], str(names))


def test_catalog_tool_returns_products(client, stocked):
    cat = rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "search_catalog",
                                  "arguments": {"query": "chai"}}})
    content = cat["body"]["result"]["content"][0]["text"]
    ok("catalog tool returns products",
       '"products"' in content and "chai" in content.lower(), content[:150])


def test_order_created_via_mcp(client, stocked):
    order = rpc(client, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "create_order",
                                    "arguments": {
                                        "buyer_session_id": "mcp-test-agent",
                                        "items": [{"sku": "masala-chai-250g",
                                                   "qty": 1}]}}})
    text = order["body"]["result"]["content"][0]["text"]
    ok("order created via MCP", '"order_id"' in text
       and '"amount_paise"' in text, text[:200])


def test_status_tool_sees_the_order(client, stocked):
    order = rpc(client, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "create_order",
                                    "arguments": {
                                        "buyer_session_id": "mcp-test-agent",
                                        "items": [{"sku": "masala-chai-250g",
                                                   "qty": 1}]}}})
    oid = json.loads(order["body"]["result"]["content"][0]["text"])["order_id"]
    status = rpc(client, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "get_order_status",
                                     "arguments": {"order_id": oid}}})
    st = status["body"]["result"]["content"][0]["text"]
    ok("status tool sees the order", '"created"' in st, st[:150])


def test_unknown_sku_surfaces_as_tool_error_text(client, stocked):
    """A tool error must come back as CONTENT, not as a transport failure.

    An agent that gets an HTTP 500 learns nothing it can act on; one that
    gets `{"error": ...}` in the result can retry differently.
    """
    bad = rpc(client, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "create_order",
                                  "arguments": {
                                      "buyer_session_id": "mcp-test-agent",
                                      "items": [{"sku": "nope", "qty": 1}]}}})
    ok("unknown sku surfaces as tool error text",
       '"error"' in bad["body"]["result"]["content"][0]["text"])
