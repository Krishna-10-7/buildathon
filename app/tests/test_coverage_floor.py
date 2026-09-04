"""The suite must not quietly lose coverage.

Every conversion refactor risks the same silent failure: a check gets
dropped, nothing errors, and `pytest -q` prints a smaller green tick.
Nothing in the run itself distinguishes "142 assertions passed" from "60
assertions passed" — both are green.

So the floor is asserted. `BASELINE_CHECKS` is the number the imperative
scripts asserted before the pytest conversion; if a future run records
fewer, this fails loudly instead of quietly shipping less evidence.

This runs LAST — forced there by `pytest_collection_modifyitems` in
conftest.py, not by filename, so renaming a module cannot quietly turn
the floor into a no-op. It also only applies to a full-suite run:
`pytest tests/test_x.py` alone will not have accumulated the others.
"""

import os

import pytest

from tests.conftest import BASELINE_CHECKS, CHECKS

# Set by CI / by `make test` when running the whole suite. A single-module
# run cannot satisfy the floor and should not try.
FULL_RUN = os.environ.get("BAZAAR_FULL_SUITE") == "1"


@pytest.mark.skipif(not FULL_RUN,
                    reason="coverage floor only applies to a full-suite run")
def test_check_count_did_not_regress():
    total = len(CHECKS)
    assert total >= BASELINE_CHECKS, (
        f"only {total} checks ran; baseline is {BASELINE_CHECKS}. "
        "A refactor dropped assertions — restore them or update "
        "BASELINE_CHECKS with a written reason.")


def test_helper_records_failing_checks_by_name():
    """`ok()` must raise on failure, or the suites silently pass.

    An assertion helper that printed instead of raising would make every
    converted suite vacuous, and the green ticks above would be fiction.
    """
    from tests.conftest import ok

    with pytest.raises(AssertionError) as exc:
        ok("deliberately false", False, "detail-for-the-failure-message")
    assert "deliberately false" in str(exc.value)
    assert "detail-for-the-failure-message" in str(exc.value)
