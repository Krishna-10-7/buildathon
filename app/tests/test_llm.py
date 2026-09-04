"""LLM lanes: mock always, live providers when a key is present.

Converted from scripts/test_llm.py.

Design note — this is an AI-judgment test as much as a code test. The
project routes every model call through one `bazaar.llm` seam with a
`mock` provider, so:

  - the whole pipeline is testable with no key, no network, no quota;
  - a missing key fails LOUDLY as `LLMError` rather than silently
    degrading into a heuristic that would quietly invalidate a run.

The live lanes below skip themselves when no key is configured, so CI —
which has none — stays green without pretending those lanes were tested.
"""

import json

import pytest

from bazaar import llm
from bazaar.config import settings
from bazaar.llm import LLMError
from tests.conftest import ok


@pytest.fixture
def provider(monkeypatch):
    """Swap the provider for one test and put it back afterwards."""
    def _set(name: str):
        monkeypatch.setenv("LLM_PROVIDER", name)
        monkeypatch.setattr(settings, "llm_provider", name)
    return _set


async def complete(system: str, user: str, **kw) -> str:
    return await llm.complete(system, [{"role": "user", "content": user}],
                              **kw)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# -- 1. mock lane -------------------------------------------------------------

def test_mock_lane_returns_a_mock_completion(provider):
    provider("mock")
    out = _run(complete("You are a mock.", "hi"))
    ok("mock lane identifies itself", '"mock": true' in out, out[:120])


def test_mock_lane_accepts_the_json_mode_flag(provider):
    """json_mode must not be silently ignored by the mock lane.

    If it were, every offline test of a JSON-returning call would pass
    while the real provider's stricter mode — the one that can reject a
    malformed schema — went untested.
    """
    provider("mock")
    out = _run(complete('Reply ONLY JSON: {"mood":"<word>"}',
                        "Diwali in 3 days.", json_mode=True))
    ok("mock lane honours json_mode", out.strip().startswith("{"), out[:120])


# -- 1b. missing-key guard -------------------------------------------------------

def test_missing_key_fails_loudly_not_silently(provider):
    """A missing key must raise, never fall back to a heuristic.

    A silent fallback would produce plausible-looking agent behaviour
    with no model involved, which would invalidate every measured run —
    and there would be no trace of it in the evidence.
    """
    if settings.nvidia_api_key:
        pytest.skip("NVIDIA_API_KEY is configured — guard not applicable")
    provider("nvidia")
    with pytest.raises(LLMError) as exc:
        _run(complete("s", "hi"))
    ok("missing key raises LLMError rather than degrading",
       "NVIDIA" in str(exc.value) or "key" in str(exc.value).lower(),
       str(exc.value))


# -- 2. live lanes --------------------------------------------------------------
#
# Marked `network` and deselected by default (see [tool.pytest.ini_options]
# in pyproject.toml). Two reasons:
#
#   - CI has no keys and no egress; these would fail for the wrong reason
#     and train everyone to ignore a red build.
#   - They spend real quota on shared free-tier keys.
#
# They are kept, and runnable with `uv run pytest -m network`, because
# "we never checked the live lane" is not an acceptable state either.
# They are skipped, not deleted: a skip says "not verified here", a
# deletion says "not a thing".

@pytest.mark.network
@pytest.mark.skipif(not settings.gemini_api_key,
                    reason="GEMINI_API_KEY not configured")
def test_gemini_fast_lane(provider):
    provider("gemini")
    out = _run(complete("You are a terse assistant. One sentence max.",
                        "Greet the Chai Bazaar agent fleet."))
    ok("gemini fast lane returns text", len(out.strip()) > 0, out[:120])


@pytest.mark.network
@pytest.mark.skipif(not settings.gemini_api_key,
                    reason="GEMINI_API_KEY not configured")
def test_gemini_smart_lane_strict_json(provider):
    provider("gemini")
    out = _run(complete(
        'You rate merchant situations. Reply ONLY JSON: '
        '{"mood": "<one word>", "confidence": <0..1>}',
        "Diwali in 3 days, chai stock at 40%, ad budget untouched.",
        smart=True, json_mode=True, temperature=0.2))
    parsed = json.loads(out)
    ok("gemini smart lane returns parseable JSON with a confidence",
       isinstance(parsed.get("confidence"), (int, float)), str(parsed))


@pytest.mark.network
@pytest.mark.skipif(not settings.nvidia_api_key,
                    reason="NVIDIA_API_KEY not configured")
def test_nvidia_break_glass_lane(provider):
    """KNOWN BROKEN (2026-09-03): NVIDIA retired `meta/llama-3.3-70b-
    instruct` on 2026-08-26 and now answers HTTP 410 Gone.

    The lane is therefore dead — the "break-glass backup provider" in
    config.py does not currently work. Left failing rather than skipped
    or deleted: this is the exact class of thing a test suite exists to
    surface, and a skip would hide it behind a green tick.
    """
    provider("nvidia")
    out = _run(complete("You are a terse assistant. One sentence max.",
                        "Greet the Chai Bazaar agent fleet.",
                        temperature=0.3))
    ok("nvidia break-glass lane returns text", len(out.strip()) > 0,
       out[:120])
