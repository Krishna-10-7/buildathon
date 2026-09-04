"""Buyer consent envelopes: bounded spending authority for agentic orders.

An AP2-flavored mandate is a signed envelope saying WHAT an agent may spend,
on WHICH categories, UNTIL when. The merchant enforces it at order creation:
a presented mandate must match its signature and satisfy every bound, or the
order is refused before Razorpay is ever involved. Spend draws down only when
payment captures — failed attempts never consume budget.

Signature = HMAC-SHA256 over canonical fields. The signing key derives from
MANDATE_SECRET (or, in dev, from the gateway secret) so envelopes are
tamper-evident without new required env vars.

MANDATE_SIGNING=ed25519 switches to asymmetric sealing. See the note on
`settings.mandate_signing` for why that matters, and `_verify` for the
downgrade rule — an envelope that claims to be HMAC-sealed is REFUSED when
the merchant is configured for Ed25519, because accepting it would hand the
merchant back the ability to mint consent it is supposed to only check.

Envelopes are verified by the algorithm written in the stored signature, so
a store that already holds HMAC-sealed mandates keeps working after the
switch; new envelopes are sealed with the configured algorithm.
"""

import base64
import hashlib
import hmac
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bazaar import audit, logging_setup
from bazaar.config import settings
from bazaar.db import connect

log = logging_setup.log_for("mandates")

# HMAC signatures are stored bare (64 hex chars) — the historical format,
# and the format two suites assert on. Ed25519 signatures need to be
# distinguishable from them, so they carry a prefix.
ED25519_PREFIX = "ed25519:"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _scope(conn: sqlite3.Connection | None, db_path: str | None):
    """Yield (connection, we_own_it).

    Every public function below takes an optional `conn` or `db_path` so a
    caller can target a store explicitly. When a connection is passed in,
    the caller owns its transaction — we neither commit nor close it. When
    we open one, we close it, and the caller commits only via the explicit
    `if owns: conn.commit()` at each call site.

    The reason this exists at all: `envelope.py` used to redirect the
    process-global `settings.db_path` for the duration of a run, and any
    other request in the same process would then read the demo store.
    """
    if conn is not None:
        yield conn, False
        return
    own = connect(db_path) if db_path is not None else connect()
    try:
        yield own, True
    finally:
        own.close()


def _signing_key() -> bytes:
    base = settings.mandate_secret or f"{settings.rzp_key_secret}:mandates-v1"
    return hashlib.sha256(base.encode()).digest()


def _canonical(buyer_ref: str, budget_cap_paise: int, max_single_txn_paise: int,
               allowed_categories: list[str], expires_at: str) -> str:
    return json.dumps(
        {"buyer_ref": buyer_ref, "budget_cap_paise": budget_cap_paise,
         "max_single_txn_paise": max_single_txn_paise,
         "allowed_categories": sorted(allowed_categories),
         "expires_at": expires_at},
        sort_keys=True, separators=(",", ":"),
    )


def signing_mode() -> str:
    """The configured algorithm, validated. Unknown values fail loudly.

    A typo in MANDATE_SIGNING must not silently fall back to hmac: that
    would look like the stronger setting was in force while every envelope
    was being sealed with the shared key.
    """
    mode = (settings.mandate_signing or "hmac").strip().lower()
    if mode not in ("hmac", "ed25519"):
        raise RuntimeError(
            f"MANDATE_SIGNING must be 'hmac' or 'ed25519', got {mode!r}")
    return mode


# --- Ed25519 ---------------------------------------------------------------
# `cryptography` is imported lazily. It is a compiled dependency and hmac
# mode never touches it, so a deployment that does not need asymmetric
# sealing pays nothing at import time; the ImportError surfaces only for
# someone who actually asked for ed25519.


def _ed25519_private():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    if not settings.mandate_ed25519_seed:
        raise RuntimeError(
            "MANDATE_SIGNING=ed25519 needs MANDATE_ED25519_SEED "
            "(base64 of a 32-byte seed). Make one with "
            "`python scripts/mandate_keygen.py`.")
    try:
        seed = base64.b64decode(settings.mandate_ed25519_seed, validate=True)
    except Exception as exc:  # noqa: BLE001 - any decode error is config error
        raise RuntimeError(
            "MANDATE_ED25519_SEED is not valid base64") from exc
    if len(seed) != 32:
        raise RuntimeError(
            f"MANDATE_ED25519_SEED must decode to 32 bytes, got {len(seed)}")
    return Ed25519PrivateKey.from_private_bytes(seed)


