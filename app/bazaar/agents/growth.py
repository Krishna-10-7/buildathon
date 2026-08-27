"""Growth agent v0 — the merchant's daily strategist.

Pipeline (one-way dependencies, nothing here can execute money movement):

    state.snapshot()  ->  llm.complete(smart lane)  ->  proposals.propose()

The LLM sees a compact, numeric snapshot and must answer strict JSON with at
most MAX_ACTIONS_PER_CYCLE actions. Every action is re-checked by the policy
engine downstream — the prompt states the bounds only so clamps stay rare
(defense in depth: prompt guides, engine enforces).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from bazaar import policy, proposals
from bazaar.db import connect
from bazaar.llm import complete

MAX_ACTIONS_PER_CYCLE = 3
LOOKBACK_DAYS = 7

SYSTEM = f"""You are the growth agent for "Chai Bazaar", an Indian D2C chai store.
Each cycle you receive a numeric snapshot (sales last {LOOKBACK_DAYS} days,
stock cover, margins, active discounts). You propose at most
{MAX_ACTIONS_PER_CYCLE} concrete actions to grow net revenue.

Hard bounds the merchant enforces (staying inside them avoids forced clamps):
- apply_discount: percent_off 1..15, days 1..3. Never propose below cost+5%.
- create_bundle: exactly 2 skus, price_paise strictly below sum of prices and
  above sum(cost)*1.05. It must be a real deal.
- restock_alert: sku + threshold_days.

Prefer margin-aware moves over volume chasing; do not discount SKUs that are
already discounted or stock-constrained. If nothing is worth doing, return an
empty actions list — restraint is a valid strategy.

Reply ONLY JSON:
{{"analysis": "<3 sentences max>",
  "actions": [{{"action_type": "...", "params": {{...}}, "rationale": "<1 sentence>"}}]}}"""


def build_snapshot() -> dict:
    """Read-only merchant state for prompting. Numbers only, compact."""
    conn = connect()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)) \
            .isoformat(timespec="seconds")
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        sold: dict[str, int] = {}
        revenue_7d = 0
        orders_7d = 0
        for row in conn.execute(
            "SELECT items_json, total_paise FROM orders"
            " WHERE status='paid' AND created_at >= ?", (since,),
        ):
            orders_7d += 1
            revenue_7d += row["total_paise"]
            for line in json.loads(row["items_json"]):
                sold[line["sku"]] = sold.get(line["sku"], 0) + line["qty"]

        products = []
        for p in conn.execute(
            "SELECT sku, title, price_paise, base_price_paise, cost_paise,"
            " stock, kind, discount_until FROM products WHERE active=1"
            " ORDER BY sku"
        ):
            units = sold.get(p["sku"], 0)
            daily = units / LOOKBACK_DAYS
            cover = round(p["stock"] / daily) if daily > 0 else None
            products.append({
                "sku": p["sku"],
                "title": p["title"][:28],
                "price_paise": p["price_paise"],
                "cost_paise": p["cost_paise"],
                "margin_pct": round(100 * (p["price_paise"] - p["cost_paise"])
                                    / max(p["price_paise"], 1)),
                "stock": p["stock"],
                "units_sold_%dd" % LOOKBACK_DAYS: units,
                "stock_cover_days": cover,
                "discounted_until": p["discount_until"],
                "kind": p["kind"],
            })

        bundles = [dict(r) for r in conn.execute(
            "SELECT id, skus_json, price_paise FROM bundles WHERE active=1")]

        return {"as_of": now_iso, "revenue_paid_%dd" % LOOKBACK_DAYS: revenue_7d,
                "paid_orders_%dd" % LOOKBACK_DAYS: orders_7d,
                "products": products, "active_bundles": bundles}
    finally:
        conn.close()


def _parse_plan(raw: str) -> dict:
    """Tolerate markdown fences; reject anything not JSON-shaped."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.split("\n", 1)[-1]
    try:
        plan = json.loads(txt)
    except json.JSONDecodeError as exc:
        # usually MAX_TOKENS truncation: thinking tokens share the budget
        raise ValueError(f"unparsable plan ({exc}): {txt[:120]!r}") from exc
    if not isinstance(plan.get("actions"), list):
        raise ValueError(f"plan has no actions list: {txt[:120]!r}")
    return plan


async def run_cycle(actor: str = "growth-agent-v0") -> dict:
    """One full strategist cycle. Returns what it PROPOSED — nothing more."""
    snap = build_snapshot()
    raw = await complete(SYSTEM, [{"role": "user",
                                   "content": json.dumps(snap)}],
                         smart=True, json_mode=True, temperature=0.4,
                         max_tokens=3000)  # thinking tokens share this budget
    plan = _parse_plan(raw)

    correlation_id = f"cycle-{uuid.uuid4().hex[:12]}"
    out = []
    for act in plan.get("actions", [])[:MAX_ACTIONS_PER_CYCLE]:
        action_type = act.get("action_type", "")
        rationale = act.get("rationale", "")
        if action_type not in policy.ACTION_RISK:
            out.append({"skipped": action_type, "reason": "unknown action_type",
                        "rationale": rationale})
            continue  # not even proposed; policy would deny anyway
        res = proposals.propose(actor, action_type, act.get("params", {}),
                                correlation_id)
        out.append({"proposal_id": res["proposal_id"], "status": res["status"],
                    "decision": res["decision"]["status"],
                    "final_params": res["decision"]["final_params"],
                    "rules": res["decision"]["rule_ids"],
                    "rationale": rationale})

    return {"correlation_id": correlation_id, "analysis": plan.get("analysis", ""),
            "proposals": out}
