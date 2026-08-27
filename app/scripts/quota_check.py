"""One tiny LLM call per configured provider lane — exit 0 iff it answers.

Usage:  uv run python scripts/quota_check.py [--live-smart]
Exit 0 = provider alive; exit 1 = quota exhausted / down / misconfigured.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.config import settings  # noqa: E402
from bazaar.llm import LLMError, complete  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-smart", action="store_true",
                    help="also probe the smart lane (costs more quota)")
    args = ap.parse_args()

    if settings.llm_provider == "mock":
        print("provider=mock — nothing to check")
        return 0

    lanes = [("fast", False)]
    if args.live_smart:
        lanes.append(("smart", True))

    ok = True
    for name, smart in lanes:
        try:
            out = await complete(
                "Reply with exactly one word: pong.",
                [{"role": "user", "content": "ping"}],
                smart=smart,
                # gemini 3.x spends thinking tokens from this budget — too
                # small means finishReason=MAX_TOKENS with EMPTY content
                max_tokens=512,
            )
            print(f"{settings.llm_provider} {name} lane OK -> {out.strip()[:40]!r}")
        except LLMError as exc:
            ok = False
            print(f"{settings.llm_provider} {name} lane FAILED: {str(exc)[:220]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