def keygen() -> dict:
    """Mint a fresh Ed25519 key. Returns the seed and its public key.

    The seed is stored as MANDATE_ED25519_SEED; the public key is what
    gets published. Only the seed is secret — the public key is derived,
    so there is no second value to keep in sync (or forget to rotate).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(encoding=serialization.Encoding.Raw,
                              format=serialization.PrivateFormat.Raw,
                              encryption_algorithm=serialization.NoEncryption())
    pub = priv.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                         format=serialization.PublicFormat.Raw)
    return {"seed_b64": base64.b64encode(seed).decode(),
            "public_key_b64": base64.b64encode(pub).decode()}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def public_key_document() -> dict:
    """The body of GET /.well-known/bazaar-mandate-key.

    An external buyer's agent needs two things to check an envelope
    without asking us: the verification key, and the exact byte layout of
    the thing being signed. Both are here. A published key with no
    canonical-form spec beside it is not verifiable — it just looks like it
    ought to be.
    """
    mode = signing_mode()
    doc = {
        "merchant": settings.public_base_url,
        "mandate_signing": mode,
        "algorithm": "Ed25519" if mode == "ed25519" else "HMAC-SHA256",
        "envelope": "AP2-flavored bounded spending authority",
        "canonical_form": {
            "encoding": "json.dumps(sort_keys=True, separators=(',', ':'))",
            "fields": ["buyer_ref", "budget_cap_paise",
                       "max_single_txn_paise",
                       "allowed_categories (sorted, deduped)",
                       "expires_at"],
            "signature_field": "mandates.signature",
            "money_unit": "integer paise",
        },
    }
    if mode == "ed25519":
        pub_b64 = ed25519_public_key_b64()
        doc["public_key_b64"] = pub_b64
        doc["jwk"] = {"kty": "OKP", "crv": "Ed25519",
                      "x": _b64url(base64.b64decode(pub_b64)),
                      "use": "sig", "alg": "EdDSA"}
    else:
        # Deliberately NOT publishing anything derived from the shared
        # secret. In hmac mode the merchant can forge consent; saying so
        # plainly is more honest than serving a key that implies otherwise.
        doc["public_key_b64"] = None
        doc["note"] = (
            "HMAC mode is symmetric: the merchant holds the only key, so a "
            "sealed envelope proves tamper-evidence, NOT non-repudiation. "
            "Set MANDATE_SIGNING=ed25519 for buyer-held keys.")
    return doc


def ed25519_public_key_b64() -> str | None:
    """The verification key, base64 raw — what the well-known endpoint
    publishes. None when not running in ed25519 mode."""
    if signing_mode() != "ed25519":
        return None
    priv = _ed25519_private()
    from cryptography.hazmat.primitives import serialization

    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def _ed25519_sign(body: str) -> str:
    return ED25519_PREFIX + base64.b64encode(
        _ed25519_private().sign(body.encode())).decode()


def _sign(body: str) -> str:
    if signing_mode() == "ed25519":
        return _ed25519_sign(body)
    return hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()


def _verify(body: str, stored: str) -> bool:
    """Check the stored signature against the canonical body.

    The algorithm comes from the stored value, not from the config, so old
    envelopes survive a mode switch. But the mode still gets a veto: in
    ed25519 mode an HMAC-sealed envelope is REFUSED even if its HMAC is
    arithmetically correct. Otherwise anyone holding the shared secret —
    which in this deployment is the merchant — could mint a "consent" the
    merchant is supposed to be merely checking. That is the downgrade this
    guards against, and it is the entire reason to run ed25519.
    """
    mode = signing_mode()
    if stored.startswith(ED25519_PREFIX):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        try:
            pub_b64 = ed25519_public_key_b64()
            pub = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(pub_b64, validate=True))
            pub.verify(base64.b64decode(stored[len(ED25519_PREFIX):],
                                        validate=True),
                       body.encode())
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    # Bare signature = hmac. Valid only while hmac mode is configured.
    if mode != "hmac":
        return False
    return hmac.compare_digest(
        hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest(),
        stored)


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str]


def create(buyer_ref: str, budget_cap_paise: int, max_single_txn_paise: int,
           allowed_categories: list[str] | None = None,
           ttl_hours: float = 24.0,
           conn: sqlite3.Connection | None = None,
           db_path: str | None = None) -> dict:
    if budget_cap_paise <= 0 or max_single_txn_paise <= 0:
        raise ValueError("caps must be positive paise amounts")
    if max_single_txn_paise > budget_cap_paise:
        raise ValueError("single-txn cap cannot exceed budget cap")
    expires_at = (datetime.now(timezone.utc) +
                  timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    cats = sorted(set(allowed_categories or []))
    body = _canonical(buyer_ref, budget_cap_paise, max_single_txn_paise,
                      cats, expires_at)
    # The nonce matters: _now() only has SECOND resolution, so two
    # envelopes opened for the same buyer with the same caps inside one
    # second produced the identical id and the second INSERT died on the
    # primary key. Real traffic hits this (a retry loop, or two tabs), and
    # it is not a hypothetical — it surfaced the moment a test opened
    # envelopes in a tight loop. uuid4 keeps the id shape unchanged.
    mid = "mnt_" + hmac.new(_signing_key(),
                            f"{buyer_ref}:{body}:{_now()}:{uuid.uuid4().hex}".encode(),
                            hashlib.sha256).hexdigest()[:12]
    with _scope(conn, db_path) as (c, owns):
        log.info("mandate opened: id=%s buyer=%s budget=%dp txn_cap=%dp "
                 "categories=%s expires=%s", mid, buyer_ref, budget_cap_paise,
                 max_single_txn_paise, cats or "ALL", expires_at)
        c.execute(
            """INSERT INTO mandates
               (id, buyer_ref, budget_cap_paise, spent_paise,
                max_single_txn_paise, allowed_categories_json, expires_at,
                revoked_at, signature, created_at)
               VALUES (?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)""",
            (mid, buyer_ref, budget_cap_paise, max_single_txn_paise,
             json.dumps(cats), expires_at, _sign(body), _now()),
        )
        audit.append(c, actor=f"buyer:{buyer_ref}", action_type="mandate.created",
                     payload={"mandate_id": mid, "budget_cap_paise": budget_cap_paise,
                              "max_single_txn_paise": max_single_txn_paise,
                              "allowed_categories": cats, "expires_at": expires_at},
                     correlation_id=mid)
        if owns:
            c.commit()
        return get(mid, conn=c)


def get(mandate_id: str,
        conn: sqlite3.Connection | None = None,
        db_path: str | None = None) -> dict | None:
    with _scope(conn, db_path) as (c, _owns):
        row = c.execute("SELECT * FROM mandates WHERE id = ?",
                        (mandate_id,)).fetchone()
        return dict(row) if row else None


def revoke(mandate_id: str,
           conn: sqlite3.Connection | None = None,
           db_path: str | None = None) -> dict:
    row = get(mandate_id, conn=conn, db_path=db_path)
    if not row:
        raise LookupError(f"unknown mandate {mandate_id}")
    if row["revoked_at"]:
        return row  # idempotent
    with _scope(conn, db_path) as (c, owns):
        log.info("mandate revoked: id=%s buyer=%s", mandate_id,
                 row["buyer_ref"])
        c.execute("UPDATE mandates SET revoked_at = ? WHERE id = ?",
                  (_now(), mandate_id))
        audit.append(c, actor=f"buyer:{row['buyer_ref']}",
                     action_type="mandate.revoked",
                     payload={"mandate_id": mandate_id},
                     correlation_id=mandate_id)
        if owns:
            c.commit()
        return get(mandate_id, conn=c)


def _bound_reasons(row: dict, total_paise: int, categories: list[str],
                   now: str) -> list[str]:
    """Every bound, evaluated against one already-read row.

    Shared by `check` and `reserve` so the two can never drift: a refusal
    reason that differed between "can I?" and "do it" would let an order
    pass the preview and fail the commit, or vice versa. The strings here
    are asserted by tests — do not paraphrase them.
    """
    reasons: list[str] = []

    body = _canonical(row["buyer_ref"], row["budget_cap_paise"],
                      row["max_single_txn_paise"],
                      json.loads(row["allowed_categories_json"]),
                      row["expires_at"])
    if not _verify(body, row["signature"]):
        reasons.append("mandate signature mismatch — envelope altered")
    if row["revoked_at"]:
        reasons.append("mandate revoked")
    if row["expires_at"] <= now:
        reasons.append("mandate expired")
    if total_paise > row["max_single_txn_paise"]:
        reasons.append(f"single txn {total_paise}p exceeds cap "
                       f"{row['max_single_txn_paise']}p")
    if row["spent_paise"] + total_paise > row["budget_cap_paise"]:
        reasons.append(f"budget exhausted: spent {row['spent_paise']}p + "
                       f"{total_paise}p > cap {row['budget_cap_paise']}p")
    allowed = json.loads(row["allowed_categories_json"])
    if allowed:
        bad = sorted({c for c in categories if c and c not in allowed})
        if bad:
            reasons.append(f"categories outside mandate: {','.join(bad)}")
    return reasons


def check(mandate_id: str, total_paise: int, categories: list[str],
          conn: sqlite3.Connection | None = None,
          db_path: str | None = None) -> tuple[dict | None, Verdict]:
    """Verify signature + every bound against the stored envelope.
    Pure: no state mutation.

    Read-only preview only. To spend, use `reserve` — this function cannot
    be made race-free, because the caller's decision is based on a value
    another writer may already have changed.
    """
    row = get(mandate_id, conn=conn, db_path=db_path)
    if not row:
        return None, Verdict(False, ["unknown mandate"])
    reasons = _bound_reasons(row, total_paise, categories, _now())
    return row, Verdict(not reasons, reasons)


def reserve(conn: sqlite3.Connection, mandate_id: str, amount_paise: int,
            categories: list[str]) -> tuple[dict | None, Verdict]:
    """Check AND draw down in one atomic step.

    `check` then `draw_down` is a classic check-then-act race: two orders
    against one envelope can both read spent=0, both pass, and both
    increment — exceeding budget_cap_paise, which is the one bound that
    must never break. The bounds are therefore re-stated in the WHERE
    clause of the UPDATE, so the database — not our earlier read — decides
    whether the spend fits. rowcount == 1 means this caller won.

    The caller owns the connection. If no transaction is open we take
    BEGIN IMMEDIATE so the whole check-and-spend holds the write lock; if
    one is already open (orders.py has already reserved stock) we join it
    and rely on the conditional UPDATE, which is correct either way.
    """
    now = _now()

    began = False
    if not conn.in_transaction:
        prev = conn.isolation_level
        conn.isolation_level = None      # manual transaction control
        conn.execute("BEGIN IMMEDIATE")
        began = True
    try:
        row = conn.execute("SELECT * FROM mandates WHERE id = ?",
                           (mandate_id,)).fetchone()
        if row is None:
            if began:
                conn.execute("ROLLBACK")
            return None, Verdict(False, ["unknown mandate"])
        row = dict(row)

        reasons = _bound_reasons(row, amount_paise, categories, now)
        if reasons:
            if began:
                conn.execute("ROLLBACK")
            return row, Verdict(False, reasons)

        cur = conn.execute(
            """UPDATE mandates SET spent_paise = spent_paise + ?
               WHERE id = ?
                 AND revoked_at IS NULL
                 AND expires_at > ?
                 AND ? <= max_single_txn_paise
                 AND spent_paise + ? <= budget_cap_paise""",
            (amount_paise, mandate_id, now, amount_paise, amount_paise),
        )
        if cur.rowcount != 1:
            # Someone else spent the room between our SELECT and here.
            # Re-read so the refusal quotes the number that actually beat
            # us, rather than the stale row we started from.
            fresh = conn.execute("SELECT * FROM mandates WHERE id = ?",
                                 (mandate_id,)).fetchone()
            fresh = dict(fresh) if fresh else row
            reasons = _bound_reasons(fresh, amount_paise, categories, now) or [
                f"budget exhausted: spent {fresh['spent_paise']}p + "
                f"{amount_paise}p > cap {fresh['budget_cap_paise']}p"]
            if began:
                conn.execute("ROLLBACK")
            return fresh, Verdict(False, reasons)

        log.info("mandate reserved: id=%s amount=%dp spent_now=%dp",
                 mandate_id, amount_paise, row["spent_paise"] + amount_paise)
        audit.append(conn, actor="mandates", action_type="mandate.reserved",
                     payload={"mandate_id": mandate_id,
                              "amount_paise": amount_paise,
                              "categories": categories,
                              "spent_paise": row["spent_paise"] + amount_paise},
                     correlation_id=mandate_id)
        if began:
            conn.execute("COMMIT")
        updated = get(mandate_id, conn=conn)
        return updated, Verdict(True, [])
    except Exception:
        if began and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        if began:
            conn.isolation_level = prev


def release(conn: sqlite3.Connection, mandate_id: str,
            amount_paise: int) -> None:
    """Return a held reservation — payment failed, or the order expired.

    Clamped at zero: releasing must never be able to invent budget, even
    if bookkeeping is somehow out of step.
    """
    if not mandate_id:
        return
    conn.execute(
        "UPDATE mandates SET spent_paise = MAX(0, spent_paise - ?) WHERE id = ?",
        (amount_paise, mandate_id))
    audit.append(conn, actor="mandates", action_type="mandate.released",
                 payload={"mandate_id": mandate_id, "amount_paise": amount_paise},
                 correlation_id=mandate_id)


def draw_down(conn, mandate_id: str, amount_paise: int) -> None:
    """Add captured spend to the envelope. Runs INSIDE the webhook's
    transaction; idempotency comes from the payments-row insert guard."""
    conn.execute("UPDATE mandates SET spent_paise = spent_paise + ? WHERE id = ?",
                 (amount_paise, mandate_id))
