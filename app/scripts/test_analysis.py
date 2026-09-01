"""Analysis tests: frozen preregistration definitions over synthetic data.

  uv run python scripts/test_analysis.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exp.analysis import (bootstrap_ci, metrics, permutation_p,  # noqa: E402
                          revenue, split_arms, stratified_diff)

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


def sess(arm, persona, outcome, amount=0, lines=1):
    return {"arm": arm, "persona": persona, "outcome": outcome,
            "amount_paise": amount,
            "basket": [{"sku": f"s{i}"} for i in range(lines)],
            "tag": "t"}


T = [  # treatment: two paid, one walked away -> revenue/session = 600/3
    sess("treatment", "ritika", "paid", 34000, lines=2),
    sess("treatment", "arjun", "paid", 26000),
    sess("treatment", "meera", "walked_away"),
]
C = [  # control: one paid -> revenue/session = 200/3
    sess("control", "ritika", "walked_away"),
    sess("control", "arjun", "paid", 20000),
    sess("control", "meera", "payment_failed"),
]

# exclusions land symmetrically in the bookkeeping regardless of counts
MIXED = T + C + [
    sess("treatment", "ritika", "risk_challenged"),
    sess("control", "arjun", "infra_error"),
    {"arm": "control", "persona": "meera", "outcome": "merchant_unreachable",
     "amount_paise": 0, "basket": [], "tag": "x"},
    {"persona": "ritika", "outcome": "paid", "amount_paise": 999,
     "basket": [], "tag": "pre-experiment"},  # no arm -> legacy
]

t, c, excluded, unreachable, legacy = split_arms(MIXED)
ok("legacy pre-experiment sessions reported, never analyzed", legacy == 1)
ok("exclusions counted per arm",
   excluded == {"treatment": 1, "control": 1}, str(excluded))
ok("unreachable set aside, not analyzed", len(unreachable) == 1)
ok("analyzed sets hold the rest", len(t) == 3 and len(c) == 3,
   f"{len(t)}/{len(c)}")

ok("paid contributes revenue; everything else is Rs 0",
   revenue(t[0]) == 34000 and revenue(t[2]) == 0)

mt, mc = metrics(t), metrics(c)
ok("primary metric: net revenue per analyzed session",
   abs(mt["rev_per_session_paise"] - 60000 / 3) < 1e-6
   and abs(mc["rev_per_session_paise"] - 20000 / 3) < 1e-6)
ok("conversion counts paid / analyzed",
   abs(mt["conversion"] - 2 / 3) < 1e-9 and abs(mc["conversion"] - 1 / 3) < 1e-9)
ok("AOV among paid only", mt["aov_paise"] == 30000 and mc["aov_paise"] == 20000)
ok("attach rate = multi-line share among paid",
   abs(mt["attach_rate"] - 0.5) < 1e-9 and mc["attach_rate"] == 0.0)

obs, lo, hi = bootstrap_ci(t, c, iters=2000)
ok("bootstrap point estimate matches raw difference",
   abs(obs - (mt["rev_per_session_paise"] - mc["rev_per_session_paise"])) < 1e-6,
   f"obs={obs:.1f}")
ok("strong synthetic effect gives CI excluding zero", lo > 0,
   f"[{lo:.0f}, {hi:.0f}]")

# Exact permutation with n=6 can NEVER reach p<=0.05 (only 2^3 label
# splits) — so power claims are tested on a properly sized dataset.
CYCLE = ["ritika", "arjun", "meera"]
t_big = [sess("treatment", CYCLE[i % 3], "paid", 30000 + (i % 5) * 1000)
         for i in range(40)]
c_big = [sess("control", CYCLE[i % 3], "paid", 20000 + (i % 5) * 1000)
         for i in range(40)]
_, lo_b, hi_b = bootstrap_ci(t_big, c_big, iters=2000)
_, p_big = permutation_p(t_big, c_big, iters=2000)
ok("powered dataset: CI excludes zero and p is tiny",
   lo_b > 0 and p_big <= 0.01, f"ci=[{lo_b:.0f},...] p={p_big}")

_, p_weak = permutation_p(t, c, iters=2000)
ok("tiny-sample effect keeps an honest large p", p_weak > 0.2, f"p={p_weak}")

# null case: identical arms must NOT be significant
t_null = [sess("treatment", p, o, a, l) for p, o, a, l in
          [("ritika", "paid", 30000, 2), ("arjun", "walked_away", 0, 1)]]
c_null = [sess("control", p, o, a, l) for p, o, a, l in
          [("ritika", "paid", 30000, 2), ("arjun", "walked_away", 0, 1)]]
obs_n, lo_n, hi_n = bootstrap_ci(t_null, c_null, iters=2000)
_, p_null = permutation_p(t_null, c_null, iters=2000)
ok("null case: CI contains zero and p is large",
   lo_n <= 0 <= hi_n and p_null > 0.3,
   f"ci=[{lo_n:.0f},{hi_n:.0f}] p={p_null}")

# determinism: same seed -> same intervals
o2, l2, h2 = bootstrap_ci(t, c, iters=2000)
ok("fixed seed reproduces the interval", (l2, h2) == (lo, hi))

# REGRESSION (2026-08-30): the point estimate must be the plug-in statistic,
# not a bootstrap draw. The fixtures above cannot catch this because their
# persona strata are singletons — rng.choice() on a 1-element list is the
# identity, so a resample silently equals the plug-in. This fixture uses
# multi-session strata AND within-persona variance, so a resampled draw
# differs from the plug-in.
t_wide = [sess("treatment", "ritika", "paid", 30000),
          sess("treatment", "ritika", "paid", 90000),
          sess("treatment", "arjun", "paid", 20000),
          sess("treatment", "arjun", "walked_away", 0)]
c_wide = [sess("control", "ritika", "paid", 50000),
          sess("control", "ritika", "paid", 10000),
          sess("control", "arjun", "paid", 20000),
          sess("control", "arjun", "walked_away", 0)]
plug = stratified_diff(t_wide, c_wide)
o3, l3, h3 = bootstrap_ci(t_wide, c_wide, iters=2000)
ok("point estimate IS the plug-in statistic, not a bootstrap draw",
   abs(o3 - plug) < 1e-9, f"obs={o3:.2f} plug-in={plug:.2f}")
ok("plug-in is deterministic across calls",
   stratified_diff(t_wide, c_wide) == plug)
ok("plug-in falls inside its own bootstrap interval", l3 <= o3 <= h3,
   f"obs={o3:.2f} ci=[{l3:.2f},{h3:.2f}]")
# Manual check of the stratified arithmetic: ritika stratum (4 of 8
# sessions, weight .5) is 60000-30000=30000; arjun (.5) is 10000-10000=0.
ok("stratified weighting matches hand computation",
   abs(plug - (0.5 * 30000 + 0.5 * 0)) < 1e-9, f"{plug:.2f}")

# JSONL loader round-trips through a temp file
tmp = Path(tempfile.mkdtemp()) / "s.jsonl"
with tmp.open("w") as fh:
    for rec in MIXED:
        fh.write(json.dumps(rec) + "\n")
from exp.analysis import load_sessions  # noqa: E402
ok("loader reads what the runner writes", len(load_sessions(tmp)) == len(MIXED))

print(f"\nANALYSIS: {PASS} CHECKS PASSED")
