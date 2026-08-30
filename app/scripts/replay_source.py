"""REPLAY SOURCE — rebuild a recorded trip from the committed fixture.

Why this exists
---------------
The live demo needs three things that fail at the worst possible moment:
a Razorpay test key that has not been burned by hCaptcha, an LLM key with
quota left, and a browser that can reach the gateway. All three have
failed on us (KEY-ROTATION-CHECKLIST.md, FAILURE-RUNBOOK.md).

`/demo` should never be dark because of that. So the default path is a
replay: the timeline is rebuilt from artifacts/replay_fixture.json,
which was produced by joining the buyer-side session log against the
merchant's own database (scripts/build_replay_fixture.py).

What is real and what is rebuilt
--------------------------------
REAL    every order id, payment id, amount, timestamp and ledger hash on
        screen. They come from the merchant database and the Razorpay
        gateway; a judge can diff them against the repo.
REBUILT the narration only — which beat is announced when, and the
        dialogue. The underlying rows happened; the show around them is
        ours.

What is deliberately NOT claimed
--------------------------------
A replay is not a new transaction. Every replayed event carries
mode="replay", the summary panel says so, and the opening banner says so.
Presenting a replayed trip as a live purchase would be the one lie that
invalidates an audit-trail submission, so the code refuses to make it
easy: there is no code path that emits a replay event without the mode
flag set.

The property that actually matters
----------------------------------
A replay needs NO keys, NO network and NO database. That is the same
property competitors get from a fully synthetic `make demo`, with the
difference that every id we show is real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPLAY_PATH = (Path(__file__).resolve().parent.parent
               / "artifacts" / "replay_fixture.json")

_CACHE: dict[str, Any] = {"mtime": None, "data": None}

# Pacing floor between beats, in seconds. The browser serialises events
# through its own animation queue, so these only stop the whole timeline
# arriving in one burst; the visible rhythm comes from the scene work.
_PACE = {
    "session_start": 0.0,
    "catalog": 1.0,
    "plan": 1.0,
    "ck_order_created": 0.6,
    "ck_browser_up": 0.6,
    "ck_contact_passed": 0.4,
    "ck_method_selected": 0.4,
    "ck_bank_picked": 0.4,
    "ck_bank_confirmed": 0.4,
    "ck_captcha_challenge": 0.6,
    "captured": 0.8,
    "outcome": 0.6,
}


class ReplayUnavailable(Exception):
    """Raised when the fixture is missing or unreadable."""


def load(path: Path | None = None) -> dict:
    """Read the fixture, re-reading only when the file changes on disk.

    Re-reading on mtime means the fixture can be regenerated while the
    demo server is running and the next replay picks it up, without the
    server holding a stale copy of the evidence.
    """
    p = Path(path) if path else REPLAY_PATH
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError as exc:
        raise ReplayUnavailable(
            f"no replay fixture at {p} — run scripts/build_replay_fixture.py "
            f"or start the demo with --mock/--live") from exc
    if _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplayUnavailable(f"replay fixture unreadable: {exc}") from exc
    _CACHE.update(mtime=mtime, data=data)
    return data


# ---------------------------------------------------------------- catalog

def catalog_from_baskets(data: dict) -> list[dict]:
    """Recover the catalog from what buyers actually ordered.

    The fixture carries no catalog snapshot; it carries 40 real baskets.
    Uniting them yields the lines that were genuinely bought, at the unit
    price charged at plan time. Coverage is therefore PARTIAL BY
    CONSTRUCTION — 12 of the merchant's 17 lines as of the current
    fixture — and is reported as such rather than padded out with
    invented rows.

    Unit price is taken from the basket (the catalog price at plan
    time), not from the order.create ledger payload (the price actually
    billed after any server-side discount). Conflating the two would
    make the "executed discount" beat look like a price change.
    """
    seen: dict[str, dict] = {}
    for trip in data.get("trips") or []:
        for line in trip.get("basket") or []:
            sku = line.get("sku")
            if not sku or line.get("unit_paise") is None:
                continue
            rec = seen.setdefault(sku, {
                "sku": sku,
                "title": line.get("title") or sku,
                "category": line.get("category"),
                "price_paise": line["unit_paise"],
                "in_stock": True,
                "times_ordered": 0,
            })
            rec["times_ordered"] += 1
    # Most-ordered first: the shopkeeper's opening line reads better when
    # it leads with what the bazaar actually sells.
    return sorted(seen.values(),
                  key=lambda r: (-r["times_ordered"], r["sku"]))


# ------------------------------------------------------------- trip pick

def list_trips(data: dict) -> list[dict]:
    """One summary row per recorded trip, for a picker UI."""
    out = []
    for t in data.get("trips") or []:
        out.append({
            "session_id": t.get("session_id"),
            "persona": t.get("persona"),
            "outcome": t.get("outcome"),
            "basket_total_paise": t.get("basket_total_paise"),
            "paid": t.get("outcome") == "paid"
                    and bool(t.get("payments")),
            "ts": t.get("ts"),
        })
    return out


def pick_trip(data: dict, persona: str, variant: str = "paid") -> dict | None:
    """Choose the trip to replay.

    variant="paid"        a trip that really captured a payment
    variant="challenged"  a trip that really stopped at the risk gate

    The fixture's own featured trip wins for ritika/paid so the default
    button and the README quote the same session.
    """
    trips = [t for t in data.get("trips") or []
             if t.get("persona") == persona]
    if variant == "challenged":
        # Anything that did not capture money: risk gate, LLM death,
        # gateway decline. All are honest endings and all are interesting
        # to watch, so prefer an explicit risk_challenged but fall back.
        pool = ([t for t in trips if t.get("outcome") == "risk_challenged"]
                or [t for t in trips if t.get("outcome") != "paid"])
    else:
        pool = [t for t in trips
                if t.get("outcome") == "paid" and t.get("payments")]
    if not pool:
        return None
    featured = data.get("featured_trip")
    if variant == "paid" and featured:
        for t in pool:
            if t.get("session_id") == featured:
                return t
    return pool[0]


# ---------------------------------------------------------------- events

def _ledger_row(trip: dict, action: str) -> dict | None:
    for row in trip.get("ledger") or []:
        if row.get("action_type") == action:
            return row
    return None


def _first_payload(trip: dict, action: str) -> dict:
    row = _ledger_row(trip, action)
    return (row or {}).get("payload") or {}


def build_events(data: dict, session_id: str) -> list[tuple[float, dict]]:
    """Turn one recorded trip into the demo's own event vocabulary.

    Emits exactly the event types bazaar_live.html already handles, so
    the replay path needs no separate renderer. Every event carries
    mode="replay".
    """
    trip = next((t for t in data.get("trips") or []
                 if t.get("session_id") == session_id), None)
    if trip is None:
        raise ReplayUnavailable(f"no such trip in fixture: {session_id}")

    persona = trip.get("persona") or "ritika"
    outcome = trip.get("outcome") or "unknown"
    basket = trip.get("basket") or []
    lines = [{"sku": b.get("sku"), "qty": b.get("qty") or 1,
              "title": b.get("title"), "unit_paise": b.get("unit_paise")}
             for b in basket]

    created = _first_payload(trip, "order.create")
    # The amount billed is whatever the gateway was actually asked for.
    # Falling back to the planned basket total is only correct when no
    # order row exists (an llm_error trip never got that far).
    billed = created.get("total_paise")
    if billed is None:
        billed = trip.get("basket_total_paise")

    rp_order = trip.get("razorpay_order_id")
    internal_order = trip.get("internal_order_id")
    pay = (trip.get("payments") or [None])[0] or {}
    method = pay.get("method")

    notes = list(trip.get("notes") or [])
    notes.append(
        f"REPLAY of {session_id} · recorded {str(trip.get('ts') or '')[:19]}Z"
        f" · real ids, narration rebuilt — no money moved now")

    ev: list[tuple[float, dict]] = []

    def add(t: str, **payload):
        ev.append((_PACE.get(t, 0.5), {"t": t, "mode": "replay", **payload}))

    add("session_start", persona=persona, sid=session_id,
        name=str(persona).capitalize(),
        budget_paise=trip.get("budget_paise"),
        brain=trip.get("llm") or "recorded",
        recorded_at=trip.get("ts"))

    add("catalog", products=catalog_from_baskets(data),
        partial=True, catalog_size=data.get("catalog_size"),
        covered=len({b.get("sku") for b in basket}))

    if outcome == "llm_error":
        # The plan never existed. Say so and stop — do not invent one.
        add("outcome", outcome="llm_error", order_id=None,
            amount_paise=None, status=None, stage="llm_plan",
            error="the LLM lane died before planning (recorded outcome)",
            payment_id=None, notes=notes, basket=lines,
            basket_total_paise=None)
        return ev

    # Plan-time notes only. Trip notes also carry attempt history, and
    # showing "attempt 1 failed at risk_challenge" during the PLANNING
    # beat would spoil a failure that has not been narrated yet.
    plan_notes = [n for n in (trip.get("notes") or [])
                  if "budget" in str(n).lower()]
    add("plan", analysis=trip.get("reasoning") or "",
        brain=trip.get("llm") or "recorded",
        items=[{"sku": b.get("sku"), "qty": b.get("qty")} for b in basket],
        lines=lines, notes=plan_notes,
        total_paise=trip.get("basket_total_paise"),
        budget_paise=trip.get("budget_paise"),
        headroom_pct=trip.get("budget_headroom_pct"))

    if not internal_order and not rp_order:
        add("outcome", outcome=outcome or "walked_away", order_id=None,
            amount_paise=None, status=None, stage=None, error=None,
            payment_id=None, notes=notes, basket=lines,
            basket_total_paise=trip.get("basket_total_paise"))
        return ev

    add("ck_order_created", order_id=internal_order or rp_order,
        rp_order_id=rp_order, amount_paise=billed,
        planned_paise=trip.get("basket_total_paise"))

    add("ck_browser_up")
    add("ck_contact_passed")

    if outcome == "risk_challenged":
        # This is the beat the submission is judged on. It is a real
        # recorded ending: the order exists, the gateway asked for proof
        # of humanity, and the run stopped instead of retrying.
        add("ck_captcha_challenge", where="recorded")
        # rp_order_id is kept here on purpose: the gateway id is the
        # proof the order really reached Razorpay and really expired
        # there, rather than being quietly dropped on our side.
        add("outcome", outcome="risk_challenged",
            order_id=internal_order or rp_order, rp_order_id=rp_order,
            amount_paise=billed,
            status=trip.get("order_status") or "expired",
            stage="risk_challenge", error=None, payment_id=None,
            notes=notes, basket=lines,
            basket_total_paise=trip.get("basket_total_paise"))
        return ev

    add("ck_method_selected", method=method or "netbanking")
    # The bank is NOT recorded anywhere in the ledger, so it is not
    # asserted. The page falls back to neutral wording rather than
    # naming a bank we have no evidence for.
    add("ck_bank_picked", bank=None)
    add("ck_bank_confirmed", bank=None)

    if pay.get("rp_payment_id"):
        add("captured", order_id=internal_order or rp_order,
            rp_order_id=rp_order, amount_paise=pay.get("amount_paise") or billed,
            payment_id=pay.get("rp_payment_id"))

    add("outcome", outcome=outcome, order_id=internal_order or rp_order,
        rp_order_id=rp_order,
        amount_paise=pay.get("amount_paise") or billed,
        status=trip.get("payment_status") or trip.get("order_status"),
        stage="webhook_poll" if outcome == "paid" else None,
        error=None if outcome == "paid" else outcome,
        payment_id=pay.get("rp_payment_id"), notes=notes, basket=lines,
        basket_total_paise=trip.get("basket_total_paise"))
    return ev


# ------------------------------------------------------------------ meta

def meta(data: dict) -> dict:
    """Evidence counters for the HUD — the numbers the pitch leads with."""
    g = data.get("governance") or {}
    return {
        "t": "replay_meta",
        "mode": "replay",
        "generated_at": data.get("generated_at"),
        "join": data.get("join_stats") or {},
        "scoreboard": data.get("scoreboard") or {},
        "risk_escalation": data.get("risk_escalation") or {},
        "governance_counts": g.get("counts") or {},
        "governance_provenance": g.get("provenance_summary") or {},
        "governance_note": g.get("note") or "",
        "featured_trip": data.get("featured_trip"),
        "failure_trip": data.get("failure_trip"),
        "trips": list_trips(data),
    }


def self_check(path: Path | None = None) -> int:
    """Validate the fixture is replayable. Returns 0 on success."""
    data = load(path)
    ok = True

    sb = data.get("scoreboard") or {}
    print(f"fixture        {REPLAY_PATH if path is None else path}")
    print(f"generated      {data.get('generated_at')}")
    print(f"trips          {len(data.get('trips') or [])}"
          f"  (join { (data.get('join_stats') or {}).get('join_rate_pct') }%)")
    print(f"scoreboard     {sb.get('paid')}/{sb.get('n')} paid"
          f" · {sb.get('overspend_violations')} overspend violations")

    cat = catalog_from_baskets(data)
    print(f"catalog        {len(cat)} lines recovered from real baskets"
          f" (merchant stocks {data.get('catalog_size')})")

    for persona in ("ritika", "meera", "arjun"):
        for variant in ("paid", "challenged"):
            trip = pick_trip(data, persona, variant)
            if trip is None:
                print(f"  {persona}/{variant:<10} NONE AVAILABLE")
                continue
            try:
                events = build_events(data, trip["session_id"])
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                print(f"  {persona}/{variant:<10} BUILD FAILED: {exc}")
                ok = False
                continue
            kinds = [e[1]["t"] for e in events]
            # Every replay event must be flagged, or the page would be
            # showing a reconstruction as if it were live.
            unflagged = [k for _, e in events if e.get("mode") != "replay"]
            if unflagged:
                print(f"  {persona}/{variant:<10} UNFLAGGED EVENTS: {unflagged}")
                ok = False
            print(f"  {persona}/{variant:<10} {trip['session_id']:<18}"
                  f" {len(events):>2} beats · {kinds[-1]}")

    # The featured trip must carry real gateway ids, or the pitch is wrong.
    feat = data.get("featured_trip")
    ft = next((t for t in data["trips"] if t["session_id"] == feat), None)
    if not ft or not ft.get("razorpay_order_id") or not ft.get("payments"):
        print(f"featured trip {feat} has no razorpay order/payment id")
        ok = False
    else:
        print(f"featured       {feat}: {ft['razorpay_order_id']} → "
              f"{ft['payments'][0]['rp_payment_id']} "
              f"@ {ft['payments'][0]['amount_paise']}p")

    print("SELF-CHECK " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(self_check())
