import asyncio

from backend.app.db.mongo import mongo_store
from backend.app.services.rag import index_knowledge_embeddings


async def main() -> None:
    await mongo_store.connect()
    try:
        result = await index_knowledge_embeddings(mongo_store.db())
    finally:
        await mongo_store.close()

    for name, value in result.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    asyncio.run(main())