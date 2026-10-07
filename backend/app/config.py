"""Env-driven settings with paths anchored to the backend directory."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"),
        extra="ignore",
    )

    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    database_url: str | None = None
    qdrant_path: str = str(BACKEND_DIR / "qdrant_data")
    database_path: str = str(BACKEND_DIR / "docflow.db")
    feature_database_path: str = str(BACKEND_DIR / "docflow_features.db")
    uploads_path: str = str(BACKEND_DIR / "uploads")

    gemini_api_key: str | None = None
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_generation_model: str = "gemini-3.6-flash"
    gemini_generation_fallback_models: str = "gemini-flash-lite-latest,gemini-3.5-flash"

    dense_embedding_model: str = "BAAI/bge-base-en-v1.5"
    dense_embedding_dim: int = 768
    embedding_batch_size: int = 16
    gemini_retry_attempts: int = 2
    gemini_timeout_seconds: int = 45

    sparse_embedding_model: str = "prithivida/Splade_PP_en_v1"

    reranker_model: str = "BAAI/bge-reranker-base"

    top_k_fetch: int = 40
    top_k_final: int = 6

    dev_tenant_id: str = "local"
    dev_user_id: str = "local-user"
    dev_org_admin: bool = True
    dev_sensitivity_clearance: int = 1
    auth_secret: str = "change-this-secret"
    auth_username: str = "admin"
    auth_password: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://127.0.0.1:5173/api/auth/google/callback"

    # Where the SPA runs in dev; used for CORS and for the post-OAuth redirect.
    frontend_url: str = "http://127.0.0.1:5173"

    email_notifications_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None


settings = Settings()
