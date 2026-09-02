"""Regression suite for the live UPI Reserve Pay envelope demo.

The properties that matter here, in order:

1. **Isolation** — the demo writes to its own store, so the merchant
   ledger we publish (704 records, chain_ok) is untouched by a judge
   clicking /demo. If this ever regresses, every number in the README
   becomes falsified by the act of verification.
2. **One reason per refusal** — each bound is demonstrated alone. A step
   that fails for two reasons proves neither.
3. **The reversal** — the request allowed at step 2 is refused at step 10
   by the same code path, for one named reason.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar import audit, db, envelope  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    print(("ok  " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        raise SystemExit(f"FAILED: {name} {detail}")
    PASS += 1


def merchant_audit_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


before = merchant_audit_count()
data = envelope.run_sequence(buyer_ref="test-buyer")
after = merchant_audit_count()

steps = {s["n"]: s for s in data["steps"]}
checks = [s for s in data["steps"] if s["kind"] == "check"]
refused = [s for s in checks if not s["allowed"]]

# -- 1. isolation ---------------------------------------------------------
ok("demo does not write to the merchant ledger", before == after,
   f"{before} -> {after}")
ok("demo ledger is a separate store",
   data["demo_ledger"]["store"] == "envelope_demo.db",
   data["demo_ledger"]["store"])
ok("demo ledger chain verifies", data["demo_ledger"]["chain_ok"] is True)
ok("demo ledger has no bad sequence",
   data["demo_ledger"]["first_bad_seq"] is None)
ok("demo ledger is non-empty (it really ran)",
   data["demo_ledger"]["records"] > 0,
   f"{data['demo_ledger']['records']} records")

# -- 2. the sequence itself ----------------------------------------------
ok("envelope was created and signed",
   data["mandate_id"].startswith("mnt_") and len(data["signature"]) == 64,
   data["mandate_id"])
ok("five checks were made", len(checks) == 5, str(len(checks)))
ok("four were refused", len(refused) == 4, str(len(refused)))

ok("step 2 (inside every bound) is allowed", steps[2]["allowed"] is True)
ok("step 4 refused on the single-txn cap alone",
   steps[4]["rules"] == ["single-txn cap"], str(steps[4]["rules"]))
ok("step 5 refused on category alone",
   steps[5]["rules"] == ["category outside envelope"], str(steps[5]["rules"]))
ok("step 8 refused on budget alone",
   steps[8]["rules"] == ["budget exhausted"], str(steps[8]["rules"]))
ok("step 10 refused on revocation alone",
   steps[10]["rules"] == ["envelope revoked"], str(steps[10]["rules"]))

# -- 3. one reason per refusal (the isolation-of-proof property) ---------
multi = [s["n"] for s in refused if len(s["rules"]) != 1]
ok("every refusal fires exactly ONE bound", not multi, f"steps {multi}")
ok("four distinct bounds demonstrated",
   len(data["distinct_refusal_rules"]) == 4,
   str(data["distinct_refusal_rules"]))

# -- 4. the reversal -----------------------------------------------------
ok("step 10 is the same request as step 2",
   steps[10]["detail"] == steps[2]["detail"],
   f"{steps[10]['detail']} vs {steps[2]['detail']}")
ok("the same request passes at 2 and fails at 10",
   steps[2]["allowed"] and not steps[10]["allowed"])

# -- 5. spend accounting -------------------------------------------------
expected_spend = sum(envelope.CAPTURES_PAISE)
ok("spend drawn down to the captured total",
   data["spent_paise"] == expected_spend,
   f"{data['spent_paise']} vs {expected_spend}")
ok("spend stays within the budget cap",
   data["spent_paise"] <= data["budget_cap_paise"])
ok("envelope is revoked at the end", data["revoked_at"] is not None)

# -- 6. repeatability ----------------------------------------------------
again = envelope.run_sequence(buyer_ref="test-buyer")
ok("a second run gets a fresh envelope",
   again["mandate_id"] != data["mandate_id"])
ok("a second run still leaves the merchant ledger alone",
   merchant_audit_count() == before,
   f"{merchant_audit_count()} vs {before}")
ok("the demo ledger chain survives repeated runs",
   again["demo_ledger"]["chain_ok"] is True)
ok("the demo ledger grows (it accumulates evidence)",
   again["demo_ledger"]["records"] > data["demo_ledger"]["records"],
   f"{data['demo_ledger']['records']} -> {again['demo_ledger']['records']}")

print(f"\nENVELOPE: {PASS} CHECKS PASSED")
