"""Render each stage element alone at 1:1 and dump it as ASCII (1 char/px).

The only way to actually LOOK at the pixel art from a headless agent: the
model cannot view PNGs, so stages and props are pulled back off the canvas
and printed one character per pixel.

Two modes:
    python inspect_stage.py        # hue buckets - is the palette warm/cold?
    python inspect_stage.py lum    # 10-level greyscale ramp - shows the fine
                                   # detail (plank seams, bevels, X-bracing)
                                   # that hue-only output hides

Props are 16px wide so a 1:1 dump is directly readable. It catches broken
geometry (overflowing books, mis-centred sign text, degenerate hashes)
that a zoomed-out canvas sample hides.
"""
import base64
import colorsys
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HTML = (Path(__file__).resolve().parent.parent / "bazaar_live.html").as_uri()

JS = r"""() => {
  const out={};
  function snap(name,w,h,draw){
    const cv=document.createElement('canvas');
    cv.width=w; cv.height=h;
    const c=cv.getContext('2d');
    c.clearRect(0,0,w,h);
    draw(c);
    const d=c.getImageData(0,0,w,h).data;
    const px=[];
    for(let i=0;i<d.length;i+=4) px.push([d[i],d[i+1],d[i+2],d[i+3]]);
    out[name]={w:w,h:h,px:px};
  }
  const PAD=24;                       /* props draw upward above the tile  */
  ['plant','lamp','crate','barrel','shelf','board','monitor'].forEach(function(t){
    snap('prop:'+t,16,40,function(c){
      const pr={t:t,x:0,y:0,label:'X'};
      c.save(); c.translate(0,PAD); drawProp(c,pr,7); c.restore();
    });
  });
  ['CATALOG','AUDIT','RISK GATE'].forEach(function(lbl){
    snap('sign:'+lbl,64,40,function(c){
      const pr={t:'sign',x:0,y:0,label:lbl,col:P.ink};
      c.save(); c.translate(16,PAD); drawProp(c,pr,7); c.restore();
    });
  });
  ['#','W','r','r2','~','D'].forEach(function(ch){
    snap('tile:'+ch,32,32,function(c){
      /* 2x2 so neighbour-sensitive edges (rug fringe, pool kerb) show up  */
      for(let ty=0;ty<2;ty++)for(let tx=0;tx<2;tx++) drawTile(c,ch,tx,ty,7);
    });
  });
  return out;
}"""


RAMP = " .:-=+*%#@"       # dark -> light, for hue-blind detail checks


def lum(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def classify(r, g, b, a):
    if a < 40:
        return " "
    if MODE[0] == "lum":
        return RAMP[min(9, int(lum(r, g, b) / 25.6))]
    mx, mn = max(r, g, b), min(r, g, b)
    v = mx / 255
    s = 0 if mx == 0 else (mx - mn) / mx
    if v > 0.90 and s < 0.10:
        return "."
    if v < 0.16:
        return "#"
    if s < 0.14:
        return "-" if v > 0.55 else "="
    h, _, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h *= 360
    if h < 18 or h >= 345:
        return "R"
    if h < 45:
        return "O"
    if h < 68:
        return "Y"
    if h < 160:
        return "G"
    if h < 200:
        return "C"
    if h < 260:
        return "B"
    return "P"


MODE = [sys.argv[1]] if len(sys.argv) > 1 else ["hue"]

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    p = b.new_page(viewport={"width": 900, "height": 620})
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(HTML, wait_until="load", timeout=45000)
    p.wait_for_timeout(1500)
    out = p.evaluate(JS)
    print("errors:", errs or "none")
    for name, o in out.items():
        w, h, px = o["w"], o["h"], o["px"]
        print(f"\n===== {name}  ({w}x{h}) =====")
        for y in range(h):
            row = "".join(classify(*px[y * w + x]) for x in range(w))
            if row.strip():
                print(f"{y:2d}|{row}|")
    b.close()
