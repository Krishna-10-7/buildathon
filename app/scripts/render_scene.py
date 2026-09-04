"""Headless render check for the redesigned scene.

Loads bazaar_live.html, forces a known world state, snaps the camera to a
given tile, waits for frames, then pulls the raw canvas pixels back out.

Two things come back:
  1. a colour-index map (each distinct colour gets a letter) so the layout
     can be verified without opening an image, and
  2. per-view assertions that the props we expect are actually painted
     (counter stone, CATALOG plaque, keeper's kurta, gate lamps, ...).

PNGs are saved to evidence/screenshots/ for human review.
Run:  python scripts/render_scene.py
"""
import asyncio
import base64
import sys
from collections import Counter
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "bazaar_live.html").as_uri()
ART = ROOT.parent / "evidence" / "screenshots"
ART.mkdir(parents=True, exist_ok=True)

LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# name, teleport x/y/dir, extra state, colours that MUST be on screen
#
# Camera positions are derived, not guessed. updateCam() is
#   cx = clamp(px - VW/2 + 8, 0, MW*TILE - VW)
#   cy = clamp(py - VH/2 + 10, 0, MH*TILE - VH)
# with VW,VH = 240,160, TILE = 16, MW,MH = 30,20. The viewport is only 10
# tiles tall, so a prop at tile y is on screen only if cy <= y*16 <= cy+160.
# Every expectation below names a colour the draw code actually emits -
# earlier entries asserted things like a "CATALOG plaque" in #ffcd75 when
# drawStallBack() paints the plaque in P.ink.
VIEWS = [
    # stall={x:4,y:4,w:4,h:3}; the CATALOG plaque sits 7px above it, i.e.
    # world y 57. cy=42 puts it at screen y 15.
    ("A-counter", 6, 7, "up",
     "dealActive=false;shrineSeq=0;gateOpen=0;paidFlag=0;towerLit=0;",
     {"#93a2b8": "countertop stone", "#ffd97a": "stall gold trim",
      "#b13e53": "keeper kurta", "#e8b48a": "skin"}),

    # gate={x:14,y:5}; the RISK GATE banner is drawn at y-7 = world y 73.
    # cy=26 keeps it on screen and still frames the guard at tile (15,7).
    ("B-gate", 14, 6, "up",
     "dealActive=true;gateOpen=0;paidFlag=0;towerLit=0;shrineSeq=1;",
     {"#ef7d57": "RISK GATE banner", "#5b6577": "gate posts",
      "#39404e": "gate hinge", "#2c3a6b": "guard navy"}),

    # towerP={x:19,y:2}; MANDATE label at world y 25. teleport x=19 pushes
    # cam.x to 192, which is the only way to get tile x=19 on screen.
    ("C-vault", 19, 5, "up",
     "gateOpen=1;paidFlag=1;towerLit=25;shrineSeq=2;",
     {"#41f2c7": "vault LED / MANDATE", "#39404e": "cabinet metal",
      "#5b6577": "tower metal", "#0e1a24": "vault screen"}),

    # shrine={x:3,y:12,w:2,h:2}; the AUDIT plate is 7px above it, world y 185.
    ("D-ledger", 6, 13, "up",
     "shrineSeq=4;gateOpen=0;paidFlag=0;towerLit=0;",
     {"#93a2b8": "shrine stone", "#ffd97a": "shrine finial",
      "#ef7d57": "shrine lamp", "#f4f4f4": "shrine panel"}),

    # The pool is the three '~' tiles at (14,2),(15,2),(14,3) - the top of
    # the map, not the entry. An earlier version of this table looked for it
    # from tile y=17, where the camera clamps to cy=160 and the pool is
    # 130px above the top of the viewport.
    ("E-pool", 14, 5, "up",
     "shrineSeq=0;gateOpen=0;paidFlag=0;towerLit=0;",
     {"#29adff": "pool water", "#5b6a80": "pool rim",
      "#41a6f6": "pool edge"}),
]

JS_SETUP = """
([tx,ty,dir,extra]) => {
  eval(extra);
  teleport(tx,ty,dir);
  updateCam(true);
  return true;
}
"""

JS_GRAB = """
() => {
  const cv=document.getElementById('game');
  const c=cv.getContext('2d');
  const d=c.getImageData(0,0,cv.width,cv.height);
  const u=new Uint8Array(d.data.buffer);
  let s='';
  const CH=8192;                       // avoid blowing the call stack
  for(let i=0;i<u.length;i+=CH)
    s+=String.fromCharCode.apply(null,u.subarray(i,i+CH));
  return {w:cv.width,h:cv.height,data:btoa(s)};
}
"""


def hexof(rgb):
    return "#%02x%02x%02x" % rgb


def colour_map(px, w, h, top, stepx=2, stepy=4):
    """Print a letter per pixel, letter = nearest of the top-N colours."""
    pal = [bytes.fromhex(hx[1:]) for hx in top]
    lines = []
    for y in range(0, h - stepy + 1, stepy):
        row = []
        for x in range(0, w - stepx + 1, stepx):
            i = (y * w + x) * 4
            r, g, b = px[i], px[i + 1], px[i + 2]
            best, bd = 0, 1 << 30
            for k, p in enumerate(pal):
                dd = (r - p[0]) ** 2 + (g - p[1]) ** 2 + (b - p[2]) ** 2
                if dd < bd:
                    bd, best = dd, k
            row.append(LETTERS[best])
        lines.append("".join(row))
    return "\n".join(lines)


async def main():
    errors, console_errors = [], []
    failures = []
    async with async_playwright() as p:
        br = await p.chromium.launch()
        pg = await br.new_page(viewport={"width": 1300, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: console_errors.append(m.text)
              if m.type == "error" else None)
        await pg.goto(HTML)
        await pg.wait_for_timeout(1200)

        for name, tx, ty, d, extra, must in VIEWS:
            await pg.evaluate(JS_SETUP, [tx, ty, d, extra])
            await pg.wait_for_timeout(450)
            shot = await pg.evaluate(JS_GRAB)
            raw = base64.b64decode(shot["data"])
            w, h = shot["w"], shot["h"]
            await pg.locator("#game").screenshot(
                path=str(ART / f"scene-{name}.png"))

            cnt = Counter()
            for i in range(0, len(raw), 4):
                cnt[hexof((raw[i], raw[i + 1], raw[i + 2]))] += 1
            top = [hx for hx, _ in cnt.most_common(26)]

            print(f"\n{'='*80}\n{name}   {w}x{h}   {len(cnt)} distinct colours")
            print("=" * 80)
            print(colour_map(raw, w, h, top))
            print("\nlegend:")
            for k, hx in enumerate(top):
                print(f"  {LETTERS[k]} {hx}  {cnt[hx]:>6} px"
                      + ("   <-- " + must[hx] if hx in must else ""))

            for hx, label in must.items():
                if cnt.get(hx, 0) == 0:
                    failures.append(f"{name}: missing {label} ({hx})")

        await br.close()

    noise = ("EventSource", "fetch", "net::ERR", "Failed to load resource",
             "api/events", "CORS policy", "fonts.g", "favicon")
    real = [e for e in errors + console_errors
            if not any(n in e for n in noise)]
    print("\n" + "=" * 80)
    if real:
        print("PAGE ERRORS:")
        for e in dict.fromkeys(real):
            print("  !", e[:200])
    else:
        print("no page errors")
    if failures:
        print("MISSING:")
        for f in failures:
            print("  !", f)
    else:
        print("all expected colours present in every view")
    return 1 if (real or failures) else 0


sys.exit(asyncio.run(main()))
