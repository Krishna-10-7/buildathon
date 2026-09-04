"""Print one order, its payment, and its audit trail — for the demo video.

Why this exists instead of a one-liner: the video needs a single command
that cannot fumble on camera. `sqlite3` is not installed on the VM, the
orders table spells the money column `total_paise` (payments spells it
`amount_paise`), and quoting a Python -c through ssh is a minefield. This
is one script, one argument, no quoting.

Usage:
    python scripts/show_order.py ord_9447818176414f
    python scripts/show_order.py --latest          # most recent paid order
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.audit import verify as audit_verify  # noqa: E402
from bazaar.config import settings  # noqa: E402

GREEN = "\033[92m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"


def rupees(paise: int) -> str:
    return f"Rs{paise / 100:,.2f}"


def show(conn: sqlite3.Connection, order_id: str) -> int:
    conn.row_factory = sqlite3.Row
    o = conn.execute("SELECT * FROM orders WHERE id = ?",
                     (order_id,)).fetchone()
    if not o:
        print(f"no order {order_id}")
        return 1

    print(f"{BOLD}ORDER{OFF}")
    print(f"  internal id   {o['id']}")
    print(f"  razorpay id   {o['rp_order_id'] or '-'}")
    print(f"  status        {o['status']}")
    print(f"  total         {o['total_paise']}p  ({rupees(o['total_paise'])})")
    print(f"  channel       {o['channel']}")
    print(f"  session       {o['buyer_session_id']}")
    print(f"  mandate       {o['mandate_id'] or '-'}")
    print(f"  created       {o['created_at']}")

    pays = conn.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY attempt_no",
        (order_id,)).fetchall()
    print(f"\n{BOLD}PAYMENTS{OFF} ({len(pays)})")
    if not pays:
        print("  none")
    for p in pays:
        print(f"  {p['rp_payment_id'] or p['id']:<24} "
              f"{p['status']:<10} {p['amount_paise']:>7}p  "
              f"{p['method'] or '-'}")

    rows = conn.execute(
        """SELECT seq, ts_utc, actor, action_type FROM audit_log
           WHERE correlation_id = ? ORDER BY seq""",
        (o["correlation_id"],)).fetchall()
    print(f"\n{BOLD}AUDIT TRAIL{OFF} (correlation {o['correlation_id'][:16]}…)")
    for r in rows:
        print(f"  {r['seq']:>5}  {r['ts_utc'][:19]}  "
              f"{r['actor'][:26]:<26} {r['action_type']}")

    # audit.verify(), not a local copy. A second implementation of the
    # hashing rule is a second thing to keep in sync, and this script is
    # run ON CAMERA during the demo — if the copy drifted, the video would
    # show a chain verdict that disagrees with the live /audit/recent
    # endpoint, and the one claim we most need to be true would be undermined
    # by the artefact meant to prove it.
    ok, n, bad = audit_verify(conn)
    print(f"\n{BOLD}LEDGER{OFF}  chain_ok={ok}  records={n}  "
          f"first_bad_seq={bad}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    db = Path(settings.db_path)
    if not db.exists():
        print(f"database not found: {db}")
        return 1
    conn = sqlite3.connect(db, timeout=10)
    try:
        if not args:
            row = conn.execute(
                """SELECT id FROM orders WHERE status='paid'
                   ORDER BY created_at DESC LIMIT 1""").fetchone()
            if not row:
                print("no paid orders yet")
                return 1
            print(f"{DIM}(no order id given — showing most recent paid)"
                  f"{OFF}\n")
            return show(conn, row[0])
        rc = 0
        for oid in args:
            rc |= show(conn, oid)
        return rc
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
