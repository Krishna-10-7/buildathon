"""Render a contact sheet of every character sprite, at readable zoom.

The point of this file: the model cannot look at the sprites in-context, and
neither can it eyeball a 16x18 PNG. So it renders all six characters in all
three facings onto one sheet, saves it for the human, AND dumps a compact
per-sprite colour histogram so the machine can assert that e.g. the keeper
really has a red kurta and a turban, not just "some skin pixels".

Run:  python scripts/sprite_sheet.py
Out:  evidence/screenshots/spritesheet.png
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

# name, body var, palette  -- mirrors npcs{} + PERSONAS{} in the page
CAST = [
    ("KEEPER",  "BODY_KEEPER", "npcs.keeper.pal"),
    ("PROF",    "BODY_PROF",   "npcs.prof.pal"),
    ("GUARD",   "BODY_GUARD",  "npcs.guard.pal"),
    ("RITIKA",  "BODY_RITIKA", "PERSONAS.ritika.pal"),
    ("MEERA",   "BODY_MEERA",  "PERSONAS.meera.pal"),
    ("ARJUN",   "BODY_ARJUN",  "PERSONAS.arjun.pal"),
]
DIRS = ["down", "up", "right"]

# per character: the distinguishing colours that MUST appear
MUST = {
    "KEEPER": {"#c33b3b": "red turban", "#b13e53": "red kurta",
               "#e8b48a": "skin", "#1a1c2c": "outline/eyes"},
    "PROF":   {"#e6ebf2": "lab coat", "#20242e": "glasses frame",
               "#e8b48a": "skin", "#b9ad96": "grey hair"},
    "GUARD":  {"#1c2548": "peaked cap", "#2c3a6b": "navy uniform",
               "#ffd97a": "gold badge", "#e8b48a": "skin"},
    "RITIKA": {"#37a85f": "green salwar", "#1f120a": "hair",
               "#e8b48a": "skin", "#ffd97a": "gold dupatta"},
    "MEERA":  {"#2f8f8a": "teal sari", "#b9ad96": "grey-streaked hair",
               "#e8b48a": "skin", "#ef7d57": "bindi"},
    "ARJUN":  {"#dfa04f": "chai shirt", "#2a1810": "hair",
               "#e8b48a": "skin"},
}

JS = """
() => {
  const Z = 7, SW = 16, SH = 32, PAD = 14, HDR = 26;
  const cast = %(CAST)s, dirs = %(DIRS)s;
  const cols = dirs.length, rows = cast.length;
  const cw = SW*Z + PAD*2, ch = SH*Z + PAD*2 + HDR;
  const cv = document.createElement('canvas');
  cv.width = cols*cw; cv.height = rows*ch;
  const c = cv.getContext('2d');
  c.imageSmoothingEnabled = false;

  // backdrop: subtle checker so transparent pixels are obvious
  for (let y=0; y<cv.height; y+=8) for (let x=0; x<cv.width; x+=8) {
    c.fillStyle = (((x/8 + y/8) | 0) & 1) ? '#e9ecf5' : '#dfe3ef';
    c.fillRect(x, y, 8, 8);
  }

  // top-level script consts were explicitly assigned to window
  const bodyLookup = window;

  const stats = [];
  for (let r=0; r<rows; r++) {
    const name = cast[r][0];
    const body = bodyLookup[cast[r][1]];
    let pal;
    if (cast[r][2] === 'npcs.keeper.pal') pal = window.npcs.keeper.pal;
    else if (cast[r][2] === 'npcs.prof.pal') pal = window.npcs.prof.pal;
    else if (cast[r][2] === 'npcs.guard.pal') pal = window.npcs.guard.pal;
    else pal = window.PERSONAS[cast[r][2].split('.')[1]].pal;
    for (let k=0; k<cols; k++) {
      const dir = dirs[k];
      const ox = k*cw + PAD, oy = r*ch + HDR + PAD;

      // cell card + label
      c.fillStyle = '#ffffff'; c.fillRect(k*cw+4, r*ch+4, cw-8, ch-8);
      c.strokeStyle = '#c3c9db'; c.strokeRect(k*cw+4.5, r*ch+4.5, cw-8, ch-8);
      pxText(c, (k===0 ? name : dir), k*cw+8, r*ch+9, '#3b4256', 1);

      // sprite
      c.save();
      c.translate(ox, oy);
      c.scale(Z, Z);
      drawShadow(c, 0, SH);
      drawMap(c, body[dir], 0, 0, pal);
      c.restore();

      // histogram of just this cell
      const d = c.getImageData(ox, oy, SW*Z, SH*Z).data;
      const cnt = {};
      for (let i=0; i<d.length; i+=4) {
        const hx = '#' + [d[i],d[i+1],d[i+2]].map(v =>
          v.toString(16).padStart(2,'0')).join('');
        cnt[hx] = (cnt[hx]||0) + 1;
      }
      stats.push({name:name, dir:dir, cnt:cnt});
    }
  }
  return {w:cv.width, h:cv.height, png:cv.toDataURL('image/png'), stats:stats};
}
""" % {"CAST": repr([list(c) for c in CAST]).replace("'", '"'),
       "DIRS": repr(DIRS).replace("'", '"')}


def main_sync():
    out = {}

    async def run():
        async with async_playwright() as p:
            br = await p.chromium.launch()
            pg = await br.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto(HTML)
            await pg.wait_for_timeout(900)
            res = await pg.evaluate(JS)
            out["res"] = res
            out["errs"] = errs
            await br.close()

    asyncio.run(run())

    if out["errs"]:
        print("PAGE ERRORS:")
        for e in dict.fromkeys(out["errs"]):
            print("  !", e[:200])
        return 1

    res = out["res"]
    png = base64.b64decode(res["png"].split(",", 1)[1])
    (ART / "spritesheet.png").write_bytes(png)
    print(f"sheet: {ART/'spritesheet.png'}  {res['w']}x{res['h']}  "
          f"{len(png)} bytes")

    # merge per-character histograms across the three facings
    merged = {}
    for s in res["stats"]:
        merged.setdefault(s["name"], Counter()).update(s["cnt"])

    bad = []
    for name, cnt in merged.items():
        print(f"\n{name}  ({sum(cnt.values())} px, {len(cnt)} colours)")
        for hx, label in MUST.get(name, {}).items():
            n = cnt.get(hx, 0)
            flag = "  ok " if n else "  MISSING "
            print(f" {flag} {hx} {label:<22} {n:>5} px")
            if not n:
                bad.append(f"{name}: {label} ({hx})")
        others = [(h, c) for h, c in cnt.most_common(9)]
        print("   top:", "  ".join(f"{h}:{c}" for h, c in others))

    print()
    if bad:
        print("MISSING:")
        for b in bad:
            print("  !", b)
        return 1
    print("every character carries its distinguishing colours")
    return 0


sys.exit(main_sync())
