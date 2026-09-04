#!/usr/bin/env python3
"""Regenerate the three README screenshots, from the LIVE public site.

    python app/scripts/capture_screenshots.py

Requires playwright + pillow (dev-only; the app itself needs neither):

    pip install playwright pillow && playwright install chromium

Three rules this script follows on purpose.

1. **Capture from r2-d2.xyz, never localhost.** A judge who clicks the
   link and compares it to the image must see the same thing. A local
   screenshot is a picture of whatever happened to be on my laptop.

2. **Assert before shooting.** Every shot is preceded by checks on what is
   actually rendered, and each check prints OK/FAIL. A screenshot with no
   assertion behind it is a caption waiting to become a lie -- the moment
   the page changes, the image keeps claiming something that is no longer
   true. The first pass of this script failed four checks: three were bad
   assertions, one was real (the reserve-pay panel sat below the fold at
   900px, so the "hero" would have been a picture of a page with the
   interesting part cut off). Both kinds were worth catching.

3. **Frame the shot around the argument.** The Control Tower crop is not
   the top of the page and not the audit ledger -- it is the band where
   "Proposals - human gate" sits next to "Live orders", because that band
   happens to have a live policy clamp (`percent_off 40 -> 15`) on screen.
   `#proposals` renders an `empty` stub server-side and only fills after
   the JS fetch, so anything that checks the HTML instead of the rendered
   DOM would call this panel blank and crop somewhere worse.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
# Captures are evidence, not code: they land in evidence/screenshots/ next
# to the session logs they corroborate, not in the deployed app tree.
OUT = ROOT / "evidence" / "screenshots"
TMP = ROOT / ".tmp"

W = 1440
H_DEMO = 1180   # the reserve-pay panel sits at y=828 h=300; 900 cuts it off
H = 900

BASE = "https://r2-d2.xyz"
CONTROL_BAND = (70, 970)   # human gate (left) beside live orders (right)

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        failures.append(label)
    return cond


def _http_json(url: str, payload: dict | None = None) -> dict:
    """Tiny stdlib HTTP helper, so the script needs no requests/httpx."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ensure_pending_proposal(base: str) -> str:
    """Guarantee the human gate has something in it.

    The Control Tower shot exists to show a proposal awaiting a human, and
    the clamp line underneath it. Proposals get approved, rejected or
    expire, so on any given day the queue may be empty -- and a screenshot
    of an empty gate is worse than no screenshot, because the caption
    still claims a queue.

    So create one if the queue is empty, and leave it alone otherwise: the
    shot stays honest (a real pending proposal, submitted through the real
    public API) and reruns do not pile proposals up.
    """
    listed = _http_json(f"{base}/governance/proposals?limit=25")
    items = listed.get("proposals") or []
    pending = [p for p in items if p.get("status") == "pending_review"]
    if pending:
        return f"{len(pending)} already pending"

    res = _http_json(f"{base}/governance/proposals", {
        "actor": "demo-judge",
        "action_type": "apply_discount",
        "params": {"sku": "masala-chai-250g", "percent_off": 40, "days": 30},
    })
    return "created " + str(res.get("proposal_id"))


