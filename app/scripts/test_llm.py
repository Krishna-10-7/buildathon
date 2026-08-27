"""Smoke-test every LLM lane: mock, gemini fast/smart (json_mode), nvidia.

Usage:
  uv run python scripts/test_llm.py            # mock only
  uv run python scripts/test_llm.py --live     # mock + real Gemini calls
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.llm import LLMError  # noqa: E402


async def run(live: bool) -> int:
    from bazaar import llm
    from bazaar.config import settings

    # -- 1. mock lane -------------------------------------------------------
    os.environ["LLM_PROVIDER"] = "mock"
    settings.llm_provider = "mock"
    out = await llm.complete("You are a mock.", [{"role": "user", "content": "hi"}])
    print("1. mock      :", out[:90], "...")
    assert '"mock": true' in out

    # -- 1b. nvidia missing-key guard (skipped when a key is configured) ----
    orig_provider = settings.llm_provider
    os.environ["LLM_PROVIDER"] = "nvidia"
    settings.llm_provider = "nvidia"
    if settings.nvidia_api_key:
        print("1b. nvidia     : key present — missing-key guard skipped")
    else:
        try:
            await llm.complete("s", [{"role": "user", "content": "hi"}])
            raise AssertionError("expected LLMError for missing NVIDIA_API_KEY")
        except LLMError as exc:
            print("1b. nvidia guard:", exc)
    os.environ["LLM_PROVIDER"] = orig_provider
    settings.llm_provider = orig_provider

    if not live:
        print("\nMOCK LANE OK (use --live for Gemini)")
        return 0

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY missing in .env")
        return 2

    # -- 2. gemini fast lane --------------------------------------------------
    os.environ["LLM_PROVIDER"] = "gemini"
    settings.llm_provider = "gemini"
    out = await llm.complete(
        "You are a terse assistant. One sentence max.",
        [{"role": "user", "content": "In one short sentence, greet the Chai Bazaar agent fleet."}],
    )
    print(f"2. gemini fast [{settings.llm_model}]:",
          out.encode("ascii", "replace").decode()[:120])

    # -- 3. gemini smart lane, strict JSON ------------------------------------
    out = await llm.complete(
        "You rate merchant situations. Reply ONLY JSON: "
        '{"mood": "<one word>", "confidence": <0..1>}',
        [{"role": "user", "content": "Diwali in 3 days, chai stock at 40%, ad budget untouched."}],
        smart=True, json_mode=True, temperature=0.2,
    )
    parsed = json.loads(out)
    print(f"3. gemini smart [{settings.llm_model_smart}]:", parsed)
    assert isinstance(parsed.get("confidence"), (int, float))

    # -- 4. nvidia break-glass lane (only when a key is configured) ---------
    if settings.nvidia_api_key:
        os.environ["LLM_PROVIDER"] = "nvidia"
        settings.llm_provider = "nvidia"
        out = await llm.complete(
            "You are a terse assistant. One sentence max.",
            [{"role": "user", "content":
              "In one short sentence, greet the Chai Bazaar agent fleet."}],
            temperature=0.3,
        )
        print(f"4. nvidia [{settings.nvidia_model}]:",
              out.encode("ascii", "replace").decode()[:120])

    print("\nALL LLM LANES OK")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(run(args.live)))
    except LLMError as exc:
        print("LLM ERROR:", str(exc).encode("ascii", "replace").decode())
        sys.exit(1)


if __name__ == "__main__":
    main()
