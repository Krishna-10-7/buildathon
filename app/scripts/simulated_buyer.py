"""CLI wrapper over exp.checkout.buy_once — the original Day-1 probe.

The real logic lives in exp/checkout.py so personas and the measurement
fleet reuse the exact same driver. This file only parses flags and prints.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exp.checkout import buy_once  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="https://r2-d2.xyz")
    ap.add_argument("--sku", action="append", default=None,
                    help="repeatable: --sku a --sku b (multi-item basket)")
    ap.add_argument("--qty", type=int, default=1, help="qty applied to each sku")
    ap.add_argument("--outcome", choices=["success", "failure"], default="success")
    ap.add_argument("--method", choices=["card", "netbanking"], default="netbanking")
    ap.add_argument("--bank", default="Canara Bank")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--tag", default="t1")
    args = ap.parse_args()

    skus = args.sku or ["masala-chai-250g"]
    items = [{"sku": s, "qty": args.qty} for s in skus]

    res = asyncio.run(buy_once(
        args.api_base, items,
        tag=args.tag, outcome=args.outcome, method=args.method,
        bank=args.bank, headed=args.headed,
    ))

    if res["ok"]:
        print(f"\nORDER {res['status']} — run complete ✅ ({res['order_id']})")
    else:
        print(f"\nRUN FAILED at {res['stage']}: {res['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
