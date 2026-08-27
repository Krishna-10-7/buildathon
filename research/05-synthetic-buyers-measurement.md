# Synthetic Buyers & Revenue-Lift Measurement

*Written by the main agent (2026-08-22) after the delegated research stream failed twice on API timeouts. This is our measurement methodology spec — designed to survive a skeptical engineer's cross-examination. Figures marked ESTIMATE.*

---

## TL;DR — the locked experimental design

- **Design:** **Paired within-persona A/B**. The same 150 buyer-personas shop twice — Arm C (growth agent OFF) and Arm T (ON) — each a *fresh* session, same catalog, same seeds, byte-identical harness. Only variable: whether the bounded growth agent may act.
- **Primary metric:** **net revenue lift per session** = Δrevenue − discount leakage − **LLM API cost treated as COGS** (nobody else will subtract their own inference bill; judges remember it).
- **Stats:** bootstrap percentile CIs (10k resamples) on the paired mean difference; McNemar for paired conversion; per-segment breakdowns. No p-value theater.
- **n:** 150 personas × 2 arms = 300 sessions (scale to 200×2=400 if time allows). Honest about power: we are powered for *medium* effects and we say so.
- **Reproducibility:** fixed persona roster + product seed + temperature; preregistered config committed BEFORE the final run; raw logs shipped; one-command rerun.

---

## 1. Prior art & the honesty ledger

Relevant lineage: Stanford *Generative Agents* (Park et al., 2023) showed LLM populations produce coherent emergent behavior; subsequent work simulates shoppers/markets. Known, documented biases we must design around:

| Bias | Effect on us | Mitigation |
|---|---|---|
| **Sycophancy / over-compliance** | Buyer agents accept offers too readily → inflated lift | Personas carry hard budget constraints enforced by *wallet math in the harness* (acceptance impossible if unaffordable); report take-rate alongside; include a sensitivity row: "lift if only 50%/25% of accepts counted" |
| Position/acquiescence bias | Later-suggested items favored | Offer placement randomized per session; offer set capped at 1 (our policy engine already enforces ≤1) |
| Demand elasticity isn't real | Simulated buyers don't respond to price like humans | We never claim absolute demand prediction — only **comparative** effect under an identical simulated population |
| Non-determinism | Run-to-run noise | Fixed seeds where supported, temperature documented, paired design cancels population noise |
| Contamination | Persona "remembers" Arm C when running Arm T | Fresh session per arm; no shared conversation state |

**Validity stance we present:** this measures *how much a bounded sales agent moves purchase decisions in a controlled synthetic population* — a lower bound story, clearly labeled, with every assumption inspectable.

## 2. Why paired beats independent at small n

Independent arms waste variance: persona heterogeneity (budget, category affinity) dominates session-level noise at n≈150/arm. Within-persona pairing differences out persona quality entirely — each persona is its own control.

Worked sanity numbers (ESTIMATE):
- Assume control revenue/session ≈ ₹180 (SD ≈ ₹220); paired-difference SD ≈ ₹200 (correlated, so < independent SD).
- SE of paired mean diff = 200/√150 ≈ ₹16 ⇒ 95% CI half-width ≈ **±₹32/session**.
- If true uplift is ₹60/session → CI ≈ [₹28, ₹92]: clean win. If true uplift is ₹12/session → CI includes 0: we report "directionally positive, not distinguishable from zero at n=150" and look *more* credible for saying it.
- Conversion (paired binary, expect ~35–45% in-sim): McNemar; minimum detectable shift ≈ ±7–10 pp at n=150 pairs. ESTIMATE.

## 3. Metrics (exact definitions)

| Metric | Definition | Why judges care |
|---|---|---|
| Revenue/session | Σ paid order values ÷ sessions | Headline |
| Conversion rate | sessions ending `paid` ÷ sessions | Funnel health |
| AOV | paid revenue ÷ paid orders | Basket depth |
| Upsell take-rate | accepted offers ÷ offered offers | Agent skill, exposed to sycophancy critique |
| **Discount leakage** | Σ discounts granted ₹ | The cost side most teams hide |
| **Net lift/session** | Δ(revenue vs Arm C) − leakage − API-cost/session | **The metric we lead with** |
| Governance events | clamps, blocks, approvals per 100 sessions | Proof bounds bite in practice |

