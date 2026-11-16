from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.db.mongo import mongo_store
from backend.app.middleware.auth_gate import AuthGateMiddleware
from backend.app.routes import academy, admin, ai, archive, cases, health, knowledge, owner, signals, tele
from backend.app.services.async_review import ensure_review_indexes
from backend.app.services.case_seal import ensure_seal_indexes
from backend.app.services.channel_ontology import ensure_ontology_indexes
from backend.app.services.expert_rota import ensure_rota_indexes
from backend.app.services.measurement_proposals import ensure_measurement_indexes
from backend.app.services.outcome_registry import ensure_outcome_indexes
from backend.app.services.seed_loader import seed_database
from backend.app.services.signal_stream import ensure_signal_stream_indexes
from backend.app.services.tele_bus import tele_hub
from backend.app.services.tele_session import ensure_tele_indexes
from backend.app.services.tenant_control import ensure_tenant_control_indexes


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mongo_store.connect()
    if mongo_store.connected and settings.auto_seed_on_startup and settings.seed_path.exists():
        await seed_database(mongo_store.db())
    if mongo_store.connected:
        await ensure_tele_indexes(mongo_store.db())
        await ensure_ontology_indexes(mongo_store.db())
        await ensure_seal_indexes(mongo_store.db())
        await ensure_signal_stream_indexes(mongo_store.db())
        await ensure_measurement_indexes(mongo_store.db())
        await ensure_outcome_indexes(mongo_store.db())
        await ensure_rota_indexes(mongo_store.db())
        await ensure_review_indexes(mongo_store.db())
        if settings.owner_enabled:
            await ensure_tenant_control_indexes(mongo_store.db())
        await tele_hub.start(mongo_store.db())
    yield
    await tele_hub.stop()
    await mongo_store.close()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

if settings.allowed_hosts and "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuthGateMiddleware)

app.include_router(health.router)
app.include_router(knowledge.router)
app.include_router(academy.router)
app.include_router(admin.router)
app.include_router(cases.router)
app.include_router(ai.router)
app.include_router(signals.router)
app.include_router(archive.router)
app.include_router(tele.router)
app.include_router(owner.router)

if settings.assets_path.exists():
    app.mount("/assets", StaticFiles(directory=settings.assets_path), name="assets")

if settings.frontend_path.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_path, html=True), name="frontend")
