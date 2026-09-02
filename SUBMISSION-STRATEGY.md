# SUBMISSION STRATEGY — how this repo wins Track 01

Written 2026-08-29 after reading the official brief, the project, and the
competition. Read this first. Then [WHAT-BROKE.md](WHAT-BROKE.md) (the form
answer they read first), then [RESEARCH-PLAN.md](RESEARCH-PLAN.md).

**None of this is about building more.** The build is already deeper than
the field. This is about packaging, one reframe, and closing four
self-inflicted wounds.

---

## 0. The honest answer to "100%"

Nobody can guarantee a hire. What can be controlled: (a) make sure nothing
in the submission *disqualifies* you, and (b) make the five minutes a
reviewer spends land on your strongest evidence instead of your weakest.
Both are fixable this week. The gap between the current submission and its
ceiling is almost entirely **presentation and hygiene**, not engineering.

Razorpay's own page says the office opens **in September** and that no hard
deadline is published. Treat the window as **days, not weeks.**

---

## 1. What and where the project is

| | |
|---|---|
| **Repo** | `https://github.com/Krishna-10-7/buildathon` — public, `main`, one commit |
| **Live** | `https://r2-d2.xyz` — storefront · `/demo` · `/control` · `/audit/recent` · `/mcp/` |
| **Hosting** | Azure VM1 (Ubuntu, **1 GB RAM**, 1–2 vCPU) · systemd `bazaar.service` (`Restart=always`, `MemoryMax=650M`) · Caddy auto-TLS → uvicorn `:8000` · SQLite WAL |
| **Second box** | Azure VM2 (1 GB) — the buyer fleet driver (Playwright + personas), talks to VM1 only over public HTTPS |
| **Third venue** | Your laptop (residential IP), used when the Azure IP's captcha reputation collapsed |
| **Payments** | Razorpay **test mode only** — real Orders API, real Standard Checkout, real risk engine, real `payment.captured` webhooks |
| **Cost** | ₹0. Free-tier LLMs, free hostname, no paid services |
| **Code** | ~5,900 LOC Python — `app/bazaar/*` (merchant core), `app/exp/*` (buyer harness), `app/scripts/*` |
| **Ledger state** | 674 records, `chain_ok: true`, `first_bad_seq: null` (verified live 2026-08-29) |

Judge-facing entry points, in the order a reviewer will click them:
`README` → `/demo` → `/control` → `/audit/recent`.

---

## 2. The actual bar (verbatim from razorpay.com/buildathon)

> **Track 01 bar:** *"Every money action explainable, bounded and gated.
> Show the audit trail and one failure handled gracefully."*

> **They score four things:** Problem taste · Build quality · AI judgment
> *(…"and where you chose not to use one")* · Failure recovery.

> **The form asks 12 things.** The last one is *"What broke, and how you got
> out."* And then: **"The last one is the one we read first."**

Read that last line again. The failure narrative is not a formality — it is
the first thing a human reads about you. Right now that answer does not
exist as a document. It is scattered across `DAY3.md` and
`MEASUREMENT-DAY.md`, buried under 275 lines of status log. **This is the
single biggest hole in the submission.** → [WHAT-BROKE.md](WHAT-BROKE.md)

---

## 3. The competition (measured, not guessed)

> **Updated 2026-08-30 — see `research/11-track01-winning-edge.md`, which
> supersedes the differentiation analysis below.**
>
> The field grew and flattened at the same time:
>
> | Query | Repos | Highest star count |
> |---|---|---|
> | `razorpay buildathon` | **459** (was 396) | 1★ |
> | `razorpay agentic commerce` | **135** | 1★ |
> | `razorpay buildathon track 01` | **16** | 0★ |
> | `UPI reserve pay agent` | **2** | 1★ |
>
> Three consequences that change the plan:
>
> 1. **Nobody has traction.** Every repo in this track has 0–1 stars. There
>    is no front-runner; the repo and the video are the entire channel.
> 2. **"Mandate-bounded spend" is now commodity.** At least four rivals
>    (`ai-buyer-firewall`, `mandate-guard`, `maryada`, `sentinel-ap2`) claim
>    it explicitly. Do not lead with it.
> 3. **UPI Reserve Pay is empty ground** — 2 repos on all of GitHub — and it
>    is the rail Razorpay has **live**. `mandates.py` is already
>    structurally identical to it. That is the wedge.
>
> The revised build order is in `research/11` §5.

