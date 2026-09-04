"""Preregistered analysis for the A/B measurement (PREREGISTRATION.md).

Pure computation over the session JSONL — no network, no state. Frozen
definitions live here, verbatim from the preregistration:

  valid session   : any outcome except merchant_unreachable (it reached the
                    store and produced a record)
  symmetric excls : risk_challenged, infra_error, driver-bug duplicates
                    (excluded from BOTH arms, counted per arm, reported)
  primary metric  : net revenue per ANALYZED session; paid orders contribute
                    amount_paise, everything else contributes Rs 0
                    (walked_away is a real commercial outcome)
  secondary       : conversion (paid / analyzed), AOV among paid,
                    multi-line attach rate among paid
  uncertainty     : bootstrap 95% CI on T-C difference, resampled WITHIN
                    persona strata (10,000 draws, fixed seed); permutation
                    test (10,000 two-sided label shuffles within strata)

Run:  uv run python exp/analysis.py ../evidence/sessions.jsonl
"""

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

EXCLUSIONS = ("risk_challenged", "infra_error")
PAID = "paid"
SEED = 42
ITERS = 10_000


def load_sessions(path: str | Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"corrupt JSONL at line {line_no}: {exc}")
            records.append(rec)
    return records


def split_arms(records: list[dict]) -> tuple[list[dict], list[dict], dict,
                                            list[dict], int]:
    """Returns (analyzed_T, analyzed_C, exclusion_counts_per_arm,
    unreachable, n_legacy_without_arm). Pre-experiment persona sessions
    carry no arm tag — they predate the experiment and are reported, never
    analyzed."""
    analyzed = {"treatment": [], "control": []}
    excluded: dict[str, int] = {"treatment": 0, "control": 0}
    unreachable: list[dict] = []
    legacy = 0
    seen_keys: set[tuple] = set()
    for rec in records:
        arm = rec.get("arm")
        if arm not in analyzed:
            legacy += 1
            continue

        # Driver-bug duplicate exclusion: same ORDER recorded twice by a
        # retrying driver. Sessions without an order id can't be duplicates.
        oid = rec.get("order_id")
        dup = False
        if oid:
            key = (oid, rec.get("outcome"))
            dup = key in seen_keys
            seen_keys.add(key)

        outcome = rec.get("outcome")
        if outcome == "merchant_unreachable":
            unreachable.append(rec)
        elif outcome in EXCLUSIONS or dup:
            excluded[arm] += 1
        else:
            analyzed[arm].append(rec)
    return (analyzed["treatment"], analyzed["control"], excluded,
            unreachable, legacy)


def revenue(rec: dict) -> int:
    return rec["amount_paise"] if rec.get("outcome") == PAID else 0


def metrics(sessions: list[dict]) -> dict:
    n = len(sessions)
    revs = [revenue(r) for r in sessions]
    paid = [r for r in sessions if r.get("outcome") == PAID]
    return {
        "n_analyzed": n,
        "revenue_paise": sum(revs),
        "rev_per_session_paise": (sum(revs) / n) if n else 0.0,
        "conversion": (len(paid) / n) if n else 0.0,
        "aov_paise": (sum(revs) / len(paid)) if paid else 0.0,
        "attach_rate": (sum(1 for r in paid
                            if len(r.get("basket") or []) > 1) / len(paid))
                       if paid else 0.0,
        "outcomes": dict(_count_outcomes(sessions)),
    }


