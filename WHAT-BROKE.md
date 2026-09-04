# What broke, and how we got out

> The application form's last question. Razorpay says: *"The last one is the
> one we read first."* So this is written to be read first.

---

## The 150-word version (paste this in the form)

> Six things broke. (1) A webhook crashed production because a table was
> missing — schema now self-heals at startup. (2) Razorpay's risk engine
> started hCaptcha-ing every automated checkout ~15 trips in; we refused to
> solve captchas programmatically, so we built a typed `risk_challenged`
> outcome, abandoned cleanly, and backed off like a human. (3) Gemini's
> free tier died mid-run twice, then NVIDIA NIM stalled at 41% failure —
> we added a circuit breaker and finished on OpenRouter. (4) A Windows
> process-kill bug left **three measurement runners alive at once**, so arm
> tags diverged from the arms sessions actually experienced. Our own
> hash-chained audit ledger caught it mechanically: we wrote a verifier
> that admits a row only if no arm flip exists between session start and
> payment capture. It voided 8 of 13 rows. We deleted nothing and reported
> the null. (5) Sessions hung for 8.7 hours — the crash-guard caught
> exceptions, not hangs; we added a watchdog. (6) The mock bank changed
> under us mid-project. Every fix is in the repo with the timestamp.

---

## The long version

Everything below happened against **live infrastructure** — a real
storefront on a public domain, real Razorpay test-mode APIs, real money
events. Nothing was staged.

### 1. The webhook that crashed production (Day 1)

A genuine `payment.captured` event — the exact event the whole system exists
to handle — arrived at `/webhooks/razorpay`, hit a `webhook_events` table
that didn't exist yet in an older database, and 500'd.

It was invisible for a while because the *only* thing that had exercised
that path was us. The fix was not a patch, it was a posture change:
`main.ensure_schema()` now migrates at startup. A payments service that
cannot survive receiving a payment is not a payments service.

**Lesson:** self-healing schema beats a migration you remember to run.

---

### 2. The risk engine changed its mind about us (Day 2–3)

Netbanking checkouts passed silently on day one. From day two, **every**
automated authorize drew an interactive hCaptcha. We tested four
configurations — headless Chromium, real Chrome headless, headed under Xvfb
on a fresh-IP VM, persistent browser profiles with stable persona identity.
All challenged.

It was a server-side change, most likely a velocity flag on the test key
after ~15 automated checkouts.

**We did not solve the captcha.** Defeating a fraud control to make a demo
look better is the wrong answer, and at a payments company it is the
disqualifying answer. Instead:

- the driver *detects* the challenge and abandons cleanly;
- the outcome becomes a typed, counted `risk_challenged` record;
- backoff is 30–75 s with jitter — what a human would do;
- fresh keys reset the velocity flag (documented in `research/08`).

Turning a blocker into a measured variable is what produced our strongest
result — but **not the one we first claimed.**

#### 2b. The finding we published, tested, and had to withdraw

For most of the build we reported an **escalation finding**: the challenge
rate climbs with sustained agent volume (0% → 23% → 14% across consecutive
thirds of the clean run; ~90% in a later high-frequency batch). It was in
the README, the sprint plan and the pitch paragraph.

Then, on the last day, I tested it *before* building the demo around it.
It did not survive:

```
homogeneity chi-square = 3.04, df = 2, p = 0.22
Cochran-Armitage trend z = 0.00, p = 1.00
```

Five challenges across 38 gate-reaching sessions. The first segment's 0/13
has a 95% upper bound of **23.1% — exactly the second segment's point
estimate.** The pattern I was calling escalation is what a constant 12.5%
rate looks like when you slice 40 sessions into thirds. And the ~90%
figure came from a *different* run on a *different network*, which I had
quietly folded onto the same curve as if it were a later point on it.

I had read noise as signal because the shape matched the story I already
believed. That is the failure worth naming: **the instrumentation was
fine. The interpretation was not, and nobody was checking it** — until a
task that required *using* the claim forced a test.

So I went looking for the variable the data could actually carry, and
recovered the VM2 evidence I had never pulled into the repo. It was
**venue**, and it is a much stronger result:

| Phase | Network | Challenge rate |
|---|---|---|
| P1 | Azure datacenter IP | 79.3% (23/29) |
| P2 | residential IP | 12.7% (7/55) |
| P3 | Azure datacenter IP, resumed | 100% (20/20) |

