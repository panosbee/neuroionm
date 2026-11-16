"""Targeted upgrade of knowledge_nodes only.

Re-seeds the `knowledge_nodes` collection from the JSON seed without touching
curated collections such as `electrode_coordinates`. Use this when node-level
metadata (e.g. SSEP montage, pedicle screw mapping, links) changes but body-map
coordinates must be preserved.
"""

import asyncio

from backend.app.db.mongo import mongo_store
from backend.app.services.seed_loader import ensure_indexes, load_seed


async def main() -> None:
    await mongo_store.connect()
    if not mongo_store.connected:
        raise RuntimeError(mongo_store.last_error or "MongoDB is not connected")

    database = mongo_store.db()
    await ensure_indexes(database)

    seed = load_seed()
    nodes = seed.get("knowledge_nodes", [])

    updated = 0
    for node in nodes:
        key = node.get("key")
        if not key:
            continue
        await database.knowledge_nodes.replace_one({"key": key}, node, upsert=True)
        updated += 1

    await mongo_store.close()
    print(f"knowledge_nodes upgraded: {updated}")


if __name__ == "__main__":
    asyncio.run(main())
