"""Governance core: pure policy engine — clamp-not-reject.

No I/O of any kind: no db, no httpx, no settings. `evaluate()` takes a fully
resolved context and returns a Decision; callers own persistence and audit.
This keeps the rules unit-testable and the dependency direction one-way:

    agents -> proposals -> policy (pure)          [domain]
                        -> db / audit             [edges]

Design rule from PLAN.md: an agent asking for something out-of-bounds gets a
SAFE ADJUSTED version (clamp) wherever a safe version exists; `deny` is
reserved for requests with no safe interpretation.
"""

from dataclasses import dataclass, field

# ---- guardrails (single source of truth; surfaced in Control Tower) --------
MAX_DISCOUNT_PCT = 15          # never deeper than this, whatever the agent asks
MAX_DISCOUNT_DAYS = 3          # a price cut must expire fast
MIN_DISCOUNT_PCT = 1           # a discount that rounds to zero is not a discount
BUNDLE_MIN_MARGIN_PCT = 5      # bundle price >= sum(cost) * (1 + margin)
PRICE_FLOOR_RULE = "price_floor_at_cost"
RULE_IDS = {
    "max_discount": "POL-DISC-001",
    "min_discount": "POL-DISC-002",
    "floor": "POL-PRICE-001",
    "duration": "POL-DISC-003",
    "concurrent": "POL-DISC-004",
    "bundle_margin": "POL-BNDL-001",
    "exists": "POL-GEN-001",
}

# Actions an agent may propose, and whether executing them moves money/price.
# risk=low  -> auto-executable when policy allows (no customer-visible change)
# risk=med  -> always requires human approval even when allowed
ACTION_RISK: dict[str, str] = {
    "apply_discount": "med",     # changes what customers pay
    "create_bundle": "med",      # changes what customers pay
    "restock_alert": "low",      # notification only
    "send_offer": "med",         # targets buyers
}


@dataclass
class Decision:
    status: str                      # allow | clamp | deny
    action_type: str
    final_params: dict
    reasons: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)

    @property
    def needs_approval(self) -> bool:
        return ACTION_RISK.get(self.action_type, "med") != "low"


def evaluate(action_type: str, params: dict, ctx: dict) -> Decision:
    """ctx: {"products": {sku: {price_paise, cost_paise, active,
    discounted}}, "active_discounts": int}"""
    handlers = {
        "apply_discount": _eval_discount,
        "create_bundle": _eval_bundle,
        "restock_alert": _eval_restock,
    }
    handler = handlers.get(action_type)
    if handler is None:
        return Decision("deny", action_type, {},
                       [f"unknown action_type '{action_type}'"],
                       [RULE_IDS["exists"]])
    return handler(params, ctx)


def _eval_discount(p: dict, ctx: dict) -> Decision:
    sku = p.get("sku")
    prod = ctx["products"].get(sku or "")
    if not prod or not prod["active"]:
        return Decision("deny", "apply_discount", {}, [f"sku '{sku}' missing/inactive"],
                        [RULE_IDS["exists"]])

    cost, price = prod["cost_paise"], prod["price_paise"]
    reasons, rules = [], []

    pct = max(MIN_DISCOUNT_PCT, min(int(p.get("percent_off", 0)), MAX_DISCOUNT_PCT))
    if pct > MAX_DISCOUNT_PCT:
        pass  # clamped silently below; reason recorded only if it changed
    asked = int(p.get("percent_off", 0))
    if asked != pct:
        if asked < MIN_DISCOUNT_PCT:
            reasons.append(f"raised percent_off {asked} -> {pct} (min {MIN_DISCOUNT_PCT})")
            rules.append(RULE_IDS["min_discount"])
        else:
            reasons.append(f"clamped percent_off {asked} -> {pct} (max {MAX_DISCOUNT_PCT})")
            rules.append(RULE_IDS["max_discount"])

    days = min(int(p.get("days", 1)), MAX_DISCOUNT_DAYS)
    if days != int(p.get("days", 1)):
        reasons.append(f"clamped days {p.get('days')} -> {days}")
        rules.append(RULE_IDS["duration"])

    floor = (cost * (100 + BUNDLE_MIN_MARGIN_PCT)) // 100  # reuse margin floor
    new_price = price - (price * pct) // 100
    if new_price < floor:
        new_price = floor
        pct = max(0, ((price - floor) * 100) // price)
        reasons.append(f"price floored at cost+{BUNDLE_MIN_MARGIN_PCT}% "
                       f"(effective discount {pct}%)")
        rules.append(RULE_IDS["floor"])

    if prod.get("discounted"):
        reasons.append("sku already discounted — concurrent discounts blocked")
        rules.append(RULE_IDS["concurrent"])
        return Decision("deny" if not reasons else "deny", "apply_discount",
                        {}, reasons, rules)

    status = "allow" if not reasons else "clamp"
    return Decision(status, "apply_discount",
                    {"sku": sku, "percent_off": pct, "days": days,
                     "new_price_paise": new_price},
                    reasons, rules)


def _eval_bundle(p: dict, ctx: dict) -> Decision:
    skus = list(p.get("skus", []))[:2]
    prods = [ctx["products"].get(s) for s in skus]
    if len(skus) < 2 or any(pr is None or not pr["active"] for pr in prods):
        return Decision("deny", "create_bundle", {}, ["missing/inactive component sku"],
                        [RULE_IDS["exists"]])

    sum_price = sum(pr["price_paise"] for pr in prods)
    sum_cost = sum(pr["cost_paise"] for pr in prods)
    lo = (sum_cost * (100 + BUNDLE_MIN_MARGIN_PCT)) // 100
    hi = sum_price - 1  # a bundle must actually be a deal

    if lo > hi:
        return Decision("deny", "create_bundle", {},
                        ["no profitable deal price exists for these components"],
                        [RULE_IDS["bundle_margin"]])

    asked = int(p.get("price_paise", sum_price))
    clamped = max(lo, min(asked, hi))
    reasons, rules = [], []
    if clamped != asked:
        reasons.append(f"clamped bundle price {asked} -> {clamped} "
                       f"(profitable deal range {lo}..{hi})")
        rules.append(RULE_IDS["bundle_margin"])

    return Decision("clamp" if reasons else "allow", "create_bundle",
                    {"skus": skus, "price_paise": clamped},
                    reasons, rules)


def _eval_restock(p: dict, ctx: dict) -> Decision:
    sku = p.get("sku")
    if not ctx["products"].get(sku or ""):
        return Decision("deny", "restock_alert", {}, [f"sku '{sku}' missing"],
                        [RULE_IDS["exists"]])
    return Decision("allow", "restock_alert",
                    {"sku": sku, "threshold_days": int(p.get("threshold_days", 7))})
