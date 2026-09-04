"""Rate limiting + demo-store bound for the public envelope endpoints.

Converted from scripts/test_ratelimit.py.

T4. The endpoints this guards are PUBLIC and UNAUTHENTICATED, and each
hit writes to SQLite. Two failure modes, both self-inflicted:

  1. **Write amplification** — anyone can loop the endpoint and grow the
     demo store without bound on a 1 GB VM.
  2. **Spoofable identity** — rate limiting on the wrong IP lets a client
     rotate a header and keep its whole budget.
"""

import threading
import time

import pytest

from bazaar import audit, db, envelope, ratelimit
from tests.conftest import ok


@pytest.fixture(autouse=True)
def demo_store(tmp_path, monkeypatch):
    """Point the demo's own store at a per-test temp file.

    The rotation checks below rename the store, which must never be the
    real one sitting in .data/.
    """
    path = tmp_path / "envelope_demo.db"
    monkeypatch.setattr(envelope, "_DEMO_DB", path)
    monkeypatch.setattr(envelope, "_DEMO_PATH", str(path))
    return path


# -- 1. the bucket itself ---------------------------------------------------

def test_burst_of_three_is_served():
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    granted = [b.allow("203.0.113.9")[0] for _ in range(3)]
    # A human clicking the demo never notices; a loop hits a wall at once.
    ok("burst of 3 is served", granted == [True, True, True], str(granted))


def test_fourth_request_in_the_burst_is_refused():
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    for _ in range(3):
        b.allow("203.0.113.9")
    allowed, retry = b.allow("203.0.113.9")
    ok("4th request in the burst is refused", allowed is False)
    ok("refusal carries a positive Retry-After", retry > 0, f"{retry:.2f}s")


def test_retry_after_is_about_one_tokens_worth_of_wait():
    """Retry-After must be accurate, or a well-behaved client backs off
    for the wrong time and either hammers us or stalls needlessly."""
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    for _ in range(3):
        b.allow("203.0.113.9")
    _allowed, retry = b.allow("203.0.113.9")
    ok("Retry-After is about one token's worth of wait", 10.0 <= retry <= 13.0,
       f"{retry:.2f}s (expected ~12s at 5/min)")


def test_a_different_client_is_not_throttled():
    """The bucket is per-key. If this failed, one viewer would starve
    everyone else — a worse failure than no limit at all."""
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    for _ in range(4):
        b.allow("203.0.113.9")
    ok("a different client is not throttled",
       b.allow("198.51.100.7")[0] is True)


def test_client_is_served_again_after_waiting_retry_after():
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    for _ in range(3):
        b.allow("203.0.113.9")
    _allowed, retry = b.allow("203.0.113.9")
    time.sleep(retry + 0.35)
    ok("client is served again after waiting Retry-After",
       b.allow("203.0.113.9")[0] is True)


# -- 2. concurrent callers cannot share one token ---------------------------

def test_twenty_simultaneous_requests_only_the_burst_is_served():
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    barrier = threading.Barrier(20)
    results = [None] * 20

    def hammer(i: int) -> None:
        barrier.wait()
        results[i] = b.allow("203.0.113.99")[0]

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    served = sum(1 for r in results if r)
    ok("20 simultaneous requests: only the burst is served", served == 3,
       f"{served} served, {20 - served} refused")
    ok("the hammer test's premise holds (mostly 429)", 20 - served >= 15,
       f"{20 - served} of 20 would return 429")


# -- 3. client IP: the rightmost XFF entry ----------------------------------

def test_no_xff_falls_back_to_the_peer_address():
    ok("no XFF falls back to the peer address",
       ratelimit.client_ip(None, "203.0.113.5") == "203.0.113.5")


def test_single_xff_entry_is_used_as_is():
    ok("single XFF entry is used as-is",
       ratelimit.client_ip("203.0.113.5", "10.0.0.1") == "203.0.113.5")


def test_spoofed_leftmost_entry_is_ignored_rightmost_wins():
    """Caddy appends the real peer. The LEFTMOST value is whatever the
    client felt like sending, so trusting it makes the bucket optional."""
    got = ratelimit.client_ip("1.2.3.4, 203.0.113.5", "10.0.0.1")
    ok("spoofed leftmost entry is ignored, rightmost wins",
       got == "203.0.113.5", got)


def test_a_client_cannot_buy_a_fresh_budget_by_rotating_the_header():
    ok("a client cannot buy a fresh budget by rotating the header",
       ratelimit.client_ip("9.9.9.9, 203.0.113.5", "10.0.0.1")
       == ratelimit.client_ip("8.8.8.8, 203.0.113.5", "10.0.0.1"))


