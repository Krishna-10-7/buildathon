"""Generates the sprite + scene block for bazaar_live.html (v3 - top-down).

This is a deliberate pivot from the previous side-view chibi art. The
reference (the real ChatDev game) uses top-down 2D pixel art with:

  - characters seen from above: hair top dominates, then face oval, then
    shoulders extending wider at the bottom
  - warm wooden plank floor with visible plank lines and grain
  - red-and-white checkered rug under the central desk
  - wooden plaque signs hanging from posts (NOT floating text labels)
  - richer props: lamps with yellow glow, plant pots, computer monitors

Engine surface kept stable so splice_scene.py and render_scene.py don't
need to change:
  - drawActor(c, ent, pal, tweak)            - same call
  - body[dir].rows[].slice()                - same data shape
  - drawTile(c, ch, tx, ty, t)              - same call
  - body is HEAD+TORSO rows (no legs), no walking stride

Sprite grid: 24 wide x 32 tall.
  rows  0-1   : top outline of head
  rows  2-9   : hair top (filled)
  rows 10-15  : hair-to-face transition (hair recedes, skin emerges)
  rows 16-20  : face area with eye dots
  rows 21-25  : collar/shoulder line
  rows 26-31  : body / arms at sides
"""

from pathlib import Path

W, H = 24, 32


# ---------------------------------------------------------------------------
# counted-segment row builder. "4. 8O 4." -> "....OOOOOOOO...."
#
# Bare tokens (no leading digit) expand to one of each character, so a typo
# is a silent off-by-N instead of a SystemExit - the width guard at the
# bottom catches the whole batch instead of one row per run.
# ---------------------------------------------------------------------------
_WERRS = []


def R(spec, total=W):
    out = []
    for tok in spec.split():
        i = 0
        while i < len(tok) and tok[i].isdigit():
            i += 1
        if i > 0:
            n = int(tok[:i])
            ch = tok[i:] if i < len(tok) else "."
            if len(ch) != 1:
                raise SystemExit(f"bad token {tok!r} in {spec!r}")
            out.append(ch * n)
        else:
            for c in tok:
                out.append(c)
    s = "".join(out)
    if len(s) != total:
        _WERRS.append(f"row {len(s)} != {total}: {s!r}  <- {spec!r}")
        s = s[:total]
    return s + "." * (total - len(s))


# ---------------------------------------------------------------------------
# top-down head anatomy helpers
# ---------------------------------------------------------------------------
def hair_top(width, m, d, g=""):
    """Rows 0-9: oval cap of hair from above.

    width is the widest row width; rows above and below narrow by 2 each side
    so the silhouette tapers to a dome.
    """
    rows = []
    hi = g if g else m
    # rows 0-1: outline top
    rows.append(R(f"{(W - width) // 2 - 1}. 1O {width}O 1O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2}. O {width + 2}O {(W - width) // 2}."))
    # rows 2: hair fill, with the highlight at the top
    rows.append(R(f"{(W - width) // 2}. O 2{hi} {width - 2}{m} O {(W - width) // 2}."))
    rows.append(R(f"{(W - width) // 2}. O 1{d} 2{hi} {width - 3}{m} O {(W - width) // 2}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{m} 1{d} 2{hi} {width - 4}{m} 1{d} 1{m} O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{m} 1{m} {width - 4}{hi} 1{m} 1{m} O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{m} {width - 2}{m} 1{m} O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{d} {width - 2}{m} 1{d} O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{d} {width - 2}{m} 1{d} O {(W - width) // 2 - 1}."))
    rows.append(R(f"{(W - width) // 2 - 1}O 1{d} {width - 2}{m} 1{d} O {(W - width) // 2 - 1}."))
    return rows


def face_band(rows_so_far, m, d, hair_to_skin="S", S="S", D="s"):
    """Rows 10-15: hair-to-face transition.

    Hair narrows, skin emerges on the temples and then expands to fill
    the width.
    """
    base_w = W - 4   # widest hair row
    rows = list(rows_so_far)
    # row 10: hair recedes by 1px each side, skin shows 2px
    rows.append(R(f"2O 1{d} 1{hair_to_skin} {base_w - 2}{m} 1{hair_to_skin} 1{d} 2O"))
    # row 11: skin widens
    rows.append(R(f"2O 1{hair_to_skin} 2{S} {base_w - 4}{m} 2{S} 1{hair_to_skin} 2O"))
    # row 12: hair ends at top, full face begins
    rows.append(R(f"2O 1{S} 4{S} {base_w - 6}{d} 4{S} 1{S} 2O"))
    rows.append(R(f"2O 1{S} 4{S} {base_w - 6}{S} 4{S} 1{S} 2O"))
    rows.append(R(f"2O 2{S} {base_w - 4}{S} 2{S} 2O"))
    rows.append(R(f"2O 2{S} {base_w - 4}{S} 2{S} 2O"))
    return rows


