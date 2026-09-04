#!/usr/bin/env python3
"""mandate_keygen.py — mint an Ed25519 key for sealing consent envelopes.

    python scripts/mandate_keygen.py

Prints two base64 strings:

    seed         -> put in app/.env as MANDATE_ED25519_SEED   (SECRET)
    public key   -> served at /.well-known/bazaar-mandate-key (public)

Only the seed is secret. The public key is derived from it, so rotating is
one value, not two that can fall out of step.

WHY YOU WOULD DO THIS
---------------------
Default (MANDATE_SIGNING=hmac) is symmetric: the merchant holds the only
key, so it can both seal and verify an envelope. That proves an envelope
was not tampered with. It does NOT prove the buyer consented — the
merchant could have written the whole thing itself.

With MANDATE_SIGNING=ed25519 the buyer (or its agent) seals consent with a
key the merchant does not hold, and the merchant verifies. Then "the
merchant says the buyer agreed" and "the buyer agreed" become different
statements, which is the property that matters once a third party is
holding the envelope.

This script does NOT edit .env for you. Writing secrets into a file behind
your back is how a key ends up in a commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from bazaar import mandates
    except ImportError as exc:  # pragma: no cover - environment problem
        print(f"cannot import bazaar: {exc}")
        return 2

    try:
        keys = mandates.keygen()
    except ImportError as exc:
        print(f"Ed25519 needs the `cryptography` package: {exc}\n"
              "  uv sync    (it is already in uv.lock)")
        return 2

    print()
    print("  Add to app/.env   (SECRET — never commit this file):")
    print(f"    MANDATE_ED25519_SEED={keys['seed_b64']}")
    print("    MANDATE_SIGNING=ed25519")
    print()
    print("  Then verify it is being served publicly:")
    print("    curl -s https://r2-d2.xyz/.well-known/bazaar-mandate-key")
    print()
    print(f"  public key (should match the endpoint): {keys['public_key_b64']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
