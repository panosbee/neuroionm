import json
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.core.config import settings


SEED_COLLECTIONS = {
    "knowledge_nodes": "knowledge_nodes",
    "procedures": "procedures",
    "modalities": "modalities",
    "nerve_roots": "nerve_roots",
    "spinal_discs": "spinal_discs",
    "cranial_nerves": "cranial_nerves",
    "muscles": "muscles",
    "electrode_coordinates": "electrode_coordinates",
    "ssep_protocols": "ssep_protocols",
    "pedicle_roots": "pedicle_roots",
    "protocols": "protocols",
    "academy_modules": "academy_modules",
}


def load_seed(path: Path | None = None) -> dict[str, Any]:
    seed_path = path or settings.seed_path
    with seed_path.open("r", encoding="utf-8") as file:
        return json.load(file)


async def ensure_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.knowledge_nodes.create_index("key", unique=True)
    await database.knowledge_nodes.create_index([("parent_key", 1), ("sort_order", 1)])
    await database.muscles.create_index("lat_name", unique=True)
    await database.electrode_coordinates.create_index("lat_name", unique=True)
    await database.nerve_roots.create_index("key", unique=True)
    await database.cranial_nerves.create_index("key", unique=True)
    await database.procedures.create_index("key", unique=True)
    await database.protocols.create_index("key", unique=True)
    await database.protocols.create_index("default_for_domains")
    await database.admin_users.create_index("user_id", unique=True)
    await database.admin_users.create_index("email", unique=True)
    await database.admin_users.create_index("username_normalized", unique=True, sparse=True)
    await database.admin_knowledge_documents.create_index("document_id", unique=True)
    await database.admin_knowledge_chunks.create_index("chunk_id", unique=True)
    await database.academy_progress.create_index([("learner_id", 1), ("node_key", 1)], unique=True)
    await database.academy_progress.create_index([("learner_id", 1), ("updated_at", -1)])
    await database.academy_attempts.create_index([("learner_id", 1), ("node_key", 1), ("created_at", -1)])
    await database.knowledge_embeddings.create_index("chunk_key", unique=True)
    await database.knowledge_embeddings.create_index([("model", 1), ("source_collection", 1)])
    await database.cases.create_index("case_id", unique=True)
    await database.case_events.create_index([("case_id", 1), ("created_at", 1)])


async def seed_database(database: AsyncIOMotorDatabase, seed: dict[str, Any] | None = None) -> dict[str, int]:
    payload = seed or load_seed()
    await ensure_indexes(database)
    counts: dict[str, int] = {}

    for seed_key, collection_name in SEED_COLLECTIONS.items():
        documents = payload.get(seed_key, [])
        collection = database[collection_name]
        count = 0
        for document in documents:
            natural_key = document.get("key") or document.get("lat_name") or document.get("module_key")
            if natural_key is None:
                continue
            if "key" in document:
                filter_doc = {"key": natural_key}
            elif "lat_name" in document:
                filter_doc = {"lat_name": natural_key}
            else:
                filter_doc = {"module_key": natural_key}
            await collection.replace_one(filter_doc, document, upsert=True)
            count += 1
        counts[collection_name] = count

    return counts