def test_missing_everything_is_labelled_not_crashed():
    ok("missing everything is labelled, not crashed",
       ratelimit.client_ip(None, None) == "unknown")


# -- 4. the dictionary itself cannot be exhausted ---------------------------

def test_bucket_key_count_stays_bounded_under_a_key_flood():
    """Without pruning, every distinct IP that ever touched the endpoint
    stays resident forever — its own memory-exhaustion bug, in the module
    that exists to prevent one."""
    b = ratelimit.TokenBucket(rate_per_min=5.0, burst=3.0)
    for i in range(ratelimit.MAX_KEYS + 500):
        b.allow(f"198.51.100.{i % 254}:{i}")
    ok("bucket key count stays bounded under a key flood",
       len(b) <= ratelimit.MAX_KEYS, f"{len(b)} keys, cap "
       f"{ratelimit.MAX_KEYS}")


# -- 5. the demo store is capped --------------------------------------------

def test_under_the_cap_nothing_is_rotated(demo_store):
    envelope._ensure_schema()
    ok("under the cap, nothing is rotated",
       envelope.rotate_if_large(max_rows=5_000) is None)


def test_over_the_cap_the_store_is_rotated(demo_store):
    """Rotated, NOT trimmed. Deleting rows from an append-only ledger is
    exactly what this project argues against, so the excess is kept under
    a timestamped name instead."""
    envelope._ensure_schema()
    conn = db.connect(str(demo_store))
    try:
        # Written through the real audit writer, so this cannot drift
        # from the schema. The row count is all that matters here.
        audit.append(conn, actor="test", action_type="test.row",
                     payload={"n": 1}, correlation_id="rotate-test")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()
    ok("seed row was written", n >= 1, f"{n} rows")

    rotated = envelope.rotate_if_large(max_rows=0)
    ok("over the cap, the store is rotated", rotated is not None, str(rotated))
    ok("rotated copy is kept as evidence",
       rotated is not None and demo_store.parent.joinpath(rotated).exists(),
       str(rotated))
    ok("live store is gone and will be rebuilt empty",
       not demo_store.exists())


def test_rebuilt_store_starts_empty(demo_store):
    envelope._ensure_schema()
    conn = db.connect(str(demo_store))
    try:
        audit.append(conn, actor="test", action_type="test.row",
                     payload={"n": 1}, correlation_id="rotate-test")
        conn.commit()
    finally:
        conn.close()
    envelope.rotate_if_large(max_rows=0)
    envelope._ensure_schema()
    conn = db.connect(str(demo_store))
    try:
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()
    ok("rebuilt store starts empty", n == 0, f"{n} rows")


# -- 6. the transcript cache -------------------------------------------------

def test_first_call_is_not_a_cache_hit():
    _data, hit = envelope.cached_sequence("cache-buyer")
    ok("first call is not a cache hit", hit is False)


def test_second_call_inside_the_ttl_is_served_from_cache():
    """Uncached, every refresh — and every bot, and every browser
    prefetch — re-runs ten enforcement steps and writes ~10 audit rows."""
    first, _ = envelope.cached_sequence("cache-buyer")
    second, hit = envelope.cached_sequence("cache-buyer")
    ok("second call inside the TTL is served from cache", hit is True)
    ok("cached transcript is the same object", second is first)


def test_cache_key_includes_the_buyer():
    envelope.cached_sequence("cache-buyer")
    _data, hit = envelope.cached_sequence("someone-else")
    ok("cache key includes the buyer", hit is False)


def test_cache_expires_after_the_ttl():
    """A cache that never expired would serve a stale mandate id forever
    on a page that prints one — a quiet lie."""
    first, _ = envelope.cached_sequence("cache-buyer")
    envelope._cached["at"] -= (envelope.TRANSCRIPT_TTL_SECONDS + 1)
    third, hit = envelope.cached_sequence("cache-buyer")
    ok("cache expires after the TTL", hit is False)
    ok("expired cache produces a NEW envelope",
       third["mandate_id"] != first["mandate_id"],
       f"{first['mandate_id']} -> {third['mandate_id']}")


def test_cached_transcript_still_holds_its_properties():
    first, _ = envelope.cached_sequence("cache-buyer")
    second, _hit = envelope.cached_sequence("cache-buyer")
    ok("cached transcript still refuses the same steps",
       second["refusals"] == first["refusals"],
       f"{second['refusals']}/{second['checks']}")
    ok("cached transcript still verifies its chain",
       second["demo_ledger"]["chain_ok"] is True)
