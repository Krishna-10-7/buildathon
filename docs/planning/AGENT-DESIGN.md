# AGENT-DESIGN.md — Are the buyer personas real agents?

The demand side of this experiment is three LLM-driven shoppers (Ritika,
Arjun, Meera). This document is the standing answer to the two hardest
questions a judge can ask them: *"is that even an agent?"* and *"couldn't
anyone build that?"* Every number below is computed from the frozen
measurement dataset (`sessions_final.jsonl`, n=94).

---

## 1. What a persona actually is

```
                    ┌─────────────────────────────────────────────┐
   goal, not script │  Persona card: identity, taste blurb,       │
   ────────────────►│  HARD budget (Rs 350 / 1000 / 1500)         │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────────┐
   PERCEIVE         │  GET /catalog  (public feed, live prices,   │
                    │  stock flags) → compacted into the prompt   │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────────┐
   DECIDE  (LLM)    │  One constrained decision: pick ≤2 skus &   │
                    │  qtys, or walk away. Stated reasoning is    │
                    │  recorded verbatim per session.             │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────────┐
   GATE  (code)     │  constrain_basket(): sku exists, in stock,  │
                    │  line cap, qty clamp, budget drop-line —    │
                    │  every deviation becomes an audit note.     │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────────┐
   ACT              │  Real browser checkout on the real gateway: │
                    │  contact, method, bank, captcha-or-pass,    │
                    │  webhook capture. One attempt policy.       │
                    └──────────────────┬──────────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────────┐
   ADAPT            │  Challenge-aware backoff (a challenged      │
                    │  shopper waits 30–75s like a human; a mere  │
                    │  failure 5–12s); fleet-level circuit breaker│
                    │  aborts on quota exhaustion; every terminal  │
                    │  outcome typed and recorded symmetrically.  │
                    └─────────────────────────────────────────────┘
```

Key code: `exp/personas.py` (`plan_basket`, `constrain_basket`,
`run_session`), shared brain adapter `bazaar/llm.py` (provider-swappable),
checkout driver `exp/checkout.py`.

## 2. The five-question agency test

(Composite of published agentic-system criteria: accepts goals not prompts;
plans; acts in external systems; handles exceptions adaptively; runs
end-to-end unattended.)

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Goal, not prompt | **Yes** | A taste blurb + hard budget; no item is scripted. Item choice is entirely the model's. |
| 2 | Planning with reasoning | **Yes** | Every session records a verbatim LLM analysis; ~80% are unique texts (below). |
| 3 | Acts in external systems | **Yes** | Real browser → real Razorpay test gateway → real risk engine. Not simulated clicks. |
| 4 | Adaptive exception handling | **Partly** | Typed outcomes, challenge-aware backoff, circuit breaker. No mid-checkout basket replanning (see §5). |
| 5 | End-to-end unattended | **Yes** | 94 consecutive overnight sessions across two VMs, zero human steering — that *is* the measurement. |

Score: 4½ / 5 — by current industry rubrics this places the personas at the
**supervised-to-bounded-autonomy level (L2–L3)**, which is where serious
production agents deliberately live. Unbounded L4 autonomy in a payments
context is called out by those same rubrics as reckless, not advanced.
Boundedness here is not a missing feature; it is the product.

## 3. Empirical proof the minds are distinct

"Same response every time" is checkable against the frozen data:

| | Ritika (₹350 cap) | Arjun (₹1500 cap) | Meera (₹1000 cap) |
|---|---|---|---|
| sessions | 37 | 30 | 27 |
| paid | 14 | 13 | 14 |
| mean spend | ₹312 | ₹1151 | ₹609 |
| median spend | ₹341 | ₹1499 | ₹561 |
| spend range | ₹249–348 | ₹399–1499 | ₹510–999 |
| top picks | chaat-masala ×26, masala-chai ×17 | premium-hamper ×13, assam-gold ×9, masterclass ×4 | masala-chai ×20, kulhad-set ×12, cookies ×8 |
| unique analyses | 27 / 37 | 23 / 30 | 24 / 27 |

- **Ritika ∩ Arjun SKU overlap: zero**, across 67 combined sessions. A
  price-sensitive mind and a premium mind that never touch the same shelf.
- Ritika's spend range tops out at ₹348 against a ₹350 budget over 37
  sessions — the budget gate held every single time, while her *choices*
  varied within it.
- Meera consistently buys pairs (chai + cookies, chai + kulhad set),
  matching her pairing heuristic — behavior tracks the persona spec, not
  a random number generator.
- The identical-looking responses observed during one live window were the
  LLM quota outage: 18 sessions died as typed `llm_error` events and were
  reported as failures, not dressed up as successes.

Sample verbatim reasoning (from session records):

> *"The signature masala chai is well-priced, and adding the budget-friendly
> chaat masala keeps the total comfortably under my Rs 350 limit."* — Ritika

> *"The Premium Chai Hamper perfectly fits my taste for high-end, curated
> single-estate teas and luxury additions."* — Arjun

## 4. Multi-provider brain — survived three providers

Across the frozen run the personas' plans came through **openrouter (49
sessions), nvidia NIM (21), gemini (5)**. The provider died mid-measurement
more than once; the fleet kept going on failover providers and the breaker
stopped it honestly when all lanes were exhausted. Provider-independence was
designed in (`LLM_PROVIDER` env swap), not retrofitted.

## 5. What this is NOT (deliberate scope)

1. **Not a free-roaming ReAct loop.** The LLM owns the *what*, code owns the
   *whether*. One decision point per trip, gated by `constrain_basket`
   before any money moves. This mirrors how payment-grade agent frameworks
   bound autonomy (spend caps enforced outside the model).
2. **No mid-checkout replanning.** After a failed attempt the persona
   re-drives the same basket with adaptive backoff rather than re-asking the
   LLM. In the frozen data most failures were risk challenges — behavioral,
   not basket-related — so replanning would mostly add LLM calls, not wins.
   Acknowledged gap, chosen deliberately.
3. **Mandates without AP2's cross-party credential chain.** We DO enforce
   signed buyer-consent envelopes (`mandates.py`): WHAT an agent may spend,
   on WHICH categories, UNTIL when — checked at order creation before
   Razorpay is involved, with spend drawing down only on capture and
   revocation via API. What we did not build is AP2's full multi-party
   verifier/credential interop; in single-merchant test scope, tamper
   -evidence comes from the HMAC-chained audit ledger (650+ records,
   `first_bad_seq: null`) covering intent → order → payment.
4. **No cross-session learning.** Personas keep stable identity (phone +
   browser profile, like returning shoppers) but do not learn across
   sessions — deliberate, to keep the RCT arms clean.

## 6. Alignment with the sponsor's own direction

Razorpay's public agentic-commerce push is built on exactly these shapes:
pre-approved per-merchant spending limits (UPI Reserve Pay), review-first
modes with mandatory approval for irreversible actions, platform validation
of amount/compliance/scope, full audit trails, and per-agent pricing. This
project is a working miniature of that governance model: bounded spend,
gated actions, verifiable history — measured over 94 real checkout trips
with every failure recorded.
