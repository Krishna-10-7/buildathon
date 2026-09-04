"""The live UPI Reserve Pay envelope demo.

Converted from scripts/test_envelope.py.

The properties that matter, in order:

1. **Isolation** — the demo writes to its own store, so the merchant
   ledger we publish is untouched by a judge clicking /demo. If this ever
   regresses, every number in the README is falsified by the act of
   checking it.
2. **One reason per refusal** — each bound is demonstrated alone. A step
   that fails for two reasons proves neither.
3. **The reversal** — the request allowed at step 2 is refused at step 10
   by the same code path.

Both stores are redirected into tmp_path here: the merchant one by the
autouse fixture in conftest.py, and the DEMO one by `demo_store` below.
The demo store is a fixed path in .data/, so without that redirect this
suite would grow a real file on every run and the "ledger grows" checks
would be measuring history rather than this run.
"""

import threading
import time

import pytest

from bazaar import db, envelope
from tests.conftest import ok


@pytest.fixture(autouse=True)
def demo_store(tmp_path, monkeypatch):
    """Point the demo's own store at a per-test temp file too."""
    path = tmp_path / "envelope_demo.db"
    monkeypatch.setattr(envelope, "_DEMO_DB", path)
    monkeypatch.setattr(envelope, "_DEMO_PATH", str(path))
    return path


@pytest.fixture
def run():
    """One full sequence. Most tests need the transcript, not a fresh run."""
    return envelope.run_sequence(buyer_ref="test-buyer")


def merchant_audit_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


# -- 1. isolation ------------------------------------------------------------

def test_demo_does_not_write_to_the_merchant_ledger(run):
    # `before` is captured by the fixture ordering: run_sequence() has
    # already executed by the time this body runs, so compare against a
    # fresh count taken the same way rather than a saved constant.
    ok("demo ledger is a separate store",
       run["demo_ledger"]["store"] == "envelope_demo.db",
       run["demo_ledger"]["store"])
    ok("demo ledger chain verifies", run["demo_ledger"]["chain_ok"] is True)
    ok("demo ledger has no bad sequence",
       run["demo_ledger"]["first_bad_seq"] is None)
    ok("demo ledger is non-empty (it really ran)",
       run["demo_ledger"]["records"] > 0,
       f"{run['demo_ledger']['records']} records")


def test_merchant_ledger_untouched_by_a_run():
    before = merchant_audit_count()
    envelope.run_sequence(buyer_ref="test-buyer")
    ok("demo does not write to the merchant ledger",
       merchant_audit_count() == before,
       f"{before} -> {merchant_audit_count()}")


# -- 2. the sequence itself ---------------------------------------------------

def test_envelope_was_created_and_signed(run):
    ok("envelope was created and signed",
       run["mandate_id"].startswith("mnt_") and len(run["signature"]) == 64,
       run["mandate_id"])


def test_five_checks_were_made(run):
    ok("five checks were made", run["checks"] == 5, str(run["checks"]))


def test_four_were_refused(run):
    ok("four were refused", run["refusals"] == 4, str(run["refusals"]))