```
datacenter (P1+P3) vs residential: z = 7.64, p = 2.1e-14
```

The reversal is what makes it evidence instead of correlation: any
monotone story (key ageing, accumulating bot history) predicts P3 ≥ P2,
and we observe the opposite sign. **Agentic traffic does not scale for
free — and *where it comes from* matters more than how much of it there
is.** We would never have found that if we had quietly solved the captcha
and moved on, and we would not have found it either if I had not gone
back and tested the claim I was already proud of.

Full correction: `research/10 §1.1`. Reproduce:
`python app/scripts/risk_venue_report.py`

---

### 2c. The statistic I was reporting was not the statistic I preregistered

**Found 2026-08-30, the day before submission, by re-deriving a number by
hand instead of trusting it.**

The README's A/B table showed group means of ₹477.90 and ₹562.90 — a
difference of ₹85.00 — next to a reported difference of −₹241.45. That
mismatch should not exist if the reported figure is the difference of the
means. My first assumption was that I had copy-pasted a stale number. I
was wrong, and the actual bug was worse than a typo.

`bootstrap_ci()` in `app/exp/analysis.py` computed its point estimate
like this:

```python
obs = _stratified_diff(t, c, t_idx, c_idx, rng)   # rng -> RESAMPLES
```

The preregistration defines the estimand as **T̄ − C̄ stratified by
persona**, with the bootstrap used only to build the *interval*. But the
code passed the random number generator into the point-estimate call, so
`obs` was a **single bootstrap draw** — one sample from a distribution
centred on −₹83.03, with a spread of hundreds of rupees. We had published
−₹241.45, which is noise with a decimal point.

Two things made it hard to see, and both are worth naming:

1. **The number looked plausible.** It had the right sign, the right
   order of magnitude, and two decimal places. Nothing about it shouted
   "random draw."
2. **The unit test could not catch it.** The test asserted
   `obs ≈ mean(T) − mean(C)`. It passed every time — because the
   fixture's persona strata each contained exactly one session per arm,
   and `rng.choice()` on a one-element list is the identity function. The
   resample silently equalled the plug-in. The test was green and
   vacuous at the same time.

The fix separates the two concepts explicitly — `stratified_diff()` is
the deterministic plug-in estimator, and `_stratified_diff(..., rng)`
resamples — and the regression test now uses **multi-session strata with
within-persona variance**, plus a hand-computed check of the stratified
weighting:

```
point estimate IS the plug-in statistic, not a bootstrap draw
plug-in is deterministic across calls
plug-in falls inside its own bootstrap interval
stratified weighting matches hand computation
```

**Corrected: −₹83.03, CI [−₹294.34, +₹131.55], p = 0.486.**

The verdict did not change. The interval already contained zero and the
permutation p was already 0.486 — and in fact the corrected point
estimate now equals the permutation test's observed statistic exactly,
which it did not before. That disagreement between two numbers in the
same report was the tell I should have caught earlier.

The lesson I would actually defend: **a green test suite is not evidence
that an estimator is correct.** It is evidence that the estimator agrees
with itself on the cases someone thought to write down. The check that
found this was me subtracting two numbers in a README with my fingers.

Reproduce: `python app/exp/analysis.py ../evidence/sessions_vm2_prereg.jsonl`

---

### 3. The measurement bug our own audit trail caught (the important one)

This is the failure I would want a payments engineer to read.

Windows: stopping a monitor pipe does **not** stop the process tree it
launched. Three restarts stacked **three concurrent measurement runners** —
launched 11:01, 11:23 and 11:26 — instead of replacing one.

Why that is fatal: the A/B switch sets **global** merchant state before each
session. Concurrent runners interleave their flips with other runners'
sessions, so a session's *arm tag* can disagree with the arm it actually
experienced. That is silent, undetectable-by-inspection corruption of the
primary endpoint. It is also exactly the class of error that produces a
confident, wrong, publishable number.

What we did:

1. **Enumerated every surviving process** (`Win32_Process`) and killed all
   three trees — 12 PIDs — including browser and driver orphans.
2. **Snapshotted the evidence** before touching anything. The original file
   stayed byte-identical.
3. **Forensics by pricing signature** — in treatment, masala chai is
   ₹211.65 vs ₹249 base, so an order's price reveals the arm it really saw.
   That found two proven mislabels, including one where a flip landed
   *between planning and payment capture*.
