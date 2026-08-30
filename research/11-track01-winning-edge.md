# 11 — Track 01: where the winning edge actually is

Written 2026-08-30. Supersedes the differentiation section of
`SUBMISSION-STRATEGY.md` §3 and re-prioritises `RESEARCH-PLAN.md`.

---

## 0. TL;DR — the one-sentence reframe

> **Everyone else built an agent that can pay. Build the thing that proves
> whether agentic payments are safe at scale — measured on Razorpay's own
> live rail, with the failure on camera.**

Track 01's brief is *"Grow the merchant's revenue, and make them sellable to
AI buyers."* The field has read the first half and ignored the second. It has
also built a great many agents that can pay and almost none that can prove
anything.

---

## 1. What changed since the last plan (verified today)

### 1.1 Razorpay has shipped the exact rail this project simulates

`razorpay.com/agentic-payments/` (fetched 2026-08-30) shows a full
**Agentic Payments Suite**:

| Surface | Status |
|---|---|
| Agentic Payments for In-App Commerce | **Live in Beta** |
| Agentic Payments on LLMs | live (ChatGPT pilot with NPCI + OpenAI) |
| Agentic Payments for Voice AI | live |

and the rails underneath it:

- **UPI Reserve Pay — LIVE.** *"Enable consent-based, pre-authorized payments
  that allow AI agents to transact securely within approved spending limits."*
- **UPI Circle — coming soon.** Delegated and shared payment authorisations.
- **AI-Ready MCP & APIs — 40+ composable tools.**
- **Advanced Risk & Compliance** — *"Real-time fraud detection, authentication,
  and compliance built for AI-led transactions."*

NPCI Executive Director Sohini Rajola, quoted on that page:

> *"With UPI Reserve Pay, users can give consent once and allow intelligent
> systems to transact on their behalf in a controlled, transparent way."*

**Why this matters more than anything else in this document:** `mandates.py`
is already a structurally identical primitive — consent once, budget cap,
per-transaction cap, category allowlist, expiry, revocation, and draw-down
**only on capture** (failed attempts never consume budget). The project did
not copy a protocol off the internet; it independently arrived at the shape
Razorpay then took to production. That is a much stronger story than
standards-compliance, and it is entirely true.

### 1.2 The competitor field is completely flat

GitHub search, 2026-08-30:

| Query | Repos | Highest star count |
|---|---|---|
| `razorpay buildathon` | 459 | 1★ |
| `razorpay agentic commerce` | **135** | **1★** |
| `razorpay buildathon track 01` | **16** | **0★** |
| `UPI reserve pay agent` | **2** | 1★ |

Two facts fall out of this.

**(a) Nobody has traction.** Every single repo in the Track 01 space has 0 or
1 stars. There is no front-runner. The judges are reading cold, which means
the repo and the 5-minute video are *all* that exists. Presentation is not a
finishing touch — it is the entire channel.

**(b) Only 2 repositories on the whole of GitHub mention UPI Reserve Pay in
an agent context.** Razorpay has it live, NPCI's ED is publicly quoted on it,
and essentially nobody is building on it. The most valuable real estate in
this track is unoccupied.

### 1.3 What is now table stakes (do not lead with any of this)

The `razorpay agentic commerce` result set shows the common shape, and it has
converged hard. The following are now *expected* and score roughly zero
differentiation:

- deterministic policy gate before any money moves
- HMAC webhook verification + dedupe on event id
- hash-chained / append-only audit ledger
- an MCP server
- an agent-readable product catalog
- "mandate-bounded" / "allow + step-up" language in the README
- AP2 or UAP mentioned in the positioning

Rivals explicitly occupying adjacent ground today:
`saivigneshpandian/ai-buyer-firewall` (signed mandate + step-up),
`manav-Mnv/mandate-guard` (deterministic risk interceptor),
`Harshit-Jethi/aegis-ap2-gateway` (A2A negotiation),
`Prajeeth-12/AgentPay` (claims first UAP-compatible),
`biru-codeastromer/maryada` (mandates + deterministic policy),
`Purvee25/sentinel-ap2` (guardrail middleware),
`jboiie/argus` (red-team harness).

