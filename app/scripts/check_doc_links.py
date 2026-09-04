"""check_doc_links.py — verify every relative markdown link resolves.

    python app/scripts/check_doc_links.py

Run after any doc move. Broken links are the classic cost of a docs
restructure: the files are all still present, nothing reaches them any
more, and a README link that 404s is worse than no README link at all.

It runs in CI, because a check that only lives on one machine cannot catch
the next break — and the next break is the one a judge clicks.
"""
import re
import sys
from pathlib import Path

# Repo root: app/scripts/ -> app/ -> repo
ROOT = Path(__file__).resolve().parent.parent.parent
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

broken, ok_count, skipped = [], 0, 0
for md in sorted(ROOT.rglob("*.md")):
    if any(p in {".git", "node_modules", ".tmp", ".workbuddy-ai",
                 ".venv", "app/.tmp"} for p in md.parts):
        continue
    raw = md.read_text(encoding="utf-8", errors="replace")
    # Strip fenced code blocks: `foo(a)` inside a fence is code, not a link.
    lines, in_fence, text = [], False, ""
    for ln in raw.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        lines.append("" if in_fence else ln)
    text = "\n".join(lines)
    for m in LINK.finditer(text):
        target = m.group(1).split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            skipped += 1
            continue
        resolved = (md.parent / target).resolve()
        if resolved.exists():
            ok_count += 1
        else:
            broken.append((str(md.relative_to(ROOT)), target))

print(f"checked {ok_count} relative links, {skipped} external/anchors")
if broken:
    print(f"\n{len(broken)} BROKEN:")
    for src, tgt in broken:
        print(f"  {src}  ->  {tgt}")
    sys.exit(1)
print("ALL RELATIVE LINKS RESOLVE")
