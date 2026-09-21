"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Read from env or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "home-organizer-api"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://homeorg:homeorg@localhost:5432/homeorg"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://homeorg:homeorg@localhost:5432/homeorg"
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20

    # JWT
    jwt_secret: str = Field(default="change-me-in-production-min-32-chars")
    jwt_algorithm: str = "HS256"
    jwt_access_ttl: int = 3600
    jwt_refresh_ttl: int = 2592000

    # AI Provider
    ai_provider: Literal["openai_compatible", "anthropic", "mock"] = "openai_compatible"
    ai_api_key: str = "sk-xxxxx-replace-me"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model_vision: str = "gpt-4o"
    ai_model_recommend: str = "gpt-4o-mini"
    ai_timeout_s: float = 30.0

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    # If set, presigned URLs the API issues will use this host instead of
    # `minio_endpoint`. Useful when the API talks to MinIO over an internal
    # network (e.g. `minio:9000` inside Docker) but the browser must reach
    # it via the loopback (e.g. `localhost:9000`).
    minio_public_endpoint: str | None = None
    minio_root_user: str = "homeorg"
    minio_root_password: str = "homeorg-minio"
    minio_bucket_items: str = "home-organizer-items"
    minio_bucket_uploads: str = "home-organizer-uploads"
    minio_use_ssl: bool = False

    # Object storage backend. `minio` talks to an S3-compatible server;
    # `local` writes to `storage_local_dir` on this machine and serves the
    # bytes back through `GET /api/v1/files/{key}`. Local exists so the app
    # works without an object store (e.g. the sandbox demo).
    storage_backend: Literal["minio", "local"] = "minio"
    storage_local_dir: str = "./var/storage"
    # Signing key for local-storage read URLs. Falls back to `jwt_secret`.
    storage_local_secret: str | None = None
    # Absolute origin prepended to locally-issued URLs. Leave empty to emit
    # root-relative URLs (the browser resolves them against the web origin and
    # the Next.js rewrite proxies /api/:path* to this API).
    api_public_base_url: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("jwt_secret")
    @classmethod
    def _warn_default_jwt(cls, v: str) -> str:
        if v.startswith("change-me"):
            import warnings

            warnings.warn(
                "JWT_SECRET is using the default placeholder. "
                "Set JWT_SECRET to a strong value in production.",
                stacklevel=2,
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