**Read that list again. Four of them claim mandate-bounded spend.**
"Signed mandate + policy gate" is no longer a differentiator in this track —
it is the entry fee. Anything that leads with it will read as commodity.

---

## 2. Decoding the brief, clause by clause

Verbatim from `razorpay.com/buildathon/`:

> **Track 01 — AI Growth & Agentic Commerce.** Grow the merchant's revenue,
> and make them sellable to AI buyers.
>
> **The bar:** Every money action explainable, bounded and gated. Show the
> audit trail and one failure handled gracefully.

| Clause | What it is really asking | Do you have it? |
|---|---|---|
| "Grow the merchant's revenue" | measured uplift, not a demo | **Partial** — bundles/upsell arms exist, `exp/` data exists; no headline uplift number |
| "make them sellable to AI buyers" | merchant-facing **diagnostic** | **Partial** — `transactability_report.py` exists, never run against the master dataset |
| "Every money action explainable" | per-action reason codes | **Yes** — `policy_evaluations` table, pure `policy.py` |
| "bounded and gated" | hard caps + human approval | **Yes** — mandates + `approvals` table |
| "Show the audit trail" | verifiable integrity | **Yes** — 686-row chain, verified 0 breaks |
| **"one failure handled gracefully"** | a real failure, on record | **Yes, and this is your strongest asset** |

And the scoring page, verbatim:

> **Problem taste** — did you pick something that actually matters
> **Build quality** — does it run, is it structured, would you trust it
> **AI judgment** — the right tool in the right place, **and where you chose
> not to use one**
> **Failure recovery** — what broke, and what you did about it

The form's last field is *"What broke, and how you got out"*, and the site
says: **"The last one is the one we read first."**

---

## 3. The five unclaimed edges, ranked

### Edge 1 — The risk-escalation measurement (highest payoff, mostly done)

**The finding:** Razorpay's test-mode risk stack is *stateful and
velocity-keyed*. Hold the code, the key and the venue constant and the
challenge rate still climbs:

| Segment | Challenge rate |
|---|---|
| first third | **0%** |
| second third | **23.1%** |
| third third | **14.3%** |
| later high-frequency batch | **~90%** (20 of 22) |

**Why nobody else has it:** it requires having run dozens of real checkouts
and logged the outcomes. Repos with a mocked payment cannot observe this at
all. It is unreachable without the exact thing almost no competitor did.

**Why it wins three criteria at once:**

- *Problem taste* — "agentic commerce does not scale for free" is a real
  problem, discovered rather than selected.
- *AI judgment* — it is a measured boundary on what agents should be trusted
  to do unsupervised.
- *Failure recovery* — the escalation *is* the thing that broke.

**Why Razorpay specifically cares:** the product page already has a
**"Advanced Risk & Compliance — built for AI-led transactions"** line, and
NPCI is building **UAP** to *register, verify and authorise trusted AI
agents*. The measurement is direct evidence that agents need a reputation
layer — the same conclusion NPCI reached, arrived at independently from
traffic data. This is the sentence to land in the video:

> *Fraud controls are stateful. Agentic traffic makes them stricter, not
> stable. NPCI's UAP is right that agents need to be registered — and here
> is the traffic data that shows why.*

**Still to do:** surface it in the product, not just the docs. A
challenge-rate counter on `/demo` and a `/risk` panel. Currently it lives
only in `research/10` and a README row.

### Edge 2 — Reserve Pay as executable code, not a table row

Only **2 repos on GitHub** touch this. The README already maps
`mandates.py` → UPI Reserve Pay, but a mapping table is an *assertion*. Make
it a *demonstration*:

1. Name it on screen. The demo should say "UPI Reserve Pay envelope", not
   "mandate".
