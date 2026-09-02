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

import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "app" / "artifacts"
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


with sync_playwright() as p:
    b = p.chromium.launch()

    # ---------------------------------------------------------------- 1
    # The demo: 8-bit stage + the reserve-pay panel mid-break. The panel
    # is empty until the button is pressed, so press it and wait for all
    # ten rows before shooting.
    print("\n[1] /demo  -- envelope panel run")
    pg = b.new_page(viewport={"width": W, "height": H_DEMO})
    pg.goto(f"{BASE}/demo/", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_selector("#btn-envelope", timeout=20000)
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