Original analysis, retained for the per-repo detail:

396 public repos matched "razorpay buildathon" when this section was
written. Direct Track 01 rivals:

| Repo | Depth of money loop | Real AI buyer? | Measurement |
|---|---|---|---|
| `Antariksh62/…track-01…` | Order creation via official SDK | No | None |
| `rat-sh/Buildathon` | Order + HMAC webhook | No (NL→cart only) | None |
| `princeVerma73/agentcart-uap` | Order + webhook + SHA-256 chain | Planner only | None |
| `Adarsh-Me/Agent-Audit` | Payment link, human-reviewed | Simulation, 220–640 trials | **Yes, 640 trials + CIs** |
| `jboiie/argus` | Red-team harness | Test harness | Yes |
| **you** | **33–47 captured payments** | **Yes — real browser, real checkout** | **Preregistered RCT n=94** |

**The uncomfortable truth:** *a deterministic policy gate + an HMAC
verified webhook + a SHA-256 chained audit log is now table stakes.* Four of
six rivals ship exactly that shape. Leading with "we have a policy engine
and an audit trail" puts you in the middle of the pack.

**What almost nobody has:**
1. **Money that actually moved.** Most stop at `order_` created. You have 47
   unique paid order ids with webhook-captured payments.
2. **An AI that drove the browser.** Real checkout, real contact screen,
   real bank selection, real risk engine.
3. **The risk-engine finding.** You measured fraud controls getting
   *stricter* under sustained agent load. No rival mentions this.
4. **Two-sided governance.** Everyone gates the *merchant* side. You also
   gate the *buyer* side (`mandates.py`) — which is Razorpay's own shipped
   product category (see §4).

**What they have that you don't:** presentation. Several rivals ship
badge-band READMEs with a hero, a TOC and a criteria-mapping table. Yours is
a chronological dev diary. Same substance, first impression inverted.

---

## 4. The reframe (highest-leverage change; costs nothing)

### 4a. Stop leading with the null result

Right now the story is *"we measured whether AI growth actions grow revenue
→ NULL."* For a track called **AI Growth**, that is a self-inflicted wound.

But the brief is an **OR**:

> *"Build an agent that grows revenue for a merchant … **or** that makes a
> merchant transactable by an AI buyer end to end."*

You did both, and the second half is **unambiguously, measurably positive**
— it just was never quantified. It is now
(`app/scripts/transactability_report.py`, from your existing data):

> **33 of 40 autonomous AI-buyer sessions completed a real, webhook-captured
> Razorpay payment — 82.5%.** 95% reached the gateway; 86.8% of those
> finished paying. ₹25,724.15 captured. **0 budget violations in 38
> budget-bounded sessions** (mean 17.8% headroom kept, tightest 0.1%). 28 of
> 40 finished on the first attempt. Across the wider corpus: **47 unique
> paid orders.**

That is the headline. *"We made a merchant transactable by AI buyers and
proved it 47 times with real money movement"* is a stronger, truer opening
than *"our growth experiment was null."*

The null then becomes what it actually is — a **finding**, placed second:
AI buyers respond to discounts by *downgrading* baskets (attach ↑ 0.48→0.80
while AOV ↓ ₹804→₹669). That is a merchant-economics insight about agent
price elasticity, and it is more useful to a payments company than a
narrowly-true +3% lift.

### 4b. Lead with the venue finding, not the null

