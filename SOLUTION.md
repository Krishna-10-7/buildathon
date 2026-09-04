# SOLUTION.md — what we built, in detail

Track 01 problem, stated plainly: **AI agents are about to spend money on
the internet, and almost every demo of that future shows only the happy
path.** The hard questions — who bounds the agent's spending, who audits
what it did, what happens when the gateway challenges it or its brain
fails — get skipped. Razorpay's own roadmap (UPI Reserve Pay spend limits,
Agent Studio review-first modes, AP2-style mandate thinking) is a bet that
*governed* agentic commerce wins. This project is a working, measured
miniature of exactly that.

## The solution in one paragraph

We built a complete two-sided agentic-commerce economy on two free-tier
Azure VMs: a **governed merchant storefront** (real FastAPI service, real
Razorpay test-mode checkout, real risk engine) whose every money-affecting
action is bounded in code, gated behind human approval, and written to a
tamper-evident hash-chained ledger; three **autonomous LLM buyer agents**
(Ritika ₹350 / Arjun ₹1500 / Meera ₹1000) who read the live catalog,
decide their own baskets with recorded reasoning, and pay through the real
checkout browser; and a **preregistered randomized controlled experiment**
that flips the storefront between control and treatment arms to measure
whether executed AI growth actions actually move purchase behavior. We ran
it 94 times, unattended, overnight — and reported the result honestly,
including that it was null.

## Subsystem 1 — the governed merchant core

A single FastAPI service (`bazaar.main:app`) behind Caddy TLS, systemd-managed.
Its design rule: **thin edges, pure cores, swappable adapters** (see
[ARCHITECTURE.md](ARCHITECTURE.md) for diagrams and module map).

- **Pure policy engine** (`policy.py`): no database, no network, no settings —
  takes a fully-resolved context, returns a Decision. Philosophy:
  *clamp-not-reject* — an out-of-bounds request gets trimmed to what is
  allowed (with reasons), not thrown away. Unit-testable by construction.
- **Two-phase proposals** (`proposals.py`): agents can only `propose()`.
  Anything touching money or prices sits in `pending_review` until a human
  approves via `/proposals/{id}/decide`; execution happens at one dispatch
  point; every transition is audited. Low-risk actions may auto-execute.
- **Buyer mandates** (`mandates.py`): signed consent envelopes saying WHAT
  an agent may spend, on WHICH categories, UNTIL when — an AP2-flavored
  implementation enforced at order creation, *before* Razorpay ever sees
  anything. Spend draws down only when payment captures; failed attempts
  never consume budget. Revocable via API.
