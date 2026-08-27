"""One governed growth-agent cycle, from the CLI.

Modes:
  uv run python scripts/run_growth_agent.py              # in-process, local DB
  uv run python scripts/run_growth_agent.py --api-base https://r2-d2.xyz
                                                          # trigger the VM's agent

Set LLM_PROVIDER=gemini for a real strategist (mock lane yields no valid plan).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _show(result: dict) -> None:
    if "error" in result:
        print("CYCLE ERROR:", result["error"], "-", result.get("detail", "")[:200])
        sys.exit(1)
    print("analysis:", result["analysis"].encode("ascii", "replace").decode())
    print(f"correlation: {result['correlation_id']}  "
          f"proposals: {len(result['proposals'])}")
    for p in result["proposals"]:
        if "skipped" in p:
            print(f"  SKIP {p['skipped']}: {p['reason']}")
            continue
        print(f"  [{p['status']}] {json.dumps(p['final_params'])}"
              f"  rules={p['rules']}")
        print(f"        why: {p['rationale'].encode('ascii', 'replace').decode()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="")
    args = ap.parse_args()

    if args.api_base:
        import httpx
        r = httpx.post(f"{args.api_base.rstrip('/')}/agent/growth/cycle",
                       timeout=90)
        _show(r.json())
        return

    from bazaar.agents import growth
    result = asyncio.run(growth.run_cycle())
    _show(result)


if __name__ == "__main__":
    main()
