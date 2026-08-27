"""Experiment arm-switch edge tests: token gate + delegation to set_arm.

  uv run python scripts/test_experiment_api.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from bazaar.audit import verify  # noqa: E402
from bazaar.db import connect  # noqa: E402
from bazaar.experiment_api import _token  # noqa: E402
from bazaar.main import app  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


with TestClient(app) as client:
    r = client.post("/experiment/arm", json={"arm": "control"})
    ok("missing token refused", r.status_code == 403, str(r.status_code))

    r = client.post("/experiment/arm", json={"arm": "control"},
                    headers={"X-Experiment-Token": "wrong"})
    ok("bad token refused", r.status_code == 403)

    r = client.post("/experiment/arm", json={"arm": "placebo"},
                    headers={"X-Experiment-Token": _token()})
    ok("unknown arm rejected by core validation", r.status_code == 422)

    r = client.post("/experiment/arm", json={"arm": "control"},
                    headers={"X-Experiment-Token": _token()})
    ok("valid flip accepted", r.status_code == 200
       and r.json()["arm"] == "control", str(r.json()))

    r = client.get("/experiment/state", headers={"X-Experiment-Token": _token()})
    ok("state endpoint reads back", r.status_code == 200
       and "looks_like" in r.json(), str(r.status_code))

    n = connect().execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_type = 'experiment.arm_switch'"
    ).fetchone()[0]
    ok("edge flips are audited", n >= 1, f"n={n}")

    good, count, bad = verify(connect())
    ok("chain intact", good and bad is None, f"n={count}")

print(f"\nEXPERIMENT EDGE: {PASS} CHECKS PASSED")
