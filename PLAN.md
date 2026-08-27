# PLAN.md — Master Build Plan

**Project codename:** `Bazaar` *(working name — merchant stack that AI agents can buy from, with a governed growth agent on top)*
**Track:** 01 — AI Growth & Agentic Commerce (Razorpay test mode)
**Team:** solo · **Window:** ~9 build days from 2026-08-23 · **Infra:** 3 × 1 GB Linux VMs + your Windows dev machine
**Research base:** `research/01…06` (all read; conflicts resolved below)

---

## 0. The pitch (memorize this)

> **"One merchant. Three standards. One Indian rail."**
> An external AI buyer discovers our merchant through an agent-readable catalog, negotiates consent through an **AP2-flavored UPI-Reserve-Pay mandate**, checks out through an **ACP-conformant session API**, pays via **Razorpay test rails** — while the merchant's own **growth agent** lifts net revenue, every rupee movement passing a policy gate into a hash-chained audit ledger you can verify live on stage.
>
> This mirrors Razorpay's actual production direction: MCP Server (Apr 2025) → Agentic Payments on ChatGPT w/ NPCI (Oct 2025) → Agentic Payments on Claude w/ Zomato/Swiggy/Zepto (Feb 2026) → Agent Studio on Claude Agent SDK (FTX'26). We are building the merchant side of *their* roadmap.

## 1. What we build (components)

| # | Component | Where it lives | Spec source |
|---|---|---|---|
| C1 | Merchant core API: catalog, orders, payments, webhooks | VM1 | research/02 |
| C2 | Policy engine + proposals/approvals queue | VM1 | research/04 |
| C3 | Hash-chained audit ledger + decision records | VM1 | research/04 |
| C4 | Growth agent (bounded conversational upsell) | VM2 | research/03 |
| C5 | Simulated buyers (persona fleet + headless-Chromium payer) | **dev machine** (experiments) + VM2 (live demo) | research/02+05 |
| C6 | ACP-conformant checkout facade (`/checkout_sessions`) | VM1 | research/01 |
| C7 | AP2-flavored mandate engine (signed mandates, budget/payee/expiry constraints) | VM1 | research/01 |
| C8 | x402-style mock settlement (402 → PAYMENT-* headers → verify/settle stubs) | VM1 | research/01 |
| C9 | Merchant MCP server (`search_catalog`, `get_product`, `create_order`, `create_payment`, `search_shop_policies_and_faqs`) | VM1 | research/03 |
| C10 | Agent-readable catalog: JSON-LD Product/Offer, robots.txt allows, `/feed.json`, `/llms.txt` pointer | VM1 | research/03 |
| C11 | Control Tower: audit explorer, approval queue, live metrics (htmx+SSE, Chart.js) | VM3 | research/04+06 |
| C12 | Measurement harness (paired A/B, bootstrap CIs, PREREGISTERED.md) | dev machine | research/05 |

### Critical architecture decisions (from research)

