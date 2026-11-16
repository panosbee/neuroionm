from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from backend.app.core.config import settings


class MongoStore:
    def __init__(self) -> None:
        self.client: AsyncIOMotorClient | None = None
        self.database: AsyncIOMotorDatabase | None = None
        self.connected: bool = False
        self.last_error: str | None = None

    async def connect(self) -> None:
        self.client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=2500)
        self.database = self.client[settings.mongo_db]
        try:
            await self.client.admin.command("ping")
            self.connected = True
            self.last_error = None
        except PyMongoError as exc:
            self.connected = False
            self.last_error = str(exc)

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None
        self.database = None
        self.connected = False

    def db(self) -> AsyncIOMotorDatabase:
        if self.database is None or not self.connected:
            detail = self.last_error or "MongoDB is not connected"
            raise RuntimeError(detail)
        return self.database


mongo_store = MongoStore()


async def get_database() -> AsyncIterator[AsyncIOMotorDatabase]:
    yield mongo_store.db()


@asynccontextmanager
async def mongo_lifespan() -> AsyncIterator[MongoStore]:
    await mongo_store.connect()
    try:
        yield mongo_store
    finally:
        await mongo_store.close()