def face_eyes(rows_so_far, S="S", D="s", eye_letter="P"):
    """Rows 16-20: face with eyes + nose tip."""
    rows = list(rows_so_far)
    # row 16: empty forehead band
    rows.append(R(f"2O 2{S} {W - 8}{S} 2{S} 2O"))
    # row 17: eyes (two dots)
    rows.append(R(f"2O 1{S} 1{D} 2{S} 3{eye_letter} 1{S} 3{eye_letter} 2{S} 1{D} 1{S} 2O"))
    # row 18: between eyes
    rows.append(R(f"2O 2{S} {W - 8}{S} 2{S} 2O"))
    # row 19: nose tip (one pixel)
    rows.append(R(f"2O 2{S} 1{S} 1{D} 1{S} {W - 12}{S} 1{S} 1{D} 1{S} 2{S} 2O"))
    # row 20: mouth (one pixel)
    rows.append(R(f"2O 2{S} 1{S} 1{S} 2{D} 1{S} {W - 12}{S} 1{S} 2{D} 1{S} 1{S} 2O"))
    return rows


def shoulders(rows_so_far, S, U, d):
    """Rows 21-31: collar + body / arms hanging at sides."""
    rows = list(rows_so_far)
    # row 21: collar row (shirt colour starts at neck)
    rows.append(R(f"2O 2{S} 1{S} 4{U} 2{S} {W - 16}{U} 2{S} 4{U} 1{S} 2{S} 2O"))
    # row 22: shoulders widening
    rows.append(R(f"2O 2{S} 1{S} {W - 12}{U} 2{S} 2{S} 2O"))
    # row 23-24: shoulders
    rows.append(R(f"2O 1{S} 3{U} 2{U} 4{U} 2{U} 4{U} 2{U} 1{S} 2O"))
    rows.append(R(f"2O 1{S} 3{U} {W - 10}{U} 3{U} 1{S} 2O"))
    # row 25-26: arms hanging
    rows.append(R(f"2O 1{S} 1{d} 2{U} {W - 10}{U} 2{U} 1{d} 1{S} 2O"))
    rows.append(R(f"2O 1{S} 1{d} 2{U} {W - 10}{U} 2{U} 1{d} 1{S} 2O"))
    # row 27: arms continue, slight body widening
    rows.append(R(f"2O 1{S} 1{d} 2{U} {W - 10}{U} 2{U} 1{d} 1{S} 2O"))
    # row 28: hands visible at sides
    rows.append(R(f"2O 1{S} 1{d} 2{U} {W - 10}{U} 2{U} 1{d} 1{S} 2O"))
    # row 29-31: bottom of body
    rows.append(R(f"2O 1{S} 1{d} 1{U} 1{U} {W - 12}{U} 1{U} 1{U} 1{d} 1{S} 2O"))
    rows.append(R(f"2O 1{S} 1{d} 1{U} 1{U} {W - 12}{U} 1{U} 1{U} 1{d} 1{S} 2O"))
    rows.append(R(f"2O 2{S} 1{d} 1{U} {W - 10}{U} 1{d} 2{S} 2O"))
    return rows


