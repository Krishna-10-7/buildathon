# DEVLOG — chronological build log

Moved out of `README.md` on 2026-08-29 so the repo's front page is a pitch
instead of a diary. Kept verbatim: this is the honest, timestamped record
of what was built, what broke, and what it cost. Nothing below has been
retroactively cleaned up.

> **Correction 2026-09-01.** Two entries below (2026-08-25) describe an
> "escalation finding" — that the challenge rate rises with sustained
> agent volume. **That claim was tested and withdrawn** on 2026-09-01:
> chi-square p ≈ 0.22, trend p ≈ 1.00, five challenges across 38
> gate-reaching sessions. The result that replaced it is **venue**, not
> volume — 79.3% → 12.7% → 100% as the fleet moved datacenter →
> residential → datacenter (p ≈ 2e-14). See `research/10 §1.1` and
> `WHAT-BROKE.md §2b`. The log below is left unedited on purpose; this
> banner is the correction.
>
> **Correction 2026-08-30.** The A/B point estimate below is quoted as
> −₹241.45. `bootstrap_ci()` returned it as a bootstrap *resample* rather
> than the preregistered plug-in statistic (stratified T̄ − C̄), so the
> published difference was one random draw instead of the estimand. The
> correct figure is **−₹83.03**; the CI, the p-value and the NULL verdict
> were never affected. Fixed and regression-tested in
> `app/scripts/test_analysis.py`. See `WHAT-BROKE.md §2c`.

For the story, start at [README.md](../../README.md). For the failures, start at
[WHAT-BROKE.md](../../WHAT-BROKE.md).

---

## Status log

- **2026-08-22** — Track locked: 01. Six research streams launched in parallel.
- **2026-08-22** — All 6 research reports complete (`research/01–06`, `07-zero-budget`).
- **2026-08-22** — `PLAN.md` locked. Razorpay test keys verified live (order created).
- **2026-08-22** — App scaffolded (FastAPI + SQLite/WAL + 17-SKU catalog) and deployed to
  Azure VM (`ssh myserver`) behind existing Caddy TLS at **https://r2-d2.xyz/healthz** ✅
- Old projects (`daal`, `pbl-fastapi`) STOPPED+DISABLED for buildathon week.
  Restore after: `sudo systemctl enable --now pbl-fastapi daal` +
  `sudo cp /etc/caddy/Caddyfile.backup-20260822 /etc/caddy/Caddyfile && sudo systemctl reload caddy`
- Pending: webhook secret from Razorpay dashboard → then HMAC receiver goes live.
- **2026-08-22 — MONEY LOOP CLOSED (real end-to-end)** ✅
  Headless-Chromium buyer (`scripts/simulated_buyer.py`) completed REAL Razorpay
  Standard Checkout on https://r2-d2.xyz: contact screen (random Indian mobile passes
  risk checks; famous fakes like 9999999999 rejected) → Netbanking → Canara Bank mock
  auto-completes. Razorpay's servers delivered `payment.captured` to
  `/webhooks/razorpay` → signature verified → order flipped `created→paid`.
  Bonus proofs with real events: webhook **auto-retry** after a deploy window was
  processed idempotently; late-delivered `payment.failed` (international-card
  declines) handled; audit hash-chain intact over 25 mixed records.
  Fixes landed: schema self-heals at startup (`main.ensure_schema`) — the captured
  webhook had crashed on missing `webhook_events` table from an older db.
  Known flake: checkout occasionally hangs at "Processing your payment"
  (hCaptcha verify state) — runner needs retry loop before measurement day.
  Card path blocked in test mode: checkout enforces domestic-only cards
  ("International cards are not supported") → Netbanking is our instrument.
- **2026-08-22** — LLM adapter live (`bazaar/llm.py`): one `complete()` over
  mock | gemini | groq, raw httpx (no SDK), free-tier 429/5xx retry. User's
  Gemini key wired into `.env` (laptop + VM). Free tier on this key is
  Flash-family only (2.5-pro retired for new users; 3.1-pro quota-walled);
  fast lane = `gemini-flash-latest` alias, smart lane = `gemini-3.6-flash`
  pinned (3.7 was 503 high-demand on the day). Strict-JSON mode verified.
  Server parity synced; provider still `mock` until agents land.
