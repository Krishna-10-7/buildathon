# 10 — Key rotation, the risk gate, and how to stop doing it daily

Written 2026-08-29. **Corrected 2026-08-31 — read §1.1 first; the
escalation claim I originally made here does not survive testing.**

Answers: *"I have to change the Razorpay test API key every day or I get
captcha errors. Can we fix this?"*

**Short answer: yes — but not by defeating the captcha. By not needing
live payments any more.** The daily rotation exists to keep one endpoint
alive (`/demo`). Decouple that endpoint and the chore largely disappears.

---

## 1. What is actually happening

Razorpay's test-mode risk stack (Checkout JS + hCaptcha + Sardine
device/behavioural signals) is **stateful and velocity-keyed**. Traffic
accumulates a reputation and, past a threshold, the engine stops
auto-verifying and starts challenging. That much is solid.

What is *not* solid is the story I first told about it. See §1.1.

### 1.1 CORRECTION — the "escalation over the run" claim is withdrawn

This document, `FINAL-SPRINT-PLAN.md` and the pitch paragraph all quoted
this table as a finding:

| Segment | Challenge rate |
|---|---|
| first third | 0% (0/13) |
| second third | 23.1% (3/13) |
| third third | 14.3% (2/14) |

I tested it before building the demo around it. **It does not survive.**

```
homogeneity chi-square = 3.04, df = 2, p = 0.22
Cochran-Armitage trend z = 0.00, p = 1.00
```

Five challenges across 38 gate-reaching sessions cannot distinguish
"escalating" from "constant 12.5%". The first segment's 0/13 has a 95%
upper bound of 23.1% — **exactly the second segment's point estimate.**
I was reading noise as signal because the shape looked like the story I
already believed.

The ~90% figure in the original note came from a *different* run under
*different conditions* (the Azure datacenter IP), not from a later point
on the same curve. Conflating the two was the actual error.

### 1.2 What the data does support — venue, not the calendar

Once the VM2 evidence was recovered and joined to the laptop evidence,
the variable that actually carries the signal is **where the traffic came
from**. Same code, same merchant, same Razorpay key:

| Phase | Network | Challenge rate | 95% CI |
|---|---|---|---|
| P1 (before) | Azure datacenter IP | **79.3%** (23/29) | [61.6, 90.2] |
| P2 (during) | residential IP | **12.7%** (7/55) | [6.3, 24.0] |
| P3 (after) | Azure datacenter IP | **100%** (20/20) | [83.9, 100] |

```
datacenter (P1+P3) vs residential (P2):  z = 7.64,  p = 2.1e-14
```

That is an **A-B-A reversal**, and the reversal is what makes it evidence
rather than a correlation. Any monotone explanation — key ageing,
accumulating bot history, "the engine gets stricter over time" — predicts
P3 ≥ P2. Observed: P3 ≫ P2, in the *opposite* direction. The effect
tracks the venue flip, not the calendar; the calendar only moved forward,
and it moved the wrong way.

Reproduce with `python scripts/risk_venue_report.py`. Limitations are
listed in that file's docstring and are not optional reading: venue is
confounded with clock time and with host, and no session record stores
the key id, so "the key did not rotate between phases" rests on
`MEASUREMENT-DAY.md`'s log rather than on the data.

**The consequence to internalise:** the binding constraint on agentic
traffic is *reputation of origin*, not just volume. A fresh key buys you
roughly the first ~10–13 checkouts, and a residential origin buys you far
more than a fresh key does. Plan around it instead of fighting it.

---

## 2. What does NOT work

| Idea | Verdict |
|---|---|
| Solving / bypassing the captcha | **Never.** Defeating a fraud control is disqualifying for a payments company, and it is the one thing that would sink this submission ethically. The driver already abandons cleanly and records `risk_challenged`. Keep doing that. |
| **Server-to-server payment API** (`POST /v1/payments/create/json`) | **Ruled out — tested 2026-08-29.** Returns `400 BAD_REQUEST_ERROR "The requested URL was not found on the server"` on this account, for both UPI collect and UPI intent. Orders create fine, so auth is good; the endpoint is PCI-DSS-gated and not enabled here. Re-test with `scripts/probe_s2s_payment.py` if the account is ever upgraded — it would end the problem outright. |
| Rotating keys faster / automatically | Works mechanically, but it treats the symptom and burns a key per session batch. Fine as automation (§4.4), not as the strategy. |
| Proxy / IP rotation to dodge reputation | Evasion-adjacent. **Pacing and venue hygiene** are legitimate; rotating IPs to look like different people is not. |

---

## 3. The actual fix — stop requiring a live payment

### 3.1 Why this is the fix

The measurement is **frozen**. You have:

- n=94 preregistered sessions, analysed and reported
- **47 unique paid orders** with webhook-captured payments
- 674-record audit ledger, verified intact
- a 40-session clean run at 82.5% completion