## 4. Cost control (ESTIMATE)

- Per session: ≤8 turns; persona model = claude-haiku-class (~$1/$5 per MTok); growth-agent turns = claude-sonnet-class (~$2/$10 per MTok).
- ~5–7K input + ~1K output tokens per session ⇒ 400 sessions ≈ 2.5–3M input + 0.4–0.5M output ⇒ roughly **$4–8 total**, plus debug replays ⇒ **budget ceiling $15**.
- Cache the frozen system+tools prefix; Batch API for replay/eval passes (−50%). Note gotcha from report 03: very short persona prompts fall below Haiku's minimum cacheable prefix (~4K tokens) — don't count on caching there.

## 5. Harness design (`experiments/`)

```
experiments/
├── personas.json        # 150 fixed personas: id, budget_inr, category_affinities, chattiness
├── catalog_seed.json    # fixed assortment w/ prices & stock
├── arm_config/{control.yaml,treatment.yaml}   # treatment adds ONLY growth_agent.enabled=true
├── runner.py            # asyncio, Semaphore(8), resumable (SQLite checkpoint per session)
├── analyze.py           # reads results.db -> metrics.json + charts.png/.html
└── PREREGISTERED.md     # frozen before final run: arms, n, seeds, metrics, exclusions rule
```

Anti-leakage rules: treatment logic lives ONLY inside the growth-agent module; the harness, prompts-to-buyer, catalog, and seeds are byte-identical across arms. Exclusion rule declared up front (e.g., "sessions crashing mid-payment excluded from both arms symmetrically") — never post-hoc.

Results schema (SQLite on VM2):

```sql
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, arm TEXT NOT NULL,
  started_at TEXT, ended_at TEXT, turns INTEGER,
  revenue_inr INTEGER DEFAULT 0, converted INTEGER DEFAULT 0,
  offers_made INTEGER DEFAULT 0, offers_accepted INTEGER DEFAULT 0,
  discount_given_inr INTEGER DEFAULT 0, api_cost_usd REAL DEFAULT 0,
  status TEXT NOT NULL);
CREATE UNIQUE INDEX ux_persona_arm ON sessions(persona_id, arm);  -- pairing integrity
CREATE TABLE session_events (session_id TEXT, turn INTEGER, role TEXT, content TEXT, ts_utc TEXT);
```

## 6. Judge-proof presentation checklist

- [ ] Lead with **net** lift (after discount + API cost), then gross.
- [ ] Show the distribution (histogram/violin of paired diffs), not a single average bar.
- [ ] Segment table: budget tiers × categories — lift consistency across segments beats one big number.
- [ ] Sensitivity row: "even if only 25% of simulated accepts reflect real behavior, net lift stays ≥ ₹X."
- [ ] Limitations slide (we list them first): synthetic elasticity, same-population comparison, sycophancy risk partially bounded by wallet math.
- [ ] Ship the artifacts: PREREGISTERED.md, raw session_events export, `make rerun` reproducing metrics from seed.
- [ ] Exact phrasing to use: *"Measured across 300 paired sessions; net lift ₹X per session, bootstrap 95% CI [A, B]; method and raw logs in the appendix."*

## 7. Top-3 attacks we should expect — prepared answers

1. **"Buyer LLMs just say yes to everything."** → Wallet math makes unaffordable accepts structurally impossible; take-rate reported next to lift; sensitivity rows at 50%/25% credit; policy engine caps total give-away so downside is bounded by construction.
2. **"Simulation ≠ reality."** → Correct, and we don't claim otherwise: the claim is *comparative* under identical conditions — the same standard used for any controlled experiment. Absolute forecasts would need live traffic; the architecture is exactly what you'd point at real traffic later.
3. **"Cherry-picked run."** → Config preregistered and committed before the final run; unique constraint guarantees one session per (persona, arm); raw event logs included; single command regenerates every number on screen.

## Sources & confidence

Methodology grounded in standard experimental practice (paired designs, bootstrap CIs, McNemar) and published LLM-simulation literature (e.g., Park et al. 2023 generative agents; sycophancy literature, Sharma et al.). Numeric examples are worked estimates (**ESTIMATE**) to be replaced with measured values after pilot runs. Model pricing cross-referenced from report 03.
