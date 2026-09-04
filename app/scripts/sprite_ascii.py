"""Dump each character sprite as ASCII art so the design can be judged in-text.

The point of this file: the model cannot open artifacts/spritesheet.png. The
colour histogram in sprite_sheet.py proves the right COLOURS are present; this
proves they are in the right PLACES. Each row of a 16x32 sprite is printed as
16 glyphs with a legend underneath.

Run:  python scripts/sprite_ascii.py [character]
"""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "bazaar_live.html").as_uri()

CAST = [
    ("KEEPER", "BODY_KEEPER", "npcs.keeper.pal"),
    ("PROF",   "BODY_PROF",   "npcs.prof.pal"),
    ("GUARD",  "BODY_GUARD",  "npcs.guard.pal"),
    ("RITIKA", "BODY_RITIKA", "PERSONAS.ritika.pal"),
    ("MEERA",  "BODY_MEERA",  "PERSONAS.meera.pal"),
    ("ARJUN",  "BODY_ARJUN",  "PERSONAS.arjun.pal"),
]

JS = """
(only) => {
  const lookup = window;
  const out = [];
  for (const [name, bodyVar, palPath] of only) {
    const body = lookup[bodyVar];
    let pal;
    if (palPath.startsWith('npcs.')) pal = window.npcs[palPath.split('.')[1]].pal;
    else pal = window.PERSONAS[palPath.split('.')[1]].pal;
    // same resolution the renderer does
    const rc = {};
    for (const k in window.ROLECOL) {
      let v = window.ROLECOL[k];
      if (v === '__HAIR__') v = pal.hair || window.P.hair;
      else if (v === '__HAIR_D__') v = pal.hairD || window.P.hairD;
      else if (v === '__HAIR_HI__') v = pal.hairL || window.P.hairL;
      else if (v === '__CLOTH__') v = pal.cloth || window.P.red;
      else if (v === '__CLOTH_D__') v = pal.clothD || window.P.kurtaRedD;
      else if (v === '__CLOTH2__') v = pal.cloth2 || window.P.jeans;
      else if (v === '__CLOTH2_D__') v = pal.cloth2D || window.P.jeansD;
      else if (v === '__TROUSER__') v = pal.trouser || window.P.jeans;
      else if (v === '__ACCENT__') v = pal.accent || window.P.gold;
      rc[k] = v;
    }
    const dirs = ['down', 'right', 'up'];
    for (const dir of dirs) {
      const rows = body[dir];
      const grid = rows.map(r => {
        let s = '';
        for (let i = 0; i < 16; i++) {
          const ch = r[i] || '.';
          s += (ch === '.') ? ' ' : ch;
        }
        return s;
      });
      out.push({name, dir, grid, rc});
    }
  }
  return out;
}
"""

# short names for the legend
LABEL = {
    "O": "outline", "K": "inner shadow", "S": "skin", "s": "skin shade",
    "W": "eye white", "P": "pupil", "D": "teeth/smile", "E": "blush",
    "N": "bindi", "Q": "glasses", "H": "hair", "h": "hair dark",
    "L": "hair light", "B": "brow", "M": "moustache", "R": "stubble/beard",
    "U": "garment", "u": "garment shade", "C": "lower garment",
    "c": "lower shade", "d": "trouser", "A": "accent", "G": "gold trim",
    "T": "turban", "t": "turban shade", "Y": "cap", "y": "cap shade",
    "F": "shoe", "f": "shoe dark",
}


async def main():
    which = sys.argv[1].upper() if len(sys.argv) > 1 else None
    cast = [c for c in CAST if not which or c[0] == which]
    if not cast:
        raise SystemExit(f"no such character {which!r}; have {[c[0] for c in CAST]}")

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page()
        await pg.goto(HTML)
        await pg.wait_for_timeout(900)
        data = await pg.evaluate(JS, cast)
        await b.close()

    for item in data:
        print(f"\n===== {item['name']}  facing {item['dir']} " + "=" * 40)
        for i, row in enumerate(item["grid"]):
            mark = "|" if i in (13, 25) else " "
            print(f"{i:2d}{mark}|{row}|")
        if item["dir"] == "down":
            used = sorted({c for row in item["grid"] for c in row if c != " "})
            rc = item["rc"]
            print("    legend: " + "  ".join(
                f"{c}={rc.get(c, '?')} {LABEL.get(c, '')}".strip()
                for c in used))


asyncio.run(main())