> The same buyer, the same code, the same merchant, the same key. Only the
> network it ran from changed — and the challenge rate moved **79.3% →
> 12.7% → 100%**: datacenter, residential, back to datacenter.
> `z = 7.64, p = 2.1e-14`.

Reframe: **where an agent pays from decides whether it can pay.** Fraud
controls score the origin, not just the key — so the same well-behaved
agent is routine traffic on one network and suspicious on another, with
nothing about the agent having changed. That is an operational constraint
no rival publishes, it is exactly the sort of thing Razorpay's own risk org
cares about, and you found it by running the thing instead of demoing it.

**The reversal is what makes it evidence.** Any monotone story — key
ageing, accumulated bot history, "the engine gets stricter over time" —
predicts P3 ≥ P2. The rate fell 66pp when the fleet moved to a residential
IP and rose 87pp when it moved back. The variable that flipped twice is the
venue; the one that only ever moved forward is the calendar, and it moved
the wrong way.

> **CORRECTION 2026-09-01 — this section used to say "promote the
> escalation finding."** It quoted **0% → 23% → 14%** across thirds of one
> run, plus a ~90% figure from a different run under different conditions.
> Both are **withdrawn**: homogeneity chi-square p ≈ 0.22, Cochran-Armitage
> trend p ≈ 1.00, five challenges across 38 gate-reaching sessions. The
> 0/13 first segment has a 95% upper bound of 23.1% — exactly the second
> segment's point estimate. We had read a confidence interval as a trend.
> See `README.md` §Finding 2b and `research/10` §1.1.
>
> The retraction costs you nothing and buys you credibility: the venue
> finding is stronger than the escalation ever was, it is unreachable for
> anyone who simulated their payments, and being the one submission that
> withdrew a claim *before* a judge could catch it is a signal in itself.

**The closing link (added 2026-08-30 — this is the best sentence in the
submission, put it in the video):**

NPCI is building the **Unified Agent Protocol (UAP)** to *register, verify
and authorise trusted AI agents* over UPI (reported July 2026, in
development, built on the UPI Circle delegated model, RBI approval pending).
Razorpay's own suite lists **"Advanced Risk & Compliance — built for
AI-led transactions"**.

Your traffic data independently arrives at the same conclusion NPCI did:
**agents need a reputation layer, and it has to be tied to the agent, not
to the pipe it happens to be running on.** You are not guessing that
agents should be registered — you measured what happens when they are not:
the identical agent is challenged 79% of the time from one network and 13%
from another.

> *Fraud controls score the origin. Right now an agent's ability to pay
> depends on where its container happens to live. NPCI's UAP is right that
> agents need to be registered — and this is the traffic data that shows
> why.*

That single paragraph scores **Problem taste**, **AI judgment** and
**Failure recovery** simultaneously, and it is unreachable for any repo that
simulated its payments.

### 4c. Mirror Razorpay's own product vocabulary

Razorpay has shipped an **Agentic Payments** suite (with NPCI and OpenAI).
Their words, from `razorpay.com/agentic-payments`:

| Their product language | Your module | Say it this way |
|---|---|---|
| **UPI Reserve Pay** (live) — *"consent-based, pre-authorized payments… within approved spending limits"* | `mandates.py` | "a Reserve-Pay-style delegated spend envelope" |
| **AI-Ready MCP & APIs** — *"40+ composable tools"* | `mcp_server.py` | "an AI-ready MCP surface" |
| **Agentic Payments on LLMs** (built on Indus/NPCI) | `/catalog` + personas | "agentic payments from an LLM surface" |
| **Granular Controls** | `policy.py` | "granular controls, clamp-not-reject" |
| **Advanced Risk & Compliance** | typed `risk_challenged` handling | "compliance-first failure taxonomy" |

Cost: an hour of editing. Payoff: a Razorpay engineer opens your README and
sees *their own roadmap* implemented. Judges reward people who did the
reading.

### 4d. Answer "AI judgment — where you chose NOT to use AI" explicitly

