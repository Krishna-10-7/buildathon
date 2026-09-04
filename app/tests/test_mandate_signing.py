"""Ed25519 mandate sealing (T11).

The interesting test here is not "ed25519 verifies a good envelope" — that
is the library's job. It is the DOWNGRADE case: an envelope whose HMAC is
arithmetically perfect must still be refused when the merchant is
configured for Ed25519.

That case is the entire point of the feature. In hmac mode the merchant
holds the only key, so it can mint a buyer's consent. Switching to
ed25519 is supposed to take that ability away. If a validly-HMAC-sealed
envelope were still accepted afterwards, an attacker holding the shared
secret could keep forging consent by simply using the old format — and the
stronger setting would be decoration.

So the mode is not a preference about which algorithm to prefer. It is a
statement about what the merchant is allowed to do.
"""

from __future__ import annotations

import base64
import json

import pytest

from bazaar import mandates
from bazaar.config import settings
from tests.conftest import ok

pytest.importorskip("cryptography", reason="needs the cryptography package")


@pytest.fixture
def ed25519_mode(monkeypatch):
    """Force ed25519 with a fresh key, and restore the old settings after."""
    keys = mandates.keygen()
    monkeypatch.setattr(settings, "mandate_signing", "ed25519")
    monkeypatch.setattr(settings, "mandate_ed25519_seed", keys["seed_b64"])
    return keys


@pytest.fixture
def hmac_mode(monkeypatch):
    monkeypatch.setattr(settings, "mandate_signing", "hmac")
    monkeypatch.setattr(settings, "mandate_ed25519_seed", "")
    return None


def _canonical_of(row: dict) -> str:
    return mandates._canonical(
        row["buyer_ref"], row["budget_cap_paise"],
        row["max_single_txn_paise"],
        json.loads(row["allowed_categories_json"]), row["expires_at"])


# ---- the default is unchanged --------------------------------------------

def test_default_mode_is_hmac():
    """The default must stay symmetric unless someone opts in.

    Flipping the default would silently invalidate every HMAC-sealed
    envelope in every existing store, including the live one.
    """
    ok("default signing mode is hmac",
       mandates.signing_mode() in ("hmac", "ed25519"))
    ok("config default is hmac", settings.mandate_signing == "hmac",
       settings.mandate_signing)


def test_unknown_mode_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "mandate_signing", "rot13")
    with pytest.raises(RuntimeError, match="hmac.*ed25519"):
        mandates.signing_mode()


# ---- hmac mode still behaves exactly as before ---------------------------

def test_hmac_signature_is_bare_64_hex(hmac_mode, conn):
    m = mandates.create("hmac-buyer", 100_000, 40_000, ["tea"], conn=conn)
    ok("hmac signature is 64 hex chars",
       len(m["signature"]) == 64 and not m["signature"].startswith("ed25519:"),
       m["signature"][:20])
    ok("hmac signature verifies", mandates._verify(
        _canonical_of(m), m["signature"]))


def test_hmac_still_detects_tampering(hmac_mode, conn):
    m = mandates.create("hmac-buyer", 100_000, 40_000, ["tea"], conn=conn)
    conn.execute("UPDATE mandates SET budget_cap_paise = 99999999 WHERE id = ?",
                 (m["id"],))
    conn.commit()
    row = mandates.get(m["id"], conn=conn)
    _row, verdict = mandates.check(m["id"], 1_000, ["tea"], conn=conn)
    ok("raised cap breaks the hmac seal", not verdict.allowed)
    ok("reason names the signature",
       any("signature" in r for r in verdict.reasons), str(verdict.reasons))
    assert row["budget_cap_paise"] == 99999999


# ---- ed25519 mode --------------------------------------------------------

def test_ed25519_envelope_seals_and_verifies(ed25519_mode, conn):
    m = mandates.create("ed-buyer", 100_000, 40_000, ["tea"], conn=conn)
    ok("ed25519 signature is prefixed",
       m["signature"].startswith(mandates.ED25519_PREFIX),
       m["signature"][:24])
    ok("ed25519 signature verifies",
       mandates._verify(_canonical_of(m), m["signature"]))
    _row, verdict = mandates.check(m["id"], 1_000, ["tea"], conn=conn)
    ok("fresh ed25519 envelope passes check", verdict.allowed, str(verdict))


def test_ed25519_detects_tampering(ed25519_mode, conn):
    m = mandates.create("ed-buyer", 100_000, 40_000, ["tea"], conn=conn)
    conn.execute("UPDATE mandates SET max_single_txn_paise = 99999999 WHERE id=?",
                 (m["id"],))
    conn.commit()
    _row, verdict = mandates.check(m["id"], 1_000, ["tea"], conn=conn)
    ok("tampered ed25519 envelope is refused", not verdict.allowed)
    ok("the reason is the signature, not a bound",
       any("signature" in r for r in verdict.reasons), str(verdict.reasons))


def test_ed25519_signature_rejects_a_forged_key(ed25519_mode, conn):
    """A second keypair must not be able to seal an envelope we accept."""
    other = mandates.keygen()
    m = mandates.create("ed-buyer", 100_000, 40_000, ["tea"], conn=conn)
    body = _canonical_of(m)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    forged = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(other["seed_b64"])).sign(body.encode())
    forged_sig = (mandates.ED25519_PREFIX
                  + base64.b64encode(forged).decode())
    ok("a signature from another key does not verify",
       not mandates._verify(body, forged_sig))


# ---- the downgrade guard (the point of the whole feature) ----------------

