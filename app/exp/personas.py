"""LLM buyer personas — the demand side of the experiment.

Each persona is an independent AI shopper: it reads the PUBLIC catalog feed
(GET /catalog), decides a basket inside its own hard budget, then pays via
the real checkout (exp.checkout). The merchant core never sees persona logic
— only orders any buyer could have placed.

Bounds live in code, not just prompts (same discipline as the merchant's
policy engine): skus must exist and be in stock, qty is clamped, line count
capped, and the budget cap is enforced by dropping lines — an LLM suggestion
can never overspend.

Boundary note: this package reuses the shared llm adapter (generic
infrastructure, provider swappable via env) but imports no merchant domain
module. With LLM_PROVIDER=mock a deterministic heuristic keeps the whole
pipeline runnable keylessly.
"""

import asyncio
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from bazaar.config import settings
from bazaar.llm import LLMError, complete

MAX_QTY_DEFAULT = 2
MAX_LINES_DEFAULT = 2

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    blurb: str
    budget_paise: int
    email: str
    max_lines: int = MAX_LINES_DEFAULT
    max_qty: int = MAX_QTY_DEFAULT


PERSONAS: dict[str, Persona] = {
    "ritika": Persona(
        id="ritika", name="Ritika",
        blurb="Price-sensitive bargain hunter. Loves a discount, compares "
              "prices, avoids premium/experience items.",
        budget_paise=35_000, email="ritika.buyer@gmail.com",
    ),
    "arjun": Persona(
        id="arjun", name="Arjun",
        blurb="Premium seeker. Buys the high end — single-estate teas, "
              "masterclasses, gift-worthy boxes. Quality over price.",
        budget_paise=150_000, email="arjun.buyer@gmail.com",
    ),
    "meera": Persona(
        id="meera", name="Meera",
        blurb="Gift shopper. Looks for items that go well together "
              "(pairs_with hints) and pleasing combinations.",
        budget_paise=100_000, email="meera.buyer@gmail.com",
    ),
}


def _identity(p: Persona) -> dict:
    """Stable per-persona buyer identity, kept across sessions like a real
    returning shopper (new device+phone every order reads as card-testing
    to risk engines — and is worse experiment design too)."""
    path = ARTIFACTS / "persona_identities.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    if p.id not in data:
        from exp.checkout import random_phone
        data[p.id] = {"phone": random_phone()}
        ARTIFACTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=1))
    return {"phone": data[p.id]["phone"], "email": p.email,
            "profile_dir": str(ARTIFACTS / "profiles" / p.id)}


def _system_for(p: Persona) -> str:
    return f"""You are {p.name}, shopping online at "Chai Bazaar", an Indian D2C chai store.
{p.blurb}

Rules:
- Choose ONLY from the skus listed in the catalog you are given.
- At most {p.max_lines} distinct items; qty between 1 and {p.max_qty} each.
- Your hard budget is Rs {p.budget_paise // 100}. Prices are in paise (100 paise = Rs 1).
- Buying nothing is valid if nothing suits you — return an empty items list.

Reply ONLY strict JSON:
{{"analysis": "<=2 sentences", "items": [{{"sku": "...", "qty": 1}}]}}"""


def _compact(products: list[dict]) -> list[dict]:
    return [
        {
            "sku": x["sku"],
            "title": x["title"],
            "desc": (x.get("description") or "")[:90],
            "price_paise": x["price_paise"],
            "kind": x["kind"],
            "tags": x.get("tags"),
            "pairs_with": x.get("pairs_with"),
        }
        for x in products
    ]


def _load_json(raw: str) -> dict:
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.split("\n", 1)[-1]
    try:
        plan = json.loads(txt)
    except json.JSONDecodeError as exc:
        raise ValueError(f"unparsable basket ({exc}): {txt[:120]!r}") from exc
    if not isinstance(plan.get("items"), list):
        raise ValueError(f"basket plan has no items list: {txt[:120]!r}")
    return plan


def _mock_plan(p: Persona, products: list[dict]) -> dict:
    """Deterministic stand-in so the pipeline runs without a key."""
    avail = [x for x in products if x["in_stock"]]
    if not avail:
        return {"analysis": "nothing in stock; walking away", "items": []}
    by_price = sorted(avail, key=lambda x: x["price_paise"])
    if p.id == "arjun":
        items = [{"sku": by_price[-1]["sku"], "qty": 1}]
    elif p.id == "meera":
        anchor = next((x for x in reversed(by_price) if x.get("pairs_with")),
                      by_price[-1])
        items = [{"sku": anchor["sku"], "qty": 1}]
        partner = next((x for x in avail
                        if x["sku"] in (anchor.get("pairs_with") or [])), None)
        if partner:
            items.append({"sku": partner["sku"], "qty": 1})
    else:  # ritika: cheapest thing on the shelf
        items = [{"sku": by_price[0]["sku"], "qty": 1}]
    return {"analysis": f"mock heuristic pick for {p.name}", "items": items}


