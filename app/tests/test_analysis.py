"""Analysis: frozen preregistration definitions over synthetic data.

Converted from scripts/test_analysis.py.

These are the definitions the A/B result was preregistered against, so
they are frozen: if `revenue()` or `split_arms()` changes meaning, every
published number moves with it. Nothing here touches a database — the
statistics are pure functions of a session list.
"""

import json

import pytest

from exp.analysis import (bootstrap_ci, load_sessions, metrics, permutation_p,
                          revenue, split_arms, stratified_diff)
from tests.conftest import ok


def sess(arm, persona, outcome, amount=0, lines=1):
    return {"arm": arm, "persona": persona, "outcome": outcome,
            "amount_paise": amount,
            "basket": [{"sku": f"s{i}"} for i in range(lines)],
            "tag": "t"}


# treatment: two paid, one walked away -> revenue/session = 600/3
T = [sess("treatment", "ritika", "paid", 34000, lines=2),
     sess("treatment", "arjun", "paid", 26000),
     sess("treatment", "meera", "walked_away")]
# control: one paid -> revenue/session = 200/3
C = [sess("control", "ritika", "walked_away"),
     sess("control", "arjun", "paid", 20000),
     sess("control", "meera", "payment_failed")]

MIXED = T + C + [
    sess("treatment", "ritika", "risk_challenged"),
    sess("control", "arjun", "infra_error"),
    {"arm": "control", "persona": "meera", "outcome": "merchant_unreachable",
     "amount_paise": 0, "basket": [], "tag": "x"},
    {"persona": "ritika", "outcome": "paid", "amount_paise": 999,
     "basket": [], "tag": "pre-experiment"},   # no arm -> legacy
]

CYCLE = ["ritika", "arjun", "meera"]
# Exact permutation with n=6 can NEVER reach p<=0.05 (only 2^3 label
# splits) — so power claims are tested on a properly sized dataset.
T_BIG = [sess("treatment", CYCLE[i % 3], "paid", 30000 + (i % 5) * 1000)
         for i in range(40)]
C_BIG = [sess("control", CYCLE[i % 3], "paid", 20000 + (i % 5) * 1000)
         for i in range(40)]

# REGRESSION (2026-08-30): the point estimate must be the plug-in
# statistic, not a bootstrap draw. The fixtures above cannot catch this
# because their persona strata are singletons — rng.choice() on a
# 1-element list is the identity, so a resample silently equals the
# plug-in. These use multi-session strata AND within-persona variance.
T_WIDE = [sess("treatment", "ritika", "paid", 30000),
          sess("treatment", "ritika", "paid", 90000),
          sess("treatment", "arjun", "paid", 20000),
          sess("treatment", "arjun", "walked_away", 0)]
C_WIDE = [sess("control", "ritika", "paid", 50000),
          sess("control", "ritika", "paid", 10000),
          sess("control", "arjun", "paid", 20000),
          sess("control", "arjun", "walked_away", 0)]


@pytest.fixture
def split():
    return split_arms(MIXED)


def test_legacy_pre_experiment_sessions_reported_never_analyzed(split):
    _t, _c, _ex, _un, legacy = split
    ok("legacy pre-experiment sessions reported, never analyzed", legacy == 1)


def test_exclusions_counted_per_arm(split):
    _t, _c, excluded, _un, _l = split
    ok("exclusions counted per arm",
       excluded == {"treatment": 1, "control": 1}, str(excluded))


def test_unreachable_set_aside_not_analyzed(split):
    _t, _c, _ex, unreachable, _l = split
    ok("unreachable set aside, not analyzed", len(unreachable) == 1)


def test_analyzed_sets_hold_the_rest(split):
    t, c, _ex, _un, _l = split
    ok("analyzed sets hold the rest", len(t) == 3 and len(c) == 3,
       f"{len(t)}/{len(c)}")


def test_paid_contributes_revenue_everything_else_is_zero(split):
    t, _c, *_ = split
    ok("paid contributes revenue; everything else is Rs 0",
       revenue(t[0]) == 34000 and revenue(t[2]) == 0)


def test_primary_metric_net_revenue_per_analyzed_session(split):
    t, c, *_ = split
    mt, mc = metrics(t), metrics(c)
    ok("primary metric: net revenue per analyzed session",
       abs(mt["rev_per_session_paise"] - 60000 / 3) < 1e-6
       and abs(mc["rev_per_session_paise"] - 20000 / 3) < 1e-6)


def test_conversion_counts_paid_over_analyzed(split):
    t, c, *_ = split
    mt, mc = metrics(t), metrics(c)
    ok("conversion counts paid / analyzed",
       abs(mt["conversion"] - 2 / 3) < 1e-9
       and abs(mc["conversion"] - 1 / 3) < 1e-9)


def test_aov_among_paid_only(split):
    t, c, *_ = split
    mt, mc = metrics(t), metrics(c)
    ok("AOV among paid only",
       mt["aov_paise"] == 30000 and mc["aov_paise"] == 20000)


