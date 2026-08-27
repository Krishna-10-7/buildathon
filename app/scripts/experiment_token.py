"""Print the experiment arm-switch token (run on the MERCHANT VM, where .env
lives). Pass it to the fleet runner via --token or EXP_TOKEN. Never commit.

    uv run python scripts/experiment_token.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.experiment_api import _token  # noqa: E402

if __name__ == "__main__":
    print(_token())
