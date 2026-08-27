"""Flip the storefront between A/B arms — run ON the merchant VM, next to the DB.

Every flip is audited (experiment.arm_switch). See PREREGISTRATION.md for
the frozen design; sessions alternate arms, this script is the only switch.

  uv run python scripts/measurement_toggle.py --status
  uv run python scripts/measurement_toggle.py --arm control
  uv run python scripts/measurement_toggle.py --arm treatment
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.experiment import ARMS, current_state, set_arm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--arm", choices=ARMS, help="flip the storefront to this arm")
    g.add_argument("--status", action="store_true", help="show current state only")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(current_state(), indent=2).encode(
            "ascii", "replace").decode())
        return 0

    result = set_arm(args.arm)
    print(f"arm -> {result['arm']}")
    print(f"  reverted to base : {', '.join(result['reverted']) or '-'}")
    print(f"  discounts active : "
          f"{json.dumps(result['discounts_active']) or '-'}")
    print(f"  bundles active   : {', '.join(result['bundles_active']) or '-'}")
    print("audited as experiment.arm_switch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
