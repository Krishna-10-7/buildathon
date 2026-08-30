"""Download ChatDev's real character sprites and profile each character.

ChatDev does not hand-code pixel art. It ships 144 pre-rendered PNGs at

    https://raw.githubusercontent.com/OpenBMB/ChatDev/main/
        frontend/public/sprites/{char}-{stance}-{frame}.png

  char  : 1..12
  stance: D (down), L (left), R (right), U (up)
  frame : 1, 2, 3

and swaps the image to animate (see their frontend/src/utils/spriteFetcher.js).

Source art is 508x847 on a white background. Each PNG holds ONE character
in a 3/4 view (hair top, shoulders, torso, legs). We download all 144, then
profile each character's dominant colours so we can pick visually distinct
ones for our six roles.
"""
import concurrent.futures
import os
import urllib.request
from collections import Counter

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "chatdev_src")
os.makedirs(OUT, exist_ok=True)

BASE = ("https://raw.githubusercontent.com/OpenBMB/ChatDev/main/"
        "frontend/public/sprites/{}-{}-{}.png")
CHARS, STANCES, FRAMES = range(1, 13), "DLRU", (1, 2, 3)


def dl(job):
    c, s, f = job
    path = os.path.join(OUT, f"{c}-{s}-{f}.png")
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return job, os.path.getsize(path), None
    try:
        with urllib.request.urlopen(BASE.format(c, s, f), timeout=60) as r:
            data = r.read()
        with open(path, "wb") as fh:
            fh.write(data)
        return job, len(data), None
    except Exception as e:                                   # noqa: BLE001
        return job, 0, str(e)


def profile(c):
    """Dominant non-background colours of character c's idle frame."""
    im = Image.open(os.path.join(OUT, f"{c}-D-1.png")).convert("RGB")
    px = im.load()
    w, h = im.size
    cnt = Counter()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            r, g, b = px[x, y]
            # white background
            if r > 235 and g > 235 and b > 235:
                continue
            cnt[(r // 24 * 24, g // 24 * 24, b // 24 * 24)] += 1
    return cnt.most_common(5)


def main():
    jobs = [(c, s, f) for c in CHARS for s in STANCES for f in FRAMES]
    print(f"fetching {len(jobs)} ChatDev sprites -> {OUT}\n")
    with concurrent.futures.ThreadPoolExecutor(10) as ex:
        res = list(ex.map(dl, jobs))

    errs = [r for r in res if r[2]]
    ok = len(res) - len(errs)
    print(f"downloaded {ok}/{len(jobs)}   errors: {len(errs)}")
    for job, _, e in errs[:5]:
        print(f"   {job}: {e}")
    total = sum(n for _, n, _ in res)
    print(f"{total / 1e6:.1f} MB total\n")

    print("character colour profiles (D-1 idle frame):")
    for c in CHARS:
        p = os.path.join(OUT, f"{c}-D-1.png")
        if not os.path.exists(p):
            print(f"  {c:2d}: MISSING")
            continue
        cols = profile(c)
        s = "  ".join("#%02x%02x%02x" % rgb for rgb, _ in cols)
        print(f"  {c:2d}: {s}")


if __name__ == "__main__":
    main()
