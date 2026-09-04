# DAY 3 PLAN — 2026-08-24 (payment closure + measurement + submission prep)

Handoff doc: everything needed to execute tomorrow without lost context.
Design decisions live in `PREREGISTRATION.md` (frozen) and
`research/08-captcha-and-agent-rails.md` (captcha strategy). Operating
procedure: `MEASUREMENT-DAY.md`.

## Where we are (60-second recap)

- Everything is BUILT and LIVE on https://r2-d2.xyz: merchant core,
  governance (policy clamps + human gates + tamper-evident ledger),
  Control Tower (/control), buyer mandates, MCP server (/mcp), failure
  choreography (rehearsed live), bundle pricing, arm switch, analysis.
- Missing for a winning entry: **(1)** ONE closed persona payment end-to-end,
  **(2)** the preregistered A/B numbers, **(3)** demo video, **(4)** key rotation.
- Yesterday's blockers: Razorpay hCaptcha on automated checkout (diagnosis:
  datacenter IP + test-key velocity — see research/08) AND Gemini free-tier
  daily quota exhausted (resets ~12:30 IST).
- `MANDATE_SECRET` is now pinned in all three `.env`s (laptop, VM1, VM2) —
  rotating Razorpay keys no longer breaks mandate signatures.
- A one-shot cron (`635a6b4b`) fires ~12:47 IST IF this Claude session is
  still alive. If not: this doc replaces it.

---

## PHASE 0 — Preconditions (~12:45 IST, after quota reset)

```bash
# 1. Gemini quota alive? (from anywhere with the app checked out)
cd C:\Users\hp\buildathon\app
uv run python -c "from bazaar.llm import complete; print(complete('ping', smart=False))"
# -> text = go. 429 = wait 2-3h, retry.

# 2. Merchant healthy?
curl https://r2-d2.xyz/healthz        # status ok, llm_provider gemini
curl "https://r2-d2.xyz/audit/recent?limit=1"   # chain_ok true

# 3. Stock sane? lowest SKU >= 15 units (Control Tower catalog panel or:)
ssh myserver 'cd ~/bazaar/app && sqlite3 bazaar.db "SELECT sku,stock FROM products ORDER BY stock LIMIT 5"'
```

**USER ACTION — fresh test keys (recommended):** Razorpay Dashboard →
Account & Settings → API Keys (Test) → Generate new keys. Then:

```bash
# update RZP_KEY_ID / RZP_KEY_SECRET in app/.env (laptop), then:
scp -q .env myserver:~/bazaar/app/.env
scp -q .env myserver2:~/bazaar/app/.env
ssh myserver 'sudo systemctl restart bazaar'
curl https://r2-d2.xyz/healthz    # must show razorpay_configured true
```

If you skip rotation: proceed anyway after confirming a few hours passed
since yesterday's attempts (cooldown). Do NOT rotate without the scp+restart.

---

## PHASE 1 — Close ONE persona payment (~10 min, LAPTOP ONLY)

Residential IP is the whole point — do NOT run this from VM2.

```bash
cd C:\Users\hp\buildathon\app
uv run python scripts/run_persona.py --persona ritika --headed --tag d3-close
```

A visible Chrome window opens; the agent reads the catalog, picks a basket,
drives checkout. Outcomes:

- **Silent pass** → done; verify capture landed:
  `curl https://r2-d2.xyz/orders | tail` shows the order `paid`.
- **hCaptcha appears** → solve it BY HAND once in the visible window. This is
  the declared honest fallback ("agent drives 100%, human proves humanity
  once"). Payment closes anyway.
- **Still blocked twice** → STOP (never hammer). Record outcome; the Day-1
  captured order remains our historical proof of the money loop, and the
  challenge handling itself is the graceful-failure demo.

Log which happened in `DAY3.md`.

---

## PHASE 2 — The preregistered measurement (few hours, fleet)

Start only after Phase 1 closes (so the store state is stable).

```bash
# 1. Get the experiment token (VM1):
ssh myserver 'cd ~/bazaar/app && uv run python scripts/experiment_token.py'

# 2. On VM2, inside tmux:
ssh myserver2
tmux new -s measure
export PATH="$HOME/.local/bin:$PATH"; cd ~/bazaar/app
EXP_TOKEN=<token> uv run python scripts/run_measurement.py \
    --sessions 90 --out artifacts/sessions.jsonl --pause-s 20
```

Rules while it runs:
- Check in every ~30 min; do NOT touch the storefront between checks
  (no proposals, discounts, toggles, manual orders).
- Runner aborts by design after 3 consecutive LLM failures = quota death →
  resume later; written sessions are kept.
- `risk_challenged` sessions are recorded and excluded symmetrically —
  expected nonzero rate from datacenter IPs; NOT a reason to stop.
- Detach tmux with Ctrl+B D; reattach `tmux attach -t measure`.

When finished (or stopped early):

```bash
uv run python exp/analysis.py artifacts/sessions.jsonl
# -> prints verdict + writes artifacts/measurement/report.json
```

Report honestly per PREREGISTRATION.md rule: CI excludes 0 with positive
sign = win; null/negative reported as-is; asymmetric exclusions = void.

---

## PHASE 3 — Wrap-up (evening)

- [ ] Write `DAY3.md` (same pattern as DAY1/DAY2: implemented/why, evidence
      table, honest self-rating vs judging bar).
- [ ] README status bullets with the actual measurement numbers.
- [ ] Update task list (#9 close-out).
- [ ] Demo video: use FAILURE-RUNBOOK.md as the script +
      https://r2-d2.xyz/control on screen + Phase-1 payment replay.
      Record AFTER measurement so numbers can be quoted.
- [ ] **Rotate ALL keys before anything public ships** (Razorpay test keys,
      webhook secret, Gemini key — all were pasted into chat). MANDATE_SECRET
      is independent now; webhook secret changes need a Dashboard update too.
- [ ] Deviations from this plan → append to MEASUREMENT-DAY.md "Deviation log".

## Key facts (don't re-derive)

| Thing | Value |
|---|---|
| Merchant | https://r2-d2.xyz (VM1 = `ssh myserver`, service `bazaar`) |
| Fleet box | `ssh myserver2`, code at `~/bazaar/app`, browser + Xvfb installed |
| Session records | `~/bazaar/app/artifacts/sessions.jsonl` on VM2 (append-only) |
| Experiment token | `scripts/experiment_token.py` on VM1 (needs `.env`) |
| Personas | ritika ₹350 / arjun ₹1500 / meera ₹1000 (`exp/personas.py`) |
| Test suites | `scripts/test_*.py` — all green as of 2026-08-23 |
| Never do | solve/route captchas programmatically · hammer retries · delete anything on VM1 · touch storefront mid-measurement |
