# PREREGISTRATION — Does governed agentic selling grow net revenue?

Written **before** any measurement session runs (2026-08-23). Numbers below
are frozen; deviations will be reported as deviations, not hidden.

## Claim under test

> The merchant's growth agent — whose every action is policy-clamped,
> human-approved and audited — grows **net revenue per buyer session**
> versus the store's base state, measured over synthetic-but-realistic
> AI-buyer sessions that pay through REAL Razorpay test-mode checkout.

This document exists so nobody (including us) can tune the experiment after
seeing results and present luck as skill.

## Arms

| Arm | Store state during session |
|---|---|
| **T (treatment)** | Approved agent actions ACTIVE: executed price discounts + active bundles exactly as recorded in `proposals` |
| **C (control)** | Base state: no discounts (`price_paise = base_price_paise`), no bundles |

Arm switches happen between sessions via `scripts/measurement_toggle.py`,
which applies/reverts state and writes an `experiment.arm_switch` audit
record. Sessions alternate T,C,T,C,… (time-blocked alternation) so
drift (captcha mood, Gemini load, time of day) hits both arms equally.

## Buyers

The three personas in `exp/personas.py` (Ritika ₹350 cap, Arjun ₹1500,
Meera ₹1000) — LLM-chosen baskets from the live public catalog, stable
per-persona identity, hard budget bounds enforced in code. Personas never
know which arm they're in (they only see the catalog feed, which naturally
reflects current prices).

## Sample size (fixed in advance)

- Target **90 valid sessions** (30 per persona), minimum **60** before any
  analysis is reported.
- "Valid session" = reached a basket decision (any outcome except
  `merchant_unreachable`). No stopping early because results look good;
  if we stop short of 90, we say so and why.

## Outcomes

- **Primary:** net revenue per valid session (sum of captured amounts ÷
  sessions in arm; INR). `walked_away` counts as ₹0 — walking away is a
  real commercial outcome.
- **Secondary:** conversion rate (paid ÷ valid), AOV among paid orders,
  multi-line attach rate.
- Counted but **not** part of the claim: discount-depth distribution the
  LLM chose per arm (mechanism evidence).

## Exclusions (symmetric, pre-declared)

Excluded from both arms identically: `infra_error`, `risk_challenged`
(Razorpay-side fraud challenges — external to the manipulation),
duplicate retries caused by driver bugs. Every exclusion is logged in the
session JSONL with its reason; the exclusion count per arm is reported
alongside results so asymmetric breakage cannot hide.

## Analysis

1. Difference in primary metric: T̄ − C̄.
2. Uncertainty: bootstrap 95% CI (10,000 resamples of sessions, stratified
   by persona) on the difference.
3. Robustness: permutation test (10,000 label shuffles, two-sided).
4. Sub-samples: per-persona effects reported with wide-interval honesty —
   they are exploratory, not confirmatory.

We call it a win only if the bootstrap interval excludes 0 **and** the
sign matches the mechanism story. A null or negative result gets reported
as-is; restraint ("agent proposed nothing worth doing") is a valid finding
by our own governance rules.

## Known threats (declared up front)

- Synthetic buyers share one LLM family across personas (free-tier
  constraint) — diversity comes from prompts + budgets, not model variety.
- Razorpay test-mode risk engine currently challenges many automated
  authorizations; if challenge rates differ materially by arm despite
  alternation, the run is voided and re-run after cooldown.
- Bundle pricing must be wired into `/orders` **before** the run, else
  bundles cannot affect behavior and the treatment reduces to discounts
  alone. Status tracked in README; the run waits for it.

## Artifacts

Every raw session lands in `artifacts/sessions.jsonl` (append-only,
one JSON object per line, includes arm tag, timestamps, notes, order ids).
Analysis notebook/script + outputs land in `artifacts/measurement/`.
