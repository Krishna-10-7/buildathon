# Agentic Commerce Protocols — Research Report for the Razorpay Buildathon (Track: AI Growth & Agentic Commerce)

**Prepared:** 2026-08-22 · **Scope:** ACP, AP2, x402, NPCI UAP / India rails, Razorpay's agentic stack, and a build plan for one solo dev + ~1 week + 3× 1GB-RAM Linux VMs.

---

## TL;DR — decision-relevant facts

- **Three protocols, three layers.** They are not competitors so much as a stack: **ACP** = commerce/checkout session layer (OpenAI + Stripe), **AP2** = authorization/consent layer via signed mandates (Google → now FIDO Alliance), **x402** = settlement layer over HTTP 402 (Coinbase → now Linux Foundation's x402 Foundation). Saying "we implement all three at their respective layers" is itself protocol fluency; conflating them is a red flag to informed judges.
- **The single highest-leverage insight:** India already shipped the *product* version of AP2. **UPI Reserve Pay** (one consented spending envelope, multiple debits, instant revocation) is functionally an **AP2 open mandate with `payment.budget` / `payment.amount_range` constraints**. Razorpay × NPCI launched exactly this on **Claude with Zomato/Swiggy/Zepto on Feb 20, 2026**. If you build an "AP2-flavored Reserve Pay simulator" in Razorpay test mode, you are mirroring what Razorpay actually took to production — that is the differentiation story.
- **Judging-bar mapping is free with AP2.** The mandate chain Intent → Checkout → Payment → Receipts is literally "every money action explainable/bounded/gated + audit trail": open mandates carry machine-evaluable constraints (`payment.budget`, `payment.amount_range`, `payment.allowed_payees`, recurrence caps); receipts bind back by hash. No other protocol hands you the judging rubric as a data structure.
- **ACP is the most copyable spec.** Merchant-side endpoints (`POST /checkout_sessions`, update/complete/cancel/GET), exact field names, status enums, error shapes, and idempotency headers are fully public. A conformant-looking test-mode implementation is 1–2 days of work and reads as "production-grade" because it matches a published schema.
- **x402 is the cheapest wow.** The whole handshake (402 → `PAYMENT-REQUIRED` header → signed `PAYMENT-SIGNATURE` header → mock facilitator `/verify`+`/settle` → `PAYMENT-RESPONSE`) can run with **zero blockchain and zero real money** in ~1 day using Base64 JSON headers and a stubbed facilitator.
- **Status corrections vs. the track brief:** NPCI's effort is the "**Unified Agent Protocol (UAP)**", not "Unified Agentic Payment Interface". And OpenAI's consumer-facing **Instant Checkout stumbled in March 2026** (~30 Shopify merchants live vs. a promised million-plus; Walmart saw 3× lower conversion in-chat), pivoting to retailer *Apps* inside ChatGPT — but **ACP the protocol remains the connective standard** (latest stable spec: **2026-04-17**).
- **Recommended build (ranked in Section 6):** One merchant flow that speaks three protocols — agent-readable catalog (+ optional x402-gated enriched feed) → AP2 open mandate as the consent envelope (≈ Reserve Pay) → ACP-shaped checkout session API → Razorpay test-mode order/capture → hash-chained mandate/receipt audit ledger → graceful failures (expired mandate, budget exceeded, `payment_declined`, consent revoked).

---

## 1. ACP — Agentic Commerce Protocol (Stripe + OpenAI)

### What it is
An open standard (Apache 2.0, status badge: **Beta**) connecting buyers, AI agents, and businesses; co-maintained by **OpenAI and Stripe** as founding maintainers. It powers **Instant Checkout in ChatGPT**: ChatGPT calls *merchant-implemented* REST endpoints, the merchant stays merchant-of-record, and payments stay on the merchant's existing PSP rails.

### Spec mechanics (the part worth mimicking)
Merchant implements these endpoints; ChatGPT is the caller:

| Method | Path | Purpose |
|---|---|---|
| POST | `/checkout_sessions` | Create session from cart + buyer context (must return 201) |
| POST | `/checkout_sessions/{id}` | Update items/shipping/discounts |
| POST | `/checkout_sessions/{id}/complete` | Finalize with payment data; creates order |
| POST | `/checkout_sessions/{id}/cancel` | Cancel (405 if already completed/canceled) |
| GET | `/checkout_sessions/{id}` | Fetch state (404 if unknown) |

- **Headers in:** `Authorization: Bearer …`, `Idempotency-Key`, `Request-Id`, `Signature` (Base64 body signature), `Timestamp` (RFC 3339), `API-Version`. Responses must echo `Idempotency-Key` and `Request-Id`.
- **Checkout Session object (required fields):** `id`, `status`, `currency` (ISO 4217 lowercase), `line_items`, `fulfillment_options`, `totals`, `messages`, `links`.
  - `status` enum: `not_ready_for_payment` | `ready_for_payment` | `completed` | `canceled`.
  - Line items use integer minor units: `"base_amount": 300, "discount": 0, "subtotal": 300, "tax": 30, "total": 330`.
  - `totals` types: `items_base_amount`, `items_discount`, `subtotal`, `discount`, `fulfillment`, `tax`, `fee`, `total` — with validation identities (subtotal = base − discount; total = base − discount − discount + fulfillment + tax + fee).
  - `messages`: type `info`|`error`; error codes include `missing`, `invalid`, `out_of_stock`, `payment_declined`, `requires_sign_in`, `requires_3ds`; `param` uses RFC 9535 JSONPath, e.g. `"$.line_items[1]"`.
- **Delegated Payments / Shared Payment Token:** `complete` carries `payment_data: { token, provider, billing_address }` — example `"token": "spt_123"`. Merchant accepts the token and runs its normal authorization/capture (for Stripe: SPT → create + confirm a PaymentIntent). Providers documented: stripe/adyen/braintree; method `card`.
- **Webhooks (merchant → platform):** `order_created`, `order_updated`, HMAC-signed with header like `"Merchant_Name-Signature"`; order status enum `created, manual_review, confirmed, canceled, shipped, fulfilled`; refunds typed `store_credit` | `original_payment`.
- **Error shape:** `{type, code, message, param}`, e.g. `{type:"invalid_request", code:"request_not_idempotent"}`.

Example complete-request payment fragment:
```json
{ "payment_data": { "token": "spt_123", "provider": "stripe" } }
```

### Versioning & extensions
Date-versioned: 2025-09-29 initial → 2025-12-12 fulfillment → 2026-01-16 capability negotiation → 2026-01-30 extensions/discounts/payment handlers → **2026-04-17 cart, feed, orders, authentication, MCP (latest stable)**. An Extensions Framework RFC (draft 2026-01-27) lets sellers advertise optional capabilities via a `capabilities` object; third-party extension naming uses reverse-DNS (`com.example.loyalty`). A Payment Handlers RFC defines how PSPs/instrument schemas plug in — this is the slot where a **UPI/Razorpay handler would live** (a claimed Razorpay-authored SEP exists only in a community landscape doc — UNVERIFIED against the official repo).

### Status Aug 2026
- Instant Checkout remains limited to approved partners; building on ACP is open to all; conformance certification required before going live.
- **March 2026 pivot (critical context):** CNBC reported Instant Checkout underperformed — ~30 Shopify merchants live (Forrester), Walmart saw 3× lower in-chat conversion than rerouting to its own site, Semrush found only 22% of users had bought inside an AI tool. Shopify moved to default "ChatGPT Agentic Storefronts" (Global Catalog discovery; checkout completes on the merchant storefront). OpenAI says ACP remains "the infrastructure that connects users to merchants across the full shopping journey," with purchases moving to Apps.
- Net: cite ACP as the *protocol*, don't claim Instant Checkout is winning.

### India angle
No India-specific provider appears in the official repo. But the track's example direction ("conversational in-app checkout") is exactly ACP-shaped, and BigBasket became the first Indian company with conversational quick commerce + embedded UPI payments via Razorpay rails (Stellagent). An ACP-shaped checkout-session facade over Razorpay test mode is a credible stand-in for "what ChatGPT would call if this merchant joined."

---

## 2. AP2 — Agent Payments Protocol (Google → FIDO Alliance)

### What it is
Announced by Google Sept 16, 2025 with **60+ partners** (Mastercard, American Express, PayPal, Revolut, Adyen, Worldpay, Checkout.com, JCB, UnionPay International, Coinbase, Ethereum Foundation, MetaMask, Mysten Labs, Etsy, Intuit, Salesforce, ServiceNow, PwC, Forter…). It works as an extension of **A2A** and **MCP** and is rail-agnostic (cards, real-time bank transfers, stablecoins). On **April 28, 2026 Google donated AP2 (v0.2) to the FIDO Alliance**, alongside Mastercard's companion standard **Verifiable Intent (VI)**; work continues in FIDO's Agentic Authentication TWG (chaired incl. CVS Health/Google/OpenAI) and Payments TWG (**chaired by Mastercard and Visa**). Original framing used **Intent Mandate + Cart Mandate**; current v0.2 terminology is **Checkout Mandate + Payment Mandate** (open/closed variants) — know both vocabularies, judges may have read either.

### Roles
Five roles (entities may hold several): **Shopping Agent** (discovery, checkout assembly, execution), **Credential Provider** (source of payment credentials; scopes them), **Merchant**, **Merchant Payment Processor**, and **Trusted Surface** — the UI obtaining informed consent, which **must be non-agentic**. All security validation must run in deterministic code regardless of LLM involvement.

### Spec mechanics (v0.2)
- **Closed Checkout Mandate** (`vct: "mandate.checkout.1"`): carries `checkout_jwt` (merchant-signed JWT of the checkout: `order_id`, `merchant{id,name,website}`, `line_items[{id, product{id,title,price,currency}, quantity}]`, `total_price`, `currency`, shipping/return policy) plus `checkout_hash` = hash of that JWT (algo matching `_sd_alg`, else sha-256).
- **Open Checkout Mandate** (`mandate.checkout.open.1`): constraints instead of fixed cart — `checkout.allowed_merchants`, `checkout.line_items` requirements (evaluated as a maximal-flow matching problem; no item reuse; splitting across checkouts unsupported).
- **Payment Mandate** (`mandate.payment.1` closed / `.open.1`): `transaction_id` (base64url hash of `checkout_jwt`), `payee`, `payment_amount` (ISO 4217 minor units — `27999` = $279.99), `payment_instrument {id,type,description}`, optional `execution_date`, `risk_data`, `iat/exp`.
- **Constraint vocabulary (open mandates) — this is your "bounded actions" toolkit:**
```json
{ "type": "payment.budget",          "max": 1000.00, "currency": "USD" }
{ "type": "payment.amount_range",    "currency": "USD", "max": 20000, "min": 0 }
{ "type": "payment.agent_recurrence","frequency": "MONTHLY", "max_occurrences": 12 }
{ "type": "payment.allowed_payees",  "allowed": [ { "id": "merchant_1", "name": "Demo Merchant" } ] }
{ "type": "payment.execution_date",  "not_before": "...", "not_after": "..." }
```
- **Crypto format:** SD-JWT VCs signed ES256; header `typ` distinguishes open (`example+sd-jwt`) vs closed/key-bound (`kb+sd-jwt`); payload has `_sd_alg: "sha-256"` and `delegate_payload` digest placeholders; disclosures are `[salt, value]` pairs; encoded form `header.payload.signature~disclosure~…~`. Autonomous mode **must** embed the agent public key as a `cnf` claim (`{"jwk":{"kty":"EC","crv":"P-256",…}}`); short-lived `exp` recommended; checkout JWTs must use non-deterministic ECDSA (not Ed25519) to block rainbow-table attacks on hashes.
- **Chain of evidence:** user signs open mandate (constraints) → Shopping Agent assembles checkout → Merchant signs Checkout JWT → closed mandate binds it by hash → verifiers recompute hashes → **Checkout Receipt** (`status Success|Error`, `iss`, `iat`, `reference` = hash of bound mandate, `order_id`) and **Payment Receipt** (`reference`, `payment_id`, `psp_confirmation_id`, `network_confirmation_id`) close the loop. At dispute time each role re-verifies its mandate independently — non-repudiable.
- **Flows:** Direct (human present; user approves closed mandates) vs Autonomous (human not present; user pre-signs open mandates, agent signs closed ones with its own key). Anti-replay rule: an SA can't submit a new open mandate until the previous one returns a rejection receipt.
- **Extension points:** mandate constraint types, checkout object contents (UCP-compatible), payment instrument types, credential formats (an **x402 sample exists among AP2's reference scenarios** — the two protocols interlock officially).

### Status Aug 2026
Spec **v0.2**, Apache 2.0, hosted at ap2-protocol.org, stewardship at FIDO. Human-not-present payments are the headline v0.2 capability. Commerce/catalog details are deliberately out of scope (UCP is the recommended carrier).

### India angle
No confirmation surfaced of Razorpay being a named AP2 launch partner (UNVERIFIED either way). But the *conceptual* alignment is strong and citable: UPI Reserve Pay's "approve once, spend within envelope, revoke anytime" ≈ AP2 open mandate with `payment.budget` + revocation. Building AP2 artifacts around a Razorpay test-mode flow demonstrates you understand why India's rails look the way they do.

---

## 3. x402 — HTTP 402 payments (Coinbase → x402 Foundation)

### What it is
An HTTP-native payment standard that revives status **402 Payment Required** for machine-to-machine, account-free payments (stablecoin-first, but framed as extensible to cards). Initially developed by Coinbase, Cloudflare, and Stripe; contributed to the **Linux Foundation on April 2, 2026**; the **x402 Foundation reached operational launch July 14, 2026** with 40 member orgs (premier: Adyen, AWS, Amex, Circle, Cloudflare, Coinbase, Fiserv, Google, Mastercard, MoonPay, Ripple, Shopify, Solana Foundation, Stellar, Stripe, Visa).

### Exact mechanism (HTTP transport, spec v2)
1. Client requests a paid resource without payment → server returns **402** with a **`PAYMENT-REQUIRED`** response header containing Base64-encoded JSON:
```json
{
  "x402Version": 2,
  "resource": { "url": "https://api.example.com/premium-data",
                "description": "Access to premium market data",
                "mimeType": "application/json" },
  "accepts": [ { "scheme": "exact", "network": "eip155:84532",
                 "amount": "10000", "asset": "0x<USDC contract>",
                 "payTo": "0x<recipient>", "maxTimeoutSeconds": 60,
                 "extra": { "name": "USDC", "version": "2" } } ]
}
```
2. Client picks one `accepts` entry, builds an EIP-3009 authorization, signs it (EIP-712), resubmits with a **`PAYMENT-SIGNATURE`** request header (Base64 JSON; v1 name was `X-PAYMENT`):
```json
{
  "x402Version": 2,
  "resource": { "url": "...", "description": "...", "mimeType": "application/json" },
  "accepted": { "scheme": "exact", "network": "eip155:84532", "amount": "10000",
                "asset": "0x…", "payTo": "0x…", "maxTimeoutSeconds": 60 },
  "payload": {
    "signature": "<65-byte hex sig>",
    "authorization": { "from": "0x…", "to": "0x…", "value": "10000",
                       "validAfter": 1700000000, "validBefore": 1700000065,
                       "nonce": "<32-byte hex>" } } }
```
3. Server verifies **locally or via a facilitator**: facilitator exposes `POST /v2/x402/verify` (read-only checks: signature, balance, amount, time window, parameter match, tx simulation) then, after doing the work, `POST /v2/x402/settle` (commits on-chain, typically `transferWithAuthorization`). Both take `{x402Version, paymentPayload, paymentRequirements}`.
4. Server responds **200** with resource + a **`PAYMENT-RESPONSE`** header (Base64):
```json
{ "success": true, "transaction": "0x<txhash>", "network": "eip155:84532", "payer": "0x…" }
```
   Failure keeps HTTP 402: `{ "success": false, "errorReason": "insufficient_funds", "transaction": "", … }`. Error→status mapping: invalid payment 400, payment failed 402, success 200, server error 500.
- Networks: CAIP-2 identifiers (`eip155:84532` = Base Sepolia); EVM chains + Solana; scheme `exact` uses EIP-3009 gasless transfer-with-authorization.
- **Bazaar extension:** a discovery index for payable APIs/MCP tools — facilitators expose `/discovery/resources`, and post-payment statuses come back in an `EXTENSION-RESPONSES` header (`success`/`processing`/`rejected`). Agentic.Market (Apr 20, 2026) self-indexes Bazaar-enabled services.

### Status Aug 2026
Self-reported last-30-day stats: **75.41M transactions, $24.24M volume, 94K buyers, 22K sellers**; zero protocol fees; production-ready per the site. Trusted-by logos include Cloudflare, AWS, Stripe, Visa, Mastercard. This is the most *live* ecosystem of the three by transaction count (micropayments skew small: ~$0.32 average).

### India angle
No direct Razorpay-x402 announcement found. Community content positions USDC-on-MCP+x402 as a cheaper alternative to Razorpay International's cross-border fees (~3% + GST) — useful color, not load-bearing. For your demo, **mock the facilitator**; optionally settle on Base Sepolia testnet (free faucet funds, no RAM-heavy node needed — you never run a chain client, just sign payloads).

---

## 4. NPCI UAP / India agentic rails + regulatory signals

### What actually exists (as of Aug 2026)
- **Name correction:** NPCI's initiative is the "**Unified Agent Protocol (UAP)**". Per Business Standard (July 9, 2026) it is *in development, industry consultation stage* — a trust/verification layer to register, verify, and authorize AI agents across UPI **without changing the underlying rails**. Built on the **UPI Circle** delegated-payments model; adds spending limits, consent controls, audit trails, dispute handling. Privacy-by-design: NPCI verifies request genuineness without seeing purchase contents. Launch needs **RBI approval**; no timeline published. Major payments firms have reportedly chosen to work with NPCI rather than build rival protocols.
- **Live today: UPI Reserve Pay** (a.k.a. Single Block Multiple Debit, SBMD), unveiled by RBI Governor Sanjay Malhotra at Global Fintech Fest Oct 8, 2025: block up to **₹10,000** for up to **90 days**, multiple partial debits within the block, initially online verified merchants with low ticket sizes (groceries, food delivery, fuel, transport MCCs), currently **one active SBMD mandate per user**. Issuer/apps coverage is still partial (per Pine Labs FAQ: savings accounts at SBI/ICICI/Axis/Kotak/IDFC First; Paytm/Navi/BHIM live; PhonePe/GPay "coming soon").
- **Proof-point demos/pilots:** GFF 2025 agentic-UPI demo with **Gemini + BigBasket + Google Pay + Razorpay (PSP) + HDFC Bank (issuer) + Axis Bank (acquirer)** ("discover, decide, complete" flow); **Razorpay × NPCI Agentic Payments on Claude** (Feb 20, 2026) with Zomato/Swiggy/Zepto in pilot; an NPCI-OpenAI UPI-in-ChatGPT partnership announced GFF 2025; an NPCI–Anthropic pilot reported with caps up to ₹15,000 (secondary source — treat as indicative); Cashfree and PayU also shipped LLM-surface agentic payments in Feb 2026.
- **Adjacent:** Pine Labs' P3P combines Reserve Pay/SBMD + OTM mandates + identity layer + **HTTP 402** for autonomous agent payments — and has drawn regulatory questions (AFA/e-mandate rules, liability, privacy). MediaNama has proposed design idioms like delegated agent handles (`agent-nixxin@ybl`), separate agent PINs, and low default limits.

### Regulatory signals (use these to justify every gate in your demo)
- **RBI FREE-AI committee report:** 7 "Sutras," 26 recommendations across 6 pillars; Rec 16 requires governance **before deploying autonomous AI systems** capable of independent financial decisions, with human oversight for medium/high-risk cases.
- **RBI draft AI model-risk framework** (comments closed July 24, 2026): experts criticize it for ignoring agentic workflows; asks include workflow-level risk classification, **agent-level authority matrices**, full audit trails recording hand-offs, reversibility/kill-switches.
- **MeitY/CERT-In Digital Threat Report 2025-26:** proposes **mandatory human-in-the-loop above defined financial thresholds** with full audit trails for agentic AI payments.
- Translation for your pitch deck: "budget cap + human confirmation threshold + append-only audit log + revocation" isn't just good UX, it's the emerging compliance posture in India.

---

## 5. Razorpay's agentic moves, 2025–2026

- **Product surface** ([razorpay.com/agentic-payments](https://razorpay.com/agentic-payments/)): three surfaces — In-App Commerce (live beta), On-LLM in-chat checkout, Voice AI; rails UPI/cards/wallets/netbanking; "**AI-Ready MCP & APIs — 40+ composable tools**"; UPI Reserve Pay marked live; UPI Circle support "coming soon."
- **Remote MCP Server:** `https://mcp.razorpay.com/mcp` (the `/sse` endpoint deprecated Aug 13, 2025); auth = Basic with `base64(key_id:key_secret)`; works with Claude, ChatGPT, Cursor, VS Code agent mode; tools span orders, payments, refunds, QR codes, settlements, payouts, saved tokens. This is your fastest integration path — an agent can drive the full test-mode lifecycle through MCP alone.
- **Headline launch:** Razorpay × NPCI **Agentic Payments on Claude** (India AI Impact Summit, Feb 20, 2026) — Zomato/Swiggy/Zepto, built on UPI Reserve Pay: approve one spending limit per merchant, transact without repeated PIN prompts, real-time visibility, flexible limits, instant consent revocation. CEO Harshil Mathur: "AI shouldn't stop at recommendations — it should finish the job… the real challenge with AI-led commerce isn't intelligence – it's trust." NPCI ED Sohini Rajola: consent once so intelligent systems transact "in a controlled, transparent way." Notably, **neither party named ACP/AP2/x402** — Razorpay's framing is entirely UPI-consent-native.
- **Earlier:** industry-first NPCI + OpenAI partnership at GFF 2025; Replit partnership (Feb 2026) for monetizing AI-first builders; **Agent Studio / AI-Native Agents Studio** marketplace on Claude's platform shipping four agents (Abandoned Cart Conversion, Dispute Responder, Subscription Recovery, Cashflow Forecaster); Razorpay CLI marketed as "agentic by design."
- **Test mode:** any `rzp_test_` keypair operates on simulated data with **no real money movement** — exactly the sandbox this buildathon expects. REST base `https://api.razorpay.com/v1`, HTTP Basic auth.
- **Buildathon fit (third-party source, verify against official rules):** the Razorpay AI Buildathon 2026 is described as a hiring program (AI Builder Intern, ₹75k/month stipend, deadline Sept 5, 2026) where the "AI Growth & Agentic Commerce" track sits beside AI Risk Manager, AI Revenue Recovery, AI Finance Controller, and Open Track; evaluation = GitHub project + 5-minute pitch + architecture walkthrough + panel interview. UNVERIFIED details — confirm on the official page.

---

## 6. What we should implement (ranked)

Constraints recap: solo dev, ~7 days, 3 VMs × 1GB RAM (so: Python FastAPI or Node/Express, SQLite/file-based ledgers, hosted LLM API or scripted tool-loop; no local models, no chain nodes).

### #1 — "Reserve-Pay-as-AP2-mandate" consent engine (highest judge-impression per hour)
Simulate UPI Reserve Pay using **real AP2 artifact formats** in test mode:
1. User sets an envelope once: generate an **open Payment Mandate** SD-JWT (ES256, `vct: "mandate.payment.open.1"`) with constraints `payment.budget` (₹10,000 — the actual Reserve Pay cap), `payment.amount_range`, `payment.allowed_payees` (your merchant), optional `payment.agent_recurrence`, short `exp`, and `cnf.jwk` binding the agent key.
2. Before every money action, deterministic Python evaluates the closed mandate against constraints **before** any Razorpay call. Budget left, payee allowed, not expired, under HITL threshold.
3. Above a threshold (say > ₹2,000), require human-in-the-loop re-approval (echoing MeitY/CERT-In + FREE-AI language).
4. After Razorpay test-mode capture, emit a **Payment Receipt** (`status`, `reference` = mandate hash, `payment_id` = Razorpay payment id, `psp_confirmation_id`).
Why first: this maps 1:1 to the judging bar (explainable/bounded/gated + audit trail), mirrors Razorpay's actual production pilot, and gives you the phrase "we implemented India's Reserve Pay semantics in the emerging global mandate format." sd-jwt libraries exist for both Python and Node; if selective disclosure proves fiddly, ship plain ES256-signed JWTs with the same field structure and note the substitution honestly.

### #2 — ACP-conformant checkout-session facade (1–2 days, maximum "looks production-grade")
Implement the merchant side of the ACP Agentic Checkout spec against Razorpay test mode: `POST/GET /checkout_sessions`, `complete`, `cancel`; correct field names, integer minor units, totals validation identities, status enum transitions, `Idempotency-Key`/`Request-Id` echo, error shape `{type, code, message, param}` with JSONPath params, and HMAC-signed `order_created` webhooks. Your "agent buyer" plays the ChatGPT role; map `payment_declined` and `out_of_stock` messages onto real Razorpay failure responses. Judges who've read the spec will recognize fidelity instantly; judges who haven't will still see disciplined API design.

### #3 — Mock-facilitator x402 handshake (1 day, cheapest wow)
Wrap your **agent-readable catalog enrichment** (e.g., premium structured data: margin-aware upsell metadata, stock confidence) behind x402: unpaid request → 402 + Base64 `PAYMENT-REQUIRED` header; agent builds the payload (sign with a local dev key; no blockchain needed) → `PAYMENT-SIGNATURE` header; a **stub facilitator** service implements `/verify` and `/settle` returning synthetic tx hashes; response carries `PAYMENT-RESPONSE`. Optionally register in the Bazaar-style discovery shape (`/discovery/resources`) for bonus points. Label clearly: "settlement simulated; handshake spec-exact."

### #4 — The unifying audit ledger + failure theater (half day, sells the whole story)
Append-only, hash-chained JSONL log where each event references prior hashes: open mandate → cart assembled → closed checkout mandate → ACP session created/updated/completed → Razorpay order/payment ids → receipts → webhook deliveries. Then demonstrate **one failure gracefully handled end-to-end** (pick two: expired mandate → clean rejection before any PSP call; budget exceeded → constraint evaluator blocks with machine-readable reason; declined payment → ACP `payment_declined` message surfaced conversationally; mid-session consent revocation → next attempt fails fast with receipt trail intact).

### Recommended narrative arc for the pitch
"One agent, three standards, one Indian rail": discover from an agent-readable catalog (Bazaar-shaped) → consent via an AP2 open mandate (= UPI Reserve Pay envelope) → negotiate an ACP checkout session → settle on Razorpay test mode → every step bound by hashes into an auditable ledger, with a hard human-in-the-loop gate. That sentence positions you simultaneously as standards-fluent, India-aware, and compliant-by-design — which is precisely the intersection Razorpay is publicly betting on.

### What NOT to do
- Don't claim real settlement anywhere (no mainnet, no live UPI debit) — everything test-mode/simulated, stated on slides.
- Don't build a fourth proprietary protocol or rename things — use exact published names (`PAYMENT-SIGNATURE`, `checkout_hash`, `ready_for_payment`); renaming signals unfamiliarity.
- Don't conflate the layers (calling ACP a payment protocol or AP2 a settlement protocol) — informed judges will notice.
- Don't burn time on Instant-Checkout-style consumer UX polish; the pivot news shows that's not where the puck is — protocol plumbing + governance is.

---

## Sources (all accessed 2026-08-22)

**ACP**
- https://developers.openai.com/commerce/specs/checkout — Agentic Checkout spec (endpoints, session object, errors, webhooks)
- https://github.com/agentic-commerce-protocol/agentic-commerce-protocol — RFC list, version history (2026-04-17 stable), governance
- https://www.agenticcommerce.dev/docs/reference/checkout — Shared Payment Token / Stripe integration
- https://www.agenticcommerce.dev/docs/concepts/capability-negotiation and /docs/concepts/extensions — capabilities + extension naming
- https://github.com/agentic-commerce-protocol/agentic-commerce-protocol/blob/main/rfcs/rfc.extensions.md — Extensions Framework draft (2026-01-27)
- https://agentic-commerce-protocol.com/docs/commerce/specs/checkout — protocol mirror site

**ACP market status**
- CNBC reporting (March 2026) on Instant Checkout stumble / pivot to Apps, via search summary; Modern Retail + Digital Commerce 360 on Shopify "ChatGPT Agentic Storefronts"; Forrester estimate (~30 Shopify merchants); Semrush survey (22%). Retrieved via WebSearch summaries of these outlets.

**AP2**
- https://ap2-protocol.org/ap2/specification/ — roles, flows, extension points, v0.2
- https://ap2-protocol.org/ap2/checkout_mandate/ — open/closed checkout mandate schemas, worked SD-JWT examples
- https://ap2-protocol.org/ap2/payment_mandate/ — payment mandate schema, constraint types, receipt schema
- https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol — original announcement, Intent/Cart Mandate framing, partner roster
- https://paymentsjournal.com/googles-agentic-commerce-protocol-gets-an-array-of-backers/ ; https://decrypt.co/339752/... ; https://replyant.com/lab/ap2-agent-payments-protocol/ — partner/technical corroboration
- FIDO Alliance donation (April 28, 2026), v0.2 Human-Not-Present, Verifiable Intent, working-group chairs — via WebSearch summaries (FIDO/Mastercard announcements)

**x402**
- https://www.x402.org/ — overview, ecosystem stats
- https://github.com/x402-foundation/x402/blob/main/specs/transports-v2/http.md — PAYMENT-REQUIRED / PAYMENT-SIGNATURE / PAYMENT-RESPONSE schemas, error mapping
- https://docs.cdp.coinbase.com/x402/core-concepts/how-it-works and https://docs.x402.org/core-concepts/facilitator — facilitator /verify + /settle
- https://www.linuxfoundation.org/press/linux-foundation-is-launching-the-x402-foundation-and-welcoming-the-contribution-of-the-x402-protocol (Apr 2, 2026); PR Newswire operational-launch release (Jul 14, 2026)
- https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx — Bazaar discovery extension

**NPCI / India / regulatory**
- https://www.business-standard.com/finance/news/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol-126070801343_1.html — UAP development, July 9 2026
- https://stellagent.ai/insights/india-npci-unified-agent-protocol-upi and https://stellagent.ai/insights/india-agentic-commerce-fintech-payment — UAP design, Feb 2026 PSP launches
- https://www.npci.org.in/uploads/RBI_Governor_unveils_New_Generation_of_Digital_Payment_Initiatives_at_GFF_6494fabdfc.pdf — Reserve Pay/SBMD unveiling
- https://www.npci.org.in/uploads/UPI_OC_No_228_FY_2025_26_...pdf — SBMD limits circular
- Inc42 — GFF 2025 agentic-UPI demo participants (Gemini, BigBasket, Google Pay, Razorpay, HDFC, Axis)
- https://www.medianama.com/2026/07/223-meity-proposes-mandatory-human-interventions-agentic-ai-payments/ — CERT-In HITL proposal, agent-handle ideas
- https://www.medianama.com/2026/06/223-pine-labs-agentic-payments-protocol-upi-liability-privacy-questions/ — P3P, regulatory questions
- https://www.rbi.org.in/Scripts/PublicationReportDetails.aspx?ID=1306 — FREE-AI committee report
- https://forum.nls.ac.in/ijlt-blog-post/... — PA Master Directions gap analysis, AIGEG
- https://news.abplive.com/business/ai-upi-payments-npci-unified-agent-protocol-explained-1855498 — UAP explainer

**Razorpay**
- https://razorpay.com/newsroom/razorpay-npci-launch-agentic-payments-on-claude-powering-zomato-swiggy-zepto-at-the-india-ai-impact-summit/ — Feb 20, 2026 launch
- https://razorpay.com/agentic-payments/ — product surfaces, 40+ tools, Reserve Pay live
- https://razorpay.com/docs/mcp-server/remote — MCP server setup, endpoint, auth
- https://razorpay.com/cli — CLI "agentic by design"
- https://inc42.com/features/razorpays-biggest-bet-from-payments-to-becoming-the-ai-brain-for-indias-small-businesses/ — Agent Studio strategy
- https://velonx.in/blog/razorpay-ai-buildathon-2026-tracks-eligibility-stipend-selection-process — buildathon mechanics (UNVERIFIED third-party)
- https://bollardai.com/resources/razorpay — test/live key behavior

**Cross-protocol landscape**
- https://github.com/goodmeta/agent-payments-landscape — community comparison table (source of the unverified "Razorpay UPI in ACP SEP" claim; treat accordingly)
