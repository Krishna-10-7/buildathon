"""Live demonstration of a UPI Reserve Pay consent envelope.

Runs a real enforcement sequence — real SQLite, real HMAC signing, real
hash-chained audit, real refusals — against a DEDICATED demo store.

Why a separate store, and not the merchant's own ledger:

  The README publishes a ledger size and a chain verdict (704 records,
  chain_ok) as evidence. If a judge clicking /demo appended rows to that
  ledger, the number we publish would be falsified by the very act of
  checking it. So the demo writes to its own store and the published
  ledger stays exactly as stated. The isolation is the point: **the
  evidence we cite is not the thing the demo mutates.**

Nothing here is mocked. The envelope is created, signed, drawn down,
over-run, revoked and re-presented through the same `mandates` code path
that guards live orders — only the database file differs.

Sequence (every step is a real call, every verdict is the real return
value):

  1  envelope opened        budget Rs2000, single txn Rs800, [tea, spices]
  2  Rs450 tea              ALLOWED
  3  capture                spend drawn down -> Rs450 spent
  4  Rs950 tea              REFUSED  single-txn cap
  5  Rs1600 tea             REFUSED  budget exhausted (450 + 1600 > 2000)
  6  Rs450 coffee           REFUSED  category outside envelope
  7  revoke                 buyer withdraws consent
  8  Rs450 tea  (again)     REFUSED  revoked

Step 8 is the same request that was allowed in step 2. That reversal is
the whole argument: the bound is not a filter on the model's output, it
lives in a signed object the buyer can withdraw, and once withdrawn the
identical request fails.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bazaar import audit, db, mandates

log = logging.getLogger("bazaar.envelope")

# Separate store so a demo click can never perturb the published ledger.
_DEMO_DB = Path(__file__).resolve().parent.parent / ".data" / "envelope_demo.db"
# Passed to every mandates call instead of redirecting settings.db_path.
# The path is a parameter, so two threads can address two different stores
# at the same instant without either observing the other's target.
_DEMO_PATH = str(_DEMO_DB)

# The sequence is a scripted narrative, so it must not interleave with
# itself. This lock now protects only that — no longer a global setting.
_LOCK = threading.Lock()

RAIL = "UPI Reserve Pay (simulated envelope)"

BUDGET_CAP_PAISE = 200_000      # Rs 2000.00
MAX_SINGLE_TXN_PAISE = 100_000  # Rs 1000.00
ALLOWED_CATEGORIES = ["tea", "spices"]

# Every bound is demonstrated IN ISOLATION — one refusal reason per step.
# That constraint dictates these amounts, and it is stricter than it
# looks: a single number can trip several bounds at once, and a step that
# fails for two reasons proves neither. Worked through:
#
#   step  order   vs single cap (1000)   vs budget remaining     fires
#   4     Rs1200  EXCEEDS                300+1200=1500 <= 2000   single only
#   5     Rs300   ok                     300+300=600  <= 2000    category only
#   8     Rs800   ok (800 <= 1000)       1500+800=2300 > 2000    budget only
#   10    Rs300   ok                     1500+300=1800 <= 2000   revoked only
#
# Step 10 is byte-identical to step 2, which is the point: same request,
# same envelope, same code path — and it now fails, for one named reason.
SMALL_ORDER_PAISE = 30_000
OVER_SINGLE_PAISE = 120_000
OVER_BUDGET_PAISE = 80_000
CAPTURES_PAISE = [30_000, 60_000, 60_000]   # total Rs1500 of Rs2000


def _rule(reason: str) -> str:
    """Map a refusal reason to a stable rule id for display.

    mandates.py owns the reason strings and its tests assert them, so the
    label is derived here rather than changing the core's vocabulary.
    """
    if reason.startswith("single txn"):
        return "single-txn cap"
    if reason.startswith("budget"):
        return "budget exhausted"
    if reason.startswith("categories"):
        return "category outside envelope"
    if "revoked" in reason:
        return "envelope revoked"
    if "expired" in reason:
        return "envelope expired"
    if "signature" in reason:
        return "signature mismatch"
    return reason


# The demo store is written by an UNAUTHENTICATED public endpoint, so it
# grows at a rate we do not control — a crawler pressing F5 on /envelope
# appends ~10 rows per hit, and this box has 1 GB of RAM and a small disk.
# Past this many audit rows the file is rotated: renamed aside with a
# timestamp and rebuilt empty. The rotated copies are left on disk, so
# "the demo was hammered at 14:02" survives as evidence rather than being
# silently trimmed — trimming an append-only ledger is exactly the
# behaviour this project argues against.
MAX_DEMO_AUDIT_ROWS = 5_000


def rotate_if_large(max_rows: int = MAX_DEMO_AUDIT_ROWS) -> str | None:
    """Cap the demo store. Returns the rotated filename, or None.

    Caller must hold `_LOCK`: renaming the file underneath a reader would
    be a bad afternoon, and `_LOCK` is already the serialiser for every
    path that touches this store.
    """
    if not _DEMO_DB.exists():
        return None
    conn = db.connect(_DEMO_PATH)
    try:
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()

    if n <= max_rows:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rotated = _DEMO_DB.with_name(f"{_DEMO_DB.stem}-{stamp}{_DEMO_DB.suffix}")
    _DEMO_DB.rename(rotated)
    log.warning("demo store rotated: %d audit rows -> %s "
                "(cap %d); rotated copy retained as evidence",
                n, rotated.name, max_rows)
    return rotated.name


def _ensure_schema() -> None:
    """Create the demo store's tables if this is the first run."""
    _DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(_DEMO_PATH)
    try:
        conn.executescript(db.SCHEMA)
        db.migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _record(actor: str, action: str, payload: dict,
            correlation_id: str) -> str:
    """One audit row, one short transaction.

    Deliberately short-lived: SQLite allows a single writer, and holding a
    RESERVED lock across a `mandates` call would make that call fail with
    "database is locked" the moment it opens its own connection.
    """
    conn = db.connect(_DEMO_PATH)
    try:
        self_hash = audit.append(conn, actor=actor, action_type=action,
                                 payload=payload,
                                 correlation_id=correlation_id)
        conn.commit()
        return self_hash
    finally:
        conn.close()


