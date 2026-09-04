"""Experiment arm-switch edge tests: token gate + delegation to set_arm.

Converted from scripts/test_experiment_api.py.

The arm flip is the one endpoint that can change what the measurement
sees, so it is token-gated and audited. These tests are about the GATE,
not the flip: a wrong token must be refused before anything is written.
"""


from bazaar.audit import verify
from bazaar.experiment_api import _token
from tests.conftest import ok


def flip(client, arm: str = "control", token: str | None = "sentinel"):
    headers = {}
    if token is not None:
        headers["X-Experiment-Token"] = (
            _token() if token == "sentinel" else token)
    return client.post("/experiment/arm", json={"arm": arm}, headers=headers)


def test_missing_token_refused(client):
    ok("missing token refused", flip(client, token=None).status_code == 403)


def test_bad_token_refused(client):
    r = flip(client, token="wrong")
    ok("bad token refused", r.status_code == 403, str(r.status_code))


def test_unknown_arm_rejected_by_core_validation(client):
    r = flip(client, arm="placebo")
    ok("unknown arm rejected by core validation", r.status_code == 422,
       str(r.status_code))


def test_valid_flip_accepted(client):
    r = flip(client)
    ok("valid flip accepted",
       r.status_code == 200 and r.json()["arm"] == "control", str(r.json()))


def test_state_endpoint_reads_back(client):
    r = client.get("/experiment/state",
                   headers={"X-Experiment-Token": _token()})
    ok("state endpoint reads back",
       r.status_code == 200 and "looks_like" in r.json(), str(r.status_code))


def test_edge_flips_are_audited(client):
    flip(client)
    n = count_arm_switches()
    ok("edge flips are audited", n >= 1, f"n={n}")


def test_refused_flip_is_not_audited(client):
    """A refused flip must not be recorded as if it happened.

    Otherwise the ledger would show arm switches that never took effect,
    which is precisely the kind of evidence that cannot be checked.
    """
    flip(client, token="wrong")
    ok("refused flip is not audited", count_arm_switches() == 0)


def test_chain_intact(client):
    flip(client)
    good, count, bad = verify_conn()
    ok("chain intact", good and bad is None, f"n={count}")


def count_arm_switches() -> int:
    from bazaar.db import connect
    c = connect()
    try:
        return c.execute(
            "SELECT COUNT(*) FROM audit_log"
            " WHERE action_type = 'experiment.arm_switch'").fetchone()[0]
    finally:
        c.close()


def verify_conn():
    from bazaar.db import connect
    c = connect()
    try:
        return verify(c)
    finally:
        c.close()
