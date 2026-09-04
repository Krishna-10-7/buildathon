"""Shared fixtures for the pytest suite.

Two properties matter more than anything else here:

1. **Every test gets its own database.** The suites assert on ledger sizes,
   chain verdicts and refusal counts. Sharing one file would make them
   order-dependent, and an order-dependent suite that passes proves
   nothing — it just means you got lucky. So `settings.db_path` is
   redirected into `tmp_path` for every single test, autouse.

   This also means the suites can never touch `app/bazaar.db`, which is
   the ledger the README publishes numbers from. Before this existed,
   running the tests perturbed the published evidence.

2. **`ok()` is still `ok()`.** The suites were written as imperative
   scripts with a tiny `ok(name, cond, detail)` helper. That helper is
   kept, because the detail strings are the useful part of a failure —
   "budget bound not enforced" tells you nothing, "budget bound not
   enforced [spent=600000 cap=100000]" tells you everything. It is now an
   asserting wrapper: it raises AssertionError instead of sys.exit, so
   pytest reports the failing check by name.

The one wrinkle worth knowing about
-----------------------------------
`bazaar.main`'s lifespan enters `mcp_session_manager().run()`, and the MCP
SDK allows that exactly once per process ("Create a new instance if you
need to run again"). A per-test TestClient therefore works for the first
test and errors for the next eleven. So the TestClient is **session
scoped** and the database is **function scoped**: the app starts once,
and each test still gets a fresh store because every request resolves
`settings.db_path` at call time through `db.connect()`.

Run:  uv run pytest -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar import db  # noqa: E402
from bazaar.config import settings  # noqa: E402

# Every check that ran, by name. Used by the coverage-floor test and the
# terminal summary, so a run that silently dropped assertions is visible.
CHECKS: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    """The project's assertion helper, as an asserting wrapper.

    Kept by name and signature so the converted suites read the same as
    they did as scripts. Raises on failure (pytest reports it by name),
    and records every check so the run can prove it did not silently
    lose coverage.
    """
    if not cond:
        raise AssertionError(
            f"{name}" + (f"\n      detail: {detail}" if detail else ""))
    CHECKS.append(name)
    print(f"    ok  {name}" + (f"  [{detail}]" if detail else ""))


# The number of checks the imperative scripts asserted before the
# conversion. Guarded by tests/test_coverage_floor.py: if a refactor
# quietly drops assertions, the suite fails instead of reporting a
# smaller green tick.
BASELINE_CHECKS = 142


def _build(path: Path) -> None:
    conn = db.connect(str(path))
    try:
        conn.executescript(db.SCHEMA)
        db.migrate(conn)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="session")
def session_db(tmp_path_factory):
    """A throwaway store for the app's one-time lifespan startup.

    The lifespan heals the schema on boot; without this it would do that
    to whatever path happens to be configured, which during a bare
    `pytest` run is the real merchant database.
    """
    path = tmp_path_factory.mktemp("session") / "boot.db"
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    mp.setattr(settings, "db_path", str(path))
    _build(path)
    yield path
    mp.undo()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, session_db, monkeypatch):
    """Point the merchant DB at a per-test temp file and build the schema.

    autouse, because a test that forgets to ask for this would silently
    write to the session store — and two tests sharing rows is exactly
    the order-dependence this fixture exists to prevent.
    """
    path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setattr(settings, "db_path", str(path))
    _build(path)
    yield path


@pytest.fixture
def conn(isolated_db):
    """An open connection to this test's database, closed afterwards."""
    c = db.connect(str(isolated_db))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def client(session_db):
    """A TestClient for the merchant app.

    Session scoped: see the module docstring. It is safe to reuse across
    tests because nothing in the app caches the database path — every
    request opens a connection through `db.connect()`.
    """
    from fastapi.testclient import TestClient

    from bazaar.main import app

    with TestClient(app) as c:
        yield c


def pytest_collection_modifyitems(session, config, items):
    """Run the coverage floor LAST, whatever the file is called.

    The floor counts the checks every other test recorded, so it is only
    meaningful after all of them have run. Relying on alphabetical order
    worked right up until a module was named something that sorted later
    — a one-word rename would silently turn the floor into a no-op that
    passes on 43 checks.
    """
    items.sort(key=lambda it: "coverage_floor" in it.nodeid)


def pytest_terminal_summary(terminalreporter):
    """Print how many checks ran, not just how many test functions."""
    total = len(CHECKS)
    if total:
        terminalreporter.write_sep("=", "bazaar checks", bold=True)
        terminalreporter.write_line(
            f"{total} checks across {len(set(CHECKS))} distinct names"
            f"  (baseline {BASELINE_CHECKS})")
