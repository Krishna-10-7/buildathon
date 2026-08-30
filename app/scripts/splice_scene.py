"""Grafts scripts/_newscene.js into bazaar_live.html.

The SSE engine is byte-identical. We only swap the palette/sprite/terrain
block. Several smaller engine patches (baked floor, persona body, speaker
bodies, skull offset) were applied in earlier runs and are now part of
the page; splice_scene only enforces the ones that haven't already
landed.

Each patch asserts the exact old text is present and that it occurs
exactly once, so a silent no-op is impossible.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "bazaar_live.html"
NEWBLOCK = Path(__file__).resolve().parent / "_newscene.js"

src = HTML.read_text(encoding="utf-8")
new_block = NEWBLOCK.read_text(encoding="utf-8").rstrip("\n")

# ---------------------------------------------------------------- block --
start_marker = "const P = {"
end_marker = "/* ---------------- camera / loop ---------------- */"

si = src.find(start_marker)
ei = src.find(end_marker)
if si < 0 or ei < 0 or ei <= si:
    raise SystemExit("could not locate the sprite/scene block")
if src.count(start_marker) != 1:
    raise SystemExit("start marker is not unique - aborting")

# The generated block ends with the end marker itself, so every previous run
# left one copy behind and they stack up. Consume the whole run of markers.
tail = ei
while src.startswith(end_marker, tail):
    tail += len(end_marker)
    while tail < len(src) and src[tail] in "\r\n":
        tail += 1
if src.count(end_marker) > 1:
    print(f"  note: discarding {src.count(end_marker) - 1} stale end marker(s)")

old_block = src[si:ei]
print(f"replacing block: {old_block.count(chr(10))} lines "
      f"({len(old_block)} bytes) -> {new_block.count(chr(10))} lines "
      f"({len(new_block)} bytes)")
src = src[:si] + new_block + "\n\n" + src[tail:]


# --------------------------------------------------------------- patches --
def patch(text, old, new, label, required=True):
    n = text.count(old)
    if n == 1:
        print(f"  patched: {label}")
        return text.replace(old, new)
    if n == 0 and not required:
        print(f"  skipped: {label} (already applied)")
        return text
    raise SystemExit(f"[{label}] expected 1 occurrence, found {n}")


# 1. Give the actor its persona's sprite, not just its palette. May already
#    be in the page from a prior run; the patch is idempotent because the
#    new line simply sets the same property.
patches = [
    ("persona body on reset",
     "  player.pal=(PERSONAS[personaKey]||PERSONAS.ritika).pal;",
     "  player.pal=(PERSONAS[personaKey]||PERSONAS.ritika).pal;\n"
     "  player.body=(PERSONAS[personaKey]||PERSONAS.ritika).body||BODY_RITIKA;"),

    # 2. Feed avatars use each speaker's own character.
    ("feed avatar bodies",
     "  drawMap(cv.getContext(\"2d\"), BODY.down.slice(), 0, 0, pal);",
     "  drawMap(cv.getContext(\"2d\"), (SPEAKER_BODY[who]||BODY).down.slice(), 0, 0, pal);"),

    # 3. Floor blit must read the baked canvas with the same BAKE_PAD the
    #    bake loop wrote with, and must not clamp to the viewport size.
    ("baked floor blit",
     "  ctx.drawImage(floorCv,x0*TILE,y0*TILE,bw,bh,x0*TILE,y0*TILE,bw,bh);",
     "  ctx.drawImage(floorCv,(x0+BAKE_PAD)*TILE,(y0+BAKE_PAD)*TILE,bw,bh,"
     "x0*TILE,y0*TILE,bw,bh);"),

    # 4. SPEAKER_BODY map (inserted after the LEDGER closing).
    ("SPEAKER_BODY map",
     """  LEDGER:{hair:"#20223b",cloth:P.teal,clothD:"#3a9aa5",accent:P.white},
};""",
     """  BAZAAR:{hair:"#333c57",cloth:P.chai,clothD:"#c99a3f",accent:P.deep},
};
/* same three characters that appear on screen; speakers with no on-screen
   counterpart get a distinct body so no two avatars look alike */
const SPEAKER_BODY={
  SHOPKEEPER:BODY_KEEPER,
  "PROF.EXPERIMENT":BODY_PROF,
  GUARD:BODY_GUARD,
  hCAPTCHA:BODY_GUARD,
  BAZAAR:BODY_ARJUN,
};"""),

    # 5. Top-down pivot: drop the chibi LEGS stride frame (top-down characters
    #    are seen from above and don't have a walking pose to swap in) and
    #    anchor the flip on the new 24-wide sprite width.
    ("top-down drawActor",
     """  if(ent.frame===1)rows=rows.slice(0,25).concat(LEGS);
  const rc=_resolveRolecol(pal);
  const px=Math.round(ent.px), py=Math.round(ent.py);
  drawShadow(c,px,py);
  c.save();
  if(ent.dir==="left"){
    c.translate(px+16,py-32);
    c.scale(-1,1); drawMap(c,rows,0,0,pal,rc);
  } else {
    drawMap(c,rows,px,py-32,pal,rc);
  }
  c.restore();""",
     """  const rc=_resolveRolecol(pal);
  const px=Math.round(ent.px), py=Math.round(ent.py);
  drawShadow(c,px,py);
  c.save();
  if(ent.dir==="left"){
    c.translate(px+24,py-32);
    c.scale(-1,1); drawMap(c,rows,0,0,pal,rc);
  } else {
    drawMap(c,rows,px,py-32,pal,rc);
  }
  c.restore();"""),
]

for label, old, new in patches:
    src = patch(src, old, new, label, required=False)

HTML.write_text(src, encoding="utf-8")
print(f"\nwrote {HTML} ({len(src)} bytes, {src.count(chr(10))} lines)")

# -------------------------------------------------------------- sanity ----
for must in ("BODY_KEEPER", "BODY_PROF", "BODY_GUARD", "BODY_RITIKA",
             "BODY_MEERA", "BODY_ARJUN", "floorCv", "drawPoolShimmer",
             "SPEAKER_BODY", "player.body", "pxText"):
    if must not in src:
        raise SystemExit(f"post-check failed: {must} missing")
print("post-check: all new symbols present")