def test_attach_rate_is_multiline_share_among_paid(split):
    t, c, *_ = split
    mt, mc = metrics(t), metrics(c)
    ok("attach rate = multi-line share among paid",
       abs(mt["attach_rate"] - 0.5) < 1e-9 and mc["attach_rate"] == 0.0)


def test_bootstrap_point_estimate_matches_raw_difference(split):
    t, c, *_ = split
    mt, mc = metrics(t), metrics(c)
    obs, _lo, _hi = bootstrap_ci(t, c, iters=2000)
    ok("bootstrap point estimate matches raw difference",
       abs(obs - (mt["rev_per_session_paise"] - mc["rev_per_session_paise"]))
       < 1e-6, f"obs={obs:.1f}")


def test_strong_synthetic_effect_gives_ci_excluding_zero(split):
    t, c, *_ = split
    _obs, lo, hi = bootstrap_ci(t, c, iters=2000)
    ok("strong synthetic effect gives CI excluding zero", lo > 0,
       f"[{lo:.0f}, {hi:.0f}]")


def test_powered_dataset_ci_excludes_zero_and_p_is_tiny():
    _o, lo_b, _hi_b = bootstrap_ci(T_BIG, C_BIG, iters=2000)
    _p_obs, p_big = permutation_p(T_BIG, C_BIG, iters=2000)
    ok("powered dataset: CI excludes zero and p is tiny",
       lo_b > 0 and p_big <= 0.01, f"ci=[{lo_b:.0f},...] p={p_big}")


def test_tiny_sample_effect_keeps_an_honest_large_p(split):
    t, c, *_ = split
    _p_obs, p_weak = permutation_p(t, c, iters=2000)
    ok("tiny-sample effect keeps an honest large p", p_weak > 0.2,
       f"p={p_weak}")


def test_null_case_ci_contains_zero_and_p_is_large():
    """Identical arms must NOT come out significant.

    This is the guard against a test that can only ever say yes. The
    published result for the growth A/B was NULL, and a harness that
    cannot produce a null is not measuring anything.
    """
    t_null = [sess("treatment", p, o, a, ln) for p, o, a, ln in
              [("ritika", "paid", 30000, 2), ("arjun", "walked_away", 0, 1)]]
    c_null = [sess("control", p, o, a, ln) for p, o, a, ln in
              [("ritika", "paid", 30000, 2), ("arjun", "walked_away", 0, 1)]]
    _obs, lo_n, hi_n = bootstrap_ci(t_null, c_null, iters=2000)
    _p_obs, p_null = permutation_p(t_null, c_null, iters=2000)
    ok("null case: CI contains zero and p is large",
       lo_n <= 0 <= hi_n and p_null > 0.3,
       f"ci=[{lo_n:.0f},{hi_n:.0f}] p={p_null}")


def test_fixed_seed_reproduces_the_interval(split):
    t, c, *_ = split
    _o, lo, hi = bootstrap_ci(t, c, iters=2000)
    _o2, l2, h2 = bootstrap_ci(t, c, iters=2000)
    ok("fixed seed reproduces the interval", (l2, h2) == (lo, hi))


def test_point_estimate_is_the_plugin_statistic_not_a_bootstrap_draw():
    plug = stratified_diff(T_WIDE, C_WIDE)
    o3, _l3, _h3 = bootstrap_ci(T_WIDE, C_WIDE, iters=2000)
    ok("point estimate IS the plug-in statistic, not a bootstrap draw",
       abs(o3 - plug) < 1e-9, f"obs={o3:.2f} plug-in={plug:.2f}")


def test_plugin_is_deterministic_across_calls():
    plug = stratified_diff(T_WIDE, C_WIDE)
    ok("plug-in is deterministic across calls",
       stratified_diff(T_WIDE, C_WIDE) == plug)


def test_plugin_falls_inside_its_own_bootstrap_interval():
    o3, l3, h3 = bootstrap_ci(T_WIDE, C_WIDE, iters=2000)
    ok("plug-in falls inside its own bootstrap interval", l3 <= o3 <= h3,
       f"obs={o3:.2f} ci=[{l3:.2f},{h3:.2f}]")


def test_stratified_weighting_matches_hand_computation():
    """ritika stratum (4 of 8 sessions, weight .5) is 60000-30000=30000;
    arjun (.5) is 10000-10000=0."""
    plug = stratified_diff(T_WIDE, C_WIDE)
    ok("stratified weighting matches hand computation",
       abs(plug - (0.5 * 30000 + 0.5 * 0)) < 1e-9, f"{plug:.2f}")


def test_loader_reads_what_the_runner_writes(tmp_path):
    p = tmp_path / "s.jsonl"
    with p.open("w") as fh:
        for rec in MIXED:
            fh.write(json.dumps(rec) + "\n")
    ok("loader reads what the runner writes",
       len(load_sessions(p)) == len(MIXED))
