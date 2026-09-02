# Final sprint plan — 5 days to submission

Written 2026-08-30, after reading five competitor READMEs rather than
guessing from search metadata. Read `research/11-track01-winning-edge.md`
for the full competitive analysis; this is the schedule.

---

## 0. Honest verdict first

**You are in the top tier. You are not clearly #1, and three rivals have
already closed gaps I had flagged as open.**

I said in `research/11` that "mandate-bounded spend is commodity." Having
now read the actual READMEs, that was an understatement. The top of this
field is *good*.

### What the top rivals actually have

**`biru-codeastromer/maryada`** — the strongest direct competitor.

- Mandates with the same feature set as yours (budget, per-txn cap,
  merchant allowlist, category scopes, **velocity limit**, validity window,
  step-up threshold, revocable)
- Deterministic policy engine, no LLM in the money path
- Verified catalogs — hallucinated SKUs and drifted prices die at the gate
- **Atomic budget holds with exactly-once settlement** — the hold is taken
  in the same DB transaction as the decision, so there is no check-then-spend
  race
- Tamper-evident chain, **Ed25519-signed**, with `verify-audit` naming the
  exact broken entry
- `make demo` — eleven scenes, **fully offline, no keys, no network**
- **168 tests**, a CI badge, a `docs/assets/demo.gif`
- `make gauntlet` — **sixteen rogue-agent attacks, scored black-box over HTTP**

**`Purvee25/sentinel-ap2`** — the best-presented one, and it already did the
thing I called your biggest open hole.

- A product description containing an instruction telling the agent to
  ignore its spending limits. It failed on arithmetic —
  `29995000 > 150000` — *without anything having read the text.*
- The agent can only send `{product_id, qty}`, never a price. Elegant.
- Ed25519 mandate signatures
- A genuinely excellent ASCII results table: 5 attempts, 4 blocked, ₹899
  charged, each rejection dying at a different named check

**`saivigneshpandian/ai-buyer-firewall`** — two-layer (deterministic +
LLM for the grey area), three verdicts: allow / step-up / block.

**`jboiie/argus`** — Open Track, not Track 01, but has a red-team harness,
a drift sentinel, and a five-tab React dashboard with screenshots.

### Where you win — and they structurally cannot catch you

| You have | Why it is unreachable for them |
|---|---|
| **51 real paid orders**, 55 payment rows, 106 webhook events, 686 audit records | Every rival demo I read is a guardrail that *blocks* things. maryada's headline demo runs "fully offline, no keys, no network." sentinel-ap2 shows one session, ₹899 charged. **None of them moved money at fleet scale.** |
| **The risk-gate venue study** (79.3% → 12.7% → 100% as the fleet moved datacenter → residential → datacenter; pooled datacenter 43/49 = 87.8% vs residential 7/55 = 12.7%; z = 7.64, p = 2.1e-14) | Requires running the same buyer on two networks at fleet scale, with the venue flipped twice. Unobservable if you simulated the payment, and unreachable for anyone who only ever ran on one network. |
| **A preregistered n=94 with a browser-driving AI buyer** against real Razorpay Checkout | Nobody else has a buyer that drives a real browser through a real risk engine. |
| **A Reserve-Pay-shaped envelope** | maryada has identical semantics but never names the rail. Naming it is free and it is Razorpay's live product. |

**This is the asymmetry that decides it.** Everything they beat you on is
closable in four days. Everything you beat them on took a week of real
traffic and cannot be faked in four days.

### Where they beat you — all three are closable

| Their advantage | The fix | Cost |
|---|---|---|
| **`make demo` with no keys, no network.** A judge can run maryada in 90 seconds. Yours needs a Razorpay key. | `/demo` **replay-by-default**, live as opt-in | 3 h |
| **Adversarial testing.** maryada: 16 attacks. sentinel-ap2: prompt injection via product description. You have none. | A prompt-injection gauntlet against your own buyer | 3 h |
| **Presentation.** demo.gif, CI badge, a results table you read in 10 seconds, 168 tests. | Screenshots + the scoreboard in the README's first screen | 3 h |

