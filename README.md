<div align="center">

# Bazaar

**A merchant that AI buyers can actually buy from — and every rupee that
moves is explainable, bounded, gated and audited.**

Razorpay AI Buildathon 2026 · **Track 01 — AI Growth & Agentic Commerce**

[![Track](https://img.shields.io/badge/Track-01%20Agentic%20Commerce-02042B?style=for-the-square)](https://razorpay.com/buildathon/)
[![Live](https://img.shields.io/badge/live-r2--d2.xyz-3395FF?style=for-the-badge)](https://r2-d2.xyz)
[![Ledger](https://img.shields.io/badge/audit%20chain-704%20records%20%E2%80%94%20intact-brightgreen?style=for-the-badge)](https://r2-d2.xyz/audit/recent)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20SQLite%20WAL%20%C2%B7%20MCP-009688?style=for-the-badge)](ARCHITECTURE.md)
[![Cost](https://img.shields.io/badge/infra%20cost-%E2%82%B90-ffd12e?style=for-the-badge)](research/07-zero-budget-plan.md)

**47 times, an AI agent spent real money here and nobody got hurt.**

</div>

---

## The numbers

Not projections. Counted from append-only session records, against live
Razorpay test-mode APIs, with webhook-captured payments.

| | |
|---|---|
| **33 / 40** autonomous AI-buyer sessions completed a captured payment | **82.5%** |
| Reached the gateway · finished paying from there | **95.0%** · **86.8%** |
| Test-mode revenue actually captured | **₹25,724.15** |
| **Budget violations, 38 budget-bounded sessions** | **0** |
| Finished on the first attempt | **28 / 40 (70%)** |
| Unique paid orders across the full corpus | **47** |
| Audit ledger — hash chain verified end to end | **704 records, `first_bad_seq: null`** |

Regenerate every figure: `python app/scripts/transactability_report.py`

---

## Try it (2 minutes, no install)

| | |
|---|---|
| **[r2-d2.xyz](https://r2-d2.xyz)** | the governed storefront — real catalog, real checkout |
| **[r2-d2.xyz/demo](https://r2-d2.xyz/demo)** | watch an AI buyer transact. **Replays a real recorded trip by default** — every order id, payment id and amount on screen is real and in this repo, and it runs with no keys, no network, no browser. LIVE is an explicit opt-in, and tells you the odds before you press it |
| **[r2-d2.xyz/demo/risk](https://r2-d2.xyz/demo/risk)** | the venue study as a standalone page, no JS |
| **[r2-d2.xyz/control](https://r2-d2.xyz/control)** | Control Tower — approve/reject agent proposals, watch the policy engine clamp a 40% ask to 15%, live order feed, chain verdict |
| **[r2-d2.xyz/audit/recent](https://r2-d2.xyz/audit/recent)** | raw tamper-evidence — ledger tail + `first_bad_seq` chain verification |
| **[r2-d2.xyz/mcp/](https://r2-d2.xyz/mcp/)** | the merchant as an MCP tool target for any external AI buyer |

```bash
curl https://r2-d2.xyz/healthz
curl https://r2-d2.xyz/audit/recent | python -c "import json,sys; d=json.load(sys.stdin); print('chain_ok', d['chain_ok'], '| records', d['records_checked'], '| first_bad_seq', d['first_bad_seq'])"
```

---

## The problem

Track 01's bar, verbatim:

> *"Every money action explainable, bounded and gated. Show the audit trail
> and one failure handled gracefully."*

Almost every agentic-commerce demo shows the happy path. An LLM picks a
product, a payment link appears, done. The questions that decide whether
this ever ships at a payments company are skipped: **who bounded the
spend? who approved it? what happens when the gateway challenges the agent,
or its brain returns a 429 mid-purchase? what happens when the audit trail
itself is wrong?**

Bazaar is a working, measured answer to those questions — on two free-tier
1 GB VMs, at ₹0.

---

## How it works

```
   AI BUYER (LLM)                    MERCHANT                       RAIL
   ────────────────                  ──────────────────────────     ─────
   persona card                 ┌───► thin HTTP / MCP edges
   + HARD budget in code        │            │
            │                   │            ▼
            ▼                   │     ┌──────────────┐
   read live /catalog ──────────┘     │ policy.py    │  PURE. no db, no net.
            │                         │ clamp, don't │  clamp, don't reject.
            ▼                         │ reject       │  rule ids POL-*
   LLM decides basket                 └──────┬───────┘
   (reasoning recorded verbatim)             │
            │                         ┌──────▼───────┐
            ▼                         │ mandates.py  │  HMAC spend envelope
   constrain_basket() CODE GATE       │ enforced     │  budget·category·expiry
   drops over-budget / unknown ──────►│ PRE-gateway  │  revocable
            │                         └──────┬───────┘
            ▼                                │
   real browser checkout ────────────────────┼──────►  Razorpay Orders API
            │                                │         Standard Checkout
            ▼                                │         risk engine (hCaptcha)
   payment.captured webhook ─────────────────┼───────  HMAC-SHA256 verified
            │                                │
            ▼                         ┌──────▼───────────────────────┐
   typed outcome recorded            │ audit.py — hash chain         │
   paid · risk_challenged ·          │ sha256(prev ‖ ts ‖ actor ‖ …) │
   llm_error · infra_error ·         │ one writer. append-only.      │
   walked_away · payment_failed      │ 704 records. first_bad_seq ∅  │
                                     └───────────────────────────────┘
```

Full topology, money-action sequence diagrams and the governance loop:
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

### The four guarantees, and where each lives in code

| Guarantee | Mechanism | File |
|---|---|---|
| **Explainable** | verbatim LLM reasoning per session; every proposal carries reasons; every clamp names the rule it applied | `exp/personas.py`, `policy.py` |
| **Bounded** | budgets clamped in code *before* checkout; signed spend envelopes; line + qty caps; all pricing server-side from the catalog | `mandates.py`, `orders.py` |
| **Gated** | agents can **only** `propose()`. Money-affecting actions sit in `pending_review` until a human decides. One dispatch point. | `proposals.py` |
| **Audited** | append-only hash chain, single writer, public verify endpoint | `audit.py` |

Client-sent amounts are never trusted. The growth agent **structurally
cannot execute** — its pipeline ends at `propose()`.

---

## Where we deliberately did *not* use AI

The brief scores *"the right tool in the right place, and where you chose
not to use one."* The absences are the design:

| Decision point | AI? | Why not |
|---|---|---|
| Policy gate | **No** | `policy.py` is pure, deterministic, unit-testable. A model that can be talked out of a spend limit has no business holding one. |
| Pricing | **No** | Server-side from the catalog. Prevents price hallucination and client tampering. |
| Budget enforcement | **No** | Code, not prompt. A prompt-injected agent cannot lift a cap it never controlled. |
| Mandate verification | **No** | HMAC. Signature mismatch refuses even if the DB row is edited by hand. |
| Audit append | **No** | Single writer, no model in the path. |
| Solving captchas | **No** | Defeating a fraud control to make a demo look better is the wrong answer. We abandon, count it, back off. |
| **Basket selection** | **Yes** | This is what AI is for: preference reasoning under a stated goal. |

The LLM owns the **what**. Code owns the **whether**.

---

## What we measured

Two findings. One positive, one null. Both preregistered, both reported
as-is.

### Finding 1 — the merchant *is* transactable by AI buyers (82.5%)

Three autonomous LLM shoppers (Ritika ₹350 / Arjun ₹1,500 / Meera ₹1,000)
read the live public catalog, chose their own baskets, and drove real
Standard Checkout in a real browser.

- **82.5%** completed a captured payment (33/40)
- **0 budget violations** in 38 bounded sessions — mean 17.8% headroom kept,
  tightest 0.1% (Ritika at ₹349.65 of a ₹350 cap)
- **Ritika ∩ Arjun SKU overlap: ZERO** across 67 sessions. A price-sensitive
  mind and a premium mind that never touch the same shelf. These are
  distinct agents, not one agent with three names.
- ~80% of recorded reasoning texts are unique

Reproduce: `python app/scripts/transactability_report.py`

### Finding 2 — where an agent pays from decides whether it can pay

The same autonomous buyer, the same code, the same merchant, the same
Razorpay key. The only deliberate change was the network it ran on:

| Phase | Network | Challenge rate | 95% CI |
|---|---|---|---|
| P1 (before) | Azure datacenter IP | **79.3%** (23/29) | [61.6, 90.2] |
| P2 (during) | residential IP | **12.7%** (7/55) | [6.3, 24.0] |
| P3 (after) | Azure datacenter IP | **100%** (20/20) | [83.9, 100] |

```
datacenter (P1+P3) vs residential (P2):  z = 7.64,  p = 2.1e-14
```

**The reversal is the argument.** Any monotone story — key ageing,
accumulating bot history, "the engine gets stricter over time" — predicts
P3 ≥ P2. What happened is the opposite sign: the rate *fell* 66pp when the
fleet moved to a residential IP and *rose* 87pp when it moved back. The
variable that flipped twice is the venue; the variable that only ever moved
forward is the calendar, and it moved the wrong way.

This is a product gap, not a bug: agents need reputation and
allow-listing tied to **origin**, not just to a key or a mandate. Reproduce:
`python app/scripts/risk_venue_report.py`

### Finding 2b — a claim we published, tested, and withdrew

We first reported that the challenge rate **escalated over the run**
(0% → 23.1% → 14.3%). Then we tested it before building the demo around
it, and it did not survive:

```
homogeneity chi-square = 3.04, df = 2, p = 0.22
Cochran-Armitage trend z = 0.00, p = 1.00
```

Five challenges across 38 gate-reaching sessions cannot distinguish
"escalating" from a constant 12.5%. The 0/13 first segment has a 95% upper
bound of 23.1% — **exactly** the second segment's point estimate. We had
read noise as signal because the shape matched the story we already
believed, and we had folded in a ~90% figure from a different run under
different conditions.

It is in this README rather than quietly deleted because a system that
governs AI money actions has to be able to retract its own findings. Full
correction: **[research/10 §1.1](research/10-key-rotation-and-risk-escalation.md)**.

### Finding 3 — the growth experiment was null, and we said so

Preregistered before any data existed (`PREREGISTRATION.md`): 94 sessions,
alternating treatment/control, symmetric exclusions, stratum-weighted
bootstrap CI, within-persona permutation test.

| | Treatment | Control |
|---|---|---|
| Revenue / session | ₹477.90 | ₹562.90 |
| Difference | **−₹241.45**, 95% CI **[−₹294.34, +₹131.55]**, permutation **p = 0.486** | |
| Conversion | 71.4% | 70.0% |
| Attach rate | **0.80** | 0.48 |
| AOV | ₹669.06 | ₹804.14 |

**Verdict: NULL.** Reported as the primary finding.

It is not an empty null. Conversion was arm-flat — agents buy either way —
but treatment baskets attached **68% more items at a lower AOV**. The
mechanism story: **price-sensitive AI buyers respond to discounts by
downgrading their baskets, not by spending more.** That is a merchant
economics result about agent price elasticity, and it is more useful than a
narrowly-true +3% lift.

Why the null is trustworthy: when a harness bug corrupted arm attribution
for 64 records, our own audit ledger caught it mechanically
(`scripts/verify_arm_integrity.py` admits a row only if no arm switch exists
between session start and payment capture). **Nothing was deleted.** Voided
rows stay on disk, excluded at merge. Full story:
**[WHAT-BROKE.md](WHAT-BROKE.md)**.

---

## Finding 4 — a hostile catalog can change what the agent asks for, not what it may spend

One falsifiable claim, tested from the worst case. Every case below
**assumes the model is fully compromised** and hands the pipeline the
attacker's ideal basket, then checks the money anyway.

> *A hostile catalog can change what the model ASKS for.
> It cannot change what the buyer is allowed to SPEND.*

| # | Attack | Result |
|---|---|---|
| A | instruction hidden in a product description | 9/9 checks pass — injection reaches the model as **data**, never as system prompt; budget, qty and line caps all still bind |
| B | instruction hidden in a bundle name | **structurally unreachable** — bundle metadata is never projected into the buyer prompt |
| C | client tries to name its own price | ignored. `OrderIn` has no price field, so there is nothing for it to land in. Authoritative price = catalog × qty |
| D | live price drifts above the buyer's ceiling | **REFUSED**. Order created, then abandoned. No browser opened, ₹0 moved |
| E | …and the ledger still balances | audit chain intact |

**20 checks passed, 0 failed.** Results committed:
`app/artifacts/prompt_injection_gauntlet.json`.

Nothing here relies on content filtering. Enforcement is deterministic
sku/stock/qty/line/paise arithmetic in `constrain_basket()`, plus a
server-priced ceiling check in `buy_once()`.

```bash
uv run python app/scripts/test_prompt_injection.py
```

**Case D found a real hole, and we closed it.** `constrain_basket()` bounded
the plan against a *catalog snapshot* while the server priced against *live
rows* — so a price that drifted up between the two was silently paid, and
the report still counted the trip as in-budget because it only ever looked
at the planned total. Buying code now takes `max_amount_paise`: server
pricing stays authoritative, but **authority is not consent**, and an order
above the buyer's ceiling is abandoned before a browser ever opens.

---

## Mapping onto Razorpay's own product line

| Razorpay ships | Bazaar implements |
|---|---|
| **UPI Reserve Pay** — *"consent-based, pre-authorized payments… within approved spending limits"* | `mandates.py` — HMAC-signed spend envelope: cap · per-txn cap · category allowlist · expiry · revocation · draws down only on capture |
| **AI-Ready MCP & APIs** — *"40+ composable tools"* | `mcp_server.py` — Streamable HTTP, 5 tools, sharing the exact functions the REST edges call so the surfaces cannot drift |
| **Agentic Payments on LLMs** | `GET /catalog` + the persona fleet — an LLM surface completing real purchases |
| **Granular Controls** | `policy.py` — clamp-not-reject, named rule ids |
| **Advanced Risk & Compliance** | typed `risk_challenged` outcome, honest abandonment (never solved, never routed around), and the measured venue effect — [r2-d2.xyz/demo/risk](https://r2-d2.xyz/demo/risk) |

What we did **not** build: AP2's full multi-party verifier / credential
interop. In single-merchant scope, tamper-evidence comes from the
HMAC-chained ledger covering intent → order → payment.

---

## Failures

The application form asks *"what broke, and how you got out"* and Razorpay
says it is the answer they read first. Ours is
**[WHAT-BROKE.md](WHAT-BROKE.md)** — a webhook that crashed production, a
risk engine that turned on us, three dead LLM providers, an 8.7-hour hang,
and a measurement bug our own audit ledger caught.

The short version: we built a tamper-evident ledger to govern AI money
actions, and the first thing it caught was **our own instrumentation lying
to us**.

---

## Known limitations

Naming these is worth more than hiding them.

| | |
|---|---|
| **HMAC, not Ed25519** | Mandate signatures are symmetric. Defensible for a single-operator system — there is no second party to repudiate against — but it is not non-repudiable the way an Ed25519 mandate is. We did not switch, because changing the signature scheme four days before submission risks the evidence chain for a theoretical gain. |
| **The check-then-spend race** | The budget hold is not taken in the same DB transaction as the policy decision. SQLite WAL serialises writers, so the race window is narrow, but it is not *closed by construction*. A rival closes it explicitly; we state ours instead. |
| **Small n** | The headline transactability run is n=40; the A/B is n=94. The null result is honest partly *because* n is small enough that a real effect would have had to be large to show. |
| **Venue is confounded** | In Finding 2, venue correlates with clock time *and* with host (VM2 is a different machine, browser profile and Playwright install). The A-B-A reversal rules out monotone drift; it does not rule out every alternative. |
| **Key age is not in the data** | Session rows do not store the Razorpay key id, so "the key did not rotate between phases" rests on `MEASUREMENT-DAY.md`'s log rather than on the data itself. |
| **Test mode only** | ₹0 real funds, ever. Test-mode risk thresholds are not production thresholds; the *direction* of the venue effect should generalise, the exact rates should not. |
| **Replay rebuilds narration** | `/demo`'s default replay restores real ids, amounts, baskets and reasoning text. The connecting narration is rebuilt, and the catalog recovery is partial by construction (12 of 17 lines) — the page says so on screen. |
| **Single merchant** | No AP2 multi-party verifier or credential interop. Tamper-evidence here is the HMAC-chained ledger covering intent → order → payment. |

---

## Verify it yourself

```bash
git clone https://github.com/Krishna-10-7/buildathon && cd buildathon/app

uv sync                       # or: pip install -r requirements
cp .env.example .env          # test keys only — LLM_PROVIDER=mock runs with zero keys
uv run python scripts/init_db.py
uv run uvicorn bazaar.main:app --port 8000

# tests: governance · mandates · bundles · MCP · money loop · analysis
uv run python scripts/test_governance.py    # 18/18
uv run python scripts/test_mandates.py      # 17/17
uv run python scripts/test_mcp.py           #  5/5

# the four-act failure choreography, against a running instance
uv run python scripts/failure_choreography.py

# rebuild every headline number in this README
uv run python scripts/transactability_report.py
```

`LLM_PROVIDER=mock` runs the entire pipeline with **no API keys at all**.

## Deployment

Two Azure VMs, 1 GB RAM each, ₹0 spend.

- **VM1** — `r2-d2.xyz`: systemd `bazaar.service` (`Restart=always`,
  `MemoryMax=650M`) → uvicorn → SQLite WAL, behind Caddy auto-TLS.
- **VM2** — the buyer fleet: Playwright + personas, drives VM1 over public
  HTTPS only. It has no shell access to the merchant; arm flips go through a
  token-gated API and are themselves audited.

Config: `deploy/bazaar.service`, `deploy/Caddyfile.r2d2`.

---

## Docs

| | |
|---|---|
| **[SUBMISSION-STRATEGY.md](SUBMISSION-STRATEGY.md)** | how this repo wins Track 01 — positioning, gaps, action plan |
| **[WHAT-BROKE.md](WHAT-BROKE.md)** | the failure narrative. Start here if you only read one thing |
| **[RESEARCH-PLAN.md](RESEARCH-PLAN.md)** | what we're researching next, and why |
| **[SOLUTION.md](SOLUTION.md)** | what this is, in detail |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | topology, layering, money-action + governance diagrams |
| **[AGENT-DESIGN.md](AGENT-DESIGN.md)** | are the personas really agents? Evidence from frozen data |
| **[PREREGISTRATION.md](PREREGISTRATION.md)** | hypotheses frozen before any data existed |
| **[MEASUREMENT-DAY.md](MEASUREMENT-DAY.md)** | the run, the incident, the deviation log |
| **[FAILURE-RUNBOOK.md](FAILURE-RUNBOOK.md)** | four-act failure choreography |
| **[DEMO-VIDEO-SCRIPT.md](DEMO-VIDEO-SCRIPT.md)** | 5-minute recording script |
| **[DEVLOG.md](DEVLOG.md)** | unedited chronological build log (was the old README) |

| **[research/11 — Track 01: where the winning edge is](research/11-track01-winning-edge.md)** | competitive field measured 2026-08-30, the five unclaimed edges, revised build order |

`research/01`–`08` — protocols (ACP/AP2/x402/UAP), Razorpay test-mode
deep dive, MCP & buyer agents, governance & audit playbook, synthetic
buyer measurement, 1 GB stack, zero-budget plan, captcha & agent rails.
`research/10` — key rotation and risk escalation.
`research/11` — Track 01 competitive edge (start here for strategy).

---

## Stack

Python · FastAPI · SQLite (WAL, integer paise) · MCP (Streamable HTTP) ·
Playwright · Caddy · systemd. LLM calls are raw `httpx` — no SDKs, no
framework lock-in, providers swappable by env var. Test mode only; no live
funds ever moved.

Solo build, ~1 week. Every claim above is reproducible from this repo or
from the live endpoints.