An explicit rubric item. You are strong here and currently silent about it.
Add a short section naming the refusals: no LLM in the policy gate, no LLM
in pricing, no LLM in mandate enforcement, no LLM in the audit path, no
captcha solving. The gate is ~200 lines of deterministic Python and that is
a *deliberate* choice.

---

## 5. Self-inflicted wounds — fix these before anything else

Four things in a public repo about *governance discipline* that a payments
company will notice immediately.

| # | Wound | Status | Fix |
|---|---|---|---|
| 1 | **A Razorpay test key id was committed** to `README.md` + `MEASUREMENT-DAY.md` | **DONE** — redacted, history rewritten, force-pushed | **Still rotate the key in the dashboard.** It was public; treat it as burned. Untracked ≠ unread. |
| 2 | **13.7 MB of Chromium browser cache committed** — 1,106 files, GPU/shader caches, `BrowserMetrics-*.pma`, cookies, session state | **DONE** — untracked + history rewritten. Repo is 201 tracked files, was 1,301 | Nothing further. `.gitignore` was not retroactive; `git rm -r --cached` fixed it. |
| 3 | `KEY-ROTATION-CHECKLIST.md` exists and is **entirely unchecked** | **OPEN — the machinery is done, the rotation itself is not (it needs you in the dashboards)** | Run the rotation, then tick it. The 30-minute three-host chore is now one command: `app/scripts/rotate_keys.sh` (2026-08-30). See `research/11` §7. An unchecked security checklist in a governance submission is worse than no checklist — finish it or delete the file. |
| 4 | `README.md` is a 275-line status log | **DONE** — pitch-first README live; the old log is preserved as `DEVLOG.md` | Nothing further. |

Wound 3 is the one to think about. A reviewer reading a project about
auditability who finds a security checklist you filed and never completed
will draw the obvious conclusion. Either complete it (and date it) or remove
it. Do not ship it empty.

---

## 6. Action plan, in order

### P0 — today, before anything else
1. **Rotate every credential.** Razorpay key id + secret, webhook secret,
   Gemini, NIM, OpenRouter. The key id was public; treat it as burned.
   *This is the one P0 item that cannot be done for you — it needs the
   dashboards.* Then tick `KEY-ROTATION-CHECKLIST.md` or delete it.
2. ~~Rewrite git history (§7)~~ — **DONE.** Amended the single commit and
   force-pushed with `--force-with-lease`. Repo is clean at `3fbd230`
   (201 tracked files, zero key matches across history).
3. ~~Write `WHAT-BROKE.md`~~ — **DONE.** Customise the voice.
4. ~~Rewrite `README.md` pitch-first~~ — **DONE.**
5. Fill the two `[FILL]` slots in `DEMO-VIDEO-SCRIPT.md` — **OPEN.**
6. **Record the 5-minute video** — **OPEN, and it is a required form
   field.** Nothing else on this list matters if there is no video.

### P1 — next 48 h
6. **Record the 5-minute video.** Non-negotiable — it is a required form
   field. Script is ready; see §8 for the reordered beats.
7. Run `transactability_report.py` on VM2's master `sessions.jsonl` too, so
   the headline covers the full corpus, not just the laptop era.
8. Add the **UPI Reserve Pay / MCP mapping table** (§4c) to `SOLUTION.md`.
9. Add the **"where we chose not to use AI"** section (§4d).
10. Add screenshots to the README. `artifacts/*.png` exist and none are
    embedded. Judges skim images before prose.

### P2 — if time remains
11. Rehearse `/demo` end to end **twice on camera-ready hardware**. Live
    LLM + live captcha = the demo can fail while a judge watches. Have a
    recorded fallback one click away.
12. Re-run `exp/analysis.py` and commit
    `artifacts/measurement/report.json` — the frozen analyzer output is
    quoted in three documents but not committed as an artifact.
13. Write `research/09-razorpay-agentic-payments.md` — map each subsystem
    onto Razorpay's shipped product line (§4c in long form).

