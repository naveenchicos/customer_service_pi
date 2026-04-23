"""
Application configuration loaded from environment variables.
All fields are validated at startup — a missing required var will crash fast
with a clear message rather than failing silently at runtime.
"""

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore unknown env vars — keeps K8s env safe
    )

    # ── Application ──────────────────────────────────────────────────────────
    environment: Environment = Environment.LOCAL
    debug: bool = False

    # ── Database (Cloud SQL via Auth Proxy on localhost:5432) ─────────────
    database_url: str = Field(
        ...,
        description="Async PostgreSQL DSN: postgresql+asyncpg://user:pass@localhost:5432/postgres",
    )
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout: float = Field(default=5.0, ge=0.5, le=30.0)

    # ── Downstream: Customer Service ─────────────────────────────────────────
    customer_service_url: AnyHttpUrl = Field(
        ...,
        description="Internal K8s DNS URL for the Customer Service",
    )
    customer_service_timeout: float = Field(default=0.5, ge=0.1, le=5.0)
    customer_service_pool_size: int = Field(default=50, ge=1, le=200)

    # ── Circuit breaker ───────────────────────────────────────────────────────
    circuit_breaker_threshold: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of consecutive failures before circuit opens",
    )
    circuit_breaker_recovery: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Seconds to wait before attempting recovery (half-open)",
    )

    # ── Security ─────────────────────────────────────────────────────────────
    # Required in local/staging (X-API-Key fallback). Not used in production —
    # Apigee validates the Bearer token before the request reaches GKE.
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key for local/staging auth. Not required in production.",
    )

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    service_name: str = Field(default="customer-service-pi")
    service_version: str = Field(default="1.0.0")

    @field_validator("api_key", mode="after")
    @classmethod
    def api_key_required_outside_production(
        cls, v: Optional[SecretStr], info: object
    ) -> Optional[SecretStr]:
        env = getattr(info, "data", {}).get("environment", Environment.LOCAL)
        if env != Environment.PRODUCTION and v is None:
            raise ValueError("API_KEY is required in local and staging environments")
        return v

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_async(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use postgresql+asyncpg:// scheme for async support"
            )
        return v

    @field_validator("log_level")
    @classmethod
    def log_level_must_be_valid(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}")
        return upper


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.
    lru_cache ensures the .env file is read only once per process.
    In tests, call get_settings.cache_clear() before overriding env vars.
    """
    return Settings()  # type: ignore[call-arg]