2. Show the envelope **refusing** a real over-limit attempt, live, with the
   reason code.
3. Show revocation mid-session and the subsequent attempt failing.

Effort: medium. Payoff: very high — it is Razorpay's live rail and the field
has left it empty.

### Edge 3 — Real money, at scale, from a real browser-driving buyer

51 paid orders, 55 payment rows, 106 webhook events, 686 audit records,
chain verified with 0 breaks. A browser-driving AI buyer completed these
against live Razorpay test mode.

Most repos in this track simulate the payment. This one has a hash-chained
ledger of real ones. Effort: done. It needs to be **legible** — one number,
one link, verifiable in under 30 seconds.

### Edge 4 — Transactability Score: answering the clause nobody answers

*"make them sellable to AI buyers"* is the half of the brief the field
skipped. `transactability_report.py` already computes it. It has never been
run against the master `sessions.jsonl` on VM2.

Run it, get one number, put it in the README's first screen and the video's
first minute. A merchant-facing score is also the most *product-shaped*
thing in the project — it is what a Razorpay PM would actually ship.

### Edge 5 — The failure, reproducible on demand

`WHAT-BROKE.md` is written. The form reads it first. But a document is a
claim about the past; a replay is evidence in the present. Make `/demo` able
to replay the exact failure — the risk challenge, the clean typed abandon,
the `risk_challenged` ledger row.

This also solves the operational problem: replay mode needs no live key,
which is exactly why it removes the daily rotation chore
(`research/10` §3.2). One change, two wins.

---

## 4. Gap analysis

| You have | You need | Why it matters |
|---|---|---|
| Risk escalation, measured | It surfaced in the product, not just docs | Criterion 1 + 3 + 4 from a single artifact |
| Mandate ≈ Reserve Pay | Named and demonstrated on screen | The empty field (2 repos) |
| 51 real paid orders | One legible headline number | Beats every simulated-payment repo |
| `transactability_report.py` | Run against master data | The clause nobody answers |
| `WHAT-BROKE.md` | A replayable failure in `/demo` | "The last one is the one we read first" |
| Key rotation chore | `scripts/rotate_keys.sh` | **Done** — see §7 |

---

## 5. Build order

Ranked by (differentiation × criteria-hit) ÷ effort.

| # | Task | Effort | Hits |
|---|---|---|---|
| 1 | Run `transactability_report.py` on VM2's master `sessions.jsonl`; publish the number | 1 h | Brief clause 2, Problem taste |
| 2 | Surface challenge rate in `/demo` + a `/risk` panel | 3 h | **Problem taste, AI judgment, Failure recovery** |
| 3 | `/demo` replay-by-default, live as opt-in | 3 h | Failure recovery + kills the rotation chore |
| 4 | Rename mandate → "UPI Reserve Pay envelope" in the demo; add live over-limit + revocation rejection | 3 h | The empty field |
| 5 | Prompt-injection test on the buyer agent, with results | 3 h | AI judgment (the biggest open hole) |
| 6 | Embed `artifacts/*.png` in the README | 1 h | Build quality at a glance |
| 7 | Record the 5-min video against `DEMO-VIDEO-SCRIPT.md` | 3 h | **Required form field** |

**Do not** start numbers 1–5 before the video is recorded. An unsubmitted
build scores zero.

---

## 6. What NOT to build

| Temptation | Why not |
|---|---|
| AP2 SD-JWT artifacts (`research/01` §6 recommends this) | Protocol theatre. 135 repos are doing standards cosplay; none of them have 51 real payments. `research/01` was written before the field was visible — the calculus changed. |
| x402 / ACP / MPP breadth | Same. Razorpay's own framing is UPI-consent-native; they did not name ACP/AP2/x402 publicly. |
| More agents / more roles | ChatDev-shaped multi-agent roleplay is now generic. The judge has seen it. |
| "Campaign orchestrator" (a listed example direction) | Listed ≠ rewarded. Shallow, and impossible to measure honestly in the time left. |
| Further UI polish | Done. The stage and characters are at the target quality. |
| Anything that solves or evades the captcha | Disqualifying for a payments company. The driver abandons cleanly and records `risk_challenged`. Keep it. |

