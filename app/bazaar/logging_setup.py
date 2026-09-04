"""Correlation IDs for the money path (T12).

The audit ledger already chains every money action, but a chain only tells
you the ORDER of things — you have to know where to start reading. When an
AI buyer's payment lands at 02:00 and the merchant's owner wakes up to a
question, the thing they need is: give me every log line this one request
produced, across orders, webhooks and mandates, in any log sink.

So: one id per request, carried in a contextvar, injected into every record
by a filter. Not passed down through function arguments — that would mean
threading a parameter through `policy.py`, which is deliberately pure and
takes no logging concern at all. The contextvar keeps the pure cores pure
while still making the edges traceable.

Usage
-----
    from bazaar.logging_setup import bind, log_for

    log = log_for("orders")            # module logger
    with bind(correlation_id):
        log.info("order created")      # -> ... cid=<hex> order created

`configure()` is called once from main.py's lifespan. Anything that runs
without it (a script, a test) still gets correct behaviour — just no
handler, so the logs go wherever the application configured.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager

# "-" rather than "" or "none": a missing id must be visible in the log
# line, not merely absent from it. An empty field is indistinguishable
# from a formatting bug. Exported as NO_CID because callers compare
# against it to decide whether to mint their own id.
NO_CID = "-"

_cid: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bazaar_correlation_id", default=NO_CID)

CORRELATION_HEADER = "X-Correlation-Id"


def current() -> str:
    """The id in scope, or "-" if none is bound."""
    return _cid.get()


@contextmanager
def bind(correlation_id: str | None = None):
    """Bind an id for the duration of the block, then restore the previous.

    Restoring matters: async handlers share a task context, and a leak
    would silently attribute one buyer's log lines to a different one.
    """
    token = _cid.set(correlation_id or uuid.uuid4().hex)
    try:
        yield _cid.get()
    finally:
        _cid.reset(token)


class _CorrelationFilter(logging.Filter):
    """Attach the in-scope id to every record, whatever logger emitted it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _cid.get() if _cid.get() != NO_CID else NO_CID
        return True


def log_for(module: str) -> logging.Logger:
    """Module logger under the `bazaar.` namespace.

    `logging.getLogger` is idempotent, so calling this at import time in
    several modules is fine and cheap.
    """
    return logging.getLogger(f"bazaar.{module}")


FORMAT = ("%(asctime)s %(levelname)-7s [%(name)s] cid=%(correlation_id)s "
          "%(message)s")


def configure(level: int = logging.INFO, stream=None,
              force: bool = False) -> None:
    """Install the handler and filter. Idempotent unless `force`.

    Idempotent on purpose: uvicorn --reload and the test client both build
    the app more than once in a process, and a second call used to mean a
    second handler and therefore every line printed twice — which looks
    exactly like a duplicate webhook, and sends you looking in the wrong
    place.

    `force` re-points logging at a new stream, replacing whatever handler
    is already there. It exists for tests: a test that cannot read the log
    line can only assert that the logger was *called*, which is a much
    weaker claim than "the line contains the id".
    """
    root = logging.getLogger("bazaar")
    for h in list(root.handlers):
        if getattr(h, "_bazaar_handler", False):
            if not force:
                return
            root.removeHandler(h)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(_CorrelationFilter())
    handler._bazaar_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    # Do not propagate to the root logger: uvicorn installs its own handler
    # there, and without this every bazaar line prints twice.
    root.propagate = False
