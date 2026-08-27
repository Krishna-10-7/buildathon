"""Bundle identity shared by every writer.

The executor (proposals.py) creates bundles; the experiment arm switch
(experiment.py) re-activates them; order pricing matches baskets against
them. All three derive the id THE SAME WAY — one helper here so the
derivation can never drift apart.
"""

import hashlib
import json


def bundle_id(skus: list[str]) -> str:
    """Deterministic id from the component sku list (same ask -> same bundle)."""
    return "bnd_" + hashlib.sha256(":".join(skus).encode()).hexdigest()[:12]


def skus_of(skus_json: str) -> list[str]:
    return json.loads(skus_json)
