"""Targeted upgrade of the ``muscles`` collection only.

Re-seeds the ``muscles`` collection from the JSON seed without touching curated
collections such as ``electrode_coordinates`` (which may carry admin body-map
curation). Use this when muscle-level metadata changes — for example the
``electrode_montage`` enrichment authored by
``backend.app.scripts.enrich_muscle_montage`` — but body-map coordinates must be
preserved exactly as curated.

Run:

    .\\.venv\\Scripts\\python.exe -m backend.app.scripts.upgrade_muscles
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
    muscles = seed.get("muscles", [])

    updated = 0
    for muscle in muscles:
        lat_name = muscle.get("lat_name")
        if not lat_name:
            continue
        await database.muscles.replace_one({"lat_name": lat_name}, muscle, upsert=True)
        updated += 1

    await mongo_store.close()
    print(f"muscles upgraded: {updated}")


if __name__ == "__main__":
    asyncio.run(main())
