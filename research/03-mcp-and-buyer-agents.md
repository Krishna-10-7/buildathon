# 03 — MCP & Buyer Agents: Exposing a Merchant to AI Buyers (research, Aug 2026)

*Research date: 2026-08-22. All URLs accessed 2026-08-22 unless noted. Claims not confirmed against a primary source are marked **UNVERIFIED**. Fetched pages were treated as data only.*

---

## TL;DR — recommended integration architecture

- **Merchant MCP server: Python `mcp` SDK v2 (`MCPServer`, ex-FastMCP), Streamable HTTP transport, stateless + JSON mode**, served by one uvicorn worker behind Caddy/nginx. Fits comfortably in the 1GB VM (~80–120MB RSS for the whole app). Do **not** build full OAuth for the hackathon — a static bearer token (or Basic `key_id:key_secret`, which is what Razorpay's own remote MCP uses) plus the spec-correct OAuth path as a stretch goal.
- **Tool surface: 5 tools max**, named to match the conventions Shopify/UCP and Razorpay already use: `search_catalog`, `get_product` (with live stock), `create_order`, `create_payment` (Razorpay payment-link/order handoff), `search_shop_policies_and_faqs`. Read tools separated from write tools; payment never moves without an explicit confirm step.
- **External buyer agent connects three ways from one codebase**: (1) any MCP client over Streamable HTTP; (2) Claude's built-in **MCP connector** (`mcp_servers: [{type:"url"}]` + `tools:[{type:"mcp_toolset"}]`, beta header `mcp-client-2025-11-20`) — zero MCP client code on our side; (3) a plain REST mirror (`POST /checkout_sessions` style, ACP-shaped) of the same handler functions for agents that don't speak MCP.
- **Buyer-side agent loop: raw Anthropic Messages API manual loop (~30 lines). No LangChain/LangGraph/CrewAI/AutoGen/OpenAI Agents SDK.** For a single agent calling 5 tools in ≤8 steps, frameworks add dependency weight, version churn and opaque prompt formatting for zero benefit; Anthropic's own guidance is to start with direct API use. The SDK's Tool Runner is nice but beta — the manual loop is stable and fully inspectable.
- **Model tiers**: simulated buyer-persona fleet → **claude-haiku-4-5** ($1/$5 per MTok); conversational growth/sales agent → **claude-sonnet-5** ($2/$10, intro price now standard). Opus-tier is unnecessary here. Prompt-cache the frozen system+tools prefix (reads are 0.1×); run offline eval replays through the Batch API (−50%). Watch out: Haiku 4.5's minimum cacheable prefix is ~4096 tokens — small persona prompts silently won't cache.
- **Structured output**: force valid arguments with `strict: true` on tool schemas (grammar-constrained); use `output_config.format` JSON schema for final structured verdicts; validate + retry once max; return failures as `is_error: true` tool results so the model self-corrects. Assistant prefilling is gone on current models — don't design around it.
- **Agent-readable storefront credibility checklist**: server-rendered JSON-LD `Product`/`Offer` (+ GTIN/SKU/availability), explicit robots.txt allows for GPTBot/OAI-SearchBot/ClaudeBot/PerplexityBot, a machine product feed endpoint, llms.txt as a cheap pointer to `/api/mcp`. Treat llms.txt as garnish (near-zero real consumption) and structured data + MCP as the meal.

---

## 1. Model Context Protocol — state of the spec, August 2026

### 1.1 Current revision: `2026-07-28`

The current spec revision is **2026-07-28** (previous revisions: 2024-11-05 → 2025-03-26 → 2025-06-18 → 2025-11-25 → 2026-07-28). Verified directly against modelcontextprotocol.io.

Transports:

| Transport | Status | Use |
|---|---|---|
| **stdio** | Current | Local servers launched as subprocesses (Claude Desktop, IDEs). Credentials come from the environment; OAuth "SHOULD NOT" be used. |
| **Streamable HTTP** | Current, reshaped in 2026-07-28 | Remote servers. This is the one to implement. |
| HTTP+SSE (2024-11-05) | **Deprecated** | Migrate away; eligible for removal. |

The **2026-07-28 Streamable HTTP reshape** (breaking vs 2025-03-26…2025-11-25):

- Single MCP endpoint accepting **POST only**; each JSON-RPC message is its own POST.
- Server answers with either a plain JSON object or a **request-scoped SSE stream** (progress notifications then the response).
- **GET stream removed** and **protocol-level sessions removed** (no `Mcp-Session-Id`, no `DELETE`; no `Last-Event-ID` resumability).
- Long-lived change notifications moved to an opt-in `subscriptions/listen` request whose response stays open.
- Server→client interactions (sampling, elicitation, roots) now ride inside results via **Multi Round-Trip Requests (MRTR, SEP-2322)** instead of being sent on SSE streams.
- Every POST must carry `MCP-Protocol-Version: 2026-07-28` (mismatch → 400), plus required routing headers **`Mcp-Method`** (all requests) and **`Mcp-Name`** (`tools/call`, `resources/read`, `prompts/get`). Servers must reject header/body mismatches with 400 + error code `-32020 HeaderMismatch`.
- Optional `x-mcp-header` schema extension mirrors primitive tool parameters into `Mcp-Param-*` headers for intermediary routing.
- Backward compat: old clients attempting GET/DELETE get `405 Method Not Allowed`; clients probe with POST and fall back to `initialize` if the body isn't a recognized modern error.

Practical implication for us: the era of hand-rolled session management is over — a stateless POST-only endpoint behind a dumb reverse proxy is now *the* compliant shape.

### 1.2 Auth: yes, the OAuth resource-server pattern

Authorization is optional, but when implemented over HTTP the spec mandates (verified from the authorization page):

- MCP server = **OAuth 2.1 resource server**; MCP client = OAuth 2.1 client; tokens only ever via `Authorization: Bearer` (never query strings); invalid/expired → 401.
- Servers **MUST** implement OAuth 2.0 Protected Resource Metadata (**RFC 9728**) at `/.well-known/oauth-protected-resource`.
- Clients **MUST** implement Resource Indicators (**RFC 8707**) — `resource` parameter bound to the canonical server URI; servers must validate token audience.
- Authorization servers must expose RFC 8414 or OIDC Discovery metadata; RFC 9207 `iss` validation required on responses.
- Client registration: **Client ID Metadata Documents (CIMD)** are the direction of travel; **Dynamic Client Registration (RFC 7591) is deprecated**, kept for back-compat.
- Step-up auth: 403 + `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`.

Buildathon shortcut that stays honest: static bearer token now, RFC 9728 metadata document + token check later (the TS SDK ships `requireBearerAuth` / `mcpAuthMetadataRouter` helpers for exactly this).

### 1.3 Python vs TypeScript SDK maturity

Verified against github.com/modelcontextprotocol/python-sdk:

- **Python `mcp` v2 is the current stable line** and supports the 2026-07-28 spec "(and every earlier revision)". The high-level class was renamed: **`from mcp.server import MCPServer`** (v1's `FastMCP` is legacy/frozen; pin `mcp>=1.28,<2` if stuck on v1). Note there is also a separate third-party project `jlowin/fastmcp` (v3) — don't confuse it with the bundled one. Client entry point: `from mcp import Client`.
- **TypeScript SDK**: official `McpServer` high-level API with Zod schemas and `registerTool` (incl. structured output support); no fork situation; ships production-grade auth middleware (`requireBearerAuth`, RFC 9728 metadata router).
- Maturity call: historically the TS SDK shipped protocol features first and has the better batteries-included auth story; the Python SDK is the most common choice for tool servers, is fully caught up as of v2, and keeps the whole stack in one language for this project (the buyer-agent loop is Python anyway). **Pick Python v2.**

#### Minimal remote MCP server — Python SDK v2

```python
# merchant_mcp.py — pip install "mcp[cli]" uvicorn
from mcp.server import MCPServer

mcp = MCPServer("acme-store")          # v2 name; v1 called this FastMCP

@mcp.tool()
def search_catalog(query: str, limit: int = 10) -> dict:
    """Search the store catalog. Returns matching products with price,
    currency and stock status."""
    ...

@mcp.tool()
def create_order(sku: str, qty: int, address_id: str) -> dict:
    """Create an order for a human-confirmed purchase."""
    ...

# Standalone: mcp.run(transport="streamable-http", json_response=True, stateless_http=True)
# Mounted into ASGI (recommended — one app for MCP + REST mirror):
app = mcp.streamable_http_app(stateless_http=True, json_response=True)
# uvicorn merchant_mcp:app --host 0.0.0.0 --port 8000
```

> **UNVERIFIED detail:** in v2 the host/port/json/stateless options moved off the constructor onto `run()` / `streamable_http_app()` (they were constructor kwargs on v1 `FastMCP`). Confirm exact kwarg names against https://py.sdk.modelcontextprotocol.io before coding; `stateless_http=True, json_response=True` remains the recommended combo for simple remote deployments behind a proxy.

#### Same thing — TypeScript SDK

```typescript
// npm i @modelcontextprotocol/sdk express zod
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import express from "express";

const server = new McpServer({ name: "acme-store", version: "1.0.0" });

server.registerTool("search_catalog", {
  title: "Search catalog",
  inputSchema: { query: z.string(), limit: z.number().int().default(10) },
}, async ({ query, limit }) => ({ content: [{ type: "text", text: JSON.stringify(search(query, limit)) }] }));

const app = express();
app.post("/mcp", async (req, res) => {
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,   // stateless
    enableJsonResponse: true,        // plain JSON, not SSE
  });
  res.on("close", () => transport.close());
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});
app.listen(8000);
```

### 1.4 How Claude itself can connect (zero client code)

Anthropic's **MCP connector** (beta, header `mcp-client-2025-11-20`; deprecated predecessor `mcp-client-2025-04-04`) lets the Messages API talk to any public Streamable-HTTP/SSE MCP server directly — we write no MCP client:

```python
resp = client.beta.messages.create(
    model="claude-haiku-4-5", max_tokens=1024,
    betas=["mcp-client-2025-11-20"],
    mcp_servers=[{"type": "url",
                  "url": "https://acme.example.com/mcp",
                  "name": "acme-store",
                  "authorization_token": BEARER}],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "acme-store",
            "default_config": {"enabled": False},
            "configs": {"search_catalog": {"enabled": True},
                        "get_product": {"enabled": True}}}],   # allowlist writes away
    messages=[{"role": "user", "content": "Find me a gift under ₹1500"}],
)
```

Both halves are mandatory (`mcp_servers` without a matching `mcp_toolset` is a validation error). Only tool calls are supported (no resources/prompts); stdio servers can't be attached; tool calls come back as `mcp_tool_use` / `mcp_tool_result` blocks. Not ZDR-eligible (standard retention applies).

---

## 2. Commerce MCP prior art

### 2.1 What the big surfaces actually expose

| Provider | Endpoint / delivery | Representative tools | Notes |
|---|---|---|---|
| **Shopify Storefront MCP** (per store) | `https://{shop}.myshopify.com/api/mcp`, unauthenticated | `search_catalog`, `lookup_catalog`, `get_product` (UCP Catalog capability); cart tools `create_cart`/`get_cart`/`update_cart`/`cancel_cart` on a separate endpoint; `search_shop_policies_and_faqs`; checkout via Checkout MCP `create_checkout` | Cart TTL long, checkout sessions short-lived; requires agent to advertise a UCP profile URI. Also a Global Catalog endpoint (`catalog.shopify.com/api/ucp/mcp`) and a browser "WebMCP" proposal |
| **Stripe MCP** | local + docs; agentic checkout via ACP | `stripe_api_search`, `stripe_api_details`, `stripe_api_read` (any GET), `stripe_api_write` (any POST/PATCH…), `create_refund`, `get_balance_summary`, analytics/metric tools | Pattern: generic read/write escape hatch + narrow safe tools. Agentic payments via **Shared Payment Token** scoped to merchant+cart so the AI surface never sees credentials |
| **PayPal MCP** | remote `https://mcp.paypal.com` (OAuth) + local toolkit (TS/Python) | orders, invoices, subscriptions, shipment tracking, disputes, transactions | Rollout began conversationally with invoice links. Plus **Agent Ready** (merchants accept agent payments with no code) and **Store Sync** (catalog syndication into Perplexity et al.) |
| **Razorpay MCP** ⭐ | remote `https://mcp.razorpay.com/mcp` + local Docker; **Basic auth `key_id:key_secret` base64** | 35+ tools: `create_order`, `fetch_order`, `update_order`, `fetch_order_payments`, `create_payment_link`, `create_payment_link_upi`, `capture_payment`, `initiate_payment`, `create_refund`, QR-code tools, settlements, payouts, plus `detect_stack`/`integrate_razorpay_checkout` helpers | **This is the alignment target.** Reuse its exact names/shapes for order & payment tools so judges see native fit |
| **Amazon Buy For Me / Shop Direct** | consumer feature, not an open API | Amazon's agent completes checkout on brand sites using encrypted customer details | Beta Feb 2025; opened to merchants **Mar 2026 via product feeds** (Feedonomics, Salsify, CEDCommerce); 100M+ products, 400K+ merchants; runs on Bedrock (Nova + Claude). Rufus rebranded "Alexa for Shopping" May 2026 |
| **Perplexity shopping** | Merchant Program + APIs | product feed/API ingestion; "Buy with Pro" one-click checkout (Shopify data), PayPal/Venmo Instant Buy (Nov 2025) | Free merchant program; discovery + hosted checkout |

### 2.2 Protocol landscape (know the acronyms for the demo narrative)

| Standard | Backed by | Layer |
|---|---|---|
| **MCP** | Anthropic → Linux Foundation | context/tool layer connecting agents to services |
| **ACP** (Agentic Commerce Protocol) | OpenAI + Stripe, open-sourced | product discovery + inline checkout in ChatGPT; REST `POST /checkout_sessions`, `/complete`, `/cancel` + webhooks; Shared Payment Token |
| **UCP** (Universal Commerce Protocol) | Google + Shopify/Etsy/Wayfair/Target/Walmart; Apache 2.0, Beta | full journey discovery→purchase→orders; REST/A2A/**MCP** transports; powers Google AI Mode/Gemini checkout (Jun 2026) |
| **AP2** (Agent Payments Protocol) | Google + 60 partners → donated to **FIDO Alliance Apr 28 2026** | trust layer *inside* a commerce protocol: signed checkout signatures, CheckoutMandate + PaymentMandate (SD-JWT-VC), compatible with UCP |
| x402 | Coinbase | stablecoin payments over HTTP |

*(Reported by third-party tracker agenticcommerceprotocol.info: OpenAI's native Instant Checkout flow ended March 2026, with ACP carrying ChatGPT commerce discovery onward — **UNVERIFIED secondary source**.)*

### 2.3 Lessons for our tool naming

1. **snake_case verb_noun**, reads prefixed `search_/lookup_/get_`, writes `create_/update_/cancel_` (Shopify, Razorpay, Stripe all converge).
2. **Separate endpoints/scopes for catalog vs cart vs checkout vs policies** — mirrors scope separation in auth.
3. **Cart is durable, checkout is short-lived** (Shopify explicitly). Our `create_order` should be idempotent and expire.
4. Ship a **policies/FAQ tool** — agents ask about returns/shipping constantly and hallucinate otherwise.
5. Keep a generic fallback minimal; prefer **narrow, safe, well-described tools** over Stripe-style god-tools (description quality drives selection accuracy).
6. Payment step must be a **tokenized handoff** (payment link / SPT / UPI Reserve Pay mandate), never credentials-in-chat.

---

## 3. Buyer-side agent loop — direct Messages API vs frameworks

### 3.1 Why lean wins here

| Criterion | Direct Anthropic API loop | LangChain/LangGraph | CrewAI / AutoGen / OpenAI Agents SDK |
|---|---|---|---|
| Code owned | ~30–60 lines | + framework state model, graph DSL | + role/agent abstractions |
| Dependencies | `anthropic` only | heavy tree, frequent breaking churn | heavy, opinionated |
| Debuggability | every byte visible | hidden prompt formatting/context mgmt | multi-layer abstraction |
| Multi-step branching? | n/a — we have one linear funnel | solves sequencing we don't have | solves orchestration we don't have |
| Fit for solo dev, 1 week, 1GB VM | ✔ | ✘ | ✘ |

Community consensus lines up: an agent that calls a handful of tools for ≤~5–8 steps doesn't need a framework ("boilerplate you write is behavior you understand"); adopt one only when you need durable crash-surviving state, human approval gates, or conditional multi-agent fan-out. Anthropic's own "building effective agents" guidance starts at direct API use. The SDK's **Tool Runner** (`client.beta.messages.tool_runner` + `@beta_tool`) automates exactly this loop but is still **beta** — fine to mention, safer to ship the manual loop.

### 3.2 Canonical tool-use cycle

```
┌ request ────────────────────────────────────────────────────────┐
│ POST /v1/messages                                               │
│ { "model": "...", "max_tokens": N,                              │
│   "system": "<frozen persona + rules>",                         │
│   "tools": [ {name, description, input_schema}, ... ],          │
│   "messages": [ {role:"user", content:"..."} ] }                │
└──────────────────────────────────────────────────────────────────┘
        ↓ stop_reason == "tool_use"
┌ response ───────────────────────────────────────────────────────┐
│ { "stop_reason": "tool_use",                                    │
│   "content": [ {"type":"text","text":"Let me check stock..."},   │
│                {"type":"tool_use","id":"toolu_01..",             │
│                 "name":"check_stock","input":{"sku":"X","qty":2}}] } │
└──────────────────────────────────────────────────────────────────┘
        ↓ you append assistant turn VERBATIM, execute tools, append ONE user msg
{ "role":"user", "content":[
    {"type":"tool_result","tool_use_id":"toolu_01..","content":"{\"in_stock\":true}"},
    {"type":"tool_result","tool_use_id":"toolu_02..","content":"Error: sku not found",
     "is_error":true} ] }
        ↓ repeat until stop_reason == "end_turn"
```

Handling rules (each one is a classic silent bug if skipped):

- Loop while `stop_reason == "tool_use"`; break on `"end_turn"`.
- Also handle `"pause_turn"` (re-send the paused assistant turn to continue), `"refusal"` (check before reading content), `"max_tokens"` (raise cap / retry).
- Append the **full `response.content`** (all blocks, incl. thinking) as the assistant turn — extracting only `.text` corrupts history.
- Return **all** `tool_result` blocks in a **single** user message; splitting them trains the model out of parallel calling.
- Failed tool ⇒ `is_error: true` result, never a dropped block.
- Parse tool inputs with `json.loads`, never string-matching (escaping varies).
- Cap turns (e.g. 12) and accumulate `response.usage` for cost telemetry.

### 3.3 The loop, ~30 lines

```python
import anthropic, json

client = anthropic.Anthropic()
TOOLS = [...]                      # strict:true JSON-schema tool defs
HANDLERS = {"search_catalog": search_catalog, "check_stock": check_stock,
            "create_order": create_order, "create_payment": create_payment}

def buy(messages, model="claude-haiku-4-5", system=SYSTEM_PROMPT, max_turns=12):
    for _ in range(max_turns):
        r = client.messages.create(model=model, max_tokens=4096, system=system,
                                   tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": r.content})
        if r.stop_reason != "tool_use":                     # end_turn/refusal/max_tokens
            return r
        results = []
        for blk in (b for b in r.content if b.type == "tool_use"):
            try:
                out = HANDLERS[blk.name](**blk.input)       # parse via SDK, not strings
                results.append({"type": "tool_result", "tool_use_id": blk.id,
                                "content": json.dumps(out)})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": blk.id,
                                "content": f"{type(e).__name__}: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})   # ONE message, all results
    raise RuntimeError("turn budget exceeded")
```

---

## 4. Agent-readable catalog patterns

### 4.1 What actually gets consumed (Aug 2026 reality)

- **llms.txt is garnish, not strategy.** Ahrefs' study of ~137K domains: 28% publish one, but **97% got zero fetches** in May 2026; Google put it in a "mythbusting" section of its GAIO guide (Mueller: akin to `<meta keywords>`), though Chrome Lighthouse 13.3 added an experimental "Agentic Browsing" audit for it. Verdict: add it (it costs ~30 minutes and doubles as your MCP-directory page), expect nothing from it.
- **JSON-LD schema.org Product/Offer is the load-bearing layer.** AI crawlers (OAI-SearchBot etc.) read embedded JSON-LD and mostly do **not** execute JS — prices must be server-rendered. Minimum credible fields: `name`, `description`, `image`, `sku`, `gtin13`/`mpn`, `brand`, `offers{price, priceCurrency, availability, url}`, `aggregateRating`.
- **robots.txt is the binary switch.** Explicitly allow `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`, `ClaudeBot`, `PerplexityBot`; remember these are independent toggles (you can allow search surfacing while disallowing training).
- **ai.txt / identity.json / spiders.txt:** ai.txt claimed at ~9% adoption and identity.json ~7% by one April 2026 vendor blog (**UNVERIFIED — single third-party source**); "spiders.txt" did not surface as an established convention in searches (**UNVERIFIED**). Skip them; spend the hour elsewhere.
- **Machine product feeds are how scale players onboard merchants.** Amazon Shop Direct opened to merchants in Mar 2026 specifically via feed providers (Feedonomics/Salsify/CEDCommerce); Perplexity's Merchant Program ingests feeds/APIs; Google AI surfaces want a clean Merchant-Center-style feed (GTIN, brand, MPN, price, availability, condition, shipping, returns).

### 4.2 Agent-readable storefront example

```html
<!-- Product PDP, server-rendered -->
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Kanchipuram Silk Stole — Indigo",
  "description": "Handloom silk stole, 220g, natural indigo dye.",
  "image": ["https://acme.example/img/stole-indigo-1.jpg"],
  "sku": "ACME-STOLE-IND-01",
  "gtin13": "8901234567890",
  "brand": {"@type": "Brand", "name": "Acme Handlooms"},
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "reviewCount": "83"},
  "offers": {
    "@type": "Offer",
    "price": "2499.00",
    "priceCurrency": "INR",
    "availability": "https://schema.org/InStock",
    "url": "https://acme.example/p/acme-stole-ind-01",
    "shippingDetails": {"@type": "OfferShippingDetails",
      "shippingRate": {"@type": "MonetaryAmount", "value": "0", "currency": "INR"}},
    "hasMerchantReturnPolicy": {"@type": "MerchantReturnPolicy",
      "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
      "merchantReturnDays": 7}
  }
}
</script>
```

```text
# /.well-known/robots.txt additions
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: PerplexityBot
Allow: /

# /llms.txt
# Acme Handlooms — machine-readable storefront
> Handloom textiles. Catalog, stock, orders and policies exposed as MCP tools.
MCP endpoint: https://acme.example/mcp (Streamable HTTP, bearer token)
REST checkout mirror: https://acme.example/api/checkout_sessions (ACP-shaped)
Feed: https://acme.example/feed.json
```

### 4.3 Credibility checklist

1. SSR JSON-LD Product/Offer on every PDP with price + availability + GTIN/SKU.
2. robots.txt explicit allows for the four-five named bots.
3. `GET /feed.json` (Merchant-Center-flavored: id, title, description, link, image_link, price, availability, brand, gtin) regenerated on inventory change.
4. `/llms.txt` pointing at the MCP endpoint, REST mirror, and feed.
5. MCP server at `https://{host}/mcp` mirroring Shopify's per-shop convention; unauthenticated reads, authenticated writes if possible.
6. Policies both as HTML and as the `search_shop_policies_and_faqs` tool answer.

---

## 5. Structured-output reliability & cost control

### 5.1 Forcing valid structure

| Mechanism | What it guarantees | Use for |
|---|---|---|
| `strict: true` on tool definition (top-level field, needs `additionalProperties: false` + `required`) | `tool_use.input` validates exactly against schema (grammar-constrained sampling); tool name always valid | every commerce tool, especially `create_order`/`create_payment` |
| `output_config: {"format": {"type": "json_schema", ...}}` (successor to deprecated `output_format`) | first text block is valid JSON per schema | persona briefs, final session verdicts/lead scores |
| Pydantic helper `client.messages.parse(...)` | validated object back | offline evals |
| `tool_choice: {"type":"tool","name":...}` | forces that specific tool | forcing the "finish_session" verdict call |
| validate + retry | nothing guaranteed — belt & braces | one repair attempt max; retries after that succeed only ~14–18% of the time, so fail fast |

Still-not-guaranteed cases even with strict modes: `stop_reason: "refusal"` (message takes precedence), truncation at `max_tokens`, enum casing mismatches (compare case-insensitively). **Assistant prefilling is removed on current models (Fable 5 / Opus 5 / Sonnet 5 / 4.6+) — it returns a 400. Design format control through schemas and system prompts, not prefills.** Errors-as-results (`is_error: true`) remain the cheapest reliability tool: the model fixes its own bad arguments next turn.

### 5.2 Model tiers (live prices verified 2026-08-22)

| Model | Input $/MTok | Output $/MTok | Cache read | Batch (in/out) | Role |
|---|---|---|---|---|---|
| claude-opus-5 | $5 | $25 | $0.50 | $2.50/$12.50 | not needed |
| **claude-sonnet-4-6** | $3 | $15 | $0.30 | $1.50/$7.50 | alternative growth agent |
| **claude-sonnet-5** | $2 | $10 | $0.20 | $1/$5 | **growth/conversational sales agent** (intro $2/$10 made standard; scheduled Sep 2026 increase cancelled) |
| **claude-haiku-4-5** | $1 | $5 | $0.10 | $0.50/$2.50 | **buyer-persona simulation fleet** |

Tool-use overhead: declaring tools adds a fixed system-prompt chunk (~354 tokens on Sonnet 5 with `auto` choice; ~474 forced) plus schema bytes — keep descriptions tight.

### 5.3 Cost-control tactics table

| Tactic | Mechanism | Expected saving | Caveat |
|---|---|---|---|
| Tier split (Haiku personas, Sonnet growth agent) | right-size per task | 5× cheaper than uniform Sonnet | Haiku weaker at nuanced negotiation — give personas rigid scripts |
| Prompt caching on frozen prefix | `cache_control: {"type":"ephemeral"}` on last system block; render order tools→system→messages | cache reads 0.1×; writes 1.25× (5-min) / 2× (1h) | **Haiku 4.5 minimum cacheable prefix ≈ 4096 tokens** — short persona prompts silently won't cache (`cache_read_input_tokens: 0`). Either pad the shared prefix (persona library up front) or skip caching for tiny calls |
| Prefix hygiene | no timestamps/UUIDs/per-session data before last breakpoint; deterministic tool ordering; never swap tools/model mid-session | preserves all downstream cache | changing tool defs invalidates everything |
| Fan-out warm-up | fire 1 request, await first streamed token, then launch remaining N−1 identical-prefix requests | avoids N cold writes | cache readable only after first response starts streaming |
| Batch API for evals/replays | async, −50% on both directions | 2× | no interactivity; results unordered — key by `custom_id` |
| Turn caps + terse tool outputs | `max_turns≈12`, return compact JSON (ids/prices/status, not prose blobs) | fewer input tokens per turn | don't truncate fields the model needs to decide |
| Strict schemas | kills validate-retry loops | removes ~1 extra call per failure | refusal/truncation still possible |
| Token counting pre-flight | `count_tokens` endpoint | budgeting | — |

Worked estimate, fleet of 150 simulated sessions × ~6 turns × ~2K input/~300 output tokens on **Haiku 4.5 uncached**: ≈1.8M input ($1.80) + 270K output ($1.35) ≈ **$3.15 total**; with caching/batching realistically **$1–2**. Growth agent on Sonnet 5 across a few dozen interactive sessions lands around **$5–15**. Cost is a non-issue at this scale — latency and determinism are the things to engineer.

---

## 6. Conversational checkout UX prior art

| Surface | Interaction pattern worth copying |
|---|---|
| **OpenAI Instant Checkout (ChatGPT)** | "Buy" button inline under a product card → sheet confirms order/shipping/payment → done, without leaving chat. Merchant stays **merchant of record** (orders, fulfillment, support unchanged); platform takes a fee on completed purchases; ranking explicitly unpurchased. Powered by ACP + Stripe Shared Payment Token |
| **Instacart in ChatGPT (Dec 8 2025)** | First full end-to-end: inspiration ("help me shop apple pie ingredients") → conversational list building → Instant Checkout in-app. Lesson: let the chat own *list building*, compress *buying* to one tap |
| **Amazon Rufus / Buy For Me** | In-app product page styled like native; confirm address/tax/payment on an Amazon-styled page; agent completes purchase on merchant site with encrypted customer data; orders tracked in a dedicated tab; merchant handles delivery/returns/support. Price-watch automation ("buy when it hits ₹x") |
| **Razorpay × NPCI × OpenAI (GFF, Oct 9 2025)** | Shopping + paying inside ChatGPT via UPI: **UPI Reserve Pay** blocks funds for future merchant debit, **UPI Circle** handles delegated authentication — no repeated PINs, AI companies never see payment data. Launch merchants BigBasket, Vi; Axis Bank / Airtel Payments Bank as rails |
| **Razorpay × NPCI × Anthropic (Feb 20 2026)** | Agentic payments in **Claude**, pilot users ordering from Zomato/Swiggy/Zepto entirely in-conversation ("dinner for two under ₹1,000"), single-confirm checkout on Reserve Pay |
| **Razorpay in-app pilots** | Vi app: AI recommends recharge plans from usage patterns and pays in-flow (GFF 2025); FTX 2026 pilots with Zomato, PVR INOX, Vodafone Idea, Bluestone, Honasa (The Derma Co) |

Synthesis — the five patterns to replicate in our growth agent:

1. **Pre-authorized bounded spend** (Reserve Pay analog): session-level cap agreed up-front; agent may transact below it, must re-confirm above it. This is also the "bounded upsell" guardrail.
2. **One confirmation moment**: browse/negotiate freely in text, but exactly one explicit approve before money moves; show itemized summary in that card.
3. **Credentials never enter the chat** — payment happens via link/token/mandate handoff.
4. **Merchant of record framing**: our agent assists; orders land in the merchant's normal pipeline (Razorpay `create_order` + webhook), keeping refunds/RPO sane.
5. **Close the loop in-chat**: order confirmation + tracking updates pushed back into the same thread (Buy For Me orders tab / Instacart model).

---

## Sources

Accessed 2026-08-22 unless noted.

**MCP spec & SDKs**
- Streamable HTTP transport, rev 2026-07-28 — https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- Authorization, rev 2026-07-28 — https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- MCP blog, 2026-07-28 release notes — https://blog.modelcontextprotocol.io/posts/2026-07-28/
- MCP Python SDK README (v2, `MCPServer`) — https://github.com/modelcontextprotocol/python-sdk
- TypeScript SDK guide (auth helpers) — https://ts.sdk.modelcontextprotocol.io/v2/documents/Documents.Server_Guide.html

**Commerce MCP / protocols**
- Shopify Storefront MCP — https://shopify.dev/docs/apps/build/storefront-mcp/servers/storefront
- Shopify catalog interfaces (Global/Storefront, UCP) — https://shopify.dev/docs/agents/catalog ; Cart MCP — https://shopify.dev/docs/agents/carts-and-checkout/cart-mcp ; WebMCP — https://shopify.dev/docs/api/web-mcp
- Stripe MCP — https://docs.stripe.com/mcp ; Stripe/OpenAI Instant Checkout + Shared Payment Token — https://stripe.com/newsroom/news/stripe-openai-instant-checkout
- PayPal Agent Toolkit & MCP — https://paypal.gitbook.io/agent-toolkit-and-mcp-server , https://developer.paypal.com/community/blog/paypal-model-context-protocol/
- Razorpay MCP server — https://razorpay.com/docs/mcp-server/tools-reference/ , https://github.com/razorpay/razorpay-mcp-server
- OpenAI "Buy it in ChatGPT" — https://openai.com/index/buy-it-in-chatgpt/ ; ACP checkout spec — https://developers.openai.com/commerce/specs/checkout ; Instacart partnership — https://openai.com/index/instacart-partnership/
- UCP/AP2 relationship — http://ucp.dev/documentation/ucp-and-ap2/ ; AP2 spec — https://github.com/google-agentic-commerce/AP2/blob/main/docs/ap2/specification.md ; UCP deep-dive — https://developers.googleblog.com/en/under-the-hood-universal-commerce-protocol-ucp/
- Amazon Buy For Me — https://www.aboutamazon.com/news/retail/amazon-shopping-app-buy-for-me-brands ; Shop Direct feeds — https://www.aboutamazon.com/news/retail/amazon-shop-direct-external-stores ; TechCrunch Mar 11 2026 — https://techcrunch.com/2026/03/11/amazon-expands-a-program-that-lets-customers-shop-from-other-retailers-sites/
- Perplexity shopping — https://www.perplexity.ai/hub/blog/shop-like-a-pro ; PayPal agentic services — https://newsroom.paypal-corp.com/2025-10-28-PayPal-Launches-Agentic-Commerce-Services-to-Power-AI-Driven-Shopping
- Standards tracker (secondary) — https://agenticcommerceprotocol.info/

**Agent loops / frameworks**
- Build an agent without a framework — https://ai-tldr.dev/learn/agent-frameworks/choosing-a-framework/agent-without-framework/
- The loop that replaces a framework — https://dreaming.press/posts/build-an-ai-agent-from-scratch-the-loop-no-framework.html
- When to skip agent frameworks (2026) — https://aisuffer.com/docs/agent-frameworks/05-when-to-skip-agent-frameworks/
- Framework-first anti-pattern — https://agentpatterns.ai/anti-patterns/framework-first/

**Agent-readable web**
- Ahrefs llms.txt study — https://ahrefs.com/blog/llmstxt-study/ ; llms.txt observatory — https://llmtxt.info/blog/state-of-llms-txt-2026/
- Google llms.txt mythbusting coverage — https://www.searchenginejournal.com/googles-llms-txt-guidance-depends-on-which-product-you-ask/575431/
- Store readiness audit (robots/SSR/schema/feed) — https://www.heartly.io/blog/prepare-store-for-ai-shoppers ; OAI-SearchBot & robots — https://cresva.ai/blog/oai-searchbot-robots-txt-chatgpt-visibility ; OpenAI crawler docs — https://developers.openai.com/api/docs/bots
- ai.txt/identity.json comparison (single-source, UNVERIFIED figures) — https://inite.ai/en/blog/llms-txt-vs-ai-txt-vs-robots-txt

**Claude API**
- Pricing (models, caching, batch, tool-use overhead) — https://platform.claude.com/docs/en/about-claude/pricing
- MCP connector — https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Strict tool use — https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use ; Structured outputs — https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Prompt caching — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Tool-use best practices (community synthesis) — https://usingclaude.com/en/api/tool-use/claude-api-tool-use-best-practices ; validation/repair-loop tactics — https://claudelab.net/en/articles/api-sdk/claude-api-structured-output-schema-validation-repair-loop

**Conversational commerce (India)**
- Razorpay×NPCI×OpenAI GFF announcement (Oct 2025) and Razorpay×NPCI×Anthropic (Feb 2026) — reported via press coverage surfaced in search; verify exact press-release URLs before citing in the pitch deck (**UNVERIFIED primary URL**)
