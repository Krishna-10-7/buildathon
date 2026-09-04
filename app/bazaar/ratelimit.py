"""In-process token bucket for the unauthenticated demo endpoints.

POST /api/envelope and GET /envelope are public and each one writes to
SQLite. On a 1 GB VM that is a self-inflicted DoS: anyone can loop the
endpoint and grow the demo store without bound. So the endpoints are
rate-limited per client and the store is capped.

Why in-process rather than Redis/nginx: there is one uvicorn worker behind
Caddy and the budget for this project is zero rupees. A per-process bucket
is honest about that -- it limits one process, and this docstring says so,
rather than looking like infrastructure it is not.

Trust model for the client IP
-----------------------------
There is exactly ONE reverse proxy in front (Caddy, on the same host, see
deploy/Caddyfile.r2d2). Caddy APPENDS the peer address to any existing
X-Forwarded-For, so:

    client sends nothing      -> XFF = "203.0.113.9"           (real)
    client sends "1.2.3.4"    -> XFF = "1.2.3.4, 203.0.113.9"  (spoof, real)

The LEFTMOST entry is attacker-controlled; the RIGHTMOST is the one Caddy
wrote. So we take the last. That is only correct because the hop count is
known to be one -- behind two proxies the right answer differs and this
function would need the trusted-proxy depth passed in explicitly.
"""

import logging
import threading
import time

log = logging.getLogger("bazaar.ratelimit")

RATE_PER_MIN = 5.0
BURST = 3.0
MAX_KEYS = 10_000          # bound the dictionary a hostile client can grow


class TokenBucket:
    """Constant-refill bucket, one counter per key.

    Not a sliding window: a bucket is what "5 per minute, burst 3" means,
    and it is O(1) per key with no history to retain.
    """

    def __init__(self, rate_per_min: float = RATE_PER_MIN,
                 burst: float = BURST) -> None:
        self._rate = rate_per_min / 60.0     # tokens per second
        self._burst = float(burst)
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds).

        retry_after is 0.0 when allowed, and otherwise the time until one
        more token exists -- exactly what the Retry-After header wants.
        """
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)

            if tokens >= 1.0:
                tokens -= 1.0
                self._state[key] = (tokens, now)
                self._prune(now)
                return True, 0.0

            self._state[key] = (tokens, now)
            self._prune(now)
            return False, (1.0 - tokens) / self._rate

    def _prune(self, now: float) -> None:
        """Drop keys that have refilled to full: they carry no state.

        Called under the lock. Without this, every distinct IP that ever
        touched the endpoint stays resident forever, which is its own
        memory-exhaustion bug -- the thing this module exists to prevent.
        """
        if len(self._state) <= MAX_KEYS:
            return
        for k in [k for k, (t, _l) in self._state.items()
                  if t >= self._burst - 1e-9]:
            del self._state[k]
        if len(self._state) > MAX_KEYS:      # still over: drop the stalest
            for k in sorted(self._state, key=lambda k: self._state[k][1])[
                    : len(self._state) - MAX_KEYS]:
                del self._state[k]

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._state)


def client_ip(x_forwarded_for: str | None, peer_host: str | None) -> str:
    """Rightmost XFF entry; see the trust model in the module docstring."""
    if x_forwarded_for:
        parts = [p.strip() for p in x_forwarded_for.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return peer_host or "unknown"
