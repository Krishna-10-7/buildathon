# Governance, Audit Trail & Failure Playbook

*Written by the main agent (2026-08-22) after the delegated research stream failed twice on API timeouts. This report doubles as the BUILD SPEC for our governance layer — it encodes the exact judging bar: "Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully." Patterns are standard fintech/trust-engineering knowledge.*

---

## TL;DR — the governance design

1. **Hand-rolled policy engine** (~150 lines of Python), not OPA/Rego — a Go daemon + a new DSL is negative-value at solo scale on 1 GB. Our engine = declarative YAML rules + pure-Python evaluator + **every evaluation logged**.
2. **Two-phase money actions**: every money-moving intent becomes a `proposals` row → policy gate evaluates → below threshold auto-executes, above threshold waits in an approval queue → execution writes to Razorpay → outcome appended.
3. **Hash-chained audit ledger** in SQLite: each record = `sha256(prev_hash || canonical_json(payload))`. Daily Merkle root printed/exported. Judges can verify any row's integrity live on stage.
4. **Decision records**: agents must attach structured rationale (`reason`, `options_considered`, `bound_applied`) to every proposal — no free-text-only excuses. This makes "explainable" clickable.
5. **Order lifecycle state machine** + idempotency keys + DB unique constraints ⇒ double-charging is *structurally impossible*, not just discouraged.
6. **Failure demo choreography** (§9): blocked-discount live, killed-payment recovery live — both leave visible audit trails.

---

## 1. Policy engine: why hand-rolled beats OPA here

| Option | Footprint | Solo-dev fit | Verdict |
|---|---|---|---|
| Hand-rolled rules | ~0 MB (in-process) | Rules are readable Python/YAML; full control over logging shape | ✅ **Choice** |
| OPA / Rego | sidecar daemon ~30–60 MB + Rego learning curve | Great at org scale; overkill + risk at hackathon scale | ❌ |
| Cedar | Rust lib, Python bindings immature | Integration risk | ❌ |

### The engine

```python
# policy/engine.py — concept
@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    bound_applied: str | None      # e.g. "discount_cap_10pct"
    clamped_value: Any | None      # e.g. discount 25% -> 10%
    reasons: list[str]

def evaluate(action: MoneyAction, ctx: SessionContext, rules: list[Rule]) -> Decision:
    # rules are pure functions: (action, ctx) -> Violation | Clamp | Approve
    ...
```

Policy catalog (YAML-configurable, hot-reloadable):

```yaml
policies:
  - id: discount_max_pct
    type: clamp          # not just reject — CLAMP then log the clamp
    max_discount_pct: 10
  - id: offers_per_session
    type: rate_limit
    action: offer_discount
    max_count: 1
    window: session
  - id: price_is_immutable
    type: forbid
    field: unit_price    # agent may never touch listed price
  - id: stock_must_exist
    type: precondition
    check: item.stock > 0
  - id: human_approval_above
    type: approval_gate
    amount_gt_inr: 500   # proposals above this wait for human click
  - id: refund_requires_human
    type: approval_gate
    action: create_refund
```

Design rules that make judges nod:
- **Clamp > reject where possible** (agent wanted 25% off → got 10% off, and the ledger shows both numbers). Reject only for forbidden things (price mutation).
- Every evaluation returns a `Decision` that gets persisted verbatim — the gate itself is auditable.

## 2. Two-phase money actions + approval queue

```
intent ──► proposals(status=PENDING_REVIEW) ──policy──► ALLOWED? ──► execute ──► outcomes
                                              │                        │
                                     requires_approval? YES        (idempotency key)
                                              ▼                        ▼
                                   approvals queue (UI on VM3)    audit.append(...)
                                              │ expires after N min → EXPIRED
```

- `auto_approve_below_inr: 500` keeps the demo fluid; anything spicy queues for a human click on the Control Tower.
- Pending approvals expire (default 15 min) → status EXPIRED, logged. No zombie money intents.

## 3. Hash-chained audit ledger (tamper-evident)

```sql
CREATE TABLE audit_log (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc      TEXT NOT NULL,
  actor       TEXT NOT NULL,            -- 'growth_agent' | 'buyer_agent:<id>' | 'human:hp'
  action_type TEXT NOT NULL,            -- 'order.create','offer.discount','payment.capture','policy.eval',...
  payload     TEXT NOT NULL,            -- canonical JSON (sorted keys, no whitespace)
  prev_hash   TEXT NOT NULL,
  self_hash   TEXT NOT NULL,            -- sha256(prev_hash || payload || seq || ts_utc)
  correlation_id TEXT NOT NULL          -- groups one business transaction end-to-end
);
CREATE UNIQUE INDEX idx_audit_selfhash ON audit_log(self_hash);
```

```python
import hashlib, json
def append(conn, actor, action_type, payload: dict, correlation_id: str):
    prev = conn.execute("SELECT self_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev[0] if prev else "GENESIS"
    row = {"seq_hint": "", "actor": actor, "action_type": action_type,
           "payload": json.dumps(payload, sort_keys=True, separators=(",", ":"))}
    ts = datetime.now(timezone.utc).isoformat()
    self_hash = hashlib.sha256(f"{prev_hash}|{ts}|{row['payload']}".encode()).hexdigest()
    conn.execute("INSERT INTO audit_log(ts_utc,actor,action_type,payload,prev_hash,self_hash,correlation_id) VALUES(?,?,?,?,?,?,?)",
                 (ts, actor, action_type, row["payload"], prev_hash, self_hash, correlation_id))
```

