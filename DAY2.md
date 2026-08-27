# DAY 2 — Build Log & Self-Assessment

**Date:** 2026-08-23 · **Track:** 01 — AI Growth & Agentic Commerce · **Builder:** solo + Claude
**End-of-day state:** both sides of the marketplace exist and are bounded — an agent that can only propose, buyers with signed spending envelopes, a merchant readable by ANY external AI buyer over MCP, a judge-facing Control Tower, a rehearsed failure choreography, and a frozen experimental design. The one thing still missing is the *closed* buyer payment (Razorpay risk-engine cooldown in progress).

---

## 1. What was implemented today, and why

### 1.1 Growth agent v0 — `bazaar/agents/growth.py`
**What:** read-only sales/inventory snapshot → Gemini (smart lane) strict-JSON plan (≤3 actions) → `proposals.propose()` per action with a cycle correlation id.
**Why it matters:** this inverted the safety model — the agent is structurally incapable of spending. It reads state through a governed surface (margin data lives only there), writes only into the proposal queue, and even a hallucinating model cannot overspend: the policy engine clamps regardless of what the prompt promised. Agents cannot execute; there is no code path.

### 1.2 Buyer personas + reusable checkout driver — `exp/personas.py`, `exp/checkout.py`
**What:** three personas with hard budgets enforced IN CODE (Ritika ₹350 / Arjun ₹1500 / Meera ₹1000); Day-1's hard-won checkout automation extracted into one reusable driver (`buy_once()`); baskets chosen by Gemini fast lane from the live public catalog feed.
**Architecture boundary:** `exp/` is the experiment harness, NOT merchant code. Personas reach the core only via public HTTP + real browser checkout. Proven live: Ritika read the real catalog, reasoned inside her cap, ordered chai + chaat masala ₹340.65 — twice, from two machines.

### 1.3 The hCaptcha escalation (honest failure)
**What happened:** Razorpay's test-mode checkout began gating authorization behind interactive hCaptcha challenges (with Sardine fraud-signal frames). Tested 4 configurations across 2 machines and 2 IPs — all challenged ⇒ server-side change, likely test-key velocity flag after ~15 automated checkouts in 2 days.
**The stance:** we do NOT solve captchas programmatically — that's defeating a fraud control, which is exactly what this track judges you on. The driver detects the challenge, abandons cleanly (`risk_challenged` outcome), backs off with jitter, retries after cooldown. A scheduled single retry (not hammering) closes the loop tomorrow; fallback demo path = human proves humanity once while the agent drives everything else.
**Why this is demo material, not just an obstacle:** "one failure handled gracefully" is the judging bar — a real risk-engine challenge detected, classified, and abandoned without fraud is stronger evidence than any staged timeout.

### 1.4 Control Tower — `bazaar/control_page.py` + `control_api.py` → https://r2-d2.xyz/control
**What:** judge-facing governance console: proposal queue with Approve/Reject/Execute, a manual "propose an action" form (throw 40%×30d at the engine, watch POL-DISC-001 clamp it live), order feed, audit ledger viewer with a continuous sha256 chain-verification banner, health pills, catalog prices.
**Why:** Day-1 self-rating said judges shouldn't have to read curl output. Pure presentation edge — it speaks only to the same public JSON APIs agents use, so there is no second source of truth.

### 1.5 Buyer mandates — `mandates.py` + thin edge
**What:** AP2-flavored HMAC-signed consent envelopes (budget cap, per-txn cap, category allowlist, expiry, revocation) presented at order creation and enforced BEFORE Razorpay is called; refusal = 403 with named reasons + audited. Tamper-evident: signature mismatch refuses even if someone edits the DB row. Spend draws down only on capture, insert-guarded so `payment.captured` + `order.paid` can't double-count.
**Why:** this is the buyer-side twin of merchant-side clamps — BOTH sides of every transaction are now bounded by construction. Live proof: an envelope scoped ["chai"] correctly refused masala chai (category tea); a tea-scoped envelope allowed it.
**Self-caught design gap before it shipped:** the first version of `check()` skipped signature verification entirely. Fixed by asking the Day-1 question again: *what must never be trusted?* Answer: anything read back from storage without re-verification.

### 1.6 MCP server — `mcp_server.py` → https://r2-d2.xyz/mcp/
**What:** the merchant as a tool-calling target for ANY external AI buyer (official `mcp` SDK v2, Streamable HTTP, stateless JSON mode): search_catalog, get_product, create_order, get_order_status, shop_policies. Tools delegate to the SAME functions the REST edges call — MCP and REST cannot drift. DNS-rebinding guard stays ON with the deployment host allowlisted.
**Live proof:** external buyer created a real ₹259 order via tools/call against production.
**Protocol lessons earned (2026-07-28 spec reshape):** stateless mode has NO initialize handshake; every request carries the `_meta` protocol envelope plus `Mcp-Method`/`Mcp-Name` routing headers while body params keep `name`; mounted Starlette sub-apps don't run lifespans so the session manager runs inside the parent FastAPI lifespan; SDK's Host guard needed explicit allowed hosts.

### 1.7 Measurement rig — PREREGISTRATION.md + bundles in `/orders` + arm switch
**What:**
- **PREREGISTRATION.md frozen BEFORE any data exists**: control vs treatment, N=90 target/60 floor, primary metric = net revenue per valid session (`walked_away` = ₹0 — walking away is a real commercial outcome), symmetric exclusions (`risk_challenged`/`infra_error`), bootstrap 95% CI + permutation test, declared threats. Deviations must be reported as deviations.
- **Bundle pricing shipped** so treatment isn't discounts-only: exact-multiset basket match against active bundles, cheapest wins, NEVER above the sum of parts; savings recorded on order + audit + API response.
- **`experiment.py` + toggle CLI**: arm flips normalize through control then replay treatment FROM THE AUDIT LEDGER (`proposal.executed` records) — the experiment can only re-apply what policy clamped and humans approved. Shared bundle-id helper keeps executor/toggle/pricing derivations identical. Every flip audited as `experiment.arm_switch`.
- **Order expiry sweep**: abandoned checkouts release reserved stock lazily (same no-scheduler pattern as discount expiry) — otherwise 90 sessions of captcha abandonments would leak inventory and corrupt the measurement.

