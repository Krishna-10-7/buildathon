"""SHIM - the suite lives in tests/test_webhooks.py.

Converted to pytest. This file is kept so every command in the README,
the devlog and the runbook still works; a dead command in a README is a
claim about the project that is no longer true.

  uv run python scripts/test_webhooks.py   ->   pytest tests/test_webhooks.py
  uv run pytest -q                       ->   everything
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

raise SystemExit(pytest.main([
    str(ROOT / "tests" / "test_webhooks.py"), "-q", "-p", "no:cacheprovider"]))