- **Tamper-evident audit** (`audit.py`): append-only records where each
  entry hashes the previous entry's hash (`sha256(prev ‖ ts ‖ actor ‖
  action ‖ payload)`). Verification replays the whole chain and names the
  first broken record. At last check: 650+ records, `first_bad_seq: null`.
- **Growth agent** (`agents/growth.py`): the merchant's daily strategist —
  reads a numeric snapshot, asks the LLM for a strict-JSON strategy, and
  can do exactly one thing with the answer: propose it. It structurally
  cannot execute anything.
- **Control Tower** (`/control`): a human governance console speaking to
  the same public JSON APIs — proposals awaiting decision, chain verdict,
  live orders.
- **MCP surface** (`mcp_server.py`): the merchant exposed as a tool-calling
  target for any external AI buyer, sharing the exact same functions as
  REST so the surfaces cannot drift.

## Subsystem 2 — the demand side: real LLM shoppers

Three personas (`exp/personas.py`) are independent AI shoppers. Each trip:
fetch the **live public catalog** → one LLM decision inside a persona card
and hard budget (reasoning recorded verbatim per session) → a **code gate**
(`constrain_basket`) that drops unknown/out-of-stock/over-budget picks into
auditable notes → checkout driven through a **real browser** against the
real test gateway and its real risk engine. Brains are provider-swappable;
the frozen run used OpenRouter (49 sessions), NVIDIA NIM (21), Gemini (5).
Full analysis with empirical differentiation data:
[AGENT-DESIGN.md](docs/planning/AGENT-DESIGN.md).

## Subsystem 3 — the measurement instrument

This is the part nobody else builds, and the reason our numbers mean
something:

- **Preregistration** (`PREREGISTRATION.md`): hypotheses and arm
  definitions written before the run.
- **Merchant-side A/B switch** (`experiment.py`): `control` = base prices,
  bundles deactivated; `treatment` = executed agent actions applied. Flipped
  via token-gated API (VM2 has no shell access to VM1), **every flip
  audited** — 78 flips in the main run.
- **Honest session discipline**: pause jitter between sessions, adaptive
  backoff after risk challenges (30–75s, like a human), one-attempt policy,
  typed outcomes for *every* ending: `paid`, `risk_challenged`,
  `llm_error`, `infra_error`, `walked_away`, `invalid_plan`,
  `merchant_unreachable`, `payment_failed`.
- **Fleet circuit breaker**: 3 consecutive LLM failures aborts the run,
  keeps written sessions, reports honestly — no retry spam against free-tier
  quotas.
- **Ethical rails**: captchas are never solved programmatically — a
  challenge ends the attempt immediately and is counted. Buyer identity is
  stable per persona (like a returning shopper) instead of fresh device +
  phone each order, which reads as card-testing to risk engines.

## What the measurement found

n=94 completed sessions: **41 paid · 30 risk-challenged · 18 llm_error ·
5 infra_error** — every one explained in `artifacts/sessions_final.jsonl`.

The headline verdict is **null**: the preregistered discount-lift effect
was not demonstrated. We report that as the *primary* finding, with the
tamper-proof trail that makes it trustworthy — because a fabricated lift
is worth nothing to a payments company, and an auditable null is worth a
lot. Secondary findings came free:

1. **Where an agent pays from decides whether it can pay**: the same
   buyer, code, merchant and key was challenged at **12.7%** (7/55) from a
   residential IP and **87.8%** (43/49) from a datacenter IP — the A-B-A
   reversal, 79.3% → 12.7% → 100%, z = 7.64, p = 2.1e-14. (79.3% and 100%
   are the two datacenter phases; 87.8% is them pooled. Quote the phases
   when you want the reversal, the pool when you want a single number —
   never "88%" as if it were a phase.) A real operational constraint on
   agentic traffic — measured, not assumed, and stronger than the
   "escalation over time" story we first told and then withdrew
   (`research/10 §1.1`).
2. **Provider resilience worked in production**: the LLM lane died mid-run
   more than once; failover providers carried the experiment and the
   breaker ended the un-survivable stretch cleanly.
3. **Graceful failure end-to-end**: quota outages, captcha walls, webhook
   races, even a driver-VM reboot all resolved into typed, auditable
   outcomes rather than crashes or silent losses.

## How this maps to the judging bar

| Bar | Mechanism in code |
|---|---|
| Every money action **explainable** | verbatim LLM reasoning per session; proposal lifecycle with reasons; clamp notes on every deviation |
| **Bounded** | budgets clamped in code pre-checkout; mandate envelopes (amount/category/time); line + qty caps; server-side pricing |
| **Gated** | two-phase proposals with human approval; token-gated arm switch; webhook signature verification before any state change |
| **Audit trail** | hash-chained append-only ledger, single writer, public verify endpoint, 650+ intact records |
| **Graceful failure** | typed outcome taxonomy, circuit breaker, honest abandonment of challenges, self-healing systemd service |

## Where to look next

- [ARCHITECTURE.md](ARCHITECTURE.md) — deployment topology, layering,
  money-action sequence diagrams, governance loop.
- [AGENT-DESIGN.md](docs/planning/AGENT-DESIGN.md) — the personas vs. the "is that a
  real agent?" bar, with frozen-data evidence.
- [PREREGISTRATION.md](PREREGISTRATION.md) · [MEASUREMENT-DAY.md](MEASUREMENT-DAY.md)
  · [FAILURE-RUNBOOK.md](docs/planning/FAILURE-RUNBOOK.md) — the experiment's paper trail.
