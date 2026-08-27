"""MCP surface smoke test: initialize -> tools/list -> tools/call against
the mounted /mcp app, using the wire protocol (JSON-RPC over HTTP POST).

  uv run python scripts/test_mcp.py            # in-process TestClient
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from bazaar.db import connect  # noqa: E402
from bazaar.main import app  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def rpc(client: TestClient, body: dict) -> dict:
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


with TestClient(app) as client:
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO products (sku, title, description, price_paise,"
        " cost_paise, stock, category, kind) VALUES"
        " ('masala-chai-250g', 'Masala Chai', 'spiced', 24900, 15000, 40,"
        "  'tea', 'physical')")
    conn.commit()
    conn.close()

    tools = rpc(client, {"jsonrpc": "2.0", "id": 2,
                         "method": "tools/list", "params": {}})
    names = sorted(t["name"] for t in tools["body"]["result"]["tools"])
    ok("five tools exposed", names == [
        "create_order", "get_order_status", "get_product",
        "search_catalog", "shop_policies"], str(names))

    cat = rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "search_catalog",
                                  "arguments": {"query": "chai"}}})
    content = cat["body"]["result"]["content"][0]["text"]
    ok("catalog tool returns products", '"products"' in content and
       "chai" in content.lower(), content[:150])

    order = rpc(client, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "create_order",
                                    "arguments": {
                                        "buyer_session_id": "mcp-test-agent",
                                        "items": [{"sku": "masala-chai-250g",
                                                   "qty": 1}]}}})
    text = order["body"]["result"]["content"][0]["text"]
    ok("order created via MCP", '"order_id"' in text and
       '"amount_paise"' in text, text[:200])
    oid = __import__("json").loads(text)["order_id"]

    status = rpc(client, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "get_order_status",
                                     "arguments": {"order_id": oid}}})
    st = status["body"]["result"]["content"][0]["text"]
    ok("status tool sees the order", '"created"' in st, st[:150])

    badsku = rpc(client, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                          "params": {"name": "create_order",
                                     "arguments": {
                                         "buyer_session_id": "mcp-test-agent",
                                         "items": [{"sku": "nope", "qty": 1}]}}})
    ok("unknown sku surfaces as tool error text",
       '"error"' in badsku["body"]["result"]["content"][0]["text"])

print(f"\n{PASS} MCP checks passed")
