"""Proposal lifecycle: the ONLY writer of the proposals/approvals tables.

Two-phase execution — agents call `propose()` and nothing else. Money- or
price-affecting actions sit in pending_review until a human approves;
risk=low actions may auto-execute. Every transition appends to the hash chain.

Execution dispatch lives here so the invariant is structural: there is no
code path from an agent to the catalog that bypasses policy + approval.
"""

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from bazaar import audit
from bazaar import bundles
from bazaar import policy
from bazaar.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _catalog_ctx(conn) -> dict:
    products = {}
    for r in conn.execute("SELECT sku, price_paise, cost_paise, active,"
                          " discount_until FROM products"):
        discounted = bool(r["discount_until"] and r["discount_until"] > _now())
        products[r["sku"]] = {
            "price_paise": r["price_paise"], "cost_paise": r["cost_paise"],
            "active": bool(r["active"]), "discounted": discounted,
        }
    return {"products": products}


def propose(actor: str, action_type: str, params: dict,
            correlation_id: str | None = None) -> dict:
    """Run the policy engine and record the outcome. Returns the proposal."""
    conn = connect()
    try:
        ctx = _catalog_ctx(conn)
        decision = policy.evaluate(action_type, params, ctx)
        correlation_id = correlation_id or params.get("correlation_id") or \
            f"prop-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        pid = ("prp_" +
               __import__("hashlib").sha256(
                   f"{actor}:{action_type}:{json.dumps(params, sort_keys=True)}"
                   f":{correlation_id}".encode()).hexdigest()[:16])

        if decision.status == "deny":
            status = "rejected"  # no safe interpretation -> terminal, not queued
        elif decision.status == "allow" and not decision.needs_approval:
            status = "auto_executed"
        else:
            status = "pending_review"
        conn.execute(
            """INSERT INTO proposals
               (id, correlation_id, actor, action_type, context_json,
                decision_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, correlation_id, actor, action_type,
             json.dumps(ctx, sort_keys=True),
             json.dumps({**asdict(decision)}, sort_keys=True),
             status, _now()),
        )
        audit.append(conn, actor=actor, action_type="proposal.created",
                     payload={"proposal_id": pid, "action_type": action_type,
                              "asked": params, "decision": decision.status,
                              "final": decision.final_params,
                              "rules": decision.rule_ids},
                     correlation_id=correlation_id)

        result = {"proposal_id": pid, "status": status,
                  "decision": asdict(decision)}
        if status == "auto_executed":
            conn.commit()
            result["execution"] = execute(pid)
            return result
        conn.commit()
        return result
    finally:
        conn.close()


def decide(proposal_id: str, decided_by: str, approved: bool) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT status FROM proposals WHERE id = ?",
                           (proposal_id,)).fetchone()
        if not row:
            raise LookupError(f"unknown proposal {proposal_id}")
        if row["status"] != "pending_review":
            raise ValueError(f"proposal {proposal_id} not pending "
                             f"(status={row['status']})")
        decision = "approved" if approved else "rejected"
        conn.execute(
            "INSERT INTO approvals (proposal_id, decided_by, decision, decided_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(proposal_id) DO UPDATE SET decided_by=excluded.decided_by,"
            " decision=excluded.decision, decided_at=excluded.decided_at",
            (proposal_id, decided_by, decision, _now()),
        )
        conn.execute("UPDATE proposals SET status = ?, resolved_at = ? WHERE id = ?",
                     (decision, _now(), proposal_id))
        audit.append(conn, actor=f"human:{decided_by}",
                     action_type=f"proposal.{decision}",
                     payload={"proposal_id": proposal_id},
                     correlation_id=proposal_id)
        conn.commit()
        return {"proposal_id": proposal_id, "status": decision}
    finally:
        conn.close()


def execute(proposal_id: str) -> dict:
    """Apply an approved/auto-executed proposal to state. Idempotent per status."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?",
                           (proposal_id,)).fetchone()
        if not row:
            raise LookupError(f"unknown proposal {proposal_id}")
        if row["status"] not in ("approved", "auto_executed"):
            raise ValueError(f"proposal {proposal_id} not executable "
                             f"(status={row['status']})")

        decision = json.loads(row["decision_json"])
        if decision["status"] == "deny":
            raise ValueError(f"proposal {proposal_id} was denied by policy")
        params = decision["final_params"]

        if row["action_type"] == "apply_discount":
            until = (datetime.now(timezone.utc) +
                     timedelta(days=int(params["days"]))).isoformat(timespec="seconds")
            conn.execute(
                """UPDATE products SET
                     base_price_paise = COALESCE(base_price_paise, price_paise),
                     price_paise = ?, discount_until = ?
                   WHERE sku = ?""",
                (params["new_price_paise"], until, params["sku"]),
            )
        elif row["action_type"] == "create_bundle":
            bid = bundles.bundle_id(params["skus"])
            conn.execute(
                """INSERT OR REPLACE INTO bundles
                   (id, skus_json, price_paise, active, created_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (bid, json.dumps(params["skus"]), params["price_paise"], _now()),
            )
        elif row["action_type"] == "restock_alert":
            pass  # notification-only; existence recorded by audit below
        else:
            raise ValueError(f"no executor for '{row['action_type']}'")

        conn.execute("UPDATE proposals SET resolved_at = ? WHERE id = ?",
                     (_now(), proposal_id))
        audit.append(conn, actor="executor", action_type="proposal.executed",
                     payload={"proposal_id": proposal_id,
                              "action_type": row["action_type"],
                              "applied": params},
                     correlation_id=row["correlation_id"])
        conn.commit()
        return {"proposal_id": proposal_id, "executed": True, "applied": params}
    finally:
        conn.close()


def list_proposals(status: str | None = None, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        q = ("SELECT p.id, p.correlation_id, p.actor, p.action_type, p.status,"
             " p.decision_json, p.created_at, p.resolved_at,"
             " a.decision AS human_decision, a.decided_by"
             " FROM proposals p LEFT JOIN approvals a ON a.proposal_id = p.id")
        args: list = []
        if status:
            q += " WHERE p.status = ?"
            args.append(status)
        q += " ORDER BY p.created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(q, args)]
    finally:
        conn.close()


def refresh_expired_discounts() -> int:
    """Revert prices whose discount window ended. Cheap, idempotent, safe to
    call before any pricing read (orders) instead of running a scheduler."""
    conn = connect()
    try:
        cur = conn.execute(
            """UPDATE products SET
                 price_paise = COALESCE(base_price_paise, price_paise),
                 base_price_paise = NULL, discount_until = NULL
               WHERE discount_until IS NOT NULL AND discount_until <= ?""",
            (_now(),),
        )
        n = cur.rowcount
        if n:
            audit.append(conn, actor="system", action_type="discount.expired_revert",
                         payload={"reverted_skus": n},
                         correlation_id=f"discount-sweep-{_now()}")
            conn.commit()
        return n
    finally:
        conn.close()
