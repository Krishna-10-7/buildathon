"""Public, agent/buyer-readable catalog feed. Read-only.

Deliberately EXCLUDES cost_paise, base_price_paise, discount internals and
audit data — buyers see what a storefront would show. Margin data stays in
the governed surface (/agent/snapshot) where it belongs.
"""

from fastapi import APIRouter

from bazaar.db import connect

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("")
def catalog() -> dict:
    conn = connect()
    try:
        products = [
            {
                "sku": r["sku"],
                "title": r["title"],
                "description": r["description"],
                "price_paise": r["price_paise"],
                "kind": r["kind"],
                "tags": r["tags_json"],       # raw json text; fine for v0
                "pairs_with": r["pairs_with_json"],
                "in_stock": r["stock"] > 0,
            }
            for r in conn.execute(
                "SELECT sku, title, description, price_paise, kind,"
                " tags_json, pairs_with_json, stock FROM products"
                " WHERE active = 1 ORDER BY price_paise"
            )
        ]
        bundles = [
            {"id": b["id"], "skus": b["skus_json"], "price_paise": b["price_paise"]}
            for b in conn.execute(
                "SELECT id, skus_json, price_paise FROM bundles WHERE active = 1"
            )
        ]
        return {"products": products, "bundles": bundles}
    finally:
        conn.close()