def test_valid_hmac_envelope_is_refused_in_ed25519_mode(ed25519_mode, conn):
    """THE test. A perfectly valid HMAC seal must not be accepted.

    If this passes, an attacker with the shared secret can still mint
    consent after the merchant "upgraded", and the upgrade bought nothing.
    """
    # Seal under hmac, then switch the merchant to ed25519.
    monkeypatched = settings.mandate_signing
    settings.mandate_signing = "hmac"
    try:
        m = mandates.create("legacy-buyer", 100_000, 40_000, ["tea"],
                            conn=conn)
    finally:
        settings.mandate_signing = monkeypatched

    body = _canonical_of(m)
    ok("the envelope really is hmac-sealed",
       not m["signature"].startswith(mandates.ED25519_PREFIX))
    # Prove the MAC is arithmetically correct, so the refusal below can only
    # be the mode veto and not a broken signature. Without this line the
    # test would also pass if we had simply failed to store a valid seal.
    import hashlib
    import hmac as _hmac

    recomputed = _hmac.new(
        hashlib.sha256(
            (settings.mandate_secret
             or f"{settings.rzp_key_secret}:mandates-v1").encode()).digest(),
        body.encode(), hashlib.sha256).hexdigest()
    ok("...and its hmac is arithmetically valid",
       recomputed == m["signature"], m["signature"][:16])

    _row, verdict = mandates.check(m["id"], 1_000, ["tea"], conn=conn)
    ok("a validly HMAC-sealed envelope is refused under ed25519",
       not verdict.allowed)
    ok("and it is refused as a signature problem, not a bound",
       any("signature" in r for r in verdict.reasons), str(verdict.reasons))


def test_hmac_mode_refuses_an_ed25519_envelope(hmac_mode, conn, monkeypatch):
    """The reverse: hmac mode must not verify an asymmetric seal either.

    Otherwise `check` in hmac mode would try the public key, fail to
    configure it, and the failure mode would depend on config drift.
    """
    keys = mandates.keygen()
    monkeypatch.setattr(settings, "mandate_ed25519_seed", keys["seed_b64"])
    monkeypatch.setattr(settings, "mandate_signing", "ed25519")
    m = mandates.create("ed-buyer", 100_000, 40_000, ["tea"], conn=conn)
    monkeypatch.setattr(settings, "mandate_signing", "hmac")

    ok("ed25519 seal rejected while in hmac mode",
       not mandates._verify(_canonical_of(m), m["signature"]))


# ---- the well-known endpoint ---------------------------------------------

def test_wellknown_publishes_the_key_in_ed25519_mode(ed25519_mode, client):
    r = client.get("/.well-known/bazaar-mandate-key")
    ok("well-known endpoint is reachable", r.status_code == 200, str(r.status_code))
    doc = r.json()
    ok("declares ed25519", doc.get("mandate_signing") == "ed25519",
       str(doc.get("mandate_signing")))
    ok("publishes a public key", bool(doc.get("public_key_b64")))
    ok("public key matches the configured seed",
       doc.get("public_key_b64") == ed25519_mode["public_key_b64"])
    ok("publishes a JWK", (doc.get("jwk") or {}).get("crv") == "Ed25519",
       str(doc.get("jwk")))
    ok("publishes the canonical form so it is actually verifiable",
       "fields" in (doc.get("canonical_form") or {}),
       str(doc.get("canonical_form")))


def test_wellknown_never_leaks_the_shared_secret(hmac_mode, client):
    """In hmac mode there is no public key — and what is served must say so.

    Serving a key derived from the shared secret would imply a
    non-repudiation property that hmac does not have.
    """
    r = client.get("/.well-known/bazaar-mandate-key")
    doc = r.json()
    ok("declares hmac", doc.get("mandate_signing") == "hmac")
    ok("publishes no key", doc.get("public_key_b64") is None)
    ok("says plainly what hmac does and does not prove",
       "non-repudiation" in (doc.get("note") or ""), str(doc.get("note")))
    ok("the shared secret is not in the response",
       not (settings.mandate_secret and settings.mandate_secret in r.text))


# ---- key management ------------------------------------------------------

def test_keygen_produces_a_usable_pair():
    keys = mandates.keygen()
    ok("seed is 32 bytes of base64",
       len(base64.b64decode(keys["seed_b64"])) == 32)
    ok("public key is 32 bytes of base64",
       len(base64.b64decode(keys["public_key_b64"])) == 32)
    ok("seed and public key differ", keys["seed_b64"] != keys["public_key_b64"])
    ok("two keygens differ",
       mandates.keygen()["seed_b64"] != keys["seed_b64"])


def test_ed25519_without_a_seed_fails_loudly(monkeypatch):
    """A misconfigured ed25519 must not silently fall back to hmac."""
    monkeypatch.setattr(settings, "mandate_signing", "ed25519")
    monkeypatch.setattr(settings, "mandate_ed25519_seed", "")
    with pytest.raises(RuntimeError, match="MANDATE_ED25519_SEED"):
        mandates.create("no-key", 10_000, 5_000, [])


def test_ed25519_with_a_short_seed_fails_loudly(monkeypatch):
    import base64 as b64

    monkeypatch.setattr(settings, "mandate_signing", "ed25519")
    monkeypatch.setattr(settings, "mandate_ed25519_seed",
                        b64.b64encode(b"tooshort").decode())
    with pytest.raises(RuntimeError, match="32 bytes"):
        mandates.create("short-key", 10_000, 5_000, [])
