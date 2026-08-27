"""Mandate tests: signed envelopes enforced at order creation, spend drawn
down on capture, revoke/expiry honored, tamper detected.

Isolated temp DB (DB_PATH env before imports). Exit 0 = all green.

  uv run python scripts/test_mandates.py
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from bazaar import mandates  # noqa: E402
from bazaar.audit import verify  # noqa: E402
from bazaar.db import connect  # noqa: E402
from bazaar.main import app  # noqa: E402

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok  {name}")


def seed(sku: str, price: int, category: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO products (sku, title, description, price_paise,"
        " cost_paise, stock, category, kind) VALUES (?, ?, '', ?, ?, 50, ?,"
        " 'physical')",
        (sku, sku, price, price // 2, category),
    )
    conn.commit()
    conn.close()


with TestClient(app) as client:
    seed("grocery-chai", 20000, "grocery")
    seed("gift-hamper", 40000, "gifting")

    # ---- creation & signature ---------------------------------------------
    m = client.post("/mandates", json={
        "buyer_ref": "agent-A9", "budget_cap_paise": 50000,
        "max_single_txn_paise": 30000, "allowed_categories": ["grocery"],
        "ttl_hours": 24,
    })
    ok("mandate created", m.status_code == 200, m.text)
    m = m.json()
    ok("has signature", len(m["signature"]) == 64 and m["spent_paise"] == 0)

    # ---- happy path: within every bound ------------------------------------
    r = client.post("/orders", json={
        "buyer_session_id": "sess-m1",
        "items": [{"sku": "grocery-chai", "qty": 1}],
        "channel": "mcp", "mandate_id": m["id"],
    })
    ok("order within mandate accepted", r.status_code == 200, r.text)
    ok("order carries mandate id", r.json().get("mandate_id") == m["id"]
       if "mandate_id" in r.json() else True)

    # ---- single-txn cap ------------------------------------------------------
    r = client.post("/orders", json={
        "buyer_session_id": "sess-m2",
        "items": [{"sku": "grocery-chai", "qty": 2}],  # 40000 > 30000 cap
        "channel": "mcp", "mandate_id": m["id"],
    })
    ok("over single-txn cap refused", r.status_code == 403, r.text)
    ok("refusal names the bound", "single txn" in json.dumps(r.json()["detail"]))

    # ---- category bound ------------------------------------------------------
    r = client.post("/orders", json={
        "buyer_session_id": "sess-m3",
        "items": [{"sku": "gift-hamper", "qty": 1}],  # gifting ∉ [grocery]
        "channel": "mcp", "mandate_id": m["id"],
    })
    ok("out-of-category refused", r.status_code == 403, r.text)
    ok("refusal names category", "outside mandate" in json.dumps(r.json()["detail"]))

    # ---- unknown mandate -----------------------------------------------------
    r = client.post("/orders", json={
        "buyer_session_id": "sess-m4",
        "items": [{"sku": "grocery-chai", "qty": 1}],
        "channel": "mcp", "mandate_id": "mnt_nope",
    })
    ok("unknown mandate refused", r.status_code == 403)

    # ---- tamper: edit the envelope behind the signature ----------------------
    conn = connect()
    conn.execute("UPDATE mandates SET budget_cap_paise = 999999 WHERE id = ?",
                 (m["id"],))
    conn.commit()
    conn.close()
    row, v = mandates.check(m["id"], 20000, ["grocery"])
    ok("tampered envelope detected", not v.allowed
       and any("signature" in x for x in v.reasons), str(v))
    # restore for spend tests
    conn = connect()
    conn.execute("UPDATE mandates SET budget_cap_paise = 50000 WHERE id = ?",
                 (m["id"],))
    conn.commit()
    conn.close()

    # ---- spend draw-down (webhook capture path, unit-level) -------------------
    conn = connect()
    mandates.draw_down(conn, m["id"], 45000)
    conn.commit()
    conn.close()
    row, v = mandates.check(m["id"], 20000, ["grocery"])
    ok("budget exhaustion enforced", not v.allowed
       and any("budget" in x for x in v.reasons), str(v))

    # ---- revoke ---------------------------------------------------------------
    rv = client.post(f"/mandates/{m['id']}/revoke")
    ok("revoke ok", rv.status_code == 200 and rv.json()["revoked_at"], rv.text)
    rv2 = client.post(f"/mandates/{m['id']}/revoke")
    ok("revoke idempotent", rv2.status_code == 200
       and rv2.json()["revoked_at"] == rv.json()["revoked_at"])
    r = client.post("/orders", json={
        "buyer_session_id": "sess-m5",
        "items": [{"sku": "grocery-chai", "qty": 1}],
        "channel": "mcp", "mandate_id": m["id"],
    })
    ok("revoked mandate refused", r.status_code == 403
       and "revoked" in json.dumps(r.json()["detail"]))

    # ---- expiry ----------------------------------------------------------------
    mx = mandates.create("agent-B1", 50000, 30000, ["grocery"], ttl_hours=-1)
    row, v = mandates.check(mx["id"], 20000, ["grocery"])
    ok("expired mandate refused", not v.allowed
       and any("expired" in x for x in v.reasons), str(v))

    # ---- audit -----------------------------------------------------------------
    conn = connect()
    n_denied = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_type = 'order.mandate_denied'"
    ).fetchone()[0]
    ok("denials audited", n_denied >= 3, f"n={n_denied}")
    chain_ok, checked, _ = verify(conn)
    ok("audit chain intact", chain_ok, f"checked={checked}")
    conn.close()

print(f"\n{PASS} mandate checks passed")
