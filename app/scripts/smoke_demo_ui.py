"""Smoke-test the LIVE /demo page in a headless browser.

Why this exists: bazaar_live.html is 1,400+ lines of hand-written shell
around a canvas engine, and it is served from disk by live_show.py with
no build step in between. A typo ships straight to the judges. This
catches the failure mode that matters -- the page loading with a dead
script and silently showing nothing.

Checks:
  1. no uncaught page errors / console errors on load
  2. the SSE stream connects (top bar leaves OFFLINE)
  3. the shell actually rendered: feed panel, stepper, controls, canvas
  4. the canvas engine painted something (not a blank frame)

Usage:
    python scripts/smoke_demo_ui.py                 # https://r2-d2.xyz/demo/
    python scripts/smoke_demo_ui.py --url http://127.0.0.1:8321/
    python scripts/smoke_demo_ui.py --shot ../evidence/screenshots/demo-ui.png
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

DEFAULT_URL = "https://r2-d2.xyz/demo/"

# elements the new shell must expose -- if any of these vanish, the
# chrome and the engine have fallen out of sync
REQUIRED = [
    ("topbar", ".topbar"),
    ("mode stat", "#stat-mode"),
    ("ledger stat", "#stat-chain"),
    ("brain stat", "#stat-brain"),
    ("game canvas", "#game"),
    ("agent feed", "#feed"),
    ("stepper", "#stepper"),
    ("stepper cells", "#stepper .st"),
    ("controls", ".btnrow"),
    ("persona buttons", ".pb"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--shot", default="../evidence/screenshots/demo-ui.png",
                    help="screenshot path (relative to the app dir)")
    ap.add_argument("--wait", type=int, default=6000,
                    help="ms to wait for the SSE stream to settle")
    args = ap.parse_args()

    errors: list[str] = []
    console_errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 950})

        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        print(f"opening {args.url}")
        page.goto(args.url, wait_until="load", timeout=45_000)
        page.wait_for_timeout(args.wait)

        problems: list[str] = []

        # 1. no runtime errors
        if errors:
            problems.append(f"{len(errors)} page error(s): {errors[:3]}")
        # google-fonts / favicon noise is not our bug
        real_console = [c for c in console_errors
                        if "fonts.g" not in c and "favicon" not in c]
        if real_console:
            problems.append(f"{len(real_console)} console error(s): "
                            f"{real_console[:3]}")

        # 2. shell rendered
        for label, sel in REQUIRED:
            n = page.locator(sel).count()
            if n == 0:
                problems.append(f"missing element: {label} ({sel})")
            print(f"  {'ok ' if n else 'MISS'} {label:18} {sel:22} x{n}")

        # 3. SSE connected (top bar mode stat left the OFFLINE default)
        mode = page.locator("#stat-mode b").inner_text().strip()
        chain = page.locator("#stat-chain b").inner_text().strip()
        print(f"\n  mode  = {mode}")
        print(f"  chain = {chain}")
        if mode.upper() == "OFFLINE":
            problems.append("SSE never connected (mode still OFFLINE) -- "
                            "is live_show.py running?")

        # 4. canvas engine actually painted (not a uniform blank)
        painted = page.evaluate("""() => {
          const c = document.getElementById('game');
          if (!c) return -1;
          const g = c.getContext('2d');
          const d = g.getImageData(0, 0, c.width, c.height).data;
          const seen = new Set();
          for (let i = 0; i < d.length; i += 4)
            seen.add(d[i] + ',' + d[i+1] + ',' + d[i+2]);
          return seen.size;
        }""")
        print(f"  distinct canvas colours = {painted}")
        if painted is not None and 0 < painted < 8:
            problems.append(f"canvas looks blank ({painted} colours)")

        shot = Path(args.shot)
        if not shot.is_absolute():
            shot = Path(__file__).resolve().parent.parent / shot
        shot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot), full_page=False)
        print(f"\n  screenshot -> {shot}")

        browser.close()

    print()
    if problems:
        print("SMOKE FAILED")
        for p in problems:
            print("  - " + p)
        return 1
    print("SMOKE PASSED — page loads clean, shell wired, engine painting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
