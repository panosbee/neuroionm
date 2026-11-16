from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="IONM Platform", alias="IONM_APP_NAME")
    environment: str = Field(default="local", alias="IONM_ENV")
    debug: bool = Field(default=False, alias="IONM_DEBUG")
    default_lang: str = Field(default="el", alias="IONM_DEFAULT_LANG")
    mongo_uri: str = Field(default="mongodb://localhost:27017", alias="IONM_MONGO_URI")
    mongo_db: str = Field(default="ionm_platform", alias="IONM_MONGO_DB")
    auto_seed_on_startup: bool = Field(default=True, alias="IONM_AUTO_SEED_ON_STARTUP")
    frontend_dir: str = Field(default="frontend", alias="IONM_FRONTEND_DIR")
    assets_dir: str = Field(default=".", alias="IONM_ASSETS_DIR")
    cors_origins_raw: str = Field(default="http://127.0.0.1:8000,http://localhost:8000", alias="IONM_CORS_ORIGINS")
    allowed_hosts_raw: str = Field(default="127.0.0.1,localhost", alias="IONM_ALLOWED_HOSTS")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", validation_alias=AliasChoices("DEEPSEEK_API_URL", "DEEPSEEK_BASE_URL"))
    deepseek_model: str = Field(default="deepseek-v4-pro", alias="DEEPSEEK_MODEL")

    # Local EU-hosted model (#13). An OpenAI-compatible endpoint (e.g. a
    # TinyLlama/BioBERT or Llama service running on the Hetzner EU host). When
    # enabled, the model router keeps PHI-bearing reasoning on this in-region
    # model instead of sending it to the external DeepSeek API. PHI never leaves
    # the EU boundary when `phi_routing_required` is set.
    local_model_base_url: str | None = Field(default=None, alias="IONM_LOCAL_MODEL_BASE_URL")
    local_model_api_key: str | None = Field(default=None, alias="IONM_LOCAL_MODEL_API_KEY")
    local_model_name: str = Field(default="ionm-eu-clinical", alias="IONM_LOCAL_MODEL_NAME")
    local_model_enabled: bool = Field(default=False, alias="IONM_LOCAL_MODEL_ENABLED")
    phi_routing_required: bool = Field(default=False, alias="IONM_PHI_ROUTING_REQUIRED")
    jina_api_key: str | None = Field(default=None, alias="JINA_API_KEY")
    jina_api_url: str = Field(default="https://api.jina.ai/v1", alias="JINA_API_URL")
    jina_embeddings_model: str = Field(default="jina-embeddings-v3", alias="JINA_EMBEDDINGS_MODEL")
    # Cross-encoder reranking of the candidate evidence retrieved by embeddings.
    # The reranker re-scores each candidate against the actual query, which
    # sharpens grounding of the clinical copilot. When the key is missing the
    # retrieval gracefully falls back to pure cosine similarity.
    jina_rerank_model: str = Field(default="jina-reranker-v2-base-multilingual", alias="JINA_RERANK_MODEL")
    jina_rerank_enabled: bool = Field(default=True, alias="JINA_RERANK_ENABLED")
    jina_rerank_candidates: int = Field(default=24, ge=4, le=128, alias="JINA_RERANK_CANDIDATES")
    pubmed_api_key: str | None = Field(default=None, alias="PUBMED_API_KEY")
    pubmed_api_url: str = Field(default="https://eutils.ncbi.nlm.nih.gov/entrez/eutils", alias="PUBMED_API_URL")
    pubmed_email: str | None = Field(default=None, alias="PUBMED_EMAIL")
    pubmed_tool: str = Field(default="ionm-platform", alias="PUBMED_TOOL")
    admin_session_secret: str = Field(default="ionm-local-admin-session-secret-change-before-production", alias="IONM_ADMIN_SESSION_SECRET")
    admin_session_hours: int = Field(default=8, alias="IONM_ADMIN_SESSION_HOURS")
    admin_accounts_json: str | None = Field(default=None, alias="IONM_ADMIN_ACCOUNTS_JSON")
    admin_bootstrap_username: str | None = Field(default=None, alias="IONM_ADMIN_BOOTSTRAP_USERNAME")
    admin_bootstrap_display_name: str | None = Field(default=None, alias="IONM_ADMIN_BOOTSTRAP_DISPLAY_NAME")
    admin_bootstrap_password: str | None = Field(default=None, alias="IONM_ADMIN_BOOTSTRAP_PASSWORD")
    admin_bootstrap_password_hash: str | None = Field(default=None, alias="IONM_ADMIN_BOOTSTRAP_PASSWORD_HASH")
    owner_enabled: bool = Field(default=False, alias="IONM_OWNER_ENABLED")
    admin_upload_dir: str = Field(default="data/admin_knowledge", alias="IONM_ADMIN_UPLOAD_DIR")
    tesseract_cmd: str | None = Field(default=None, alias="TESSERACT_CMD")
    tessdata_prefix: str | None = Field(default=None, alias="TESSDATA_PREFIX")
    tesseract_languages: str = Field(default="eng+ell", alias="TESSERACT_LANGUAGES")

    # Live signal ingestion (Stage B): when set, the server may tail EDF files that
    # an acquisition system writes inside this directory. Left unset, only the
    # push transport (client agent POSTs byte deltas) is available.
    signal_stream_dir: str | None = Field(default=None, alias="IONM_SIGNAL_STREAM_DIR")

    # Tele-IONM Phase 2 (WebRTC voice + screen share) ICE servers. STUN is a
    # public default (works for most direct-path networks); TURN is optional and
    # only emitted when both URLs and credentials are configured.
    tele_stun_urls: str = Field(default="stun:stun.l.google.com:19302", alias="IONM_TELE_STUN_URLS")
    tele_turn_urls: str | None = Field(default=None, alias="IONM_TELE_TURN_URLS")
    tele_turn_secret: str | None = Field(default=None, alias="IONM_TELE_TURN_SECRET")
    tele_turn_username: str | None = Field(default=None, alias="IONM_TELE_TURN_USERNAME")
    tele_turn_credential: str | None = Field(default=None, alias="IONM_TELE_TURN_CREDENTIAL")
    tele_turn_ttl_seconds: int = Field(default=3600, alias="IONM_TELE_TURN_TTL")

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def frontend_path(self) -> Path:
        path = Path(self.frontend_dir)
        return path if path.is_absolute() else self.project_root / path

    @property
    def assets_path(self) -> Path:
        path = Path(self.assets_dir)
        return path if path.is_absolute() else self.project_root / path

    @property
    def seed_path(self) -> Path:
        return self.project_root / "backend" / "app" / "seed_data" / "ionm_seed.json"

    @property
    def admin_upload_path(self) -> Path:
        path = Path(self.admin_upload_dir)
        return path if path.is_absolute() else self.project_root / path

    @property
    def signal_stream_path(self) -> Path | None:
        if not self.signal_stream_dir:
            return None
        path = Path(self.signal_stream_dir)
        return path if path.is_absolute() else self.project_root / path

    @property
    def cors_origins(self) -> list[str]:
        return split_csv(self.cors_origins_raw)

    @property
    def allowed_hosts(self) -> list[str]:
        return split_csv(self.allowed_hosts_raw)

    @property
    def docs_enabled(self) -> bool:
        return self.environment.lower() not in {"production", "prod"} or self.debug

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