**You do not need another payment for evidence.** Nothing in the
submission requires a fresh checkout. The only thing forcing daily
rotation is `/demo` performing a live trip for whoever presses START.

### 3.2 Decouple `/demo`

Make `/demo` default to **replaying a verified real trip**, with live
mode as an explicit opt-in button.

A replay is not fake evidence. It shows:

- the **verbatim LLM reasoning** from the recorded session
- the **real order id** and **real payment id**
- the **real ledger records** the trip produced
- and it still ends honestly — including the trips that were
  `risk_challenged`

Every one of those is a real artifact already on disk in
`app/artifacts/*.jsonl`. The difference is that the outcome is
*pre-determined by history* rather than *re-negotiated with the risk
engine* every time a judge clicks.

**Result:** `/demo` becomes 100% reliable, needs zero key rotation, and
loads in ~2 seconds instead of ~90.

### 3.3 Keep the live path, but make it honest about its state

Do not delete live mode — it is the strongest single demo beat when it
works. Instead:

- Show the key's **current challenge rate** next to the START button
- If the key is "warm" (challenge rate above ~30%), say so on the page
  and offer the replay by default
- This turns your infrastructure weakness into **your Finding 2**,
  demonstrated live rather than asserted in a doc

---

## 4. Supporting measures

### 4.1 Budget the key

Fresh key ≈ 10–13 clean checkouts. So:

- **Never run the fleet on the same key you demo with.** Two key slots:
  a *demo key* kept cold, and a *fleet key* you burn and rotate.
- Cap live demo checkouts at **≤3/day on the cold key**.
- Space them **hours apart**, not minutes.

### 4.2 Use the correct test instruments

The `International cards are not supported` error is a symptom of using
the wrong test cards. Razorpay's Indian test cards are:

- Mastercard `5120 4333 9011 9037`
- Visa `4628 9499 7226 2986`

(random CVV, any future expiry). Worth one experiment: the card flow may
carry a different risk profile than netbanking, and it is a shorter
driver path.

### 4.3 Venue matters more than anything else

Measured: Azure datacenter IP → ~90% challenge under load; laptop
residential IP → near zero on a fresh key. Run anything that must pay
from **residential**, never from the datacenter.

### 4.4 Automate the rotation

Rotation still has to happen for the fleet key. Make it one command —
see `app/scripts/rotate_keys.sh`. It pushes the new key to laptop + VM1 +
VM2, restarts `bazaar.service` and `bazaar-town.service`, and verifies
`/healthz`, the webhook HMAC (old rejected / new accepted) and that
`MANDATE_SECRET` is byte-identical before and after. Turns a 30-minute
chore into 30 seconds, and rolls back automatically if anything fails.

    bash app/scripts/rotate_keys.sh --status     # fingerprint drift
    bash app/scripts/rotate_keys.sh --selftest   # escaping is safe
    bash app/scripts/rotate_keys.sh              # interactive rotation

It hard-fails if `MANDATE_SECRET` is empty anywhere, because
`mandates.py` derives the signing key as
`MANDATE_SECRET or f"{RZP_KEY_SECRET}:mandates-v1"` — with it empty,
rotating the key secret would silently void every signed mandate.

**Operational rule the script cannot enforce for you:** after running it,
update the webhook secret in Razorpay Dashboard → Settings → Webhooks to
match. Inbound events will 400 until you do.

### 4.5 Ask Razorpay

You are in **their** buildathon. Ask in the buildathon channel for test
keys with risk challenges relaxed for agentic testing. This is a normal
request, they want the submissions to work, and it costs one message.

---

## 5. Turning the pain into an asset

The escalation is currently an operational annoyance. It is also the
most original finding in the project:

> **Fraud controls are stateful. Agentic traffic makes them stricter, not
> stable. Agentic commerce does not scale for free.**

Make it visible:

1. Add a `/risk` surface (or a Control Tower panel) tracking challenge
   rate over a rolling window, per key and per venue.
2. Alert above a threshold — "this key is warm, rotate or switch to
   replay".
3. Frame it in the Razorpay product vocabulary: it belongs under
   **Advanced Risk & Compliance**, and the product gap it implies is
   **agent reputation / allow-listing** — agents need stepped-up vs
   frictionless lanes the way 3-D Secure does for humans.

A judge who watches your demo fail on a captcha sees a broken demo. A
judge who watches your dashboard *predict* the captcha sees someone who
understands payments.

---

## 6. Decision log

- **2026-08-29** — Probed `POST /v1/payments/create/json` (UPI collect +
  UPI intent). Not enabled on this account. Recorded in
  `scripts/probe_s2s_payment.py`.
- **2026-08-29** — No captcha was solved, proxied or bypassed at any
  point in this investigation. Every probe ends in a clean, typed
  abandon.
