#!/usr/bin/env python3
"""dump_replay_source.py — export the authoritative evidence needed for a replay.

WHY THIS EXISTS
---------------
The session JSONL files in evidence/ record what the *buyer* did: the
persona, the LLM's reasoning, the basket it chose, its budget, and the
outcome. What they do NOT record is the merchant-side truth — the real
Razorpay order id, the real payment id, and the ledger rows the trip
produced. Those live only in the merchant database on VM1.

This script runs on the VM that owns the database and exports that
merchant-side truth as JSON, so a replay fixture can be built by joining
the two halves. Without this join a "replay" would be a mock; with it,
every id shown on screen is real.

It CHANGES NOTHING. Read-only: opens the database in read-only mode and
prints counts. No writes, no network, no credentials required.

USAGE
-----
    python scripts/dump_replay_source.py -o /tmp/replay_source.json

Run it on the host that has bazaar.db (VM1), then pull the JSON back.

PRIVACY NOTE
------------
This dumps buyer_session_id and persona-adjacent fields, but no real
customer data exists — every buyer in this system is a synthetic persona
(arjun / meera / ritika) driving a test-mode store. Review the output
before committing it anywhere public.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Tables and columns the replay needs. Nothing else is exported.
ORDERS_COLS = [
    "id", "rp_order_id", "buyer_session_id", "channel", "items_json",
    "total_paise", "status", "attempt_no", "mandate_id", "correlation_id",
    "created_at", "updated_at", "bundle_id",
]
PAYMENTS_COLS = [
    "id", "order_id", "attempt_no", "rp_payment_id", "method",
    "amount_paise", "status", "error_code", "error_desc", "created_at",
]
AUDIT_COLS = [
    "seq", "ts_utc", "actor", "action_type", "payload",
    "prev_hash", "self_hash", "correlation_id",
]
MANDATES_COLS = [
    "id", "buyer_ref", "budget_cap_paise", "spent_paise",
    "max_single_txn_paise", "allowed_categories_json", "expires_at",
    "revoked_at", "created_at",
]

# The catalog is exported so a replay can render "2x Masala Chai 250g"
# instead of "2x masala-chai-250g". Prices are NOT taken from here for any
# money decision in the replay — they are display only. The authoritative
# amount is the one captured on the order row.
PRODUCT_COLS: list[str] | None = None  # resolved at runtime from the schema


def _rows(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    collist = ", ".join(cols)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f"SELECT {collist} FROM {table}")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", required=True, help="output JSON path")
    ap.add_argument("--db", default=None,
                    help="path to bazaar.db (default: next to this script's parent app dir)")
    args = ap.parse_args()

    db_path = args.db
    if db_path is None:
        here = Path(__file__).resolve().parent
        candidates = [here.parent / "bazaar.db", here / "bazaar.db",
                      Path("bazaar.db").resolve()]
        for cand in candidates:
            if cand.exists():
                db_path = str(cand)
                break
    if db_path is None or not Path(db_path).exists():
        print("could not locate bazaar.db — pass --db", file=sys.stderr)
        return 2

    # read-only URI so this can never mutate evidence
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    orders = _rows(conn, "orders", ORDERS_COLS)
    payments = _rows(conn, "payments", PAYMENTS_COLS)
    audit = _rows(conn, "audit_log", AUDIT_COLS)
    mandates = _rows(conn, "mandates", MANDATES_COLS)

    # Products: take whatever columns exist rather than hardcoding, so this
    # keeps working if the catalog schema gains a field.
    conn.row_factory = sqlite3.Row
    pcols = [r[1] for r in conn.execute("PRAGMA table_info(products)")]
    products = [dict(r) for r in conn.execute(
        f"SELECT {', '.join(pcols)} FROM products")]

    # Parse the audit payload JSON so consumers don't have to. A payload that
    # fails to parse is kept verbatim rather than dropped — a replay must never
    # silently lose a ledger row.
    for row in audit:
        raw = row.get("payload")
        try:
            row["payload"] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            row["payload"] = {"_unparsed": raw}

    # Index audit rows by correlation id: the replay wants "the ledger rows
    # this trip produced", and correlation_id is the thread that ties them.
    audit_by_corr: dict[str, list[dict]] = {}
    for row in audit:
        cid = row.get("correlation_id") or "_none"
        audit_by_corr.setdefault(cid, []).append(row)
    for cid in audit_by_corr:
        audit_by_corr[cid].sort(key=lambda r: r["seq"])

    payments_by_order: dict[str, list[dict]] = {}
    for row in payments:
        payments_by_order.setdefault(row["order_id"], []).append(row)
    for oid in payments_by_order:
        payments_by_order[oid].sort(key=lambda r: r["attempt_no"] or 0)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(db_path),
        "counts": {
            "orders": len(orders), "payments": len(payments),
            "audit_log": len(audit), "mandates": len(mandates),
            "products": len(products),
        },
        "orders": orders,
        "payments_by_order": payments_by_order,
        "audit_by_correlation": audit_by_corr,
        "mandates": mandates,
        "products": products,
    }

    Path(args.out).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  orders={len(orders)} payments={len(payments)} "
          f"audit={len(audit)} mandates={len(mandates)} products={len(products)}")
    print(f"  correlation threads: {len(audit_by_corr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
