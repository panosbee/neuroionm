"""Targeted upgrade of legacy ``electrode_coordinates`` from the JSON seed.

This is the coordinate analogue of ``upgrade_muscles`` and
``upgrade_knowledge_nodes``. It re-seeds body-map coordinates from
``ionm_seed.json`` **only for legacy entries**, and never overwrites
admin-curated coordinates.

A stored coordinate document is treated as curated (and therefore preserved)
when any of the following holds:

* ``source == "admin_bodymap_curation"``
* ``coordinate_system == "percentage_of_full_image"``
* the document, or any of its ``coordinates`` rows, has ``status == "reviewed"``

Use this to propagate legacy coordinate edits (for example separating two
overlapping midline pins) without disturbing curated anchors.

Run:

    .\\.venv\\Scripts\\python.exe -m backend.app.scripts.upgrade_coordinates
"""

import asyncio

from backend.app.db.mongo import mongo_store
from backend.app.services.seed_loader import ensure_indexes, load_seed


def _is_curated(document: dict | None) -> bool:
    if not document:
        return False
    if document.get("source") == "admin_bodymap_curation":
        return True
    if document.get("coordinate_system") == "percentage_of_full_image":
        return True
    if document.get("status") == "reviewed":
        return True
    for row in document.get("coordinates", []) or []:
        if isinstance(row, dict) and row.get("status") == "reviewed":
            return True
    return False


async def main() -> None:
    await mongo_store.connect()
    if not mongo_store.connected:
        raise RuntimeError(mongo_store.last_error or "MongoDB is not connected")

    database = mongo_store.db()
    await ensure_indexes(database)

    seed = load_seed()
    entries = seed.get("electrode_coordinates", [])

    updated = 0
    preserved = 0
    for entry in entries:
        lat_name = entry.get("lat_name")
        if not lat_name:
            continue
        existing = await database.electrode_coordinates.find_one({"lat_name": lat_name})
        if _is_curated(existing):
            preserved += 1
            continue
        await database.electrode_coordinates.replace_one(
            {"lat_name": lat_name}, entry, upsert=True
        )
        updated += 1

    await mongo_store.close()
    print(f"coordinates updated (legacy): {updated}")
    print(f"coordinates preserved (curated): {preserved}")


if __name__ == "__main__":
    asyncio.run(main())
