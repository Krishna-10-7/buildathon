"""Run LLM buyer persona shopping trips; append records to a JSONL log.

  uv run python scripts/run_persona.py --persona ritika
  uv run python scripts/run_persona.py --persona all --api-base https://r2-d2.xyz

Each line of --out is one session record: catalog seen, LLM analysis, the
basket it chose, and the payment result. This file is the only writer.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exp.personas import PERSONAS, run_session  # noqa: E402

APP = Path(__file__).resolve().parent.parent


def _ascii(s) -> str:
    return str(s).encode("ascii", "replace").decode()


def _show(rec: dict) -> None:
    icon = "OK " if rec["ok"] else "FAIL"
    print(f"\n[{icon}] {rec['session_id']} outcome={rec['outcome']}")
    if rec["analysis"]:
        print(f"  thinks: {_ascii(rec['analysis'])}")
    for ln in rec["basket"]:
        print(f"  basket: {ln['sku']} x{ln['qty']}")
    if rec["order_id"]:
        print(f"  order {rec['order_id']} amount={rec['amount_paise']}p "
              f"status={rec['payment_status']}")
    for n in rec["notes"]:
        print(f"  note: {_ascii(n)}")


async def main_async(args: argparse.Namespace) -> int:
    ids = list(PERSONAS) if args.persona == "all" else [args.persona]
    out_path = APP / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for pid in ids:
            rec = await run_session(
                args.api_base, pid,
                tag=args.tag, method=args.method, bank=args.bank,
                headed=args.headed, attempts=args.attempts,
            )
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            _show(rec)
            n_ok += rec["ok"]

    print(f"\n{n_ok}/{len(ids)} sessions valid; log {out_path}")
    return 0 if n_ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="https://r2-d2.xyz")
    ap.add_argument("--persona", default="all",
                    choices=["all", *PERSONAS.keys()])
    ap.add_argument("--method", choices=["card", "netbanking"],
                    default="netbanking")
    ap.add_argument("--bank", default="Canara Bank")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--tag", default="day3")
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--out", default="artifacts/sessions.jsonl")
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
