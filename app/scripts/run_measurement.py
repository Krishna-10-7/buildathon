"""Preregistered measurement runner — runs on the FLEET box (VM2).

Alternates storefront arms via the token-gated experiment edge on the
merchant, then drives one persona session per arm flip, appending every raw
record (with its arm tag) to the shared sessions JSONL. T,C,T,C,... per
PREREGISTRATION.md so drift hits both arms equally.

Setup (one-time, on the MERCHANT VM — where .env lives):
    uv run python scripts/experiment_token.py      # prints the token

Run (on VM2):
    EXP_TOKEN=... uv run python scripts/run_measurement.py \
        --api-base https://r2-d2.xyz --sessions 90

Cooldown discipline: sessions are spaced with jitter; risk_challenged
sessions are recorded (and excluded symmetrically at analysis time), never
retried aggressively.
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from exp.personas import PERSONAS, run_session  # noqa: E402

PERSONA_CYCLE = list(PERSONAS)  # dict keys, insertion-ordered
ARMS = ("treatment", "control")  # preregistration: T,C,T,C,...


def flip_arm(base: str, token: str, arm: str) -> dict:
    r = httpx.post(f"{base}/experiment/arm", json={"arm": arm},
                   headers={"X-Experiment-Token": token}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"arm flip to {arm} refused: HTTP {r.status_code} "
                         f"{r.text[:120]} - refusing to run a session in an "
                         f"unknown store state")
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api-base", default="https://r2-d2.xyz")
    ap.add_argument("--sessions", type=int, default=90)
    ap.add_argument("--out", default="artifacts/sessions.jsonl")
    ap.add_argument("--method", default="netbanking")
    ap.add_argument("--bank", default="Canara Bank")
    ap.add_argument("--token", default=os.environ.get("EXP_TOKEN", ""))
    ap.add_argument("--pause-s", type=float, default=20.0,
                    help="base pause between sessions (jittered)")
    ap.add_argument("--session-timeout", type=float, default=600.0,
                    help="hard per-session watchdog (s); a hung browser call "
                         "records infra_error instead of stalling the fleet")
    args = ap.parse_args()
    if not args.token:
        raise SystemExit("missing experiment token (--token or EXP_TOKEN)")
    base = args.api_base.rstrip("/")

    out = Path(args.out)
    done = 0
    consecutive_llm_failures = 0
    for i in range(args.sessions):
        arm = ARMS[i % 2]
        persona = PERSONA_CYCLE[i % len(PERSONA_CYCLE)]
        state = flip_arm(base, args.token, arm)
        print(f"[{i + 1}/{args.sessions}] arm={arm} "
              f"(discounts={list(state['discounts_active'])}, "
              f"bundles={len(state['bundles_active'])}) persona={persona}",
              flush=True)
        t0 = time.monotonic()

        try:
            rec = asyncio.run(asyncio.wait_for(
                run_session(base, persona, tag=f"m{i:03d}-{arm}",
                            method=args.method, bank=args.bank),
                timeout=args.session_timeout))
        except (TimeoutError, asyncio.TimeoutError):
            # The per-session crash-guard traps exceptions; this watchdog
            # traps HANGS (a driver pipe can block a session forever —
            # observed live as an 8.7 h silent gap). Record and move on.
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "session_id": f"hang-{i:03d}", "persona": persona,
                   "base": base, "outcome": "infra_error", "ok": False,
                   "notes": [f"watchdog: session exceeded "
                             f"{args.session_timeout:.0f}s"]}
        except Exception as exc:
            # One broken session must never kill a 90-session run.
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "session_id": f"crash-{i:03d}", "persona": persona,
                   "base": base, "outcome": "infra_error", "ok": False,
                   "notes": [f"{type(exc).__name__}: {str(exc)[:180]}"]}
        rec["arm"] = arm
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        done += 1
        print(f"    -> {rec['outcome']} "
              f"(amount={rec.get('amount_paise')}p ok={rec['ok']} "
              f"in {time.monotonic() - t0:.0f}s)", flush=True)

        # A dead LLM quota would silently record a garbage experiment.
        # Abort loudly instead — sessions already written stay (analysis
        # reports the outcome mix), the rest of the run waits for quota.
        if rec["outcome"] in ("llm_error", "invalid_plan"):
            consecutive_llm_failures += 1
        else:
            consecutive_llm_failures = 0
        if consecutive_llm_failures >= 3:
            raise SystemExit(
                "ABORT: 3 consecutive LLM failures — free-tier quota likely "
                "exhausted. Resume after the daily reset; written sessions "
                "are kept and reported.")

        if i + 1 < args.sessions:  # gentle spacing, jittered
            time.sleep(args.pause_s + random.uniform(0, 0.5 * args.pause_s))

    print(f"\n{done} sessions recorded to {out}; "
          f"analyze with: uv run python exp/analysis.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