4. **Then something better.** The merchant's own ledger already logs every
   `experiment.arm_switch` with a timestamp — 78 of them — and the orders
   table holds each payment's exact capture time. So we wrote
   `scripts/verify_arm_integrity.py` with one mechanical rule:

   > a paid row is admitted **iff** no opposing arm switch exists in
   > `[session_start − 2s, payment_capture + 1s]`

   The ledger test proved *strictly stronger* than price forensics — it
   voided two rows that had passed the pricing check, because flips landed
   seconds before capture. **Result: 5 admitted, 8 voided**, each printing
   the offending switch.

5. **We deleted nothing.** Voided rows stay on disk, excluded at merge
   time. The experiment went on to report a **null** — and the integrity
   incident is part of why that null is trustworthy.

The reason this story matters: we built a tamper-evident audit ledger to
govern AI money actions, and the first thing it caught was **our own
instrumentation lying to us**. That is the whole argument for the project,
proven on ourselves by accident.

Hardening that shipped before the relaunch, all of it now standard:
- per-session watchdog (`--session-timeout 600`) — a hung session records
  `hang-NNN` and the run continues;
- per-session duration in stdout, so stalls are visible immediately;
- a single-instance lock directory — a second launch **refuses** instead of
  stacking;
- the launcher takes the session budget as an explicit argument rather than
  recounting a possibly-corrupt file.

---

### 4. Sessions that hung instead of crashing

Twice, the fleet produced nothing for hours — once for **8.7 hours** — while
the processes stayed alive. The per-session crash-guard was working exactly
as designed: it trapped exceptions. A hang throws no exception.

Fixed with a watchdog timer. Recorded as `infra_error`. The general lesson
generalises: *a guard that catches errors is not a guard against silence.*
Liveness needs a clock, not a try/except.

---

### 5. Three LLM providers died mid-experiment

Gemini's free tier expired twice (~50 calls/day against a ~240-call
requirement — structurally insufficient, not bad luck). NVIDIA NIM then
stalled at a 41% failure rate, with one 25-minute total-stall cluster that
correctly tripped the circuit breaker. We finished on OpenRouter's
zero-priced tier.

Because the run was **preregistered**, none of this was fatal — the
alternation balances provider drift across arms, and every switch is in the
deviation log with a timestamp. What made it survivable was designed in
beforehand: a provider-swappable LLM adapter, and a circuit breaker that
**aborts after three consecutive failures** instead of retry-spamming a
free tier into a blacklist and recording the rubble as data.

---

### 6. The driver rotted against a live flow it did not own

Four separate breakages, all the same genus — the integration was correct
when written and wrong later:

- `wait_until="networkidle"` never settles because fraud beacons hold
  sockets open → switched to `load` + explicit timeout;
- a stale hCaptcha frame kept dead "Please try again" text long after the
  flow had moved to the bank page, producing a false-positive challenge →
  the driver now checks **flow progress**, not widget text;
- **the mock test bank changed under us** from auto-complete to requiring an
  explicit "Success" click → the driver now handles both;
- a race between the "Processing your payment" overlay and the bank-pick
  click ate ~6% of attempts → caught, and deliberately *not* fixed
  mid-run, because restarting to ship a 6% fix was costlier than the
  exclusions it would prevent. Activated on the next relaunch.

---

### 7. Small ones worth naming

- **Thinking tokens share `maxOutputTokens`.** Burned us twice: once
  truncating agent plans, once making a health probe return *empty* content
  that looked like an outage. Raise the ceiling.
- **Copying a WAL database by file copies a stale tail.** The tamper demo
  initially "broke" the chain in the wrong place. Use `sqlite3.backup()`.
- **`gemini-flash-latest` currently thinks >45 s** on trivial prompts. Pin
  model versions; aliases are not stable contracts.

---

## What I'd tell the panel

The failures were not interruptions to the project. They were most of its
value. The captcha wall became a measured finding about agentic traffic and
fraud controls. The multi-runner corruption became the strongest possible
demonstration of why an append-only, hash-chained ledger is worth building
— because it caught a lie my own code was telling me, and caught it
mechanically rather than by someone noticing.

And the experiment returned a **null**. We reported the null. A growth
experiment that "works" because you picked the metric after seeing the data
is worth nothing to a payments company. An auditable null is worth a lot.

**Everything above is timestamped in `MEASUREMENT-DAY.md`, `DAY3.md`, and
the git history. Voided records were preserved, not deleted.**