### 1.8 Failure choreography — `scripts/failure_choreography.py` + FAILURE-RUNBOOK.md
**What:** four narrated acts, rehearsed LIVE against production:
1. forged webhook → **400 invalid signature**, audited, state untouched;
2. mandate revoked mid-flight → next order **403 mandate_denied before the gateway call**;
3. agent asks 40%×30d → engine **denies on concurrency** (real rule hit live — the Day-1 discount is still running!) then retargets → **clamps to 15%×3d** → human rejects → price unmoved;
4. one byte flipped in a WAL-consistent snapshot → **chain breaks at exactly seq 20** while production verifies clean at 86 records.
**Lessons from rehearsing honestly:** the first tamper act silently no-op'd (payload marker absent) AND file-copying a WAL database snapshots a stale tail — fixed with the sqlite backup API + a guaranteed-visible edit. A demo that lies to itself would be worse than no demo.

---

## 2. Evidence inventory

| Claim | Evidence |
|---|---|
| Growth agent proposes within bounds | 3 pending_review proposals from production VM data (assam+kulhad bundle, elaichi −10%, masterclass −15%) |
| Persona reasons inside budget | Ritika chai+chaat ₹340.65 ≤ ₹350, orders created from laptop AND VM2 |
| Risk challenge handled | `risk_challenged` outcome + jittered backoff; cooldown retry scheduled, not hammered |
| Mandates enforce + refuse | 17/17 tests; live deny (["chai"] envelope vs tea sku) + live revoke-mid-flight refusal (403, audited seq 53) |
| MCP merchant surface | 5/5 wire checks; external ₹259 order via tools/call on production |
| Control Tower live | https://r2-d2.xyz/control — queue, clamp form, chain banner |
| Preregistration frozen | PREREGISTRATION.md written before any measurement session exists |
| Bundle pricing correct | 23/23 bundles+arms checks (exact match, superset ≠ deal, never-more-than-parts, ledger-only replay, idempotent toggles) |
| Failure choreography | all four acts performed live against prod; ledger 86 records, chain intact |
| Full regression | bundles+arms 23 · governance 18 · mandates 17 · mcp 5 · moneyloop green |

---

## 3. Honest self-rating against the judging bar

| Dimension | D1 | D2 | Reasoning |
|---|---|---|---|
| Money actions explainable | 9 | **9.5** | Both sides now carry envelopes/rule-ids; Control Tower makes it visible in seconds. Remaining gap: nothing material. |
| Bounded | 9 | **9.5** | Buyer mandates + merchant clamps + stock reservation + order expiry. The store cannot oversell or overspend in either direction. |
| Gated | 9 | **9.5** | Revocation proven live mid-flight; execute-before-approve 409; agents structurally unable to spend. |
| Audit trail | 9.5 | **9.5** | Chain survived its first deliberate tamper attempt with the break pinned to the exact record. |
| Failure handled gracefully | 8.5 | **9.5** | Rehearsed four-act choreography on production + the real hCaptcha escalation classified and abandoned without fraud. |
| Agentic commerce substance | 5 | **8.5** | Agent exists and proposes governed actions; external AI buyers can transact via MCP + checkout. Missing: one CLOSED persona payment end-to-end (cooldown pending). |
| Measurement/impact | 2 | **6** | Design frozen + rig complete (bundles, arms, expiry, fleet synced). Zero sessions run — by design, preregistration precedes data. |
| Demo-ability | 6 | **8.5** | Control Tower + failure runbook + MCP wire proofs. Video still to record. |

### Overall Day 2: **8.5 / 10** against what a winning entry needs.

**Why above the field:** most entries will have a chatbot that calls a payment link. We have TWO bounded counterparties (governed seller agent, mandated buyer agents) meeting over a protocol surface (MCP + REST + real checkout), with every transition in a tamper-evident ledger and a rehearsed failure story. The governance story is now *complete* rather than promised.

**What keeps it from 9.5+ — the Day 3 list, in order:**
1. **Close ONE persona payment post-cooldown** (scheduled single retry; fallback = human solves captcha once, agent drives).
2. **Run the preregistered measurement** (alternating arms on VM2 fleet) + analysis script with bootstrap CIs.
3. **Demo video** following FAILURE-RUNBOOK.md, recorded after the payment close.
4. Key rotation before anything public ships.

---

## 4. Before-submission checklist
- [ ] **Rotate ALL keys** (Razorpay test keys + webhook secret + Gemini key were pasted in chat during setup) before any public repo/release.
- [x] Preregistration written before measurement (PREREGISTRATION.md, 2026-08-23).
- [ ] Close persona E2E payment (cooldown retry scheduled 18:23 today).
- [ ] Run measurement per preregistration; publish analysis + artifacts.
- [ ] Record demo video after failure rehearsal (runbook ready).
- [ ] DAY3 log.

**Bottom line:** Day 1 built the bank and the regulator. Day 2 built both traders — a seller agent that can only propose and buyer agents that can only spend within signed envelopes — and made them meet over open protocols. Day 3 has one job: prove the last inch of the payment rail, then measure what the whole machine earns.