Two smaller ones worth knowing: they sign with **Ed25519** (non-repudiable)
where you use HMAC (symmetric), and maryada closes the **check-then-spend
race** explicitly. Do not chase the first — HMAC is defensible for a
single-operator system and switching is risky this late. Do state it as a
known limitation; naming your own limits scores better than hiding them.

---

## 1. What to do first

**Today, before anything else: rotate the credentials in the dashboards.**

Not because it adds points. Because a project about *governance discipline*
with a live test key id sitting in its git history is the one thing that
could actively lose you this. The redaction and history rewrite are done;
**the key was never rotated in the dashboard, so it is still live.**
`KEY-ROTATION-CHECKLIST.md` has the five providers. It is ~45 minutes of
your time, mostly waiting, and the script is ready:

```bash
bash app/scripts/rotate_keys.sh --status     # confirm the three hosts agree
bash app/scripts/rotate_keys.sh              # interactive; hides what you type
```

Do it first because it is the only task that can *subtract* points, and
because it unblocks the honest claim "no live credential in this repo."

**Second, done already:** the transactability number. I ran it just now —

```
CLEAN RUN — sessions_laptop2.jsonl                        n=40
  end-to-end completion        33/40   82.5%
  reached gateway              38/40   95.0%
  completed payment of those   33/38   86.8%
  captured revenue             Rs 25,724.15
  overspend violations            0    0.0%
  mean budget headroom kept   17.8%    (tightest 0.1%)
  finished first attempt      28/40   70.0%

RISK-ENGINE VENUE STUDY
  P1 datacenter IP   23/29   79.3%
  P2 residential IP   7/55   12.7%
  P3 datacenter IP   20/20  100.0%
  datacenter vs residential:  z = 7.64,  p = 2.1e-14
```

That is your opening line. It is positive, it is measured, and it answers
the half of the brief nobody else answers.

> **CORRECTION 2026-09-01 — do not use the numbers this section used to
> print.** It originally opened with an "escalation over the run" table
> (0% → 23.1% → 14.3% across thirds) and told you to lead with it. That
> claim was **tested and withdrawn**: homogeneity chi-square p ≈ 0.22,
> Cochran-Armitage trend p ≈ 1.00, five challenges across 38 sessions. The
> table above replaces it with the venue study, which does survive. If you
> have already internalised the escalation line, unlearn it before you
> record — a judge who reads `README.md` §Finding 2b will find the
> retraction, and a pitch that contradicts its own repo is worse than one
> that simply claims less.

---

## 2. The five days

### Day 1 — unblock, and make it runnable without keys

| # | Task | Who | Time | Done when |
|---|---|---|---|---|
| 1 | Rotate all five credentials in the dashboards | **you** | 45 m | Checklist ticked and dated |
| 2 | Transactability number | done | — | 82.5%, ₹25,724.15, 0 violations |
| 3 | `/demo` **replay-by-default**, live as opt-in | me | 3 h | `/demo` loads and completes a full trip with no Razorpay key configured |

Task 3 is the counter to maryada's `make demo`. It also *removes the daily
key rotation chore*, because a replay needs no live key
(`research/10` §3.2). One change, two wins.

**Feasibility checked 2026-08-30 — this is easier and stronger than it
looks.** The session JSONL alone is not enough (it holds no Razorpay ids and
no transcript), but it joins to the authoritative database perfectly:

```
sessions_laptop2.jsonl.order_id  ->  VM1 orders.id  ->  rp_order_id
                                                    ->  payments.rp_payment_id
```

Sampled 6 paid sessions, **6/6 joined**, each returning a real
`order_TTu…` and a real `pay_TTu…` with the real captured amount:

```
ord_a04c004009084b -> order_TTuZvwGbQFT8Sn  pay_TTua8S5ErnCwZV   34065p
ord_38ded371f7524b -> order_TTuc6206Gn5gti  pay_TTucGD76qOxnxT  129700p
ord_39b4858d8ed346 -> order_TTujksrzMrW31N  pay_TTujv6u3gx56Ra  149900p
```