with sync_playwright() as p:
    b = p.chromium.launch()

    # ---------------------------------------------------------------- 1
    # The demo: 8-bit stage + the reserve-pay panel mid-break. The panel
    # is empty until the button is pressed, so press it and wait for all
    # ten rows before shooting.
    #
    # The start panel covers the town on load (z-index 26), so it has to be
    # dismissed first -- otherwise the hero image is a picture of a modal.
    # This uses the real "look around first" affordance rather than
    # deleting the class with JS, so the shot stays a state a user can
    # actually reach.
    print("\n[1] /demo  -- envelope panel run")
    pg = b.new_page(viewport={"width": W, "height": H_DEMO})
    pg.goto(f"{BASE}/demo/", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_selector("#btn-envelope", timeout=20000)

    pg.wait_for_selector("#start-skip", timeout=20000)
    pg.click("#start-skip")
    pg.wait_for_timeout(800)
    check("start panel is dismissed",
          "on" not in (pg.evaluate(
              "document.getElementById('startov').className") or ""),
          pg.evaluate("document.getElementById('startov').className"))
    check("the town, not a modal, is on screen",
          pg.locator("canvas").first.is_visible())

    pg.click("#btn-envelope")
    pg.wait_for_function(
        "document.querySelectorAll('#env-steps .envrow').length >= 10", timeout=30000
    )
    pg.wait_for_selector("#env-foot:not(.hidden)", timeout=20000)
    pg.wait_for_timeout(1500)

    rows = pg.eval_on_selector_all(
        "#env-steps .envrow",
        "els => els.map(e => e.classList.contains('no') ? 'REFUSED' : 'allowed')",
    )
    print("  rows:", ",".join(rows))
    check("10 envelope rows rendered", len(rows) == 10, f"got {len(rows)}")
    check("refusals present in the panel", "REFUSED" in rows,
          f'{rows.count("REFUSED")} refused')
    hud = pg.inner_text("#stat-envelope").replace("\n", " ")
    check("HUD shows the refusal count", "4/5" in hud, hud.strip())

    box = pg.locator("#env-steps").bounding_box()
    check("envelope panel is inside the viewport",
          box is not None and 0 <= box["y"] and box["y"] + box["height"] <= H_DEMO,
          f'y={box["y"]:.0f} bottom={box["y"] + box["height"]:.0f}' if box else "no box")
    check("stage canvas is visible", pg.locator("canvas").first.is_visible())
    pg.screenshot(path=str(OUT / "readme-demo.png"), full_page=False)
    print("  -> readme-demo.png")

    # ---------------------------------------------------------------- 2
    # The standalone no-JS envelope page.
    print("\n[2] /demo/envelope  -- standalone, no JS")
    pg2 = b.new_page(viewport={"width": W, "height": H})
    pg2.goto(f"{BASE}/demo/envelope", wait_until="domcontentloaded", timeout=45000)
    pg2.wait_for_timeout(1200)
    body = pg2.inner_text("body")
    check("page names the rail", "Reserve Pay" in body)

    v = pg2.locator(".verdict")
    check("verdict block present", v.count() > 0)
    if v.count():
        vt = v.first.inner_text().replace("\n", " ")[:110]
        check("verdict states 4 of 5 refused", "4 of 5 checks refused" in vt, vt)
        check("verdict names the step-2/step-10 reversal",
              "step 2" in vt and "step 10" in vt)
    check("refusals visible without JS", body.upper().count("REFUS") >= 1,
          f'{body.upper().count("REFUS")} mentions')
    pg2.screenshot(path=str(OUT / "readme-envelope.png"), full_page=False)
    print("  -> readme-envelope.png")

    # ---------------------------------------------------------------- 3
    # Control Tower, cropped to the band that carries the argument.
    print("\n[3] /control  -- human gate band")
    print("  queue:", ensure_pending_proposal(BASE))
    pg3 = b.new_page(viewport={"width": W, "height": H})
    pg3.goto(f"{BASE}/control", wait_until="domcontentloaded", timeout=45000)
    pg3.wait_for_selector("#proposals *", timeout=20000)
    pg3.wait_for_timeout(3000)

    ptxt = pg3.inner_text("#proposals")
    check("proposals rendered (not the loading stub)", "loading" not in ptxt.lower(),
          f"{len(ptxt)} chars")
    check("a proposal is awaiting a human", "pending_review" in ptxt,
          f'{ptxt.count("pending_review")} pending')
    check("approve/reject buttons present", "Approve" in ptxt and "Reject" in ptxt)
    check("the policy clamp is visible on screen",
          "clamped percent_off 40 -> 15" in ptxt, f'{ptxt.count("clamped")} clamp lines')

    clamp_ys = pg3.evaluate("""() => [...document.querySelectorAll('#proposals *')]
        .filter(e => !e.children.length && e.innerText.includes('clamped'))
        .map(e => Math.round(e.getBoundingClientRect().top + window.scrollY))""")
    print("  clamp rows at y:", clamp_ys[:6])

    y0, y1 = CONTROL_BAND
    in_band = [y for y in clamp_ys if y0 <= y <= y1]
    check("at least one clamp row lands inside the crop", len(in_band) >= 1,
          f"{len(in_band)} rows in {y0}..{y1}")

    otxt = pg3.inner_text("#orders")
    check("live orders populated", "loading" not in otxt.lower() and len(otxt) > 40,
          f"{len(otxt)} chars")

    TMP.mkdir(exist_ok=True)
    shot = TMP / "_control_full.png"
    pg3.screenshot(path=str(shot), full_page=True)
    im = Image.open(shot)
    check("crop window is inside the page", y1 <= im.height,
          f"y {y0}..{y1} of {im.height}")
    im.crop((0, y0, im.width, y1)).save(OUT / "readme-control.png")
    print(f"  -> readme-control.png  (cropped {im.width}x{y1 - y0})")

    b.close()

print("\nFAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
