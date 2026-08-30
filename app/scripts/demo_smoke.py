"""Headless smoke test for /demo.

Drives the real page in a real browser: waits for the SSE handshake,
starts a replay trip, then reports console errors and the text that ended
up on screen. The point is to catch front-end wiring mistakes (a handler
that throws, a chip that never appears, dialogue that still claims "live"
over a replay) which no amount of server-side testing will surface.

Requires playwright, which only lives in the Python 3.12 install:

    "C:/Program Files/Python312/python.exe" scripts/demo_smoke.py

Dev tool for the sprint; not part of the demo.
"""

import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8399"
VARIANT = sys.argv[2] if len(sys.argv) > 2 else "paid"
WAIT_S = float(sys.argv[3]) if len(sys.argv) > 3 else 75.0

errors: list[str] = []
console: list[str] = []
spoken: list[str] = []


def main() -> int:
    with sync_playwright() as pw:
        # A loopback runner must bypass any machine-wide HTTP proxy, which
        # would otherwise answer 127.0.0.1 with a 502 of its own. The public
        # host is the opposite case: it may only be reachable THROUGH that
        # proxy, so leave it alone.
        args = ["--no-sandbox"]
        if re.search(r"//(127\.0\.0\.1|localhost)", BASE):
            args.append("--no-proxy-server")
        browser = pw.chromium.launch(args=args)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        page.on("console", lambda m: (
            console.append(f"{m.type}: {m.text}"),
            errors.append(m.text) if m.type == "error" else None))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)

        # Wait for the runner handshake (start overlay becomes usable).
        page.wait_for_function(
            "() => document.getElementById('start-status') &&"
            " !document.getElementById('start-status').textContent"
            "   .includes('connecting')",
            timeout=20000)

        status = page.inner_text("#start-status")
        print(f"runner status : {status}")
        print("start desc    : " +
              re.sub(r"\s+", " ", page.inner_text("#start-desc"))[:150])

        overlay_on = page.evaluate(
            "() => document.getElementById('startov')"
            ".classList.contains('on')")
        print(f"start overlay : {overlay_on}")

        # Choose the variant (paid / risk gate), then start a trip.
        if VARIANT == "challenged":
            page.click(".pb.vb[data-v='challenged']")
        else:
            page.click(".pb.vb[data-v='paid']")
        page.click(".pb[data-p='ritika']")
        print(f"clicked       : {VARIANT} / ritika")

        # Let the scene play out; the page serialises beats through an
        # animation queue, so this genuinely takes a while.
        deadline = time.time() + WAIT_S
        summary_on = False
        while time.time() < deadline:
            # Capture narration as it appears so we can assert on wording.
            txt = page.evaluate(
                "() => (document.getElementById('dlg-text')||{}).textContent"
                " || ''")
            if txt and (not spoken or spoken[-1] != txt):
                spoken.append(txt)
            summary_on = page.evaluate(
                "() => document.getElementById('sum')"
                ".classList.contains('on')")
            if summary_on:
                break
            time.sleep(0.6)

        print("-" * 72)
        print(f"summary shown : {summary_on}")
        if summary_on:
            for row, sel in (("outcome", "#sum-outcome"), ("order", "#s-order"),
                             ("amount", "#s-amount"), ("basket", "#s-basket"),
                             ("brain", "#s-brain")):
                print(f"  {row:<8}: "
                      f"{re.sub(r'\\s+', ' ', page.inner_text(sel))[:110]}")
            detail = page.inner_text("#s-detail")
            print(f"  detail  : {re.sub(r'\\s+', ' ', detail)[:200]}")

        print("-" * 72)
        print("hud:", re.sub(r"\s+", " ", page.inner_text(".topstats"))[:160])
        print("conn:", page.inner_text("#hud-conn"))

        print("-" * 72)
        joined = " ".join(spoken)
        checks = {
            "says REPLAY (not LIVE)":
                "REPLAY" in joined or "REPLAY" in page.inner_text("#hud-conn"),
            "does NOT claim 'for real'":
                "for real" not in joined.lower(),
            "gateway id shown":
                bool(re.search(r"order_[A-Za-z0-9]{10,}", joined)),
            "no console errors": not errors,
        }
        for k, v in checks.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")

        if errors:
            print("-" * 72)
            print("CONSOLE ERRORS:")
            for e in errors[:15]:
                print("  " + e[:200])

        browser.close()

    ok = not errors and summary_on
    print("-" * 72)
    print("SMOKE " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
