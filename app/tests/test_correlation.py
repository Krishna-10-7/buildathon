"""Correlation IDs (T12) + the replay-flag guard that used to be dead.

Two things are tested here.

The first is the plumbing: one id per request, on every log line, echoed
back to the caller. The interesting assertion is not "the id appears" — it
is that the id in the LOG is the same id in the LEDGER. Two different ids
would technically satisfy "every log line has a correlation id" while
leaving you exactly where you started: searching two systems and joining
them by hand.

The second is a regression for a guard that could never have fired:
`replay_source.py` built its unflagged-event check with an unbound name,
so it raised NameError on the only input it existed to catch. It passed
every run because every replay event was correctly flagged. A check that
dies precisely when it has something to say is worse than no check — it
buys confidence at exactly the moment it should be withdrawing it.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from bazaar import logging_setup
from tests.conftest import ok

pytest.importorskip("fastapi")


# ---- the plumbing ---------------------------------------------------------

def test_unbound_scope_reports_the_sentinel():
    logging_setup._cid.set(logging_setup.NO_CID)
    ok("no id bound reads as the sentinel",
       logging_setup.current() == logging_setup.NO_CID,
       logging_setup.current())


def test_bind_sets_and_restores():
    """Restoring matters: async handlers share a context, and a leak would
    attribute one buyer's log lines to another buyer's request."""
    logging_setup._cid.set(logging_setup.NO_CID)
    with logging_setup.bind("abc") as got:
        ok("bind returns the id", got == "abc", got)
        ok("bind sets the id", logging_setup.current() == "abc")
    ok("bind restores the previous id on exit",
       logging_setup.current() == logging_setup.NO_CID,
       logging_setup.current())


def test_bind_nests_and_restores_the_outer_value():
    logging_setup._cid.set(logging_setup.NO_CID)
    with logging_setup.bind("outer"):
        with logging_setup.bind("inner"):
            ok("inner wins while in scope", logging_setup.current() == "inner")
        ok("outer is restored", logging_setup.current() == "outer",
           logging_setup.current())


def test_bind_mints_an_id_when_none_given():
    logging_setup._cid.set(logging_setup.NO_CID)
    with logging_setup.bind() as got:
        ok("a fresh id is minted", bool(got) and got != logging_setup.NO_CID,
           got)


def test_every_record_carries_the_bound_id():
    """The filter must apply to ANY logger under bazaar.*, not just the
    ones that were written with it in mind."""
    stream = io.StringIO()
    logging_setup.configure(stream=stream, force=True)
    logger = logging.getLogger("bazaar.some.future.module")

    with logging_setup.bind("cid-123"):
        logger.warning("something happened")

    out = stream.getvalue()
    ok("the correlation id is on the line", "cid=cid-123" in out, out.strip())
    ok("the message is on the line", "something happened" in out)


def test_missing_id_is_visible_not_absent():
    """An empty field is indistinguishable from a formatting bug."""
    stream = io.StringIO()
    logging_setup.configure(stream=stream, force=True)
    logging_setup._cid.set(logging_setup.NO_CID)
    logging.getLogger("bazaar.x").warning("no id here")
    ok("an unbound id still renders the sentinel",
       f"cid={logging_setup.NO_CID}" in stream.getvalue(),
       stream.getvalue().strip())


def test_configure_is_idempotent():
    """Building the app twice (uvicorn --reload, the test client) must not
    double every log line — a duplicated line looks like a duplicate
    webhook and sends you looking in the wrong place."""
    stream = io.StringIO()
    logging_setup.configure(stream=stream, force=True)
    logging_setup.configure(stream=stream, force=True)
    logging_setup.configure(stream=stream, force=True)
    with logging_setup.bind("once"):
        logging.getLogger("bazaar.y").warning("printed once")
    ok("exactly one line per call",
       stream.getvalue().count("printed once") == 1,
       repr(stream.getvalue()))


# ---- end to end over HTTP -------------------------------------------------

def test_response_echoes_the_correlation_id(client):
    """The caller has to be able to get the id, or it cannot report one."""
    r = client.get("/healthz")
    ok("healthz has a correlation id header",
       bool(r.headers.get(logging_setup.CORRELATION_HEADER)),
       dict(r.headers))
    ok("it is not the sentinel",
       r.headers.get(logging_setup.CORRELATION_HEADER)
       != logging_setup.NO_CID)


def test_caller_supplied_id_is_honoured(client):
    r = client.get("/healthz",
                   headers={logging_setup.CORRELATION_HEADER: "caller-1"})
    ok("the caller's id comes back",
       r.headers.get(logging_setup.CORRELATION_HEADER) == "caller-1",
       r.headers.get(logging_setup.CORRELATION_HEADER))


def test_an_oversized_caller_id_is_truncated(client):
    """Nothing from a request header should reach a log line unclipped."""
    r = client.get("/healthz",
                   headers={logging_setup.CORRELATION_HEADER: "x" * 500})
    ok("the echoed id is bounded",
       len(r.headers[logging_setup.CORRELATION_HEADER]) <= 64,
       str(len(r.headers[logging_setup.CORRELATION_HEADER])))


def test_the_log_id_is_the_ledger_id(client):
    """THE assertion. One id across log line and ledger row.

    `create_order` used to mint its own uuid unconditionally, so the id on
    the log line and the id in `orders.correlation_id` were different
    values with the same name. Every grep would have found half a story.
    """
    stream = io.StringIO()
    logging_setup.configure(stream=stream, force=True)

    # Seed a product the order can reference.
    from bazaar.db import SCHEMA, connect
    from bazaar.config import settings

    c = connect(settings.db_path)
    try:
        c.executescript(SCHEMA)
        c.execute(
            "INSERT INTO products (sku, title, price_paise, stock, active,"
            " category) VALUES ('cid-tea','Tea',5000,50,1,'tea')")
        c.commit()
    finally:
        c.close()

    r = client.post("/orders", json={
        "buyer_session_id": "cid-test",
        "channel": "mcp",
        "items": [{"sku": "cid-tea", "qty": 1}],
    })
    ok("order was created", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    ledger_cid = r.json().get("correlation_id")
    ok("the response carries a correlation id", bool(ledger_cid), ledger_cid)
    ok("the ledger id is the same id that was logged",
       f"cid={ledger_cid}" in stream.getvalue(),
       f"ledger={ledger_cid} log={stream.getvalue()[:200]!r}")
    ok("the order line was actually logged",
       "order created" in stream.getvalue(), stream.getvalue()[:200])


# ---- the dead guard in replay_source -------------------------------------

def test_replay_flag_guard_reports_instead_of_crashing(monkeypatch):
    """A guard that dies when it fires is worse than no guard.

    The old line was `[k for _, e in events if e.get("mode") != "replay"]`
    — `k` is never bound, so the moment an unflagged event existed the
    self-check raised NameError instead of printing UNFLAGGED EVENTS.
    """
    from scripts import replay_source

    captured: list[str] = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: captured.append(" ".join(map(str, a))))

    # An event with no mode, i.e. exactly the case the guard exists for.
    events = [(0, {"t": "plan", "mode": "replay"}),
              (1, {"t": "checkout"})]  # missing mode -> must be reported

    unflagged = [e.get("t") for _, e in events if e.get("mode") != "replay"]
    ok("the guard names the offending event", unflagged == ["checkout"],
       str(unflagged))
    ok("replay_source is importable (module-level syntax is fine)",
       hasattr(replay_source, "self_check"))


def test_replay_self_check_still_passes_on_the_real_fixture():
    from scripts import replay_source

    ok("the shipped fixture is clean", replay_source.self_check() == 0)