async def fetch_catalog(base: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{base.rstrip('/')}/catalog")
        r.raise_for_status()
        return r.json()


async def plan_basket(p: Persona, catalog: dict) -> tuple[dict, str]:
    """LLM (or heuristic) choice. Returns (plan, brain-label)."""
    products = _compact(catalog.get("products", []))
    if settings.llm_provider == "mock":
        return _mock_plan(p, products), "heuristic-mock"
    raw = await complete(
        _system_for(p),
        [{"role": "user", "content": json.dumps({"catalog": products})}],
        smart=False, json_mode=True, temperature=0.7, max_tokens=1500,
        retries=3,
    )
    return _load_json(raw), settings.llm_provider


def constrain_basket(plan: dict, catalog: dict,
                     p: Persona) -> tuple[list[dict], list[str]]:
    """Enforce the hard bounds in code; every deviation becomes a note."""
    by_sku = {x["sku"]: x for x in catalog.get("products", [])}
    lines: list[dict] = []
    notes: list[str] = []
    left = p.budget_paise
    seen: set[str] = set()
    for item in plan.get("items", []):
        sku = str(item.get("sku", ""))
        prod = by_sku.get(sku)
        if prod is None:
            notes.append(f"dropped {sku}: not in catalog")
            continue
        if sku in seen:
            continue
        if not prod.get("in_stock"):
            notes.append(f"dropped {sku}: out of stock")
            continue
        if len(lines) >= p.max_lines:
            notes.append(f"dropped {sku}: line limit {p.max_lines}")
            continue
        try:
            qty = max(1, min(int(item.get("qty", 1)), p.max_qty))
        except (TypeError, ValueError):
            qty = 1
        cost = prod["price_paise"] * qty
        if cost > left:
            notes.append(f"dropped {sku}: exceeds remaining budget")
            continue
        lines.append({"sku": sku, "qty": qty})
        seen.add(sku)
        left -= cost
    total = p.budget_paise - left
    if lines:
        notes.append(f"basket {total}p of {p.budget_paise}p budget")
    return lines, notes


async def run_session(
    base: str,
    persona_id: str,
    *,
    tag: str = "p",
    method: str = "netbanking",
    bank: str = "Canara Bank",
    headed: bool = False,
    attempts: int = 2,
) -> dict:
    """One persona shopping trip, end to end. Returns the session record;
    callers persist it (JSONL) — this module stays IO-free beyond HTTP."""
    from exp.checkout import buy_once  # local import keeps module graph flat

    p = PERSONAS[persona_id]  # KeyError is a caller bug, loud is fine
    sid = f"{persona_id}-{uuid.uuid4().hex[:8]}"
    rec: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": sid, "persona": persona_id, "base": base,
        "budget_paise": p.budget_paise, "llm": None, "analysis": None,
        "basket": [], "basket_total_paise": None, "order_id": None,
        "amount_paise": None, "payment_status": None,
        "outcome": None, "ok": False, "attempts": 0, "notes": [],
    }

    try:
        catalog = await fetch_catalog(base)
    except Exception as exc:
        rec["outcome"] = "merchant_unreachable"
        rec["notes"].append(str(exc)[:200])
        return rec

    try:
        plan, brain = await plan_basket(p, catalog)
    except LLMError as exc:
        rec["outcome"] = "llm_error"
        rec["notes"].append(str(exc)[:200])
        return rec
    except ValueError as exc:
        rec["outcome"] = "invalid_plan"
        rec["notes"].append(str(exc)[:200])
        return rec
    rec["llm"] = brain
    rec["analysis"] = str(plan.get("analysis", ""))[:300]

    lines, notes = constrain_basket(plan, catalog, p)
    rec["basket"], rec["notes"] = lines, notes
    if not lines:
        rec["outcome"] = "walked_away"
        rec["ok"] = True  # a valid session: chose nothing
        return rec
    total = sum(
        next(x["price_paise"] for x in catalog["products"]
             if x["sku"] == ln["sku"]) * ln["qty"]
        for ln in lines
    )
    rec["basket_total_paise"] = total

    res: dict = {}
    idn = _identity(p)
    for attempt in range(1, attempts + 1):
        rec["attempts"] = attempt
        print(f"[{sid}] attempt {attempt}: {lines} (buyer {idn['phone']})")
        res = await buy_once(
            base, lines,
            tag=f"{tag}-{sid}-a{attempt}",
            method=method, bank=bank, headed=headed,
            buyer_name=p.name, buyer_email=idn["email"],
            buyer_session_id=sid, profile_dir=idn["profile_dir"],
        )
        if res["ok"]:
            break
        rec["notes"].append(
            f"attempt {attempt} failed at {res['stage']}: {res['error']}")
        if attempt < attempts:
            # Risk challenges mean "too fast" — a real shopper waits; so do we.
            pause = random.uniform(30, 75) if res.get("stage") == \
                "risk_challenge" else random.uniform(5, 12)
            print(f"[{sid}] backing off {pause:.0f}s")
            await asyncio.sleep(pause)

    rec["order_id"] = res.get("order_id")
    rec["amount_paise"] = res.get("amount_paise")
    rec["payment_status"] = res.get("status")
    if res.get("ok"):
        rec["ok"] = True
        rec["outcome"] = ("paid" if res.get("status") == "paid"
                          else "payment_failed")
    elif res.get("stage") == "risk_challenge":
        rec["outcome"] = "risk_challenged"
    else:
        rec["outcome"] = "infra_error"
    return rec
