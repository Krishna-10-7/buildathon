# DAY 1 — Build Log & Self-Assessment

**Date:** 2026-08-22 · **Track:** 01 — AI Growth & Agentic Commerce · **Builder:** solo + Claude
**End-of-day state:** a real Razorpay payment loop and a live governance layer running on production TLS, verified with real webhooks.

---

## 1. What was implemented today, and why

### 1.1 Foundation research (6 reports + zero-budget plan) — `research/01–07`
**What:** Agentic commerce standards (ACP, AP2, x402, NPCI UAP), Razorpay test-mode mechanics, checkout automation constraints, free-tier LLM landscape, measurement methodology, deployment architecture.
**Why:** Track 01 is new and fast-moving — building on assumptions instead of the actual 2026 protocol landscape is how entries get disqualified on accuracy. The research directly produced three design decisions: UPI-Reserve-Pay-style **mandates** as our consent model, **webhooks as the only trustworthy settlement signal**, and a **hybrid laptop/VM split** because Chromium cannot fit in 1GB.

### 1.2 Merchant core — `bazaar/` (FastAPI + SQLite WAL)
**What:** 17-SKU catalog; `POST /orders` with server-side pricing and atomic stock reservation; Razorpay order creation; `GET /orders/{id}`.
**Why each choice:**
- **Server-side pricing only** — client-sent amounts are never trusted. This is the first thing a payments judge checks.
- **`UPDATE ... WHERE stock >= qty`** — one statement closes the two-buyers-one-unit race without locks or transactions gymnastics.
- **SQLite WAL over Postgres** — single-writer fits our traffic; zero-op on 1GB RAM; one less service to keep alive. Deliberate restraint, not ignorance.
- **No LangChain/no agent frameworks** — raw tool loops. Frameworks hide the exact control flow this track judges you on.
- **Integer paise everywhere** — float money is a disqualifying bug.

### 1.3 Webhook receiver — `bazaar/webhooks.py`
**What:** HMAC-SHA256 signature verification **before anything else**; dedupe on event id; idempotent payment application; forged signatures get 400 *and* an audit row.
**Why:** The order of operations is the security model. An early bug (dedupe ran before signature check → forged webhook answered 200 "duplicate") was caught and fixed by asking one question: *what must never touch state?* Answer: unauthenticated input. The fix made authentication the first line, and forged input now leaves evidence (audit) rather than silence.

### 1.4 Hash-chained audit ledger — `bazaar/audit.py`
**What:** Every state transition appends `sha256(prev_hash|ts|actor|action|payload)`; `verify()` walks the chain.
**Why:** The judging bar says *"every money action explainable."* A tamper-evident ledger turns "trust our logs" into "break our math." It also gave us free debugging superpowers on Day 1 — every incident is reconstructable.

### 1.5 Deployment — Caddy TLS + systemd on the existing Azure VM
**What:** `https://r2-d2.xyz` serving the API; `MemoryMax=650M`; auto-restart; **schema self-heals at startup** (`ensure_schema` + explicit `migrate()`).
**Why:** Reusing the user's existing domain gave free valid TLS for webhooks (ngrok is blacklisted by Razorpay). The self-healing startup exists because Day 1 *proved* the failure mode: a real webhook crashed on a table that postdated the deployed DB. Now schema drift between deploys is structurally impossible.

### 1.6 Simulated buyer — `scripts/simulated_buyer.py` (Playwright, headless)
**What:** A headless Chromium "agent buyer" that completes **real Razorpay Standard Checkout**: contact screen → Netbanking → Canara Bank mock → settlement.
**Why:** Razorpay test mode has no pure-API payment completion — the browser *is* the payment rail. Discoveries earned the hard way:
- Famous fake phone numbers (9999999999) fail 2026 risk validation; **random realistic Indian numbers pass**.
- The new checkout shows a save-card interstitial (`Maybe later`) that must be dismissed.
- **Test-mode checkout enforces domestic-only cards** — the classic 4111 Visa is rejected as "international." Netbanking is the correct instrument anyway: it's the Indian rail, on-theme for this track.
- Netbanking auto-completes (no Success button) — the agent must read checkout.js callbacks, not hunt for buttons.

### 1.7 The money loop, closed with real events
**What happened:** buyer paid → **real webhook from Razorpay's IP** → signature verified → order flipped `created → paid`.
**Two bonus proofs from live traffic:**
- **Webhook auto-retry + idempotency:** the captured event was delivered *late* (after a deploy restart) and processed exactly once. Order self-healed.
- **Graceful failure:** late-delivered `payment.failed` events (card declines) processed cleanly — error codes stored, orders marked failed, stock already rolled back.

### 1.8 LLM adapter — `bazaar/llm.py`
**What:** One `complete()` over `mock | gemini | groq`; raw httpx (no SDK); 429/503 retry with backoff; two lanes (fast alias / smart pinned).
**Why:** Agents arrive tomorrow; the provider boundary had to exist first. Free-tier reality discovered live: `gemini-2.0-flash` is retired, Pro is quota-walled for new keys, `3.7-flash` was 503 under launch demand → **stable aliases (`gemini-flash-latest`) as default** so Google's deprecation treadmill can never break a demo. Mock lane keeps all agent development keyless and CI-able.

