"""Create the SQLite database, apply schema, seed the catalog if empty."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bazaar.config import settings  # noqa: E402
from bazaar.db import SCHEMA, connect  # noqa: E402

SEED_PATH = Path(__file__).resolve().parent.parent / "seeds" / "catalog_seed.json"


def main() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    n = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if n:
        print(f"db ready at {settings.db_path} ({n} products already present)")
        return

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for p in seed["products"]:
        conn.execute(
            """INSERT INTO products
               (sku, title, description, price_paise, cost_paise, stock,
                category, kind, tags_json, pairs_with_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p["sku"], p["title"], p.get("description", ""),
                p["price_paise"], p.get("cost_paise", 0), p["stock"],
                p["category"], p.get("kind", "physical"),
                json.dumps(p.get("tags", [])), json.dumps(p.get("pairs_with", [])),
            ),
        )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"seeded {total} products into {settings.db_path}")
    conn.close()


if __name__ == "__main__":
    main()