---

## 7. Purging git history

> **STATUS: DONE (2026-08-29).** There was only one commit, so instead of
> `filter-repo` the commit was simply amended and force-pushed with
> `--force-with-lease`. Result: 1,301 → **201 tracked files**, zero
> `rzp_test_`/API-key matches anywhere in history, and no browser-profile
> blobs. The pre-rewrite commit (`2661be2`) is still recoverable from reflog
> if you ever need it.
>
> The commands below are kept for reference. **What is still NOT done is
> item 1 in §6 — actually rotating the credentials in the dashboards.** A
> rewritten history does not un-publish a key that was public.

Only one commit exists, so a rewrite is clean and safe. **Do this before
sharing the repo link anywhere else.**

```bash
# 1. install
pip install git-filter-repo          # or: uv tool install git-filter-repo

# 2. remove the browser caches from all history
git filter-repo --path app/artifacts/profiles --invert-paths --force

# 3. verify nothing sensitive survived anywhere in history
git log --all -p | grep -iE "rzp_test|rzp_live|nvapi-|sk-or-v1|AIza" ; echo "exit=$? (1 = clean)"

# 4. also add these to .gitignore first
printf '\n# browser automation state\napp/artifacts/profiles/\napp/artifacts/**/*.png.tmp\n' >> .gitignore

# 5. push (filter-repo already rewrote the remote; force is expected)
git push --force origin main
```

`filter-repo` **rewrites history** — take a backup first:

```bash
cp -r ../buildathon ../buildathon.backup-$(date +%F)
```

If you would rather not force-push, the untracking already done removes the
files from `HEAD`; only the historical blobs remain. That is acceptable if
the key itself has been rotated.

---

## 8. Video: reorder the beats

The script is good. Only the **order** is wrong — it currently buries the
strongest proof behind 3.5 minutes of architecture.

| Time | Beat | Change |
|---|---|---|
| 0:00–0:20 | Cold open | Keep. Add: *"…and 47 times, real money actually moved."* |
| 0:20–0:50 | **Transactability scoreboard** ← **MOVE UP** | 82.5% / 0 violations. This is your best number; lead with it. |
| 0:50–1:30 | One AI buyer paying, live or recorded | Keep |
| 1:30–2:10 | Architecture in 40 s | Keep, trimmed |
| 2:10–3:20 | **Failure choreography, 4 acts** | Keep — this is the rubric's "one failure handled gracefully" |
| 3:20–4:00 | **The venue finding** ← **NEW** | 79.3% → 12.7% → 100% (datacenter → residential → datacenter), z = 7.64, p = 2.1e-14. Your most original result. **Not** 0%→23%→14% — that one was withdrawn, see §4b. |
| 4:00–4:35 | The null, honestly | Reframe as a finding (§4a). Cut this beat first if you are over time — it is in the README, the venue reversal is not. |
| 4:35–5:00 | Close | Keep. End card: repo + `/audit/recent` showing `chain_ok: true` |

Burn in captions. Show the **TEST MODE** badge. Never show the dashboard
with key fields visible.

---

## 9. Scorecard

| Criterion | Now | Ceiling | What closes it |
|---|---|---|---|
| **Problem taste** | 7/10 | 9/10 | Lead with transactability, not the null; name the risk-engine finding |
| **Build quality** | 9/10 | 9.5/10 | Purge the 13 MB of browser cache; commit the frozen analyzer output |
| **AI judgment** | 8/10 | 10/10 | Make the deliberate non-use of AI explicit (§4d) |
| **Failure recovery** | 9/10 | 10/10 | `WHAT-BROKE.md` as a first-class document |
| **Presentation** | 5/10 | 9/10 | Pitch-first README, embedded screenshots, badges, mapping table |
| **Hygiene** | 4/10 | 9/10 | Rotate keys, purge history, finish or delete the checklist |

Nothing here needs new engineering. It is one reframe, one new document,
one README rewrite, one history purge, and one video.