- **2026-08-22 — GOVERNANCE CORE LIVE** ✅ (verified on https://r2-d2.xyz)
  `policy.py` pure clamp-not-reject engine (max 15% off, 3-day window,
  price floor at cost+5%, no concurrent discounts, bundles must be profitable
  deals — rule ids POL-*); `proposals.py` sole writer of proposal lifecycle +
  executor (deny = terminal, low-risk = auto-executed, med-risk = human gate);
  `governance_api.py` thin HTTP edge. Schema v2: bundles table, discount
  columns with explicit `migrate()`; lazy expiry reversion in order pricing.
  Tests: 18/18 governance (isolated temp DB) + moneyloop regression green.
  Live proof: agent asked 40%×30d → clamped 15%×3d → execute-before-approve
  =409 → approved → price applied ₹249→₹211.65; chain intact at 28 records.
  Hardware: spare 4GB VM reserved for measurement-day buyer fleet + standby.
- **2026-08-23 — GROWTH AGENT v0 LIVE (Day 2)** ✅ first real agentic cycle
  `bazaar/agents/growth.py`: read-only sales/inventory snapshot → Gemini smart
  lane strict-JSON plan (≤3 actions, bounds stated in prompt, enforced by
  engine regardless) → `proposals.propose()` per action with cycle correlation
  id. Agents structurally cannot execute — only queue. Thin `/agent/*` edge +
  CLI runner (`scripts/run_growth_agent.py`). Lesson: Gemini 3.x thinking
  tokens share maxOutputTokens → 900 was truncating plans; raised to 3000.
  Local cycle: chai+kulhad bundle ₹529 / masterclass −15% (96% margin) /
  assam −10%, zero clamps. Production cycle via API on VM data: assam+kulhad
  bundle ₹649, elaichi −10%, masterclass −15% — 3 pending_review in queue,
  ledger intact at 31 records. Provider now gemini on VM.
- **2026-08-23 — BUYER SIDE BUILT: personas + reusable checkout driver** 
  Day-1 probe extracted into `exp/checkout.py` (`buy_once()` — one function,
  multi-item baskets, structured result; failed payments are valid results,
  only infra breakage fails a run); `scripts/simulated_buyer.py` is now a thin
  CLI over it. `exp/` package is the experiment harness boundary: personas are
  NOT merchant code, they reach the core only via public HTTP + real checkout.
  `exp/personas.py`: Ritika/Arjun/Meera with hard budgets enforced in CODE
  (drop-lines-not-overspend, mirrors merchant policy discipline), catalog read
  from new public `GET /catalog` feed (deliberately excludes cost/margin),
  basket chosen by Gemini fast lane strict JSON. Proven live from VM2: Ritika
  read the real catalog, reasoned inside her ₹350 cap, picked chai+chaat
  masala ₹340.65, order created end-to-end.
- **2026-08-23 — Razorpay raised checkout risk bar (hCaptcha challenges)**
  Yesterday silent-pass netbanking; today every automated authorize gets an
  interactive hCaptcha (api.sardine.ai fraud signals present). Tested 4
  configurations (headless Chromium, real Chrome headless, headed-under-Xvfb
  on fresh-IP VM2, persistent browser profiles + stable persona identity):
  all challenged ⇒ server-side change, likely test-key velocity flag after ~15
  automated checkouts in 2 days. Stance: we do NOT solve captchas
  programmatically — that's defeating a fraud control. Driver detects the
  challenge, abandons cleanly (`risk_challenged` outcome), backs off with
  jitter, retries fresh. Cooldown retry scheduled; fallback demo path =
  agent drives everything, human proves humanity once.
- **2026-08-23 — CONTROL TOWER LIVE** ✅ https://r2-d2.xyz/control
  Judge-facing governance console: proposal queue with Approve/Reject/
  Execute buttons + manual "propose an action" form (throw a 40%×30d ask at
  the policy engine live, watch POL-DISC-001 clamp it), live order feed,
  audit ledger viewer with continuous sha256 chain verification banner,
  health pills, public catalog prices. Pure presentation edge — speaks only
  to the same public JSON APIs agents use. New read edges: `GET /audit/recent`
  (chain verdict computed by audit.verify()), `GET /orders` list.
- **2026-08-23 — BUYER MANDATES LIVE** ✅ signed spending envelopes enforced
  AP2-flavored `mandates.py`: HMAC-signed consent envelope (budget cap,
  per-txn cap, category allowlist, expiry, revocation) presented at order
  creation and enforced BEFORE Razorpay is ever called — refusal is 403 with
  named reasons + audited (`order.mandate_denied`). Tamper-evident: signature
  mismatch refuses even if someone edits the DB row. Spend draws down only on
  payment capture (webhook path, insert-guarded so captured+order.paid can't
  double-count). Tests 17/17; live proof on prod: envelope scoped ["chai"]
  refused masala chai ("categories outside mandate: tea"), tea-scoped envelope
  allowed it. This is the buyer-side twin of merchant-side policy clamps:
  BOTH sides of every transaction are now bounded by construction.
- **2026-08-23 — MCP SERVER LIVE** ✅ https://r2-d2.xyz/mcp/ — merchant as a
  tool-calling target for ANY external AI buyer. Official `mcp` SDK v2,
  Streamable HTTP, stateless JSON mode, mounted into the core app (one
  uvicorn worker). 5 tools: search_catalog, get_product, create_order,
  get_order_status, shop_policies — thin delegates to the SAME functions the
  REST edges call (no drift possible). DNS-rebinding guard kept ON with host
  allowlist from settings.public_base_url. Stateless mode lesson: no
  initialize handshake; every request carries the 2026-07-28 `_meta`
  protocol envelope + Mcp-Method/Mcp-Name routing headers. Live proof: an
  external buyer created order ₹259 via tools/call against production.
- **VM2 fleet box ready**: uv venv synced, Playwright Chromium + Google
  Chrome stable installed, Xvfb available, app bundle + gemini provider env
  deployed, first persona sessions logged to artifacts/sessions.jsonl.
- **2026-08-23 — MEASUREMENT RIG COMPLETE** ✅ PREREGISTRATION.md frozen
  before any data exists: arms (control = base catalog; treatment = every
  EXECUTED agent action replayed from the audit ledger), N=90 target/60
  floor, primary metric net revenue per valid session (walked_away = ₹0),
  symmetric exclusions (risk_challenged / infra_error), bootstrap 95% CI +
  permutation test, declared threats incl. "bundle pricing must ship first".
  Both prerequisites shipped same day:
  (1) Bundle pricing in `/orders` — exact-multiset basket match against
  active bundles, cheapest wins, NEVER above the sum of parts; savings +
  bundle_id recorded on order + audit + API response; orders.bundle_id via
  migrate(). The MCP create_order tool description tells buyer agents.
  (2) `bazaar/experiment.py` + `scripts/measurement_toggle.py` — arm flips
  normalize through control then replay treatment FROM THE LEDGER
  (`proposal.executed` records), so the experiment can only re-apply what
  policy clamped + humans approved; shared bundle-id helper (bundles.py)
  keeps executor/toggle derivations identical; every flip audited as
  experiment.arm_switch. Tests: bundles+arms 23/23, governance 18/18,
  mandates 17/17, mcp 5/5, moneyloop pass — all green, deployed to prod,
  toggle verified read-only (live state correctly reads "treatment" from
  the Day-1 executed chai discount). No arm flips until measurement day.
- **2026-08-23 — FAILURE CHOREOGRAPHY REHEARSED LIVE** ✅ four acts, one
  command (`scripts/failure_choreography.py` + FAILURE-RUNBOOK.md): forged
  webhook → 400 + `webhook.rejected_invalid_signature`; mandate revoked
  mid-flight → next order 403 mandate_denied BEFORE gateway; agent asks
  40%×30d → engine denies-on-concurrent (real rule hit live!) then clamps
  15%×3d on a clean sku → human rejects → price unmoved; one byte flipped in
  a WAL-consistent snapshot → chain breaks at EXACTLY that seq while
  production verifies clean. Rehearsal ran end-to-end against prod; ledger
  at 86 records, chain intact. Lesson baked into the act: file-copying a WAL
  db snapshots a stale tail — use sqlite backup().
- **2026-08-23 — MEASUREMENT PIPELINE WIRED END-TO-END** ✅ the frozen design
  now has working machinery, rehearsed on prod without touching the
  experiment: token-gated `POST /experiment/arm` edge (403 without token,
  verified live; every flip audited) so the VM2 fleet can switch arms
  without shell access to VM1; `scripts/run_measurement.py` (fleet runner:
  T,C,T,C alternation, persona cycling, jittered spacing, arm-tagged JSONL,
  aborts loudly after 3 consecutive LLM failures instead of recording a
  garbage experiment); `exp/analysis.py` (frozen definitions verbatim —
  symmetric exclusions counted per arm, walked_away=₹0, stratum-weighted
  bootstrap CI + within-persona label-shuffle permutation, fixed seed,
  pre-experiment arm-less sessions reported as legacy and never analyzed).
  Dry-run from VM2 proved flips+records+analysis end-to-end into a separate
  dryrun file, then treatment was restored. Findings: Gemini free-tier DAILY
  quota exhausted by today's agent/persona work (429) → measurement day is
  tomorrow after reset; tests caught two real estimator bugs before they
  could touch data (unweighted strata sum; permutation split ignoring
  imbalance). Analysis suite: 16/16 local + VM2.
- **2026-08-23 — DAY2 LOG** (`DAY2.md`): honest self-rating 8.5/10 with the
  gap list that remains: close one persona payment, run the measurement,
  record the video, rotate keys.
- **2026-08-24 — PERSONA PAYMENT CLOSED (Day 3)** ✅ the full agentic money
  loop, end to end, on an LLM persona with a code-enforced budget: Ritika
  (₹350 cap) read the live catalog, chose chai + chaat masala ₹340.65 (chai
  at the agent-negotiated ₹211.65 treatment price), created the order, drove
  real Standard Checkout → Netbanking → Canara Bank mock → `payment.captured`
  webhook verified → `ord_9447818176414f` flipped **paid**; ledger chain_ok
  at 101 records. The only human act in the loop: clicking "Success" on the
  mock test-bank simulator page — no captcha puzzle was ever shown (fresh
  test keys reset the velocity flag — key id deliberately not recorded in
  any public file; research/08 Option A validated). Forensics that got us there, all in DAY3.md: Gemini probe
  starved by thinking tokens (they consume maxOutputTokens — twice-burned
  lesson) + `flash-latest` alias thinking >45 s → fast lane pinned
  `gemini-3.6-flash`; NVIDIA NIM wired as break-glass provider (dormant
  until a key is pasted); driver hardened ×4 (`load` not `networkidle`,
  crash-guard turns exceptions into structured records, stale-hCaptcha-text
  false-positive fixed via bank-page awareness, netbanking path now clicks
  mock-bank Success). Governance never paused: lazy `order.expired_released`
  (seq 95) auto-released orphaned attempt orders mid-hunt. Measurement-day
  deviations logged in MEASUREMENT-DAY.md (model pin, driver hardening, key
  rotation).
- **2026-08-25 — PREREGISTERED MEASUREMENT COMPLETE: verdict NULL, reported
  as-is.** n=94 tagged sessions across three venues/eras (Azure VM2 → laptop
  residential), four LLM providers survived (Gemini quota deaths ×2 → NIM
  stalls → OpenRouter ox-alpha), two captcha regimes, two Razorpay key
  velocity resets. Primary endpoint revenue/session: treatment ₹477.90 vs
  control ₹562.90 — diff −₹241.45, 95% CI [−₹294.34, +₹131.55] (includes 0),
  permutation p=0.486 → **no significant effect; point estimate negative**.
  Secondary observation (hypothesis, not claim): conversion arm-flat (~71%),
  attach rate UP under treatment (0.80 vs 0.48) but AOV DOWN (₹669 vs ₹804)
  — consistent with discount-driven basket-downgrade among agentic buyers.
  Integrity story the judges should ask about: a harness incident spawned
  concurrent runners that corrupted arm attribution for ~64 laptop records;
  our merchant audit ledger + payments DB caught it mechanically
  (`scripts/verify_arm_integrity.py` admits only rows whose arm provably
  held from session start through payment capture); voided rows kept on
  disk, excluded at merge, every decision logged same-hour in
  MEASUREMENT-DAY.md. Full results + honest read in DAY3.md.
- **2026-08-26 — SUBMISSION PACKAGE LIVE** ✅ judge-facing docs written
  (SOLUTION.md detailed solution description; ARCHITECTURE.md with Mermaid
  topology/money-action/governance diagrams; AGENT-DESIGN.md — the personas
  defended against "is that a real agent?" with framework mapping + frozen-
  data evidence: Ritika∩Arjun SKU overlap ZERO across 67 sessions, ~80%
  unique reasoning texts, 3-provider survival). Public demo viewer deployed
  on VM1 (`bazaar-town.service`, Caddy `/demo` route) and flipped to **LIVE
  mode**: first public trip was a real Gemini plan — "the Chai Hamper
  Premium is the ultimate choice within my Rs 1500 budget" (₹1499 of ₹1500,
  verbatim) — real order `ord_9e05146891304d`, real hCaptcha challenge,
  abandoned unsolved, honest `risk_challenged`. The escalation finding now
  reproduces on demand for judges. 30s start cooldown; storefront protected
  by its own MemoryMax cap; both services under watch.
