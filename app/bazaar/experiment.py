"""Merchant-side A/B arm switch (PREREGISTRATION.md).

Flips the storefront between the two measured states:

  control   — base catalog prices, every bundle deactivated
  treatment — every EXECUTED agent action re-applied (discounts + bundles)

Treatment's definition is read from the audit ledger (`proposal.executed`
records): exactly what the policy engine clamped, a human approved, and the
executor applied. The experiment therefore cannot invent an action that was
never governed — it can only replay governed ones. Every switch is itself
an audited event (`experiment.arm_switch`).

Switches always normalize through control first, so repeated toggles are
idempotent and the base price can never compound. (The buyer-side harness
lives in exp/; this module only touches merchant state, next to the DB.)
"""

import json
from datetime import datetime, timedelta, timezone

from bazaar.audit import append
from bazaar.bundles import bundle_id
from bazaar.db import connect

ARMS = ("control", "treatment")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _executed_treatment(conn) -> tuple[dict[str, dict], dict[tuple[str, ...], int]]:
    """Replay the ledger: latest executed discount per sku, latest executed
    bundle per component set."""
    discounts: dict[str, dict] = {}
    bundles: dict[tuple[str, ...], int] = {}
    for row in conn.execute(
        "SELECT payload FROM audit_log"
        " WHERE action_type = 'proposal.executed' ORDER BY seq"
    ):
        applied = json.loads(row["payload"]).get("applied") or {}
        if "sku" in applied and "new_price_paise" in applied:
            discounts[applied["sku"]] = applied          # latest execution wins
        elif "skus" in applied and "price_paise" in applied:
            bundles[tuple(applied["skus"])] = applied["price_paise"]
    return discounts, bundles


def _apply_control(conn) -> list[str]:
    """Base prices everywhere, bundles off. Returns the skus that were
    actually reverted (for the audit record)."""
    reverted = [r["sku"] for r in conn.execute(
        "SELECT sku FROM products"
        " WHERE base_price_paise IS NOT NULL OR discount_until IS NOT NULL")]
    conn.execute(
        """UPDATE products SET
             price_paise = COALESCE(base_price_paise, price_paise),
             base_price_paise = NULL, discount_until = NULL""")
    conn.execute("UPDATE bundles SET active = 0")
    return reverted


def set_arm(arm: str) -> dict:
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    conn = connect()
    try:
        discounts, bundles = _executed_treatment(conn)
        reverted = _apply_control(conn)

        active_discounts: dict[str, int] = {}
        active_bundles: list[str] = []
        if arm == "treatment":
            now = datetime.now(timezone.utc)
            for sku, p in discounts.items():
                until = (now + timedelta(days=int(p["days"]))).isoformat(
                    timespec="seconds")
                cur = conn.execute(
                    """UPDATE products SET base_price_paise = price_paise,
                         price_paise = ?, discount_until = ?
                       WHERE sku = ?""",
                    (p["new_price_paise"], until, sku),
                )
                if cur.rowcount:
                    active_discounts[sku] = p["new_price_paise"]
            for skus in bundles:
                conn.execute("UPDATE bundles SET active = 1 WHERE id = ?",
                             (bundle_id(list(skus)),))
                active_bundles.append(bundle_id(list(skus)))

        append(conn, actor="experiment", action_type="experiment.arm_switch",
               payload={"arm": arm, "reverted_to_base": reverted,
                        "discounts_active": active_discounts,
                        "bundles_active": active_bundles},
               correlation_id=f"arm-switch-{_now()}")
        conn.commit()
        return {"arm": arm, "reverted": reverted,
                "discounts_active": active_discounts,
                "bundles_active": active_bundles}
    finally:
        conn.close()


def current_state() -> dict:
    """Read-only view for the toggle CLI and pre-run verification."""
    conn = connect()
    try:
        products = {
            r["sku"]: {"price_paise": r["price_paise"],
                       "base_price_paise": r["base_price_paise"],
                       "discounted": bool(r["discount_until"])}
            for r in conn.execute(
                "SELECT sku, price_paise, base_price_paise, discount_until"
                " FROM products ORDER BY sku")
        }
        active_bundles = [r["id"] for r in conn.execute(
            "SELECT id FROM bundles WHERE active = 1 ORDER BY id")]
        discounted = sorted(s for s, p in products.items() if p["discounted"])
        return {"discounted_skus": discounted,
                "active_bundles": active_bundles,
                "looks_like": "treatment" if (discounted or active_bundles)
                              else "control",
                "products": products}
    finally:
        conn.close()