def _count_outcomes(sessions: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in sessions:
        counts[r.get("outcome", "?")] += 1
    return dict(counts)


def _stratified_diff(t: list[dict], c: list[dict],
                     t_idx: list[int], c_idx: list[int],
                     rng: random.Random | None = None) -> float:
    """Mean(T) - Mean(C), computed WITHIN persona strata; each stratum's
    difference is weighted by its share of analyzed sessions.

    rng=None gives the PLUG-IN estimate on the given indices — the
    preregistered point statistic (PREREGISTRATION.md: "Difference in
    primary metric: T-bar - C-bar"). Passing an rng resamples WITHIN each
    arm-stratum and yields one bootstrap draw.

    These two are NOT interchangeable, and conflating them is a real bug:
    a bootstrap draw is a random variable centred on the plug-in, so
    returning a draw as the reported point estimate publishes noise
    (see the 2026-08-30 correction note in WHAT-BROKE.md).
    """
    n_total = len(t_idx) + len(c_idx)

    def arm_mean(sessions: list[dict], idx: list[int]) -> float:
        return sum(revenue(sessions[i]) for i in idx) / len(idx)

    def draw(idx: list[int]) -> list[int]:
        return idx if rng is None else [rng.choice(idx) for _ in idx]

    diff = 0.0
    personas = ({t[i]["persona"] for i in t_idx} |
                {c[i]["persona"] for i in c_idx})
    for p in sorted(personas):
        tp = [i for i in t_idx if t[i]["persona"] == p]
        cp = [i for i in c_idx if c[i]["persona"] == p]
        w = (len(tp) + len(cp)) / n_total
        if tp and cp:
            diff += w * (arm_mean(t, draw(tp)) - arm_mean(c, draw(cp)))
        elif tp:
            diff += w * arm_mean(t, draw(tp))
        elif cp:
            diff -= w * arm_mean(c, draw(cp))
    return diff


def stratified_diff(t: list[dict], c: list[dict]) -> float:
    """The preregistered point estimate: stratified T-bar - C-bar, no
    resampling. Deterministic — same data always gives the same number."""
    return _stratified_diff(t, c, list(range(len(t))),
                            list(range(len(c))), None)


def bootstrap_ci(t: list[dict], c: list[dict], iters: int = ITERS,
                 seed: int = SEED) -> tuple[float, float, float]:
    """Returns (point_estimate, ci_low, ci_high).

    The point estimate is the PLUG-IN stratified difference, never a
    bootstrap draw. The interval is a percentile bootstrap (10,000
    within-stratum resamples, fixed seed), per PREREGISTRATION.md.
    """
    rng = random.Random(seed)
    t_idx, c_idx = list(range(len(t))), list(range(len(c)))
    obs = stratified_diff(t, c)
    diffs = sorted(_stratified_diff(t, c, t_idx, c_idx, rng)
                   for _ in range(iters))
    return obs, diffs[int(0.025 * iters)], \
        diffs[min(iters - 1, int(0.975 * iters))]


def permutation_p(t: list[dict], c: list[dict], iters: int = ITERS,
                  seed: int = SEED) -> tuple[float, float]:
    """Two-sided label shuffle WITHIN persona strata. Statistic mirrors the
    bootstrap estimand: stratum-size-weighted difference of means."""
    rng = random.Random(seed + 1)
    t_rev: dict[str, list[int]] = defaultdict(list)
    c_rev: dict[str, list[int]] = defaultdict(list)
    for r in t:
        t_rev[r["persona"]].append(revenue(r))
    for r in c:
        c_rev[r["persona"]].append(revenue(r))
    n_total = len(t) + len(c)

    def stat(assign: dict[str, tuple[list[int], list[int]]]) -> float:
        d = 0.0
        for p, (tp, cp) in assign.items():
            if tp and cp:
                w = (len(tp) + len(cp)) / n_total
                d += w * ((sum(tp) / len(tp)) - (sum(cp) / len(cp)))
        return d

    persons = sorted(set(t_rev) | set(c_rev))
    obs = abs(stat({p: (list(t_rev.get(p, [])), list(c_rev.get(p, [])))
                    for p in persons}))

    hits = 0
    for _ in range(iters):
        assign: dict[str, tuple[list[int], list[int]]] = {}
        for p in persons:
            vals = t_rev.get(p, []) + c_rev.get(p, [])
            n_tp = len(t_rev.get(p, []))
            rng.shuffle(vals)
            assign[p] = (vals[:n_tp], vals[n_tp:])
        if abs(stat(assign)) >= obs - 1e-9:
            hits += 1
    return obs, hits / iters


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    sessions = load_sessions(sys.argv[1])
    t, c, excluded, unreachable, legacy = split_arms(sessions)

    mt, mc = metrics(t), metrics(c)
    obs, lo, hi = bootstrap_ci(t, c)
    pdiff, pval = permutation_p(t, c)

    report = {
        "n_sessions_total": len(sessions),
        "n_legacy_without_arm": legacy,
        "excluded_per_arm": excluded,
        "unreachable": len(unreachable),
        "treatment": mt,
        "control": mc,
        "primary_diff_paise": obs,
        "primary_ci95_paise": [lo, hi],
        "permutation_abs_diff_paise": pdiff,
        "permutation_p": pval,
        "significant_at_005": bool((lo > 0) or (hi < 0)),
    }

    out = Path(__file__).resolve().parent.parent.parent / "evidence" / "measurement"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    verdict = ("WIN per preregistration rule" if report["significant_at_005"]
               and obs > 0 else
               "NULL/NEGATIVE — reported as-is per preregistration")
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
