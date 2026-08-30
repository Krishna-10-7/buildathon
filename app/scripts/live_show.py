"""BAZAAR LIVE — broadcast one REAL persona session into the pixel town.

A tiny local server (FastAPI is already a project dependency) that:
  - serves bazaar_live.html at /
  - streams session events to viewers over SSE (/api/events) — no new deps
  - runs ONE real persona trip against the merchant on demand (/api/start),
    reusing the exact production building blocks: personas' catalog fetch,
    LLM planning, code-enforced budget clamp, and the shared checkout
    driver with its additive on_event hook.
  - polls the public audit tail so the town's HUD shows the LIVE chain.

This is a demo instrument, not the experiment:
  - it never touches /experiment/arm (no arm flips, ever)
  - it writes NO measurement JSONL — evidence files stay pristine
  - one attempt per start; every outcome (paid / walked away / challenged /
    failed) is broadcast honestly and ends the trip

Three run modes, in order of how likely they are to work right now:

  replay  (DEFAULT) rebuilds a RECORDED trip from artifacts/replay_fixture.json.
          Needs no Razorpay key, no LLM key, no browser, no network. Every
          order id / payment id / amount on screen is real and verifiable —
          only the narration is rebuilt. scripts/replay_source.py.
  live    one real trip, real LLM plan, real Razorpay test checkout.
  mock    rehearsal: real catalog prices, simulated checkout, ids prefixed
          ord_REHEARSAL so a recording can't pass them off as captured.

Replay is the default because the live path depends on a test key that
hCaptcha burns roughly daily (KEY-ROTATION-CHECKLIST.md) and an LLM key
with finite quota. A demo that is dark during judging is worse than a
demo that is honest about being a replay — and a replay of real evidence
beats a synthetic `make demo` outright, because ours is checkable.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

import httpx  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402

from bazaar.config import settings  # noqa: E402
from exp.personas import (  # noqa: E402
    PERSONAS,
    _compact,
    _identity,
    _mock_plan,
    constrain_basket,
    fetch_catalog,
    plan_basket,
)
from scripts import replay_source  # noqa: E402

HTML_PATH = Path(__file__).resolve().parent.parent / "bazaar_live.html"
AUDIT_PERIOD_S = 6.0
MODES = ("replay", "live", "mock")

ARGS = argparse.Namespace(base="https://r2-d2.xyz", port=8321,
                          mode="replay", headed=False)


def replay_ok() -> bool:
    """Is the fixture present and parseable? Cheap enough to re-check."""
    try:
        replay_source.load()
        return True
    except replay_source.ReplayUnavailable:
        return False


def resolve_mode(requested: str | None = None) -> str:
    """Decide which runner a /api/start should use.

    An explicit request wins when that mode is actually runnable;
    otherwise we fall back rather than handing the viewer a dead button.
    """
    want = (requested or ARGS.mode or "replay").lower()
    if want not in MODES:
        want = ARGS.mode
    if want == "replay" and not replay_ok():
        return "mock" if ARGS.base else "live"
    return want

_subs: set[asyncio.Queue] = set()
_session_task: asyncio.Task | None = None
_last_audit: dict | None = None  # replayed to each new viewer so the HUD
                                 # starts informed, not at "chain: ?"
_last_start = 0.0  # public-host guard (behind Caddy): one start per window
START_COOLDOWN_S = 30.0


def broadcast(kind: str, **payload) -> None:
    ev = {"t": kind,
          "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          **payload}
    print("EVENT " + json.dumps(ev)[:500], flush=True)
    for q in list(_subs):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:  # slow viewer: drop rather than block
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def _lifespan(_app):
    poller = asyncio.create_task(audit_poller(ARGS.base.rstrip("/")))
    broadcast("server_up", mode=ARGS.mode, base=ARGS.base,
              modes={m: (replay_ok() if m == "replay" else True)
                     for m in MODES})
    yield
    poller.cancel()


app = FastAPI(lifespan=_lifespan)


# --------------------------------------------------------------- transport

@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subs.add(q)

    async def gen():
        hello = {"t": "hello", "ts": _now(),
                 "mode": ARGS.mode, "base": ARGS.base,
                 # Told up-front so the page can grey out a mode that
                 # would fail, instead of letting the viewer discover it
                 # by pressing the button and getting nothing.
                 "modes": {m: (replay_ok() if m == "replay" else True)
                           for m in MODES}}
        yield f"data: {json.dumps(hello)}\n\n"
        if replay_ok():
            try:
                yield f"data: {json.dumps(replay_source.meta(replay_source.load()))}\n\n"
            except Exception as exc:  # noqa: BLE001 - degrade, never 500 a viewer
                yield f"data: {json.dumps({'t': 'replay_meta_error', 'error': str(exc)[:160]})}\n\n"
        if _last_audit is not None:
            yield f"data: {json.dumps(_last_audit)}\n\n"
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # comment frame; SSE-safe
        finally:
            _subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return FileResponse(HTML_PATH)


@app.post("/api/start")
async def start(req: Request):
    global _session_task, _last_start
    body = await req.json()
    persona = str(body.get("persona", "ritika"))
    if persona not in PERSONAS:
        return {"ok": False, "reason": f"unknown persona {persona!r}"}
    if _session_task is not None and not _session_task.done():
        return {"ok": False, "reason": "a session is already running"}
    now = time.monotonic()
    if now - _last_start < START_COOLDOWN_S:
        return {"ok": False,
                "reason": f"cooldown: try again in "
                          f"{START_COOLDOWN_S - (now - _last_start):.0f}s"}
    _last_start = now

    mode = resolve_mode(body.get("mode"))
    variant = str(body.get("variant") or "paid")
    if mode == "replay":
        data = replay_source.load()
        trip = replay_source.pick_trip(data, persona, variant)
        if trip is None:
            return {"ok": False,
                    "reason": f"no recorded {variant} trip for {persona}"}
        _session_task = asyncio.create_task(
            run_replay_session(trip["session_id"]))
        return {"ok": True, "mode": "replay", "persona": persona,
                "variant": variant, "session_id": trip["session_id"]}

    runner = run_mock_session if mode == "mock" else run_live_session
    _session_task = asyncio.create_task(runner(persona))
    return {"ok": True, "mode": mode, "persona": persona, "variant": variant}


@app.get("/api/state")
async def state():
    running = _session_task is not None and not _session_task.done()
    return {"running": running, "mode": ARGS.mode, "base": ARGS.base,
            "viewers": len(_subs),
            "modes": {m: (replay_ok() if m == "replay" else True)
                      for m in MODES}}


@app.get("/api/replay")
async def replay_index():
    """The evidence behind the replay, served so a viewer (or judge) can
    check the numbers without cloning the repo."""
    try:
        data = replay_source.load()
    except replay_source.ReplayUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **replay_source.meta(data)}


# ------------------------------------------------------------ audit tail

async def audit_poller(base: str) -> None:
    global _last_audit
    last_seq = None
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                r = await client.get(f"{base}/audit/recent",
                                     params={"limit": 1})
                data = r.json()
                recs = data.get("records") or [{}]
                seq = recs[0].get("seq")
                if seq != last_seq:
                    last_seq = seq
                    _last_audit = {
                        "t": "audit", "ts": _now(), "seq": seq,
                        "chain_ok": bool(data.get("chain_ok")),
                        "records_checked": data.get("records_checked"),
                        "latest": str(recs[0].get("action_type") or "")}
                    broadcast("audit", **{
                        k: v for k, v in _last_audit.items()
                        if k not in ("t", "ts")})
            except Exception as exc:
                broadcast("audit_unreachable", error=str(exc)[:120])
            await asyncio.sleep(AUDIT_PERIOD_S)


# --------------------------------------------------------- the real thing

def _with_stock(products: list[dict]) -> list[dict]:
    """_compact drops in_stock (LLM prompt stays small); the mock planner
    still needs it, so re-attach from the source records."""
    by_sku = {x["sku"]: x for x in products}
    out = []
    for x in _compact(products):
        src = by_sku.get(x["sku"], {})
        out.append({**x, "in_stock": bool(src.get("in_stock", True))})
    return out


async def run_live_session(persona_id: str) -> None:
    """One production-blocks shopping trip, narrated event by event."""
    # local import keeps playwright off rehearsal-only hosts (the merchant
    # VM has no browser installed)
    from exp.checkout import buy_once
    base = ARGS.base.rstrip("/")
    p = PERSONAS[persona_id]
    sid = f"{persona_id}-{uuid.uuid4().hex[:8]}"
    broadcast("session_start", persona=persona_id, sid=sid,
              name=p.name, budget_paise=p.budget_paise,
              brain=settings.llm_provider, mode="live")

    try:
        catalog = await fetch_catalog(base)
    except Exception as exc:
        broadcast("outcome", outcome="merchant_unreachable",
                  notes=[str(exc)[:200]], order_id=None, amount_paise=None)
        return

    products = catalog.get("products", [])
    broadcast("catalog", products=_with_stock(products))

    try:
        plan, brain = await plan_basket(p, catalog)
    except Exception as exc:
        kind = "llm_error" if type(exc).__name__ == "LLMError" \
            else "invalid_plan"
        broadcast("outcome", outcome=kind, notes=[str(exc)[:200]],
                  order_id=None, amount_paise=None)
        return

    lines, notes = constrain_basket(plan, catalog, p)
    prices = {x["sku"]: x["price_paise"] for x in products}
    total = sum(prices[l["sku"]] * l["qty"] for l in lines)
    broadcast("plan", analysis=str(plan.get("analysis", ""))[:300],
              brain=brain, items=plan.get("items"), lines=lines,
              notes=notes, total_paise=total, budget_paise=p.budget_paise)
    if not lines:
        broadcast("outcome", outcome="walked_away", notes=notes,
                  order_id=None, amount_paise=None)
        return

    idn = _identity(p)
    res = await buy_once(
        base, lines,
        tag=f"live-{sid}",
        method="netbanking", bank="Canara Bank",
        buyer_name=p.name, buyer_email=idn["email"],
        buyer_session_id=sid, profile_dir=idn["profile_dir"],
        headed=ARGS.headed, debug=False,
        on_event=lambda k, pl: broadcast("ck_" + k, **pl),
    )

    final: dict = {}
    if res.get("order_id"):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                final = (await client.get(
                    f"{base}/orders/{res['order_id']}")).json()
        except Exception:
            final = {}
    pay_id = next((final[k] for k in final
                   if "pay" in k.lower() and isinstance(final[k], str)), None)

    if res["ok"]:
        outcome = "paid" if res.get("status") == "paid" else "payment_failed"
    elif res.get("stage") == "risk_challenge":
        outcome = "risk_challenged"
    else:
        outcome = "infra_error"
    broadcast("outcome", outcome=outcome, order_id=res.get("order_id"),
              amount_paise=res.get("amount_paise"), status=res.get("status"),
              stage=res.get("stage"), error=res.get("error"),
              payment_id=pay_id, notes=[],
              basket=lines, basket_total_paise=total)


# -------------------------------------------------------------- replay

async def run_replay_session(session_id: str) -> None:
    """Rebuild one recorded trip, beat by beat, from the committed fixture.

    No keys, no network, no browser, no database. This is the path that
    keeps /demo alive when hCaptcha has burned the test key and the LLM
    quota is gone — which, on current evidence, is most days.
    """
    try:
        data = replay_source.load()
        events = replay_source.build_events(data, session_id)
    except replay_source.ReplayUnavailable as exc:
        broadcast("outcome", outcome="infra_error", mode="replay",
                  order_id=None, amount_paise=None,
                  error=f"replay fixture unavailable: {exc}",
                  notes=["start --mode live to run a real trip instead"],
                  basket=[], basket_total_paise=None)
        return

    for delay, ev in events:
        if delay:
            await asyncio.sleep(delay)
        payload = {k: v for k, v in ev.items() if k != "t"}
        broadcast(ev["t"], **payload)


# ------------------------------------------------------------ rehearsal

async def run_mock_session(persona_id: str) -> None:
    """Same choreography, real catalog prices, no browser, no gateway calls.
    Ids are explicitly REHEARSAL-marked so a recording can't pass them off
    as captured payments."""
    base = ARGS.base.rstrip("/")
    p = PERSONAS[persona_id]
    sid = f"REHEARSAL-{persona_id}-{uuid.uuid4().hex[:6]}"
    broadcast("session_start", persona=persona_id, sid=sid,
              name=p.name, budget_paise=p.budget_paise,
              brain="rehearsal-mock", mode="mock")

    try:
        catalog = await fetch_catalog(base)
    except Exception as exc:
        broadcast("outcome", outcome="merchant_unreachable",
                  notes=[f"even rehearsal needs the real catalog: "
                         f"{exc}"[:200]],
                  order_id=None, amount_paise=None)
        return
    products = catalog.get("products", [])
    broadcast("catalog", products=_with_stock(products))
    await asyncio.sleep(2.5)

    plan = _mock_plan(p, _with_stock(products))
    lines, notes = constrain_basket(plan, catalog, p)
    prices = {x["sku"]: x["price_paise"] for x in products}
    total = sum(prices[l["sku"]] * l["qty"] for l in lines)
    # honest, basket-specific analysis instead of a canned string - the
    # rehearsal planner really is a heuristic, so say what it actually did
    basket_desc = " + ".join(f"{l['qty']}x {l['sku']}" for l in lines) or "nothing"
    plan["analysis"] = (f"Rehearsal heuristic (no LLM call): picked {basket_desc} "
                        f"= Rs {total/100:.2f}, the anchor basket that fits the "
                        f"Rs {p.budget_paise/100:.0f} budget.")
    broadcast("plan", analysis=str(plan.get("analysis", ""))[:300],
              brain="rehearsal-mock", items=plan.get("items"), lines=lines,
              notes=notes, total_paise=total, budget_paise=p.budget_paise)
    if not lines:
        broadcast("outcome", outcome="walked_away", notes=notes,
                  order_id=None, amount_paise=None)
        return

    steps = [("ck_browser_up", {}, 1.0),
             ("ck_order_created", {"amount_paise": total}, 1.5),
             ("ck_contact_passed", {}, 2.5),
             ("ck_method_selected", {"method": "netbanking"}, 2.0),
             ("ck_bank_picked", {"bank": "Canara Bank"}, 2.0),
             ("ck_bank_confirmed", {"bank": "Canara Bank"}, 1.8)]
    order_id = f"ord_REHEARSAL{uuid.uuid4().hex[:12]}"
    for kind, payload, delay in steps:
        payload.setdefault("order_id", order_id)
        broadcast(kind, **payload)
        await asyncio.sleep(delay)

    broadcast("captured", order_id=order_id, amount_paise=total,
              payment_id=f"pay_REHEARSAL{uuid.uuid4().hex[:10]}")
    await asyncio.sleep(1.5)
    broadcast("outcome", outcome="paid", order_id=order_id,
              amount_paise=total, status="paid", payment_id=None,
              stage="webhook_poll", error=None,
              notes=["REHEARSAL MODE - no real checkout ran; "
                     "prices came from the live catalog"], basket=lines,
              basket_total_paise=total)


# ----------------------------------------------------------------- main

def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=settings.public_base_url)
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--headed", action="store_true",
                    help="show the real checkout browser alongside the town")
    ap.add_argument("--mode", choices=list(MODES), default="replay",
                    help="default run mode (replay needs no keys at all)")
    ap.add_argument("--mock", action="store_true",
                    help="deprecated alias for --mode mock")
    ARGS = ap.parse_args()
    ARGS.base = ARGS.base.rstrip("/")
    if ARGS.mock:  # keep older service files working
        ARGS.mode = "mock"
    if ARGS.mode == "replay" and not replay_ok():
        print("WARNING: no replay fixture — falling back to live mode. "
              "Build one with scripts/build_replay_fixture.py.", flush=True)
        ARGS.mode = "live"

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")


if __name__ == "__main__":
    main()