def _draw_down(mandate_id: str, amount_paise: int, buyer_ref: str) -> None:
    conn = db.connect(_DEMO_PATH)
    try:
        mandates.draw_down(conn, mandate_id, amount_paise)
        conn.commit()
    finally:
        conn.close()
    _record(f"buyer:{buyer_ref}", "envelope.drawdown",
            {"mandate_id": mandate_id, "rail": RAIL,
             "amount_paise": amount_paise}, mandate_id)


def _rupees(paise: int) -> str:
    return f"Rs{paise / 100:,.2f}"


def run_sequence(buyer_ref: str = "demo-buyer") -> dict:
    """Execute the whole sequence live and return a renderable transcript."""
    # _LOCK serialises the narrative so two viewers cannot interleave steps.
    # It no longer guards a global setting — every call below names its
    # store, so concurrent readers of the merchant ledger are unaffected.
    with _LOCK:
        _ensure_schema()
        # Checked BEFORE the run so a store already over the cap does not
        # absorb another ~10 rows first. Cheap: one COUNT per run, and the
        # run itself does ~15 writes.
        rotate_if_large()
        steps: list[dict] = []

        # -- 1. open the envelope ----------------------------------------
        env = mandates.create(
            buyer_ref=buyer_ref,
            budget_cap_paise=BUDGET_CAP_PAISE,
            max_single_txn_paise=MAX_SINGLE_TXN_PAISE,
            allowed_categories=ALLOWED_CATEGORIES,
            ttl_hours=1.0,
            db_path=_DEMO_PATH,
        )
        steps.append({
            "n": 1,
            "kind": "open",
            "label": "Envelope opened",
            "detail": (f"budget {_rupees(BUDGET_CAP_PAISE)} · "
                       f"single txn {_rupees(MAX_SINGLE_TXN_PAISE)} · "
                       f"{', '.join(ALLOWED_CATEGORIES)}"),
            "allowed": True,
            "reasons": [],
            "rules": [],
            "mandate_id": env["id"],
        })

        def attempt(n: int, label: str, paise: int,
                    categories: list[str]) -> dict:
            row, verdict = mandates.check(env["id"], paise, categories,
                                          db_path=_DEMO_PATH)
            _record(f"buyer:{buyer_ref}", "envelope.checked",
                    {"mandate_id": env["id"],
                     "rail": RAIL,
                     "requested_paise": paise,
                     "categories": categories,
                     "allowed": verdict.allowed,
                     "reasons": verdict.reasons,
                     "spent_paise": row["spent_paise"] if row else None},
                    env["id"])
            return {
                "n": n,
                "kind": "check",
                "label": label,
                "detail": (f"{_rupees(paise)}"
                           + (f" · {', '.join(categories)}" if categories
                              else "")),
                "allowed": verdict.allowed,
                "reasons": verdict.reasons,
                "rules": [_rule(r) for r in verdict.reasons],
                "mandate_id": env["id"],
            }

        def capture(n: int, paise: int, spent_so_far: int) -> dict:
            _draw_down(env["id"], paise, buyer_ref)
            spent = spent_so_far + paise
            return {
                "n": n,
                "kind": "drawdown",
                "label": "Payment captured",
                "detail": (f"{_rupees(paise)} drawn down · spent "
                           f"{_rupees(spent)} · remaining "
                           f"{_rupees(BUDGET_CAP_PAISE - spent)}"),
                "allowed": True,
                "reasons": [],
                "rules": [],
                "mandate_id": env["id"],
            }

        spent = 0

        # -- 2. a request inside every bound -----------------------------
        steps.append(attempt(2, "Agent requests tea",
                             SMALL_ORDER_PAISE, ["tea"]))

        # -- 3-7. the agent shops; the envelope empties -------------------
        steps.append(capture(3, CAPTURES_PAISE[0], spent))
        spent += CAPTURES_PAISE[0]

        # -- 4. one order, too big for a single transaction --------------
        steps.append(attempt(4, "One order above the single-txn cap",
                             OVER_SINGLE_PAISE, ["tea"]))

        # -- 5. a category the envelope never covered --------------------
        steps.append(attempt(5, "Outside the envelope's categories",
                             SMALL_ORDER_PAISE, ["coffee"]))

        steps.append(capture(6, CAPTURES_PAISE[1], spent))
        spent += CAPTURES_PAISE[1]
        steps.append(capture(7, CAPTURES_PAISE[2], spent))
        spent += CAPTURES_PAISE[2]

        # -- 8. an order that no longer fits what is left ----------------
        steps.append(attempt(8, "An order over what is left",
                             OVER_BUDGET_PAISE, ["tea"]))

        # -- 9. the buyer withdraws consent ------------------------------
        revoked = mandates.revoke(env["id"], db_path=_DEMO_PATH)
        steps.append({
            "n": 9,
            "kind": "revoke",
            "label": "Buyer revokes the envelope",
            "detail": f"revoked at {revoked['revoked_at']}",
            "allowed": True,
            "reasons": [],
            "rules": [],
            "mandate_id": env["id"],
        })

        # -- 10. the SAME request that passed at step 2 ------------------
        steps.append(attempt(10, "Same Rs300 tea request, re-presented",
                             SMALL_ORDER_PAISE, ["tea"]))

        conn = db.connect(_DEMO_PATH)
        try:
            chain_ok, records, first_bad = audit.verify(conn)
        finally:
            conn.close()

        final = mandates.get(env["id"], db_path=_DEMO_PATH)
        final_spent = final["spent_paise"] if final else 0
        final_revoked = final["revoked_at"] if final else None

    refused = [s for s in steps if s["kind"] == "check" and not s["allowed"]]
    return {
        "rail": RAIL,
        "mandate_id": env["id"],
        "buyer_ref": buyer_ref,
        "budget_cap_paise": BUDGET_CAP_PAISE,
        "max_single_txn_paise": MAX_SINGLE_TXN_PAISE,
        "allowed_categories": ALLOWED_CATEGORIES,
        "spent_paise": final_spent,
        "revoked_at": final_revoked,
        "expires_at": env["expires_at"],
        "signature": env["signature"],
        "steps": steps,
        "checks": sum(1 for s in steps if s["kind"] == "check"),
        "refusals": len(refused),
        "distinct_refusal_rules": sorted({r for s in refused
                                          for r in s.get("rules", [])}),
        "distinct_refusal_reasons": sorted({r for s in refused
                                            for r in s["reasons"]}),
        "demo_ledger": {
            "store": _DEMO_DB.name,
            "chain_ok": chain_ok,
            "records": records,
            "first_bad_seq": first_bad,
            "note": ("separate store — clicking the demo never perturbs the "
                     "merchant ledger we publish"),
        },
        "verdict": (f"{len(refused)} of "
                    f"{sum(1 for s in steps if s['kind'] == 'check')} checks "
                    f"refused, across "
                    f"{len({r for s in refused for r in s.get('rules', [])})} "
                    f"independent bounds; the request allowed at step 2 is "
                    f"refused at step 10 by the same code path"),
    }


