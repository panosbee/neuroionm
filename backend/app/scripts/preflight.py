import asyncio

from backend.app.core.config import settings
from backend.app.db.mongo import mongo_store


async def main() -> None:
    await mongo_store.connect()
    checks = {
        "mongo_connected": mongo_store.connected,
        "seed_file_present": settings.seed_path.exists(),
        "frontend_present": settings.frontend_path.exists(),
        "assets_present": settings.assets_path.exists(),
        "deepseek_configured": bool(settings.deepseek_api_key),
        "jina_configured": bool(settings.jina_api_key),
    }
    if mongo_store.connected:
        database = mongo_store.db()
        checks["knowledge_nodes_count"] = await database.knowledge_nodes.count_documents({})
        checks["muscles_count"] = await database.muscles.count_documents({})
        checks["coordinates_count"] = await database.electrode_coordinates.count_documents({})
        checks["protocols_count"] = await database.protocols.count_documents({})
        checks["protocol_domain_defaults_count"] = await database.protocols.count_documents({"default_for_domains": {"$exists": True, "$ne": []}})
        checks["admin_users_count"] = await database.admin_users.count_documents({})
        checks["admin_knowledge_documents_count"] = await database.admin_knowledge_documents.count_documents({})
        checks["admin_knowledge_chunks_count"] = await database.admin_knowledge_chunks.count_documents({})
        checks["academy_progress_count"] = await database.academy_progress.count_documents({})
        checks["academy_attempts_count"] = await database.academy_attempts.count_documents({})
        checks["knowledge_embeddings_count"] = await database.knowledge_embeddings.count_documents({})
    checks["tesseract_cmd_configured"] = bool(settings.tesseract_cmd)
    checks["tesseract_languages"] = settings.tesseract_languages
    await mongo_store.close()

    for name, value in checks.items():
        print(f"{name}: {value}")

    required = ["mongo_connected", "seed_file_present", "frontend_present", "assets_present"]
    if not all(bool(checks[name]) for name in required):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