1. **No pure-API payment completion exists** — confirmed by Razorpay (GitHub issue #113). The mock bank "Success/Failure" button is scriptable → we drive **Playwright headless Chromium** as the simulated buyer.
   - **Memory consequence:** Chromium (~350–450 MB) does NOT comfortably co-exist with the agent fleet on a 1 GB VM.
   - **Resolution (hybrid split):**
     - **Dev machine runs the 300–400-session measurement experiments** (plenty of RAM, faster iteration).
     - **VM2 runs only the live-demo path:** growth-agent service + ONE sequential headless Chromium (spawn per payment, kill after, `--disable-gpu --no-sandbox --single-process`), budget ≈ 600 MB peak with swap insurance. Live demo = ~6 payments total, sequential — fits.
2. **Webhooks need public HTTPS on ports 80/443.** `ngrok.io` is blacklisted by Razorpay docs; cloudflared UNVERIFIED. → Get a real domain (or free DuckDNS subdomain) + Caddy auto-TLS on VM1 during setup day. Test this Day 1, not later.
3. **Test-mode quirks to design around:** UPI payment links unsupported in test; UPI cancellation becomes success in test (→ use **cards** for deterministic demos); test QR vanishes in 2 s; subscription tokens expire after 3 days (→ run subscription-recovery arc on demo day); Payment Links capped at 30/business (→ don't burn them).
4. **Protocols at their correct layers** (not competitors): ACP=checkout session, AP2=consent/mandate, x402=settlement. Implement in that order of judge-impression-per-hour: **AP2 mandate engine first** (it IS the judging bar's audit story), then ACP facade, then x402 mock gating premium catalog data.
5. **Stack everywhere:** Python 3.12 + FastAPI + uvicorn(1 worker) + SQLite WAL + Caddy + systemd(`MemoryMax`) + htmx/Alpine/SSE + Chart.js. No Next.js, no Postgres, no Docker, no LangChain. **LLM access = one adapter (`app/llm.py`), provider via env var (`mock` | `gemini` | `groq`) — ₹0 spend, free tiers only** (Gemini Flash primary ~15 RPM/1,500 req/day; Groq backup; details in `research/07`). Days 1–3 build keyless on `mock`.

## 2. Data model (single SQLite file on VM1; results DB separate on dev machine)

Core tables: `products, orders, payments, proposals, approvals, audit_log(hash-chain), policy_evaluations, mandates(AP2), personas, sessions` — DDL sketches already in `research/04` §3–5 and `research/05` §5. Amounts: integer paise only.

Order state machine: `created → attempting → paid | failed→attempting(max 2 retries) | expired(sweep) | cancelled`. Idempotency key = `{order_id}:{attempt_no}`; UNIQUE(order_id, attempt_no) makes double-charge structurally impossible.

## 3. Day-by-day schedule

**Day 0 (today, evening): accounts & access — ₹0 total spend**
- [ ] Razorpay account → test-mode keys (`rzp_test_…`) — free
- [ ] Google AI Studio → free Gemini API key (no card) — needed by Day 4 at the latest; Days 1–3 run keyless on `LLM_PROVIDER=mock` (see `research/07`)
- [ ] Free DuckDNS subdomain pointing at VM1 IP; ports 80/443 open (if ports are blocked: polling-reconciliation path replaces webhooks for the demo)
- [ ] GitHub repo `bazaar`; local venv via uv
- [ ] From docs site, screenshot/save the full test-cards table incl. error-scenario cards (remote fetch failed for us)

**Day 1 — Skeleton + webhook proof.** Repo scaffold (app/, config via env), SQLite schema migration runner, catalog seed (15–20 SKUs w/ margins & stock), `/healthz`, systemd units, Caddy TLS on VM1. **Milestone: Razorpay webhook received + HMAC-verified on VM1 over public HTTPS.** This de-risks the single scariest unknown first.

**Day 2 — Money loop works.** POST /orders → Playwright simulated buyer completes Standard Checkout with test card → GET payment status → order transitions. Idempotency + state machine enforced. Deterministic failure triggers wired (Failure button, `failure@razorpay`, decline card).
**Milestone: scripted end-to-end purchase, and a declined purchase handled without retry-storms.**

**Day 3 — Governance layer.** Policy engine + YAML rules, proposals/approvals flow, hash-chained audit ledger, decision records, `verify_chain.py`.
**Milestone: a discount attempt clamps 25%→10% with a logged bound; chain verifies clean.**

**Day 4 — Agents.** Growth agent (best free-tier model available at build time — Gemini Flash class primary, Groq backup; bounded offers, reason attached) + persona buyer loop (≤8 turns, wallet math). Wire both through gates via `app/llm.py`. Control Tower gets chat view + proposal timeline.
**Milestone: one full conversation ends in a paid order with complete audit trail.**

**Day 5 — Protocols I: AP2 mandate engine.** Signed mandate objects (budget cap, allowed payees, expiry, revocation) using python `joserfc`/Ed25519; deterministic constraint check before EVERY Razorpay call; human gate above ₹500; receipt binds back by mandate hash. Graceful failure #1: expired mandate / budget-exceeded.
**Milestone: a payment attempt against a revoked/budget-blown mandate is blocked and audited.**

**Day 6 — Protocols II: ACP facade + x402 mock + MCP server + readable catalog.** `/checkout_sessions` CRUD mirroring published ACP shapes (minor-unit ints, totals identities, Idempotency-Key echo); x402 402-flow gating premium catalog data; MCP server (stateless Streamable HTTP, bearer auth) exposing the five tools; JSON-LD + robots allows + feed.json + llms.txt.
**Milestone: an MCP client lists products and buys end-to-end with zero custom code.**

**Day 7 — Measurement.** Run paired experiment on dev machine (150×2; scale 200×2 if smooth). analyze.py → metrics.json + charts. Control Tower metrics page + SSE live updates.
**Milestone: net-lift number with CI, reproducible from seed, artifacts committed.**

**Day 8 — Failure choreography + polish.** Rehearse: clamp demo, killed-network mid-payment recovery, duplicate-submit idempotency, sweep reconciliation. Reset scripts, pre-warm routine, fallback screen recording. Subscription recovery arc dry-run (tokens expire in 3 days — time it for demo week if we include it).
**Milestone: the 6-minute demo runs twice back-to-back without a hitch.**

**Day 9 — Dress rehearsal on the real VMs + buffer.** Full demo from cold boot. Pitch doc/deck finalized. Record backup video. Freeze code (only copy-fixes after).

## 4. Demo script (target ≈6 min)

1. **Hook (30 s):** Control Tower on VM3 shows empty ledger. An external MCP client (Claude with our `mcp_servers` connector) discovers the shop, asks for chai, buys — zero custom integration. Ledger fills live.
2. **Growth (60 s):** human-ish buyer chats; growth agent offers ONE bounded bundle; accepted; paid. Click transaction → decision record (options considered, reason, gate verdict).
3. **Governed overreach (60 s):** ask agent for 50% off → clamp to 10%, bound logged → run `verify_chain.py` live ✅.
4. **Graceful failure (90 s):** start payment, cut VM1 egress 20 s → timeout handled, order parked; connectivity restored; sweep reconciles; zero double charges; incident fully audited.
5. **Numbers (60 s):** paired A/B results — net lift ₹X/session, CI [A,B], take-rate, sensitivity row, API cost included as COGS.
6. **Close (30 s):** "Every rupee movement had a reason, a bound, and a receipt — and it speaks the three protocols your own pilots use."

## 5. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Webhook public endpoint blocked/slow to set up | Med | Day-1 milestone; fallback cloudflared (UNVERIFIED — test before relying); worst case: poll-based confirmation + document honestly |
| Chromium OOMs 1 GB VM2 | Med | Sequential spawn/kill, flags, MemoryMax, swap; ultimate fallback: demo payments driven from laptop joined to same flow |
| Scope creep (subscription recovery, campaigns) | High | Cut line drawn: subscriptions ONLY if Days 1–6 land early |
| LLM non-determinism derails live demo | Low | Demo conversations follow rehearsed paths; temperature modest; failure paths scripted |
| Test-card table gaps | Certain (fetch failed) | Saved manually Day 0 from docs |
| Solo-builder burnout / context switching | Med | One milestone per day, commit at each; PLAN is the single source of truth |

## 6. Explicitly cut (say no on stage too)

Real money, fraud ML, multi-tenant anything, Kubernetes, OAuth-on-MCP (stretch only), blockchain anchors, mobile apps.

---
*Status: plan locked 2026-08-22. Next action: Day 0 checklist.*
