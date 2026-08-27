# FAILURE RUNBOOK — the narrated graceful-failure demo

Every money pathway in this store has a failure mode it handles **on purpose**.
This is the rehearsal script for the demo video and for judge Q&A. One command
performs all four acts live against production, leaving the audit evidence
behind on purpose:

```bash
ssh myserver 'cd ~/bazaar/app && uv run python scripts/failure_choreography.py'
```

Preconditions: service healthy (`curl https://r2-d2.xyz/healthz`), test-mode
keys configured. Flags: `--base` (alternate URL), `--sku`, `--db`.

---

## Act 1 — a forged webhook knocks on the door

**What happens:** the script POSTs a fake `payment.captured` with a garbage
signature. The receiver answers **400 invalid signature**, refuses to touch
state, and appends `webhook.rejected_invalid_signature` to the hash chain.

**Narration:** "Only cryptographically signed events can move money state.
And genuine gateway retries are safe by construction: deliveries dedupe on
Razorpay's event id, payment rows key on sha256(order:payment) with INSERT OR
IGNORE — proven live on Aug 22 when a real retry after a deploy window was
absorbed without double-counting."

## Act 2 — a spending mandate revoked mid-flight

**What happens:** buyer envelope minted (₹500 cap, tea-only) → order accepted
→ principal revokes → same basket re-ordered → **403 mandate_denied,
reasons ["mandate revoked"]** — refused *before* Razorpay is ever called.

**Narration:** "Consent is enforceable, not decorative. Revocation takes
effect immediately, the refusal happens before any gateway call, and both
sides — the revoke and the denial — land in the ledger."

## Act 3 — an agent asks for too much; the engine clamps it

**What happens:** agent proposes 40% off × 30 days. If the target sku already
runs a discount, policy DENIES outright (concurrency rule) and the act
retargets — itself a graceful failure worth narrating. Otherwise the engine
**clamps** to 15% × 3 days citing POL-DISC-001/003. The human then REJECTS
the clamped proposal; catalog price unchanged.

**Narration:** "Agents cannot move prices. The engine bounds every ask, and
only a human decision spends it — here the human said no, so nothing moved.
Watch it live at https://r2-d2.xyz/control."

## Act 4 — flip one byte in the ledger, watch it scream

**What happens:** WAL-consistent snapshot via the sqlite backup API, one
character rewritten in a money payload. Verification reports
`chain_ok=False ... first_bad_seq=<that exact seq>` while production still
verifies clean end-to-end.

**Narration:** "Every record hash-seals its predecessor. Edit history and the
forgery points at exactly the record you touched."

---

## Honest footnotes

- Acts leave audit records, one revoked mandate, one rejected proposal, and
  1–2 unpaid orders behind — unpaid orders hold stock reservations until the
  release/expiry follow-up ships. Evidence, not litter.
- Amounts are Razorpay test mode; nothing real moves anywhere in this demo.
- If the public URL is unreachable, everything except Act 4's production-side
  verification runs against `--base http://localhost:8000` on the VM.

## The one-slide summary

| Pathway | Attack/failure | Behavior | Proof |
|---|---|---|---|
| Webhooks | forged capture event | 400 + audited, state untouched | `webhook.rejected_invalid_signature` |
| Buyer spend | revoked consent envelope | 403 pre-gateway + audited | `order.mandate_denied` |
| Agent actions | over-budget ask | clamped/denied by pure rules | POL-* rule ids in decision |
| History | DB tampering | chain breaks at edited seq | `audit.verify` |
