import asyncio

from backend.app.db.mongo import mongo_store
from backend.app.services.seed_loader import seed_database


async def main() -> None:
    await mongo_store.connect()
    if not mongo_store.connected:
        raise RuntimeError(mongo_store.last_error or "MongoDB is not connected")
    counts = await seed_database(mongo_store.db())
    await mongo_store.close()
    print("Seed complete")
    for collection, count in sorted(counts.items()):
        print(f"{collection}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