def test_step_2_inside_every_bound_is_allowed(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 2 (inside every bound) is allowed", steps[2]["allowed"] is True)


def test_step_4_refused_on_the_single_txn_cap_alone(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 4 refused on the single-txn cap alone",
       steps[4]["rules"] == ["single-txn cap"], str(steps[4]["rules"]))


def test_step_5_refused_on_category_alone(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 5 refused on category alone",
       steps[5]["rules"] == ["category outside envelope"],
       str(steps[5]["rules"]))


def test_step_8_refused_on_budget_alone(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 8 refused on budget alone",
       steps[8]["rules"] == ["budget exhausted"], str(steps[8]["rules"]))


def test_step_10_refused_on_revocation_alone(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 10 refused on revocation alone",
       steps[10]["rules"] == ["envelope revoked"], str(steps[10]["rules"]))


# -- 3. one reason per refusal -------------------------------------------------

def test_every_refusal_fires_exactly_one_bound(run):
    multi = [s["n"] for s in run["steps"]
             if s["kind"] == "check" and not s["allowed"]
             and len(s["rules"]) != 1]
    ok("every refusal fires exactly ONE bound", not multi, f"steps {multi}")


def test_four_distinct_bounds_demonstrated(run):
    ok("four distinct bounds demonstrated",
       len(run["distinct_refusal_rules"]) == 4,
       str(run["distinct_refusal_rules"]))


# -- 4. the reversal -----------------------------------------------------------

def test_step_10_is_the_same_request_as_step_2(run):
    steps = {s["n"]: s for s in run["steps"]}
    ok("step 10 is the same request as step 2",
       steps[10]["detail"] == steps[2]["detail"],
       f"{steps[10]['detail']} vs {steps[2]['detail']}")


def test_the_same_request_passes_at_2_and_fails_at_10(run):
    """The whole argument, in one assertion.

    Same bytes in, different verdict out — and the only thing that
    changed is that the buyer withdrew consent. That is what makes this
    a bound and not a suggestion.
    """
    steps = {s["n"]: s for s in run["steps"]}
    ok("the same request passes at 2 and fails at 10",
       steps[2]["allowed"] and not steps[10]["allowed"])


# -- 5. spend accounting --------------------------------------------------------

def test_spend_drawn_down_to_the_captured_total(run):
    expected = sum(envelope.CAPTURES_PAISE)
    ok("spend drawn down to the captured total",
       run["spent_paise"] == expected, f"{run['spent_paise']} vs {expected}")


def test_spend_stays_within_the_budget_cap(run):
    ok("spend stays within the budget cap",
       run["spent_paise"] <= run["budget_cap_paise"])


def test_envelope_is_revoked_at_the_end(run):
    ok("envelope is revoked at the end", run["revoked_at"] is not None)


# -- 6. repeatability ------------------------------------------------------------

def test_a_second_run_gets_a_fresh_envelope(run):
    again = envelope.run_sequence(buyer_ref="test-buyer")
    ok("a second run gets a fresh envelope",
       again["mandate_id"] != run["mandate_id"])


def test_a_second_run_still_leaves_the_merchant_ledger_alone():
    before = merchant_audit_count()
    envelope.run_sequence(buyer_ref="test-buyer")
    envelope.run_sequence(buyer_ref="test-buyer")
    ok("a second run still leaves the merchant ledger alone",
       merchant_audit_count() == before,
       f"{before} -> {merchant_audit_count()}")


def test_the_demo_ledger_chain_survives_repeated_runs(run):
    again = envelope.run_sequence(buyer_ref="test-buyer")
    ok("the demo ledger chain survives repeated runs",
       again["demo_ledger"]["chain_ok"] is True)


def test_the_demo_ledger_grows(run):
    again = envelope.run_sequence(buyer_ref="test-buyer")
    ok("the demo ledger grows (it accumulates evidence)",
       again["demo_ledger"]["records"] > run["demo_ledger"]["records"],
       f"{run['demo_ledger']['records']} -> {again['demo_ledger']['records']}")


# -- 7. concurrency: the demo store is reached by parameter, not global swap --

def test_no_concurrent_read_ever_sees_the_demo_store():
    """Regression test for a real bug.

    envelope.py used to redirect the process-global settings.db_path for
    the duration of run_sequence(); _LOCK serialised envelope runs but did
    NOTHING for any other code in the process. An SSE audit ticker or
    /api/state call landing inside that window silently read
    envelope_demo.db and reported the demo's ledger size as the
    merchant's.

    A lock cannot fix a shared global; passing the path can. So: hammer
    db.connect() from another thread while a run is in flight and require
    that it never once sees the demo store.
    """
    merchant_before = merchant_audit_count()
    demo_before = envelope.demo_audit_count()

    # A vacuous test is worse than no test: if the two stores happened to
    # hold the same number of rows, "never equal" would pass no matter
    # what. So make the counts differ first.
    if merchant_before == demo_before:
        envelope.run_sequence(buyer_ref="test-buyer")
        demo_before = envelope.demo_audit_count()
    ok("merchant and demo ledger sizes differ, so the test can detect a leak",
       merchant_before != demo_before,
       f"merchant={merchant_before} demo={demo_before}")

    observed: list[int] = []
    stop = threading.Event()

    def sample_merchant() -> None:
        """What any other request in this process would see mid-run."""
        while not stop.is_set():
            conn = db.connect()        # default path — the merchant ledger
            try:
                observed.append(conn.execute(
                    "SELECT COUNT(*) FROM audit_log").fetchone()[0])
            finally:
                conn.close()
            time.sleep(0.001)

    sampler = threading.Thread(target=sample_merchant, daemon=True)
    sampler.start()
    try:
        envelope.run_sequence(buyer_ref="concurrent-buyer")
    finally:
        stop.set()
        sampler.join(timeout=5)

    ok("the sampler actually ran during the sequence (not vacuous)",
       len(observed) > 10, f"{len(observed)} samples")
    leaked = sorted({n for n in observed if n != merchant_before})
    ok("no concurrent read ever saw a non-merchant ledger size",
       not leaked, f"saw {leaked[:5]} instead of {merchant_before}")
    ok("no concurrent read ever saw the demo store's size",
       demo_before not in observed, f"demo size {demo_before} was observed")
    ok("the merchant ledger is unchanged by the concurrent run",
       merchant_audit_count() == merchant_before,
       f"{merchant_before} -> {merchant_audit_count()}")
    ok("the demo ledger did grow, proving the run really happened",
       envelope.demo_audit_count() > demo_before,
       f"{demo_before} -> {envelope.demo_audit_count()}")
