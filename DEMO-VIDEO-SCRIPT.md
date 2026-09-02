# DEMO VIDEO SHOOTING SCRIPT

**Target length:** 4–5 minutes. **Recording:** OBS (free) at 1080p, mic on.
**Rule:** every claim on screen must be backed by something visible — a URL,
a command output, or a ledger record. No slideware claims.

**Timing budget — hard 5:00 ceiling, and the video is currently planned to
fill it exactly.** Rehearse with a stopwatch before you record.

| Shot | Window | Budget | If you are over |
|---|---|---|---|
| 0 cold open | 0:00–0:20 | 20 s | — |
| 1 architecture | 0:20–0:55 | 35 s | cut the persona-fleet sentence |
| 2 money shot | 0:55–1:45 | 50 s | drop the dashboard cross-check |
| 3 choreography + envelope | 1:45–3:20 | 95 s | cut act 3 (the clamp is also visible on `/control`) |
| 4 experiment | 3:20–4:35 | 75 s | **cut the A/B null, keep the venue reversal** |
| 5 close | 4:35–5:00 | 25 s | — |

Two beats were added on 2026-09-02 (the live envelope at ~25 s, the venue
reversal at ~35 s) and two were trimmed to pay for them. If you must cut,
cut in the order in the last column — never the venue reversal, which is
the only finding in this submission nobody else has.

**Pre-shoot checklist (15 min):**
- [ ] `curl https://r2-d2.xyz/healthz` → ok
- [ ] `curl "https://r2-d2.xyz/audit/recent?limit=1"` → `chain_ok: true`
- [ ] Terminal open on laptop, ssh alias working (`ssh myserver`)
- [ ] Browser tab preloaded: `https://r2-d2.xyz` (storefront) and `/control` (Control Tower)
- [ ] Razorpay dashboard tab logged in, TEST MODE badge visible
- [ ] Shot 4 numbers re-verified against a fresh run (see Shot 4) — the
      analysis was corrected on 2026-09-02, so do not reuse an old figure
      you remember from memory

**Verify the three live beats the morning of the shoot** (all four commands
must pass — if any fails, that shot gets its fallback):

```bash
# Shot 2 — the order + its audit trail (must print Rs340.65 + chain_ok=True)
ssh myserver 'cd ~/bazaar/app && ./.venv/bin/python scripts/show_order.py ord_9447818176414f'

# Shot 3 — the envelope page (must return 200)
curl -s -o /dev/null -w '%{http_code}\n' https://r2-d2.xyz/demo/envelope

# Shot 4 — the venue study (must show the datacenter vs residential gap)
curl -s https://r2-d2.xyz/demo/api/risk | head -c 200

# Shot 5 — the chain verdict on the Control Tower
curl -s https://r2-d2.xyz/audit/recent | head -c 120
```

---

## Shot 0 — Cold open (0:00–0:20)

**Screen:** storefront homepage.
**Say:**
> "AI agents are about to buy things on the internet. But would you let one
> spend your money? This is Bazaar — a merchant store built for agentic
> commerce, where every money-moving action is explainable, bounded, gated,
> and permanently audited. Watch."

---

## Shot 1 — The architecture in 35 seconds (0:20–0:55)

**Screen:** README diagram section (or whiteboard sketch).
**Say:**
> "Three layers. One: a real FastAPI storefront on Razorpay test mode.
> Two: a pure policy engine — agents *propose*, rules *bound* — discounts,
> bundles, stock, all clamped by POL-dash rule ids. Only a human decision
> spends a proposal; agents never touch prices directly. Three: an
> append-only hash-chained ledger — every money event seals its predecessor.
> On the buyer side, an LLM persona fleet shops the store through real
> browser sessions."

---

## Shot 2 — The money shot: an AI buyer pays end-to-end (0:55–1:45)

**Screen:** terminal + Razorpay dashboard.
**Do:** show the recorded session evidence:

```bash
ssh myserver 'cd ~/bazaar/app && ./.venv/bin/python scripts/show_order.py ord_9447818176414f'
```

Verified on camera-path 2026-09-02 — it prints:

```
ORDER
  internal id   ord_9447818176414f
  razorpay id   order_TTYaRK5oQUsyPw
  status        paid
  total         34065p  (Rs340.65)
  channel       chat
  session       ritika-a05fde41

PAYMENTS (1)
  pay_TTYc6dyZxxwWZL       captured     34065p  netbanking

AUDIT TRAIL (correlation 0974cfab53d6455c…)
     99  order.create
    100  razorpay:webhook   payment.captured
    101  razorpay:webhook   order.paid

LEDGER  chain_ok=True  records=720  first_bad_seq=None
```

Then Razorpay dashboard → Payments → search `pay_TTYc6dyZxxwWZL` → Captured
₹340.65. (Same id as the terminal — that is the point.)

**Say:**
> "Here is a completed purchase by an AI persona: it browsed the catalog,
> planned a basket inside its own budget, checked out through netbanking,
> and paid three hundred forty rupees sixty-five paise. Real order id, real
> payment id — and the moment I switch to the Razorpay dashboard, the same
> payment id, captured. Webhook-verified, and the whole story is sealed in
> the ledger: create, capture, paid, three records, chain intact."

**Why a script instead of a one-liner:** `sqlite3` is not installed on the
VM, the orders table spells money `total_paise` while payments spells it
`amount_paise`, and quoting `python -c` through ssh fumbles on camera. One
script, one argument, no quoting.

**Fallback if it fumbles:** just show the dashboard payment row +
`https://r2-d2.xyz/audit/recent` filtered to that order id.

---

## Shot 3 — Failure choreography, four acts + the live envelope (1:45–3:20)