### 1.9 Governance core — `policy.py`, `proposals.py`, `governance_api.py`
**What:**
- **Pure policy engine** (no I/O): clamp-not-reject. Rules: ≤15% discount, ≤3-day window, price floor at cost+5%, no concurrent discounts, bundles must be profitable deals, unknown actions denied. Rule IDs (`POL-DISC-001`…) so every decision cites its law.
- **Proposal lifecycle:** agents can only *propose*. Deny = terminal; low-risk = auto-execute; price-affecting = human gate. The executor lives inside the lifecycle module — **there is structurally no code path from an agent to the catalog that bypasses policy + approval.**
- **Thin HTTP edge:** parse → delegate → map errors. Zero rules in the router.
- **Schema v2:** `bundles` table, discount columns with base-price preservation, explicit `migrate()`, lazy expiry reversion (no scheduler process to babysit).

**Live proof sequence (all on production today):**
agent asked **40% × 30 days** → clamped to **15% × 3 days** citing `POL-DISC-001/003` → execute-before-approval **409** → merchant approved → price applied ₹249 → ₹211.65 → hash chain intact at 28 records.

---

## 2. Evidence inventory

| Claim | Evidence |
|---|---|
| Money loop works end-to-end | `ord_b9807188ff1845` = `paid`, real `payment.captured` from 52.66.75.174 |
| Webhook retry/idempotency | late redelivery processed once (audit seq 20) |
| Failure handling | 4 × real `payment.failed` processed with error codes (audit seq 22–25) |
| Governance clamps | `prp_d223df5473068345`: 40→15%, 30→3d, live on r2-d2.xyz |
| Approval gate | execute-before-approve = 409; approved path applies price |
| Ledger integrity | `verify()` ok=True across 28 mixed local+real records |
| Test suites | `test_governance.py` 18/18 (isolated temp DB); `test_moneyloop.py` green |
| Keyless dev path | mock LLM lane; full governance suite needs no keys |

---

## 3. Honest self-rating against the judging bar

Bar (from track description): *every money action explainable/bounded/gated · show audit trail · one failure handled gracefully.*

| Dimension | Score | Reasoning |
|---|---|---|
| Money actions explainable | **9/10** | Every transition cites actor + correlation id + rule IDs in a tamper-evident chain. Missing: the Control Tower UI that makes this *visible* to judges in 10 seconds. |
| Bounded | **9/10** | Clamp-not-reject with numeric bounds and cost floor is stronger than the ask (most entries will hard-reject). Missing: mandate/budget caps wired into policy (schema exists, enforcement is Day 3). |
| Gated | **9/10** | Structural gating (executor inside lifecycle) beats conventional gating (agents "promised" not to spend). Missing: mandate revocation flow. |
| Audit trail | **9.5/10** | Hash-chained, verified across real+local events. Best-in-class for this track, likely. |
| Failure handled gracefully | **8.5/10** | Real webhook retry, real declines, gateway 502 rollback all proven with production events. Missing: deliberate, *narrated* failure choreography for the demo. |
| Agentic commerce substance | **5/10** | The rails and rules are done; **the agent itself doesn't exist yet.** This is the biggest gap and it's next. |
| Measurement/impact | **2/10** | Methodology is researched and preregistration is drafted; zero experiments run. |
| Demo-ability | **6/10** | Everything is real and live, but there is no visual surface yet — judges shouldn't have to read curl output. |

### Overall Day 1: **7.5 / 10** against *what a winning entry needs*, and ahead of any plausible competitor's Day 1.

**Why above the field:** most entries will spend Day 1 on a UI mock and a chatbot wrapper. We closed a **real payment loop with real webhooks** and shipped **enforceable governance** — the two things this track explicitly scores — before writing a single line of agent code. The agent is now the easy part: it can only *propose*, so even a hallucinating model cannot overspend. That inversion (safety first, intelligence second) is the story.

**What keeps it from 9.5+ — the Day 2–4 list, in order:**
1. **Growth agent** (Gemini, smart lane) reading real sales state → one governed proposal per insight.
2. **Buyer personas** (fast lane) driving the proven checkout flow → transactions the agent must react to.
3. **Control Tower dashboard** — proposals queue, approve/reject, ledger viewer, live order feed. Judges *see* the governance.
4. **Mandate enforcement** (budget caps, categories, expiry) wired into `policy.evaluate`.
5. **Failure choreography** — scripted, narrated: kill webhook receiver → Razorpay retries → self-heal; revoke a mandate mid-flight → in-flight order completes, next one blocked.
6. **Measurement** — paired A/B with bootstrap CIs, net lift after discount cost. The number that makes the pitch memorable.

---

## 4. Before-submission checklist
- [ ] **Rotate all keys** (Razorpay test keys + webhook secret + Gemini key were pasted in chat during setup) before any public repo/release.
- [ ] `.env` stays gitignored; verify with a fresh clone.
- [ ] Record the demo video *after* failure choreography is rehearsed, not before.
- [ ] Preregister the experiment before running it (`PREREGISTERED.md`).

**Bottom line:** Day 1 built the bank and the regulator. Days 2–4 build the trader — inside limits the regulator enforces. That order is why this can win.
