# DEMO VIDEO SHOOTING SCRIPT

**Target length:** 4–5 minutes. **Recording:** OBS (free) at 1080p, mic on.
**Rule:** every claim on screen must be backed by something visible — a URL,
a command output, or a ledger record. No slideware claims.

**Pre-shoot checklist (15 min):**
- [ ] `curl https://r2-d2.xyz/healthz` → ok
- [ ] `curl "https://r2-d2.xyz/audit/recent?limit=1"` → `chain_ok: true`
- [ ] Terminal open on laptop, ssh alias working (`ssh myserver`)
- [ ] Browser tab preloaded: `https://r2-d2.xyz` (storefront) and `/control` (Control Tower)
- [ ] Razorpay dashboard tab logged in, TEST MODE badge visible
- [ ] Numbers filled in below where you see `[FILL]`

---

## Shot 0 — Cold open (0:00–0:20)

**Screen:** storefront homepage.
**Say:**
> "AI agents are about to buy things on the internet. But would you let one
> spend your money? This is Bazaar — a merchant store built for agentic
> commerce, where every money-moving action is explainable, bounded, gated,
> and permanently audited. Watch."

---

## Shot 1 — The architecture in 40 seconds (0:20–1:00)

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

## Shot 2 — The money shot: an AI buyer pays end-to-end (1:00–2:00)

**Screen:** terminal + Razorpay dashboard.
**Do:** show the recorded session evidence:

```bash
ssh myserver 'cd ~/bazaar/app && sqlite3 bazaar.db \
  "SELECT order_id, status, amount_paise FROM orders WHERE order_id='"'"'ord_9447818176414f'"'"'"'
```

Then Razorpay dashboard → Payments → search `pay_TTYc6dyZxxwWZL` → Captured ₹340.65.

**Say:**
> "Here is a completed purchase by an AI persona: it browsed the catalog,
> planned a basket inside its own budget, negotiated an agent discount,
> checked out through netbanking, and paid three hundred forty rupees.
> Real order id, real payment id, webhook-verified — and the whole story is
> sealed in the ledger."

**Fallback if db query fumbles:** just show the dashboard payment row +
audit recent endpoint filtered to that order id.

---

## Shot 3 — Failure choreography, four acts (2:00–3:40)

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

---

## Shot 4 — The experiment: does growth actually grow? (3:40–4:30)

**Screen:** PREREGISTRATION.md top + analysis report JSON.
**Say:**
> "We didn't just build it — we measured it. Before running anything, we
> froze a preregistration document: ninety sessions, alternating treatment
> and control, primary metric defined up front, exclusions symmetric,
> bootstrap confidence intervals. Here's the frozen plan — and here is the
> result."

**Fill from morning run:**
> "[FILL: N analyzed sessions, net-revenue-per-session T vs C, CI, verdict
> per the preregistered rule.] And here is every raw session record —
> including our failures. We excluded risk-challenged sessions symmetrically
> and we report provider outages honestly in the deviation log. That's the
> difference between a demo and an experiment."

---

## Shot 5 — Close (4:30–5:00)

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
