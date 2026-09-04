#!/usr/bin/env python3
"""build_replay_fixture.py — join buyer-side sessions to merchant-side truth.

WHY THIS EXISTS
---------------
`/demo` currently performs a live trip: real LLM call, real Razorpay order,
real browser-driven checkout. That is the strongest demo beat when it works
and the most fragile thing in the project when it does not — Razorpay's
test-mode risk engine is velocity-keyed, so a key degrades after roughly
10-13 checkouts (`research/10`). It also means a judge cannot run the demo
without credentials.

This script builds the alternative: a **replay fixture** assembled from
evidence already on disk. The buyer-side session JSONL knows what the agent
decided; the merchant database knows what actually happened. Joining them
produces a trip record in which every id is real.

The replay is therefore not a mock. It shows:
  - the verbatim LLM reasoning recorded for that session
  - the real internal order id AND the real Razorpay `order_*` id
  - the real `pay_*` payment id and the real captured amount
  - the real ledger rows the trip produced, with their hash-chain values

The only thing predetermined is the OUTCOME, and it is predetermined by
history rather than re-negotiated with the risk engine on every click.

It CHANGES NOTHING. Reads two files, writes one. No network, no database,
no credentials.

USAGE
-----
    # 1. on VM1 (the host owning bazaar.db):
    python scripts/dump_replay_source.py -o /tmp/replay_source.json

    # 2. pull it back, then locally:
    python scripts/build_replay_fixture.py \
        --sessions ../evidence/sessions_laptop2.jsonl \
        --source  .tmp/replay_source.json \
        --out     artifacts/replay_fixture.json

Exit code is always 0 — a reporting tool, never a gate. But see join_stats:
a low join rate means the fixture is not trustworthy and should not ship.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Audit action types, in the order they occur within a trip, with the label
# the demo shows. Anything not listed is rendered with a generic label rather
# than dropped — a replay must never silently lose a ledger row.
STEP_LABELS = {
    "mandate.created":              "consent envelope issued",
    "proposal.created":             "agent proposed an action",
    "proposal.approved":            "human approved the proposal",
    "proposal.rejected":            "human REJECTED the proposal",
    "proposal.executed":            "approved action executed",
    "order.create":                 "order created at gateway",
    "order.mandate_denied":         "envelope REFUSED the order",
    "payment.captured":             "payment captured",
    "order.paid":                   "order marked paid",
    "payment.failed":               "payment failed",
    "order.expired_released":       "order expired, stock released",
    "webhook.rejected_invalid_signature": "webhook signature REJECTED",
    "experiment.arm_switch":        "experiment arm switched",
    "key_rotation.probe":           "key-rotation probe (orphan event)",
}

# Action types that represent a refusal or failure — the demo renders these
# differently, because Track 01's bar explicitly asks for one of them.
FAILURE_TYPES = {
    "order.mandate_denied", "payment.failed", "proposal.rejected",
    "webhook.rejected_invalid_signature", "order.expired_released",
}


def money(paise) -> str:
    if paise is None:
        return "—"
    return f"Rs {paise / 100:,.2f}"


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_steps(audit_rows: list[dict]) -> list[dict]:
    """Ledger rows for one trip, in chain order, labelled for the demo."""
    steps = []
    for r in sorted(audit_rows, key=lambda x: x["seq"]):
        at = r["action_type"]
        steps.append({
            "seq": r["seq"],
            "ts": r["ts_utc"],
            "actor": r["actor"],
            "action_type": at,
            "label": STEP_LABELS.get(at, at.replace(".", " ")),
            "is_failure": at in FAILURE_TYPES,
            "payload": r["payload"],
            "self_hash": r["self_hash"][:12] if r.get("self_hash") else None,
        })
    return steps


# Audit action types that constitute a *refusal* — the merchant declining to
# move money. Track 01's bar asks for "one failure handled gracefully", and
# these are the rows that prove it with dated, hash-chained evidence rather
# than a narrative.
GOVERNANCE_TYPES = {
    "order.mandate_denied":              "envelope_refusals",
    "mandate.revoked":                   "revocations",
    "proposal.rejected":                 "human_vetoes",
    "webhook.rejected_invalid_signature": "forged_webhooks",
    "payment.failed":                    "payment_failures",
}

GOVERNANCE_LABELS = {
    "order.mandate_denied": "spending envelope REFUSED the order",
    "mandate.revoked": "consent REVOKED by its owner",
    "proposal.rejected": "human REJECTED the agent's proposal",
    "webhook.rejected_invalid_signature": "forged webhook signature REJECTED",
    "payment.failed": "gateway declined the payment",
}


def build_governance(src: dict) -> dict:
    """Real refusal events from the WHOLE ledger, not just the clean run.

    The clean run's 40 trips contain no ledger failure steps — the risk
    challenges happened in the browser and were recorded as an outcome, not as
    a refusal row. The refusals this system actually made live elsewhere in
    the ledger: envelopes denying orders, owners revoking consent, humans
    vetoing proposals, forged webhook signatures being rejected. Pulling them
    out is what turns "a failure handled gracefully" from a claim into
    evidence.
    """
    buckets: dict[str, list[dict]] = {
        "envelope_refusals": [], "revocations": [], "human_vetoes": [],
        "forged_webhooks": [], "payment_failures": [],
    }

    for cid, rows in src["audit_by_correlation"].items():
        for r in sorted(rows, key=lambda x: x["seq"]):
            at = r["action_type"]
            bucket = GOVERNANCE_TYPES.get(at)
            if not bucket:
                continue
            payload = r["payload"] or {}
            buckets[bucket].append({
                "seq": r["seq"],
                "ts": r["ts_utc"],
                "actor": r["actor"],
                "action_type": at,
                "label": GOVERNANCE_LABELS.get(at, at),
                "correlation_id": r["correlation_id"],
                "mandate_id": payload.get("mandate_id"),
                "proposal_id": payload.get("proposal_id"),
                "reasons": payload.get("reasons") or [],
                "amount_paise": payload.get("total_paise") or payload.get("amount_paise"),
                "error_code": payload.get("error_code"),
                "error_description": payload.get("error_description"),
                "event": payload.get("event"),
                "self_hash": (r.get("self_hash") or "")[:12] or None,
            })

    for name in buckets:
        buckets[name].sort(key=lambda x: x["seq"])

    # ---- provenance: rehearsed vs organic -------------------------------
    # Most of these rows were produced by scripts/failure_choreography.py,
    # the documented failure suite in FAILURE-RUNBOOK.md. They are real,
    # hash-chained, dated ledger rows and they prove the mechanism works —
    # but they were deliberately provoked, not discovered mid-measurement.
    # Saying so is the whole point of shipping an audit trail: a judge who
    # later works out that a "failure we handled" was staged will discount
    # everything else. Label it up front and it stays an asset.
    REHEARSED_MARKERS = ("failure-choreography", "demo-buyer")

    def tag(entry: dict, provenance: str, why: str) -> None:
        entry["provenance"] = provenance
        entry["provenance_note"] = why

    for entry in buckets["revocations"] + buckets["human_vetoes"]:
        if any(m in (entry["actor"] or "") for m in REHEARSED_MARKERS):
            tag(entry, "rehearsed",
                "produced by scripts/failure_choreography.py (see FAILURE-RUNBOOK.md)")
        else:
            tag(entry, "organic", "occurred during normal operation")

    for entry in buckets["forged_webhooks"]:
        tag(entry, "rehearsed",
            "forged-signature injection from the failure choreography suite")

    for entry in buckets["payment_failures"]:
        tag(entry, "organic", "real gateway decline during live test traffic")

    # An envelope refusal chained to a rehearsed revocation is itself
    # rehearsed; anything else came through the API on its own.
    rehearsed_mandates = {e["mandate_id"] for e in buckets["revocations"]
                          if e.get("provenance") == "rehearsed" and e.get("mandate_id")}
    for entry in buckets["envelope_refusals"]:
        if entry.get("mandate_id") in rehearsed_mandates:
            tag(entry, "rehearsed",
                "paired with a choreographed revocation of the same mandate")
        elif entry.get("provenance") is None:
            tag(entry, "organic",
                "refused by the policy engine during normal API traffic")

    # Link each revocation to the refusal that followed it, so the demo can
    # show "consent withdrawn -> next attempt refused" as one sequence. This is
    # the UPI Reserve Pay revocation story, with real rows.
    refusals = buckets["envelope_refusals"]
    for rev in buckets["revocations"]:
        mid = rev.get("mandate_id")
        after = next((r for r in refusals
                      if r.get("mandate_id") == mid and r["seq"] > rev["seq"]), None)
        if after:
            rev["followed_by_refusal"] = {
                "seq": after["seq"], "ts": after["ts"],
                "reasons": after["reasons"], "amount_paise": after["amount_paise"],
            }
            after["caused_by_revocation_at"] = rev["seq"]

    counts = {k: len(v) for k, v in buckets.items()}
    summary = {}
    for name, rows in buckets.items():
        summary[name] = {
            "total": len(rows),
            "rehearsed": sum(1 for r in rows if r.get("provenance") == "rehearsed"),
            "organic": sum(1 for r in rows if r.get("provenance") == "organic"),
        }

    return {
        "counts": counts,
        "provenance_summary": summary,
        "note": (
            "Rows marked 'rehearsed' were deliberately provoked by "
            "scripts/failure_choreography.py, the failure suite documented in "
            "FAILURE-RUNBOOK.md. Rows marked 'organic' happened during real "
            "traffic. Both are genuine hash-chained ledger entries and both "
            "prove the money pathway refuses safely — but they are labelled "
            "because a staged failure presented as a discovery would "
            "undermine the whole submission."
        ),
        **buckets,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", required=True,
                    help="buyer-side session JSONL (the clean run)")
    ap.add_argument("--source", required=True,
                    help="replay_source.json produced by dump_replay_source.py")
    ap.add_argument("--out", required=True, help="output fixture path")
    ap.add_argument("--label", default="clean run",
                    help="human label for this corpus")
    args = ap.parse_args()

    sessions = load_jsonl(Path(args.sessions))
    src = json.loads(Path(args.source).read_text(encoding="utf-8"))

    orders = {o["id"]: o for o in src["orders"]}
    pays_by_order = src["payments_by_order"]
    audit_by_corr = src["audit_by_correlation"]
    products = {p["sku"]: p for p in src["products"]}

    trips = []
    joined = 0

    for s in sessions:
        oid = s.get("order_id")
        order = orders.get(oid)
        joined_flag = order is not None
        if joined_flag:
            joined += 1

        # Basket, enriched with catalog titles. The AMOUNT shown is never
        # recomputed from catalog prices — it is the amount captured on the
        # order, so a price change cannot make the replay lie.
        basket = []
        for item in s.get("basket", []):
            sku = item.get("sku")
            prod = products.get(sku, {})
            basket.append({
                "sku": sku,
                "title": prod.get("title", sku),
                "qty": item.get("qty", 1),
                "category": prod.get("category"),
                "unit_paise": prod.get("price_paise"),
            })

        payments = pays_by_order.get(oid, []) if oid else []
        steps = build_steps(audit_by_corr.get(order["correlation_id"], [])) if order else []

        budget = s.get("budget_paise")
        total = s.get("basket_total_paise")
        headroom = None
        if budget and total is not None:
            headroom = round((budget - total) / budget * 100, 1)

        trips.append({
            "session_id": s.get("session_id"),
            "persona": s.get("persona"),
            "ts": s.get("ts"),
            "llm": s.get("llm"),
            "arm": s.get("arm"),
            "joined": joined_flag,
            "reasoning": s.get("analysis"),
            "basket": basket,
            "budget_paise": budget,
            "basket_total_paise": total,
            "budget_headroom_pct": headroom,
            "outcome": s.get("outcome"),
            "payment_status": s.get("payment_status"),
            "attempts": s.get("attempts"),
            "notes": s.get("notes", []),
            "internal_order_id": oid,
            "razorpay_order_id": order.get("rp_order_id") if order else None,
            "order_status": order.get("status") if order else None,
            "mandate_id": order.get("mandate_id") if order else None,
            "correlation_id": order.get("correlation_id") if order else None,
            "payments": [{
                "rp_payment_id": p.get("rp_payment_id"),
                "amount_paise": p.get("amount_paise"),
                "method": p.get("method"),
                "status": p.get("status"),
                "attempt_no": p.get("attempt_no"),
                "created_at": p.get("created_at"),
            } for p in payments],
            "ledger": steps,
        })

    # ---- scoreboard -------------------------------------------------------
    n = len(trips)
    paid = [t for t in trips if t["outcome"] == "paid"]
    reached = [t for t in trips if t["internal_order_id"]]
    challenged = [t for t in trips if t["outcome"] == "risk_challenged"]
    captured = sum(
        p["amount_paise"] for t in paid for p in t["payments"]
        if p["status"] == "captured" and p["amount_paise"]
    )
    bounded = [t for t in trips if t["budget_paise"]]
    violations = [t for t in bounded
                  if t["basket_total_paise"] and t["basket_total_paise"] > t["budget_paise"]]

    # ---- escalation: challenge rate across thirds, chronological ----------
    ordered = sorted(trips, key=lambda t: t["ts"] or "")
    segments = []
    if ordered:
        k = max(1, len(ordered) // 3)
        for i in range(3):
            chunk = ordered[i * k:(i + 1) * k] if i < 2 else ordered[2 * k:]
            if not chunk:
                continue
            ch = sum(1 for t in chunk if t["outcome"] == "risk_challenged")
            segments.append({
                "segment": i + 1, "n": len(chunk), "challenged": ch,
                "challenge_rate_pct": round(ch / len(chunk) * 100, 1),
            })

    scoreboard = {
        "label": args.label,
        "n": n,
        "paid": len(paid),
        "paid_pct": round(len(paid) / n * 100, 1) if n else 0,
        "risk_challenged": len(challenged),
        "reached_gateway": len(reached),
        "reached_gateway_pct": round(len(reached) / n * 100, 1) if n else 0,
        "completed_of_reached_pct": round(len(paid) / len(reached) * 100, 1) if reached else 0,
        "captured_paise": captured,
        "captured_display": money(captured),
        "bounded_sessions": len(bounded),
        "overspend_violations": len(violations),
        "first_attempt_completions": sum(1 for t in paid if t["attempts"] == 1),
    }

    # ---- featured trips ---------------------------------------------------
    # Prefer a paid trip whose ledger is longest (most to show), and a failure
    # trip that is a genuine risk challenge rather than an infra error.
    paid_sorted = sorted(paid, key=lambda t: -len(t["ledger"]))
    featured = paid_sorted[0]["session_id"] if paid_sorted else None
    failure = next((t["session_id"] for t in trips
                    if t["outcome"] == "risk_challenged"), None)

    fixture = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": {
            "buyer_side": str(args.sessions),
            "merchant_side": src.get("source_db"),
            "source_generated_at": src.get("generated_at"),
            "note": ("Every razorpay_order_id and rp_payment_id below was read "
                     "from the merchant database. Nothing is synthesised. "
                     "Outcomes are predetermined by history, not re-negotiated."),
        },
        "join_stats": {
            "sessions": n,
            "joined_to_merchant_db": joined,
            "join_rate_pct": round(joined / n * 100, 1) if n else 0,
        },
        "scoreboard": scoreboard,
        "risk_escalation": {
            "segments": segments,
            "reading": ("Identical code, identical keys, identical venue. The only "
                        "variable is how much traffic the key has seen. Fraud "
                        "controls are stateful: agentic traffic makes them "
                        "stricter, not stable."),
        },
        "featured_trip": featured,
        "failure_trip": failure,
        "governance": build_governance(src),
        "catalog_size": len(products),
        "trips": trips,
    }

    Path(args.out).write_text(
        json.dumps(fixture, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {args.out}")
    print(f"  sessions={n} joined={joined} ({fixture['join_stats']['join_rate_pct']}%)")
    print(f"  paid={len(paid)} ({scoreboard['paid_pct']}%)  "
          f"captured={scoreboard['captured_display']}  "
          f"violations={len(violations)}")
    print("  escalation: "
          + " -> ".join(f"{s['challenge_rate_pct']}%" for s in segments))
    print(f"  featured={featured}  failure={failure}")
    gov = fixture["governance"]["counts"]
    print("  governance evidence: " + "  ".join(f"{k}={v}" for k, v in gov.items()))
    if fixture["join_stats"]["join_rate_pct"] < 95:
        print("\n  WARNING: join rate below 95%. Do not ship this fixture — "
              "some trips would show fabricated ids.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
