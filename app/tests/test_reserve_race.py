"""The check-then-spend race, and why reserve() closes it.

Converted from scripts/test_reserve_race.py.

This is the bound that must never break: `spent_paise <= budget_cap_paise`.
Under the old `check()` then `draw_down()` sequence, two orders against one
envelope could both read spent=0, both pass, and both increment — so the
cap was enforced only when nobody was racing.

Each thread tries to reserve 60% of the budget. At most one can fit.
"""

import threading

import pytest

from bazaar import audit, db, mandates
from tests.conftest import ok

CAP = 100_000          # Rs 1000.00
BIG = 60_000           # 60% — two of these do not fit
THREADS = 20


def fresh_conn() -> "db.sqlite3.Connection":
    """Each thread gets its OWN connection — that is the whole point."""
    c = db.connect()
    c.executescript(db.SCHEMA)
    db.migrate(c)
    c.commit()
    return c


@pytest.fixture
def race():
    """Run the 20-thread barrage once and hand back everything observed.

    One fixture rather than one test, because the race is the expensive
    and non-deterministic part; the assertions about its outcome are
    cheap and should each be able to fail on their own.
    """
    setup = fresh_conn()
    env = mandates.create("race-buyer", CAP, CAP, ["tea"], ttl_hours=1.0,
                          conn=setup)
    # A passed-in connection is ours to commit; reserve() will not.
    setup.commit()
    setup.close()

    results: list[tuple[int, bool, list[str]]] = []
    errors: list[str] = []
    barrier = threading.Barrier(THREADS)

    def worker(i: int) -> None:
        conn = fresh_conn()
        try:
            barrier.wait()      # make them hit the envelope simultaneously
            _row, verdict = mandates.reserve(conn, env["id"], BIG, ["tea"])
            if verdict.allowed:
                conn.commit()
            results.append((i, verdict.allowed, verdict.reasons))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"thread {i}: {type(exc).__name__}: {exc}")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    check = fresh_conn()
    try:
        final = mandates.get(env["id"], conn=check)
        chain_ok, records, first_bad = audit.verify(check)
        n_reserved = check.execute(
            "SELECT COUNT(*) FROM audit_log"
            " WHERE action_type='mandate.reserved'").fetchone()[0]
        yield {"env": env, "results": results, "errors": errors,
               "final": final, "chain_ok": chain_ok, "records": records,
               "first_bad": first_bad, "n_reserved": n_reserved,
               "conn": check}
    finally:
        check.close()


def test_no_thread_raised(race):
    ok("no thread raised", not race["errors"],
       "; ".join(race["errors"][:3]))


def test_every_thread_got_a_verdict(race):
    ok("every thread got a verdict", len(race["results"]) == THREADS,
       f"{len(race['results'])}/{THREADS}")


def test_exactly_one_reservation_succeeded(race):
    allowed = [i for i, a, _ in race["results"] if a]
    ok(f"exactly one of {THREADS} reservations succeeded", len(allowed) == 1,
       f"{len(allowed)} succeeded: {allowed}")


def test_spent_never_exceeds_the_budget_cap(race):
    """The invariant the whole envelope exists to protect."""
    ok("spent never exceeds the budget cap",
       race["final"]["spent_paise"] <= CAP,
       f"spent={race['final']['spent_paise']} cap={CAP}")


def test_spent_equals_exactly_one_reservation(race):
    ok("spent equals exactly one reservation",
       race["final"]["spent_paise"] == BIG,
       f"{race['final']['spent_paise']} vs {BIG}")


def test_every_loser_got_a_reason(race):
    losers = [r for _, a, r in race["results"] if not a]
    ok("every loser got a reason", bool(losers) and all(losers))


def test_losers_were_refused_on_the_budget_bound(race):
    """A refusal with no reason is unusable — an agent cannot correct a
    course it was not told it was off."""
    losers = [r for _, a, r in race["results"] if not a]
    ok("losers were refused on the budget bound",
       all(any("budget" in x for x in r) for r in losers),
       str(losers[0]) if losers else "no losers")


def test_audit_chain_intact_after_the_race(race):
    ok("audit chain intact after the race", race["chain_ok"] is True,
       f"records={race['records']}")


def test_no_bad_sequence(race):
    ok("no bad sequence", race["first_bad"] is None, str(race["first_bad"]))


def test_reservations_were_audited(race):
    ok("reservations were audited", race["n_reserved"] == 1,
       f"expected exactly 1 reservation row, got {race['n_reserved']}")


def test_release_returns_the_hold(race):
    rel = fresh_conn()
    try:
        mandates.release(rel, race["env"]["id"], BIG)
        rel.commit()
        after = mandates.get(race["env"]["id"], conn=rel)
        ok("release returns the full hold", after["spent_paise"] == 0,
           f"{after['spent_paise']}")
    finally:
        rel.close()


def test_release_clamps_at_zero_and_cannot_invent_budget(race):
    """Over-releasing must not produce negative spend.

    Negative spent would mean the envelope could spend more than its cap
    later — the bound leaking through the refund path.
    """
    rel = fresh_conn()
    try:
        mandates.release(rel, race["env"]["id"], BIG)
        rel.commit()
        mandates.release(rel, race["env"]["id"], BIG)   # over-release
        rel.commit()
        after = mandates.get(race["env"]["id"], conn=rel)
        ok("release clamps at zero and cannot invent budget",
           after["spent_paise"] == 0, f"{after['spent_paise']}")
    finally:
        rel.close()


def test_budget_freed_by_release_can_be_reserved_again(race):
    rel = fresh_conn()
    try:
        mandates.release(rel, race["env"]["id"], BIG)
        rel.commit()
    finally:
        rel.close()
    fit = fresh_conn()
    try:
        _row, v2 = mandates.reserve(fit, race["env"]["id"], BIG, ["tea"])
        fit.commit()
        ok("budget freed by release can be reserved again", v2.allowed is True,
           str(v2.reasons))
    finally:
        fit.close()