---

## 7. The rotation chore — solved

`app/scripts/rotate_keys.sh` (written 2026-08-30) turns the daily 30-minute
rotation into one command across laptop + VM1 + VM2.

```bash
bash scripts/rotate_keys.sh --status     # fingerprint drift across all 3 hosts
bash scripts/rotate_keys.sh --selftest   # proves the escaping is safe
bash scripts/rotate_keys.sh --dry-run --key-id rzp_test_X --key-secret S --webhook-secret W
bash scripts/rotate_keys.sh              # interactive, hidden prompts
```

Verified live on 2026-08-30:

- `--status` — all three hosts hold identical `RZP_*` and `MANDATE_SECRET`
  fingerprints
- `--selftest` — round-trips a 22-byte value containing `\ & | = space $ *
  [ ] ' "` byte-exactly on the laptop and both VMs; unrelated keys and
  comments survive; files forced to `600`
- pre-flight rejects a fake key pair with **HTTP 401 from Razorpay before a
  single byte is written**
- passwordless `sudo` confirmed on VM1
- webhook HMAC probe verified end-to-end against the live endpoint:
  correct secret → **200**, wrong secret → **400**

Safety invariants encoded in the script (documented in its header):

1. **`MANDATE_SECRET` is never rotated and must be non-empty.** `mandates.py`
   derives the signing key as `MANDATE_SECRET or f"{RZP_KEY_SECRET}:mandates-v1"`,
   so with it empty, rotating the key secret would *silently* void every
   signed mandate in the database. Pre-flight hard-fails (exit 5).
2. **Nothing is written until the new pair authenticates against
   `api.razorpay.com`.** A typo cannot take `/demo` offline (exit 6).
3. **Old values are held in memory for automatic rollback**, including
   mid-write, with services restarted back.
4. **No captcha is solved, proxied or evaded.** Rotation is credential
   hygiene; defeating a risk check would be disqualifying.

Known side effect, measured and benign: a correctly-signed probe inserts one
`webhook_events` row with `event='key_rotation.probe'`. It matches no order,
so it touches no payment, mandate or audit row. Rejected probes insert
nothing. Clean up with
`DELETE FROM webhook_events WHERE event='key_rotation.probe';`

---

## 8. Sources

Verified 2026-08-30 unless noted.

- `https://razorpay.com/buildathon/` — track brief, four criteria, the bar,
  the 12 form fields, "The last one is the one we read first"
- `https://razorpay.com/agentic-payments/` — the Agentic Payments Suite, UPI
  Reserve Pay live, UPI Circle coming soon, 40+ MCP tools, Advanced Risk &
  Compliance, NPCI + OpenAI + Vodafone Idea + bigbasket partners
- GitHub search API — `razorpay buildathon` 459, `razorpay agentic commerce`
  135, `razorpay buildathon track 01` 16, `UPI reserve pay agent` 2; max
  1★ across all
- `research/01-agentic-commerce-protocols.md` § on UPI Reserve Pay / SBMD —
  ₹10,000 cap, 90 days, RBI Governor unveiling at GFF Oct 8 2025
- `research/10-key-rotation-and-risk-escalation.md` — challenge-rate table,
  s2s payment API ruled out (400, PCI-gated)
- `app/bazaar/mandates.py` — envelope semantics, `_signing_key()` derivation
- `research/02-razorpay-testmode-deepdive.md` — Agentic Payments timeline
  (ChatGPT pilot Oct 2025, Claude Feb 20 2026)
- NPCI **Unified Agent Protocol (UAP)** — reported July 2026, in
  development / industry consultation, built on the UPI Circle delegated
  model, needs RBI approval, no published timeline