# ---------------------------------------------------------------------------
# one character
# ---------------------------------------------------------------------------
def char_topdown(name, hair_main, hair_dark, hair_hi="",
                 shirt_main="U", shirt_dark="u", skin="S", skin_d="s",
                 eyes="P", accent_rows=None):
    """Build the 32-row top-down sprite for one character.

    Returns a dict with 'down', 'up', 'right' keys, each a 32-row list
    of width-24 strings.
    """
    base = []
    # rows 0-9: hair top, widest = 18
    base = hair_top(18, hair_main, hair_dark, hair_hi or hair_main)
    # rows 10-15: face band
    base = face_band(base, hair_main, hair_dark, hair_to_skin=skin, S=skin, D=skin_d)
    # rows 16-20: face with eyes
    base = face_eyes(base, S=skin, D=skin_d, eye_letter=eyes)
    # rows 21-31: shoulders/body
    base = shoulders(base, skin, shirt_main, shirt_dark)
    assert len(base) == H, f"{name}: top-down rows {len(base)} != {H}"

    # right-facing variant: shift brows/eyes/mouth 1 col right
    right = []
    for row in base:
        # shift everything except leading '.'  and trailing '.' by one right
        if row.startswith(".."):
            shift_in = row[2:]
        else:
            shift_in = row
        shifted = "." + shift_in[:-1] if shift_in else row
        # keep width
        if len(shifted) < W:
            shifted = shifted + "." * (W - len(shifted))
        elif len(shifted) > W:
            shifted = shifted[:W]
        right.append(shifted)

    # up-facing variant: hair fills the whole thing (back of head)
    up = []
    for i, row in enumerate(base):
        if i < 9:
            # full hair back
            up.append(R(f"{(W - 18) // 2 - 1}O 1{hair_dark} 16{hair_main} 1{hair_dark} O {(W - 18) // 2 - 1}."))
        elif i < 21:
            # nape / back of neck
            up.append(R(f"2O 2{skin} {W - 8}{hair_main} 2{skin} 2O"))
        else:
            # collar same as down
            up.append(row)

    return {"down": base, "up": up, "right": right}


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------
def emit():
    chars = {
        "KEEPER": char_topdown(
            "KEEPER", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="U", shirt_dark="u", skin="S", skin_d="s",
            eyes="P"),
        "PROF": char_topdown(
            "PROF", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="W", shirt_dark="w", skin="S", skin_d="s",
            eyes="P"),
        "GUARD": char_topdown(
            "GUARD", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="B", shirt_dark="b", skin="S", skin_d="s",
            eyes="P"),
        "RITIKA": char_topdown(
            "RITIKA", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="K", shirt_dark="k", skin="S", skin_d="s",
            eyes="P"),
        "MEERA": char_topdown(
            "MEERA", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="M", shirt_dark="m", skin="S", skin_d="s",
            eyes="P"),
        "ARJUN": char_topdown(
            "ARJUN", hair_main="H", hair_dark="h", hair_hi="L",
            shirt_main="C", shirt_dark="c", skin="S", skin_d="s",
            eyes="P"),
    }

    lines = []
    lines.append("/* ---------------- top-down character sprites (24x32) ---------------- */")
    for name, dirs in chars.items():
        lines.append(f"const BODY_{name}={{")
        for d, rows in dirs.items():
            lines.append(f"  {d}:[")
            for r in rows:
                lines.append(f"    {r!r},")
            lines.append("  ],")
        lines.append("};")
        lines.append("")

    # ROLECOL: standard legend
    lines.append("const ROLECOL={")
    lines.append("  O:P.ink, S:P.skin, s:P.skinD, W:P.white, P:P.ink,")
    lines.append("  H:P.hair, h:P.hairD, L:P.hairL, B:P.hairD, R:P.hairD, M:P.hairD,")
    lines.append("  U:P.red, u:P.kurtaRedD, K:P.kurtaRed, k:P.kurtaRedD,")
    lines.append("  C:P.shirtChai, c:P.shirtChaiD, W:P.labWhite, w:P.labWhiteD,")
    lines.append("  B:P.guardNavy, b:P.guardNavyD, M:P.sariTeal, m:P.sariTealD,")
    lines.append("  D:P.bindi, Q:P.glasses, G:P.gold, A:P.gold, F:P.shoe, f:P.shoeD,")
    lines.append("};")
    lines.append("")

    # placeholder resolver (kept for backwards-compat with old engine calls)
    lines.append("function _resolveRolecol(pal){")
    lines.append("  const out={}; for(const k in ROLECOL){")
    lines.append("    let v=ROLECOL[k];")
    lines.append("    if(v===\"__HAIR__\")v=pal.hair||P.hair;")
    lines.append("    else if(v===\"__HAIR_D__\")v=pal.hairD||P.hairD;")
    lines.append("    else if(v===\"__HAIR_HI__\")v=pal.hairL||P.hairL;")
    lines.append("    else if(v===\"__CLOTH__\")v=pal.cloth||P.red;")
    lines.append("    else if(v===\"__CLOTH_D__\")v=pal.clothD||P.kurtaRedD;")
    lines.append("    else if(v===\"__CLOTH2__\")v=pal.cloth2||P.jeans;")
    lines.append("    else if(v===\"__CLOTH2_D__\")v=pal.cloth2D||P.jeansD;")
    lines.append("    else if(v===\"__TROUSER__\")v=pal.trouser||P.jeans;")
    lines.append("    else if(v===\"__ACCENT__\")v=pal.accent||P.gold;")
    lines.append("    out[k]=v;")
    lines.append("  } return out;")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    if _WERRS:
        print(f"{len(_WERRS)} ROW WIDTH ERROR(S):")
        for e in _WERRS:
            print("  " + e)
        raise SystemExit(1)
    src = emit()
    out = Path(__file__).resolve().parent.parent / "_newscene.js"
    out.write_text(src, encoding="utf-8")
    print(f"ok: wrote {out} ({len(src)} bytes)")