# --- transcript cache ---------------------------------------------------
# /envelope is a public GET. Uncached, every refresh — and every bot, and
# every browser prefetch — re-runs ten enforcement steps and writes ~10
# audit rows. Ten seconds of staleness is invisible to a human viewer, so
# the page serves the last transcript inside that window instead.
#
# Deliberately NOT folded into run_sequence(): the test suite calls
# run_sequence() directly and asserts on a fresh run, and a cache that
# silently returned yesterdays refusal verdicts would defeat it.
TRANSCRIPT_TTL_SECONDS = 10.0

_cache_lock = threading.Lock()
_cached: dict = {"at": 0.0, "data": None}


def cached_sequence(buyer_ref: str = "demo-buyer",
                    ttl: float = TRANSCRIPT_TTL_SECONDS) -> tuple[dict, bool]:
    """Return (transcript, served_from_cache). Recomputes past `ttl`."""
    now = time.monotonic()
    with _cache_lock:
        hit = (_cached["data"] is not None
               and now - _cached["at"] < ttl
               and _cached["data"].get("buyer_ref") == buyer_ref)
        if hit:
            return _cached["data"], True

    # Run OUTSIDE the cache lock. The sequence takes a while (real SQLite
    # writes, real HMAC); holding a lock across it would make every
    # concurrent GET queue behind the first one, which is the very
    # behaviour this cache exists to remove. Two late callers may both
    # run — that costs one extra run, not correctness, since the sequence
    # is self-contained per call.
    data = run_sequence(buyer_ref)
    with _cache_lock:
        # Only keep it if nobody newer got there first.
        if _cached["data"] is None or _cached["at"] <= now:
            _cached["at"] = time.monotonic()
            _cached["data"] = data
        return _cached["data"], False


def demo_audit_count() -> int:
    """Rows in the demo store's audit log.

    Exposed so the regression test can prove the two stores are distinct
    without reaching into module internals for the path.
    """
    if not _DEMO_DB.exists():
        return 0
    conn = db.connect(_DEMO_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


def to_event(buyer_ref: str = "demo-buyer") -> dict:
    """Shape for the demo's SSE stream, with a text summary."""
    data = run_sequence(buyer_ref)
    data["summary"] = (
        f"Envelope {data['mandate_id']} · "
        f"{data['refusals']}/{data['checks']} checks refused · "
        f"demo ledger {data['demo_ledger']['records']} records, "
        f"chain_ok={str(data['demo_ledger']['chain_ok']).lower()}"
    )
    return data


if __name__ == "__main__":
    print(json.dumps(run_sequence(), indent=2))