So the replay is not a mock. It shows the **real order id, the real payment
id and the real amount**, plus the buyer's real recorded `analysis` text and
the real ledger rows.

**Build it as a committed fixture, not a live DB read.** Generate
`app/artifacts/replay_fixture.json` once by joining the JSONL against the
DB, commit it, and have `/demo` read the file. Then `/demo` needs no keys,
no network and no database — the same property maryada gets from
`make demo`, with the difference that every id on screen is real.

### Day 2 — close the gaps rivals already closed

| # | Task | Time | Done when |
|---|---|---|---|
| 4 | **Prompt-injection gauntlet** against your own buyer | 3 h | A hostile product description, a hidden instruction in a bundle name, and a price-drift attempt — each refused, with the reason code, in a committed results file |
| 5 | Challenge-rate counter on `/demo` + a `/risk` panel | 3 h | The demo shows the key's live challenge rate; the page *predicts* the captcha instead of being surprised by it |

Task 4 is no longer optional. Two rivals have it and it is the cleanest
possible answer to the **AI judgment** criterion — the injection fails on
arithmetic, not on content filtering.

Task 5 turns your infrastructure weakness into your Finding 2, demonstrated
rather than asserted.

### Day 3 — claim the empty ground

| # | Task | Time | Done when |
|---|---|---|---|
| 6 | Rename mandate → **"UPI Reserve Pay envelope"** in the demo; add a live over-limit rejection and a live revocation | 3 h | The demo shows the envelope refusing, on screen, with the rail named |
| 7 | README: embed `artifacts/*.png`, put the scoreboard in the first screen, add the gauntlet results, add a "known limitations" section | 3 h | A judge gets the whole pitch in 30 seconds without scrolling |

### Day 4 — record

| # | Task | Time | Done when |
|---|---|---|---|
| 8 | Dry-run `DEMO-VIDEO-SCRIPT.md`, then **record** | 4 h | A take that is under 5:00 and covers every beat |

**Record on day 4, not day 5.** It is a required form field, and takes go
bad. Day 5 is for re-recording, not for the first attempt.

### Day 5 — submit, with slack

| # | Task | Time |
|---|---|---|
| 9 | Fill the form's 12 fields; final `git push`; re-read `WHAT-BROKE.md` out loud | 2 h |
| 10 | **Buffer. Do not schedule anything here.** | rest |

---

## 3. If you run out of time, cut from the bottom

In order, safest to drop first:

1. Task 7's README screenshots (keep the scoreboard text)
2. Task 5's full `/risk` panel (keep the counter on `/demo`)
3. Task 6's revocation demo (keep the over-limit rejection)

**Never cut:**

- Task 1 — credential rotation. The only task that can subtract points.
- Task 3 — replay mode. maryada ships `make demo` with no keys.
- Task 4 — the gauntlet. Two rivals have it; it is the AI-judgment answer.
- Task 8 — the video. Required field. No video, no submission.

---

## 4. The one paragraph to land in the video

> We let an AI buyer loose on a real store with real money and watched
> what happened. In the headline run of 40 sessions it completed a real
> payment 82.5% of the time and never once broke its budget. Then we
> found the thing nobody predicts: the same buyer, same code, same key,
> was blocked at the fraud gate **79%** of the time from a datacenter IP
> and **13%** of the time from a residential one. We only believe it
> because the venue flipped twice — 79%, then 13%, then 100%. Where an
> agent pays from decides whether it can pay at all.
>
> And one correction we made ourselves: we first thought the gate got
> stricter the more we transacted. We tested it. It didn't — five
> challenges across forty sessions, p greater than 0.2. We withdrew the
> claim and went looking for the variable the data could actually carry.
> That one was real.

That scores Problem taste, AI judgment and Failure recovery in thirty
seconds, and none of the four rivals can say it. The second half is the
more valuable half: a submission that retracts its own finding is making
the exact judgement the rubric asks for, and it is unfakeable.