- Verification job (`scripts/verify_chain.py`): recompute hashes from genesis → prints ✅ chain intact. Run it LIVE on stage after the demos. Cheap theater, real substance.
- Optional daily snapshot: store `sha256(last_hash)` externally (even just a signed gist/file) → external anchor without blockchain cosplay.

## 4. Explainability: decision records

Every agent proposal carries:

```json
{
  "proposal_id": "prp_01J9...",
  "action": "offer_discount",
  "context": {"cart_value_inr": 1299, "cart_items": ["sku_chai_250g"], "persona_budget": "mid"},
  "options_considered": [
    {"option": "bundle_chai+cup", "est_uplift_inr": 199, "chosen": false, "why_not": "stock=2 low"},
    {"option": "discount_10pct_chai", "est_uplift_inr": 130, "chosen": true}
  ],
  "reason": "high-affinity complement, margin-safe, stock-safe",
  "bound_applied": null,
  "gate": {"decision": "allowed", "requires_approval": false}
}
```

Control Tower UI: click any transaction → timeline of its `correlation_id`: context → options → choice → gate verdict → payment result. **"Explainable" becomes a feature you demo, not a claim you make.**

## 5. Orders, idempotency, state machine

```sql
CREATE TABLE orders (
  id TEXT PRIMARY KEY,                  -- 'ord_' + uuid (OUR id, generated once)
  rp_order_id TEXT UNIQUE,              -- Razorpay order id
  buyer_session_id TEXT NOT NULL,
  items_json TEXT NOT NULL,
  total_inr INTEGER NOT NULL,           -- paise-precise integers, never floats
  status TEXT NOT NULL CHECK(status IN
    ('created','attempting','paid','failed','expired','cancelled')),
  idempotency_key TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  attempt_no INTEGER NOT NULL,
  rp_payment_id TEXT UNIQUE,
  method TEXT, error_code TEXT, error_desc TEXT,
  amount_inr INTEGER NOT NULL, status TEXT NOT NULL,
  UNIQUE(order_id, attempt_no)          -- structural duplicate protection
);
```

State machine — **only these transitions exist**, enforced in code:

```
created ──► attempting ──► paid
   │            │
   │            ├──► failed ──► attempting   (new attempt_no, new idem key; max 2 retries)
   │            │        └──► expired       (sweep job)
   └──► cancelled                    (user/agent abandon)
```

- Idempotency key = `f"{order_id}:{attempt_no}"` sent as our own reference on every mutating call; combined with the UNIQUE constraints, a replay can't create a second charge even if the network lies to us.
- All amounts in integer paise. Floats and money don't mix; say this out loud in the demo, judges who've been burned will love it.

## 6. Failure taxonomy & graceful-handling playbook

| Failure class | Example | Deterministic test trigger (test mode) | Graceful response |
|---|---|---|---|
| Hard decline | card declined | Razorpay test decline card | NO auto-retry. Offer alternate method / save cart. Log `payment.failed` |
| Auth dropout | 3DS abandoned mid-flow | close checkout during auth | order stays `attempting`; sweep marks `expired` after T; cart preserved |
| Network timeout | our call to API times out | proxy kill / `iptables -A OUTPUT -p tcp --dport 443 -j DROP` for N seconds | idempotent retry w/ backoff ONCE; then park order `attempting`, reconcile later |
| Webhook lost | captured but we never got pinged | block webhook endpoint briefly | reconciliation sweep polls order status once before expiring |
| Stock race | two buyers, one unit left | scripted concurrent buys | `UPDATE ... WHERE stock>0` guard → loser gets honest out-of-stock + alternative suggestion |
| Duplicate submit | buyer agent double-fires pay | replay same request | idempotency key returns SAME result, second call is a no-op — show it live |
| Agent overreach | tries 50% discount | judge asks for it | policy clamp/reject + audit row (this IS our star demo) |

## 7. Reconciliation sweep (the quiet hero)

Background loop every 60 s:
1. Find orders `attempting` older than T (e.g., 10 min).
2. Poll Razorpay order/payments status exactly once per sweep.
3. If actually paid → mark paid + audit (`webhook_lost_recovered`). If genuinely dead → `expired`, release any reserved stock, emit `checkout.recoverable` event (feeds our recovery story).

This single loop converts "one failure handled gracefully" into "a *system* that heals."

## 8. What NOT to build (scope discipline)

- No ML fraud scoring, no anomaly detection dashboards (Track 02 creep).
- No generic workflow engine — our state machines are explicit code.
- No blockchain anything — hash chain is enough and honest.
- No real-money anything. Test mode only, stated everywhere.

## 9. Demo choreography (the governed-failure show)

1. **Happy path**: buyer agent checks out → Control Tower shows proposal → gate verdict → payment.captured, all one `correlation_id`.
2. **Overreach**: judge asks growth agent for 50% off → policy engine clamps to 10% → UI shows `requested: 25% → applied: 10%, bound: discount_max_pct` → `verify_chain.py` proves the log wasn't edited.
3. **Killed payment**: start payment, kill VM1's outbound net for ~20 s → timeout handled → connectivity restored → sweep reconciles → order lands correctly, zero double charges, full audit trail of the incident.
4. Close: "Every rupee movement you saw had a reason, a bound, and a receipt."

## Confidence & sources

Standard, well-documented engineering practice (idempotency keys, HMAC/hash chains, state machines, two-phase approvals). No fast-moving external facts asserted. Razorpay-specific triggers owned by `02-razorpay-testmode-deepdive.md` — cross-check §6 column 3 against it when it lands.
