# Project: buildathon — Razorpay AI Buildathon 2026, Track 01

Goal: **win Track 01 → secure the AI Builder internship.** Every decision
should be judged against the four published criteria, not against "is the
code nice".

## Hosting / deployment

- **Live: https://r2-d2.xyz** (merchant, VM1). Judge entry points:
  `/demo` (live AI buyer), `/control` (governance console),
  `/audit/recent` (chain verdict), `/mcp/` (MCP tool target).
- **VM1** — Azure, 1 GB RAM, Ubuntu. systemd `bazaar.service`
  (`Restart=always`, `MemoryMax=650M`) → uvicorn :8000 → SQLite WAL,
  behind Caddy auto-TLS. Config in `deploy/`.
- **VM2** — the buyer fleet (Playwright + personas). Drives VM1 over public
  HTTPS only; **no shell access to the merchant** (arm flips go through a
  token-gated API and are audited).
- SSH aliases: `myserver` (VM1), `myserver2` (VM2).
- Infra spend: **₹0**. Two free-tier Azure VMs, free LLM tiers, test mode only.
- **Repo: https://github.com/Krishna-10-7/buildathon** (public — scrub
  anything before committing).

## The judging rubric (from razorpay.com/buildathon)

1. Problem taste · 2. Build quality · 3. AI judgment (**including where you
   chose NOT to use AI**) · 4. Failure recovery.

Track 01 bar: *"Every money action explainable, bounded and gated. Show the
audit trail and one failure handled gracefully."*

**"What broke, and how you got out" is the answer they read first.**
 → `WHAT-BROKE.md` is the highest-leverage document in the repo.

## Headline numbers (regenerate, never hand-edit)

    python app/scripts/transactability_report.py

- 33/40 autonomous AI-buyer sessions completed a captured payment (82.5%)
- 0/38 budget violations · ₹25,724.15 captured · 47 unique paid orders
- Audit ledger 674 records, `chain_ok: true`, `first_bad_seq: null`
- Growth A/B (n=94, preregistered): **NULL** — −₹241.45, CI [−294, +132],
  p=0.486. Reported as-is; that honesty is a feature, not a failure.

The null is the *discount* half. The **transactability** scoreboard is the
positive half of the same brief. Never lead with the null alone.

## Hard rules

- **Never solve a captcha programmatically.** Abandon, count it as
  `risk_challenged`, back off. Defeating a fraud control is disqualifying
  behaviour for a payments company.
- Money is **integer paise** everywhere. Client-sent amounts are never
  trusted; `orders.py` prices from the catalog server-side.
- Agents can only `propose()`. Execution is one dispatch point behind human
  approval. `policy.py` is pure — no db, no net — and clamps rather than
  rejects.
- Append-only evidence files are never edited or trimmed. Voided rows stay
  on disk and are excluded at merge time.
- All keys are test-mode only and have appeared in plaintext chat before:
  **rotate before anything public** (`KEY-ROTATION-CHECKLIST.md`).
- **`.gitignore` is not retroactive** — after adding rules, run
  `git rm -r --cached <path>` for already-tracked files.

## Where things live

- `app/bazaar/` — merchant core (policy, proposals, mandates, orders,
  audit, mcp_server, agents/growth). Thin edges → pure cores → adapters.
- `app/exp/` — the demand side (personas, checkout driver, analysis).
  Deliberately NOT merchant code; reaches the core only over public HTTP.
- `app/artifacts/*.jsonl` — append-only session evidence (in repo).
- `research/01`–`08` — protocols, Razorpay test-mode, MCP, governance,
  measurement, 1 GB stack, zero-budget, captcha rails.
