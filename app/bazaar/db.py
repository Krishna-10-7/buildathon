"""SQLite access layer — WAL mode, integer-paise money, hash-chained audit log."""

import sqlite3
from pathlib import Path

from bazaar.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  sku          TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  price_paise  INTEGER NOT NULL CHECK (price_paise > 0),
  cost_paise   INTEGER NOT NULL DEFAULT 0,
  stock        INTEGER NOT NULL CHECK (stock >= 0),
  category     TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'physical'
               CHECK (kind IN ('physical', 'digital', 'subscription')),
  tags_json    TEXT NOT NULL DEFAULT '[]',
  pairs_with_json TEXT NOT NULL DEFAULT '[]',
  active       INTEGER NOT NULL DEFAULT 1
);

-- Order lifecycle (research/04 section 5):
-- created -> attempting -> paid | failed(->attempting, max retries) | expired | cancelled
CREATE TABLE IF NOT EXISTS orders (
  id              TEXT PRIMARY KEY,
  rp_order_id     TEXT UNIQUE,
  buyer_session_id TEXT NOT NULL,
  channel         TEXT NOT NULL DEFAULT 'mcp',   -- mcp | acp | chat | x402
  items_json      TEXT NOT NULL,
  total_paise     INTEGER NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                    ('created','attempting','paid','failed','expired','cancelled')),
  attempt_no      INTEGER NOT NULL DEFAULT 0,
  mandate_id      TEXT REFERENCES mandates(id),
  bundle_id       TEXT,                -- set when the basket bought a bundle
  -- How much of the envelope THIS order is holding, so a failed or expired
  -- order hands back exactly that much. Recorded per order rather than
  -- recomputed at release time, because the catalog price or the mandate
  -- may have changed in between — and 0 distinguishes a legacy row
  -- created before reserve() existed from a real reservation.
  mandate_reserved_paise INTEGER NOT NULL DEFAULT 0,
  correlation_id  TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
  id            TEXT PRIMARY KEY,
  order_id      TEXT NOT NULL REFERENCES orders(id),
  attempt_no    INTEGER NOT NULL,
  rp_payment_id TEXT UNIQUE,
  method        TEXT,
  amount_paise  INTEGER NOT NULL,
  status        TEXT NOT NULL,
  error_code    TEXT,
  error_desc    TEXT,
  idempotency_key TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  UNIQUE (order_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS audit_log (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc         TEXT NOT NULL,
  actor          TEXT NOT NULL,
  action_type    TEXT NOT NULL,
  payload        TEXT NOT NULL,
  prev_hash      TEXT NOT NULL,
  self_hash      TEXT NOT NULL UNIQUE,
  correlation_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
  id             TEXT PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  actor          TEXT NOT NULL,
  action_type    TEXT NOT NULL,
  context_json   TEXT NOT NULL,
  decision_json  TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending_review','auto_executed','approved','rejected','expired')),
  created_at     TEXT NOT NULL,
  resolved_at    TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
  proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),
  decided_by  TEXT,
  decision    TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  decided_at  TEXT NOT NULL
);

-- AP2-flavored consent envelope (UPI Reserve Pay semantics, research/01)
CREATE TABLE IF NOT EXISTS mandates (
  id                   TEXT PRIMARY KEY,
  buyer_ref            TEXT NOT NULL,
  budget_cap_paise     INTEGER NOT NULL,
  spent_paise          INTEGER NOT NULL DEFAULT 0,
  max_single_txn_paise INTEGER NOT NULL,
  allowed_categories_json TEXT NOT NULL DEFAULT '[]',
  expires_at           TEXT NOT NULL,
  revoked_at           TEXT,
  signature            TEXT NOT NULL,
  created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_evaluations (
  id             TEXT PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  action_type    TEXT NOT NULL,
  requested_json TEXT NOT NULL,
  decision_json  TEXT NOT NULL,
  ts_utc         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id              TEXT PRIMARY KEY,   -- X-Razorpay-Event-Id (dedupe)
  event           TEXT NOT NULL,
  signature_valid INTEGER NOT NULL,
  payload_json    TEXT NOT NULL,
  processed_at    TEXT NOT NULL
);

-- Bundles created by approved agent proposals (policy POL-BNDL-001)
CREATE TABLE IF NOT EXISTS bundles (
  id          TEXT PRIMARY KEY,
  skus_json   TEXT NOT NULL,
  price_paise INTEGER NOT NULL CHECK (price_paise > 0),
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

-- v2 columns on products: discounting keeps base price intact so expiry
-- reverts cleanly. Added via migrate() because ALTER is not covered by
-- CREATE TABLE IF NOT EXISTS on pre-v2 databases.
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a connection.

    `path` exists so a caller can address a specific store WITHOUT mutating
    `settings.db_path`. That setting is process-global, and swapping it for
    the duration of a call makes every concurrent caller in the process
    (SSE ticker, /api/state, /api/replay) silently read the wrong database
    for that window. Passing the path is thread-safe; swapping the global
    is not, and no lock around the swap can make it so.
    """
    conn = sqlite3.connect(Path(path or settings.db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column-level migrations beyond CREATE TABLE IF NOT EXISTS."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    if "base_price_paise" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN base_price_paise INTEGER")
    if "discount_until" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN discount_until TEXT")
    ocols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    if "bundle_id" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN bundle_id TEXT")
    # How much of the envelope THIS order is holding. Recorded per order
    # rather than recomputed at release time so the release is exact even
    # if the catalog price or the mandate has since changed, and so a
    # legacy row (0) is distinguishable from a real reservation.
    if "mandate_reserved_paise" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN mandate_reserved_paise"
                     " INTEGER NOT NULL DEFAULT 0")


def db_ready() -> tuple[bool, str]:
    try:
        conn = connect()
        n = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        return True, f"sqlite:{mode}, {n} products"
    except Exception as exc:  # pragma: no cover - health endpoint only
        return False, f"db error: {exc}"
