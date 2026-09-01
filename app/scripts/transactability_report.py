#!/usr/bin/env python3
"""
transactability_report.py — the "can an AI buyer actually buy?" scoreboard.

WHY THIS EXISTS
---------------
PREREGISTRATION.md measures whether the *growth agent* grows revenue
(discount A/B — verdict: NULL, reported honestly). That is only half of
Track 01. The brief also asks:

    "...or that makes a merchant transactable by an AI buyer end to end."

That half was never quantified. It is now. This script turns the existing
session JSONL corpus into the transactability scoreboard — the positive,
mechanically-derived headline that the discount A/B could not provide.

It CHANGES NOTHING. It reads append-only evidence files and prints
counts. No arm attribution is used, so the multi-runner incident that
voided 64 rows for the A/B does not affect any number below except where
explicitly noted.

HEADLINE (clean run, sessions_laptop2.jsonl, n=40):
    33/40 autonomous sessions completed a real captured payment = 82.5%
    0/38 budget violations
    Rs 25,724.15 test-mode revenue captured

USAGE
-----
    python scripts/transactability_report.py
    python scripts/transactability_report.py --dir ../artifacts

Exit code is always 0 — this is a reporting tool, never a gate.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

# The clean, single-runner, post-hardening run. This is the file whose
# numbers we quote as the headline: it is the only era provably free of the
# multi-runner corruption (see MEASUREMENT-DAY.md, 2026-08-25 12:40 IST).
CLEAN_RUN = "sessions_laptop2.jsonl"
OTHER_RUNS = ("sessions_laptop.jsonl", "sessions.jsonl")


def load(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # evidence files are never edited to fix parse errors
    return rows


def pct(n: int, d: int) -> str:
    return f"{(n / d * 100):.1f}%" if d else "n/a"


def rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def scoreboard(name: str, rows: list[dict]) -> dict:
    if not rows:
        print(f"\n### {name}: no records")
        return {}

    n = len(rows)
    outcomes = Counter(r.get("outcome") for r in rows)
    paid = [r for r in rows if r.get("outcome") == "paid"]
    reached = [r for r in rows if r.get("order_id")]

    # Budget gate: did any agent ever plan a basket above its hard cap?
    gated = [
        r
        for r in rows
        if isinstance(r.get("budget_paise"), int)
        and isinstance(r.get("basket_total_paise"), int)
        and r["budget_paise"] > 0
    ]
    violations = [r for r in gated if r["basket_total_paise"] > r["budget_paise"]]
    headroom = [
        (r["budget_paise"] - r["basket_total_paise"]) / r["budget_paise"] * 100
        for r in gated
    ]

    print(f"\n{'=' * 68}")
    print(f"  {name}   n={n}")
    print("=" * 68)

    print("\n  OUTCOMES")
    for outcome, count in outcomes.most_common():
        bar = "#" * int(count / n * 40)
        print(f"    {str(outcome):<22} {count:>3}  {pct(count, n):>6}  {bar}")

    print("\n  TRANSACTABILITY  (did the merchant let an AI buyer finish?)")
    print(f"    reached gateway (order created)   {len(reached):>3}/{n}  {pct(len(reached), n)}")
    if reached:
        print(f"    completed payment of those       {len(paid):>3}/{len(reached)}  {pct(len(paid), len(reached))}")
    print(f"    end-to-end completion            {len(paid):>3}/{n}  {pct(len(paid), n)}")

    if paid:
        amounts = [r.get("amount_paise", 0) for r in paid]
        print("\n  MONEY  (Razorpay test mode, webhook-captured)")
        print(f"    captured revenue                 {rupees(sum(amounts))}")
        print(f"    AOV / median                     {rupees(int(statistics.mean(amounts)))} / {rupees(int(statistics.median(amounts)))}")
        print(f"    range                            {rupees(min(amounts))} - {rupees(max(amounts))}")

    print("\n  BOUNDEDNESS  (the part that has to be 100%)")
    print(f"    sessions with a hard budget      {len(gated):>3}")
    print(f"    overspend violations             {len(violations):>3}  {pct(len(violations), len(gated))}")
    if headroom:
        print(f"    mean budget headroom kept        {statistics.mean(headroom):.1f}%  (tightest {min(headroom):.1f}%)")

    attempts = [r.get("attempts", 0) for r in rows if isinstance(r.get("attempts"), int)]
    if attempts:
        first = len([a for a in attempts if a == 1])
        print(f"    finished on the first attempt    {first:>3}/{len(attempts)}  {pct(first, len(attempts))}")

    print("\n  PER PERSONA")
    for persona in sorted({r.get("persona") for r in rows if r.get("persona")}):
        sub = [r for r in rows if r.get("persona") == persona]
        p = len([r for r in sub if r.get("outcome") == "paid"])
        rc = len([r for r in sub if r.get("outcome") == "risk_challenged"])
        rev = sum(r.get("amount_paise", 0) for r in sub if r.get("outcome") == "paid")
        cap = next((r["budget_paise"] for r in sub if isinstance(r.get("budget_paise"), int)), None)
        cap_s = f"cap {rupees(cap):<10}" if cap else ""
        print(f"    {persona:<8} n={len(sub):<3} paid={p:<3} challenged={rc:<3} {cap_s} revenue={rupees(rev)}")

    providers = Counter(r.get("llm") for r in rows if r.get("llm"))
    if providers:
        print(f"\n  LLM providers surviving in this era: {dict(providers)}")

    return {
        "n": n,
        "paid": len(paid),
        "reached": len(reached),
        "completion_rate": len(paid) / n if n else 0,
        "revenue_paise": sum(r.get("amount_paise", 0) for r in paid),
        "gated": len(gated),
        "violations": len(violations),
        "headroom_pct": statistics.mean(headroom) if headroom else None,
        "outcomes": dict(outcomes),
    }


def _chi_square(cells: list[tuple[int, int]]) -> tuple[float, float]:
    """Homogeneity chi-square over (n, count) cells.

    df=2 has the closed form exp(-x/2), so this needs no scipy — the repo
    has no scipy dependency and should not grow one for two numbers.
    """
    N = sum(n for n, _ in cells)
    C = sum(c for _, c in cells)
    if N == 0 or C == 0 or C == N:
        return (0.0, 1.0)
    p = C / N
    x = sum((c - n * p) ** 2 / (n * p) for n, c in cells)
    df = len(cells) - 1
    if df == 2:
        return (x, math.exp(-x / 2))
    if df == 1:
        return (x, 2 * (1 - 0.5 * (1 + math.erf(math.sqrt(x / 2)))))
    t = (x / df) ** (1 / 3)  # Wilson-Hilferty for df >= 3
    mu, sd = 1 - 2 / (9 * df), math.sqrt(2 / (9 * df))
    return (x, 1 - 0.5 * (1 + math.erf((t - mu) / (sd * math.sqrt(2)))))


def _trend_z(cells: list[tuple[int, int]]) -> tuple[float, float]:
    """Cochran-Armitage trend test with integer scores 0..k-1."""
    xs = list(range(len(cells)))
    N = sum(n for n, _ in cells)
    C = sum(c for _, c in cells)
    if N == 0 or C == 0 or C == N:
        return (0.0, 1.0)
    p = C / N
    mx = sum(n * x for (n, _), x in zip(cells, xs)) / N
    S = sum(c * (x - mx) for (c, _), x in zip(cells, xs))
    var = p * (1 - p) * sum(n * (x - mx) ** 2 for (n, _), x in zip(cells, xs))
    if var <= 0:
        return (0.0, 1.0)
    z = S / math.sqrt(var)
    return (z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))))


def escalation(rows: list[dict], buckets: int = 3) -> None:
    """WITHDRAWN CLAIM — challenge rate by chronological segment.

    This section used to be printed as "Finding 2: fraud controls escalate
    under sustained agent traffic." It is not. Tested against its own
    corpus the segment differences are not significant (chi-square 3.04,
    df 2, p 0.22; Cochran-Armitage trend p 1.00), and with five challenges
    in 38 gate-reaching sessions the data cannot separate "escalating"
    from a constant rate.

    It is kept — and labelled — rather than deleted, because the script is
    the thing that published the claim and a retraction that leaves no
    trace in the tool that made it is not a retraction. The real finding
    is VENUE, and it lives in scripts/risk_venue_report.py.
    """
    dated = sorted([r for r in rows if r.get("ts")], key=lambda r: r["ts"])
    if len(dated) < buckets * 4:
        return
    size = len(dated) // buckets

    print(f"\n{'=' * 68}")
    print("  CHALLENGE RATE BY SEGMENT  — CLAIM WITHDRAWN, see below")
    print("=" * 68)
    cells = []
    for i in range(buckets):
        chunk = dated[i * size : (i + 1) * size] if i < buckets - 1 else dated[i * size :]
        rc = len([r for r in chunk if r.get("outcome") == "risk_challenged"])
        p = len([r for r in chunk if r.get("outcome") == "paid"])
        cells.append((len(chunk), rc))
        print(f"    segment {i + 1}  n={len(chunk):<3} paid={p:<3} challenged={rc:<3} challenge_rate={pct(rc, len(chunk)):>6}")

    x, pv = _chi_square(cells)
    zt, pvt = _trend_z(cells)
    print(f"\n    homogeneity chi-square = {x:.2f}, df = {len(cells) - 1}, p = {pv:.3f}")
    print(f"    Cochran-Armitage trend z = {zt:.2f}, p = {pvt:.3f}")
    print(f"\n    VERDICT: NOT SIGNIFICANT. "
          f"{sum(c for _, c in cells)} challenges in "
          f"{sum(n for n, _ in cells)} sessions cannot distinguish")
    print("    'escalating' from a constant rate. Do not quote these three")
    print("    numbers as a trend — that was the original error.")
    print("\n    The finding that DID survive is venue, not time: run")
    print("    scripts/risk_venue_report.py. Correction in research/10 §1.1.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent / "artifacts"))
    args = ap.parse_args()

    base = Path(args.dir)
    print("\n" + "#" * 68)
    print("#  BAZAAR — TRANSACTABILITY SCOREBOARD")
    print("#  Can an autonomous AI buyer actually complete a purchase here?")
    print("#" * 68)

    clean = load(base / CLEAN_RUN)
    clean_stats = scoreboard(f"CLEAN RUN — {CLEAN_RUN} (headline evidence)", clean)
    escalation(clean)

    for name in OTHER_RUNS:
        rows = load(base / name)
        if rows:
            scoreboard(f"SUPPORTING CORPUS — {name}", rows)

    print("\n" + "-" * 68)
    print("  HOW TO CITE THIS")
    print("-" * 68)
    if clean_stats:
        print(f"    {clean_stats['paid']}/{clean_stats['n']} autonomous sessions completed a real,")
        print(f"    webhook-captured Razorpay payment ({pct(clean_stats['paid'], clean_stats['n'])}), moving")
        print(f"    {rupees(clean_stats['revenue_paise'])} with {clean_stats['violations']} budget violations")
        print(f"    across {clean_stats['gated']} budget-bounded sessions.")
    print("\n    These are test-mode funds on live Razorpay APIs. Every order id in")
    print("    the source JSONL is real and independently checkable against the")
    print("    merchant's own hash-chained ledger: GET /audit/recent")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
