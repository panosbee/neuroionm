from fastapi import APIRouter, Response, status

from backend.app.core.config import settings
from backend.app.db.mongo import mongo_store


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "app": settings.app_name,
        "environment": settings.environment,
        "mongo_connected": mongo_store.connected,
        "mongo_database": settings.mongo_db,
        "mongo_error": mongo_store.last_error,
        "deepseek_model": settings.deepseek_model,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "jina_configured": bool(settings.jina_api_key),
    }


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    checks = {
        "mongo_connected": mongo_store.connected,
        "seed_file_present": settings.seed_path.exists(),
        "frontend_present": settings.frontend_path.exists(),
        "assets_present": settings.assets_path.exists(),
    }
    ready_state = all(checks.values())
    if not ready_state:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready_state else "not_ready", "checks": checks}