**Screen:** full-screen terminal.
**Do:** run the one-liner and narrate over each act as it prints:

```bash
ssh myserver 'cd ~/bazaar/app && uv run python scripts/failure_choreography.py'
```

| Act | Say while it prints |
|-----|---------------------|
| 1 forged webhook | "A fake payment-captured event knocks. Garbage signature — refused with a 400, state untouched, refusal itself written to the tamper-evident ledger." |
| 2 revoked mandate | "This buyer had a five-hundred-rupee spending mandate. The principal revokes it mid-flight — the very next order attempt is refused BEFORE Razorpay is ever called. Consent is enforceable, not decorative." |
| 3 clamped ask | "An agent asks for forty percent off for a month. The engine clamps it to policy bounds and cites its rule ids. Then watch — the human REJECTS it. Nothing moved. Agents propose; humans dispose." |
| 4 tamper check | "Now the paranoid part: flip ONE byte in the stored ledger. Verification screams and points at the exact record I edited — while production still verifies clean." |

**Then — immediately after act 4, without cutting (0:25):**

Switch to the browser on `https://r2-d2.xyz/demo/envelope` and press
**BREAK IT LIVE**. Watch the rows land one at a time.

**Say:**
> "That was revocation in a script. Here it is live, and wider. This is a
> UPI Reserve Pay envelope — two thousand rupees of budget, a thousand per
> transaction, tea and spices only. A three-hundred-rupee tea order: fine.
> Twelve hundred in one go — refused, per-transaction cap. Coffee — refused,
> wrong category. The agent keeps buying, the envelope drains to five
> hundred, and now an eight-hundred-rupee order is refused: budget. Every
> one of those named its reason. And now watch the last one — I revoke the
> envelope, and I re-send the *exact* three-hundred-rupee order that
> succeeded a moment ago. Refused. Same request, same code, different
> answer — because the buyer withdrew consent. That is the difference
> between a filter on the model's output and a signed object the buyer
> controls."

> **Camera note:** the footer says "demo ledger … records, chain_ok=true.
> Separate store." Say the last sentence of that out loud — it is the
> answer to "did you just invalidate your own evidence by demoing it?"

---

## Shot 4 — The experiment: null result + the venue reversal (3:20–4:35)

**Screen:** PREREGISTRATION.md top + analysis report JSON.
**Say:**
> "We didn't just build it — we measured it. Before running anything, we
> froze a preregistration document: ninety sessions, alternating treatment
> and control, primary metric defined up front, exclusions symmetric,
> bootstrap confidence intervals. Here's the frozen plan — and here is the
> result."

**Filled 2026-09-02 from `app/scripts/risk_venue_report.py` and
`python app/exp/analysis.py app/artifacts/sessions_vm2_prereg.jsonl`.**
Re-verify both before you record:

```bash
cd buildathon/app
python exp/analysis.py artifacts/sessions_vm2_prereg.jsonl   # A/B
python scripts/risk_venue_report.py                          # venue study
```

**Say (read the numbers, do not paraphrase them):**
> "Fifty-eight analyzed sessions out of ninety-four. Treatment: four
> hundred seventy-eight rupees per session. Control: five hundred
> sixty-three. The stratified difference is **minus eighty-three rupees**,
> ninety-five percent confidence interval minus two-ninety-four to plus
> one-thirty-two — it contains zero. Permutation test: **p equals nought
> point four nine**. By the rule we froze *before* we saw any data, that's
> a null. So it's a null, and here it is."

**Then (0:15) — this is the part that wins the criterion:**
> "But look at *what* the null is made of. Conversion was flat — seventy-one
> versus seventy percent. Agents buy either way. What changed is the
> basket: attach rate nought point eight versus nought point four eight,
> average order value down. Discounts didn't make AI buyers spend more —
> they made them trade down. That's a real merchant-economics result, and
> I'd rather ship it than a narrowly-true three percent lift."

**Then the venue finding (0:20) — switch to `/demo/risk`:**
> "And the strongest thing we measured came from a failure. Our automated
> buyers kept hitting hCaptcha. We refused to solve it — defeating a fraud
> control is disqualifying behaviour at a payments company — so we recorded
> it, backed off, and measured. Same code, same key: eighty-eight percent
> challenged from a datacenter IP, thirteen percent from a residential one.
> Then we moved back to the datacenter and it went to a hundred. p below
> one in ten billion. Agentic traffic does not scale for free, and *where
> it comes from* matters more than how much of it there is."

> **Camera note:** if you have to cut one of these two beats for time, cut
> the A/B null and KEEP the venue reversal. The A/B is in the README; the
> reversal is the thing nobody else has.

---

## Shot 5 — Close (4:35–5:00)

**Screen:** Control Tower (`https://r2-d2.xyz/control`) with chain_ok true.
**Say:**
> "Bounded actions, human-gated spends, consent enforcement, tamper-evident
> history, and a preregistered measurement. This is what commerce looks like
> when you let agents in — without letting go of control. Bazaar. Built on
> Razorpay test mode, free tiers only."

---

## If-live-breaks contingencies

| Breakage | Cover |
|---|---|
| Public URL down | Run choreography with `--base http://localhost:8000` via ssh tunnel narration |
| Act fails oddly | It's a graceful-failure demo — narrate the oddity honestly, move on |
| Numbers not ready | Shot 4 becomes "measurement completes this week; here is the frozen design" |

## Post-production notes

- Burn captions for narration (judges often watch muted).
- Show test-mode badges whenever the dashboard appears — twice.
- End card: repo link + PREREGISTRATION.md + audit `chain_ok: true`.
