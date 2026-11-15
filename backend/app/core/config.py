from functools import lru_cache
from typing import Annotated, Literal, Self

from fastapi import Depends
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder so local runs work out of the box; rejected when ENVIRONMENT=production.
INSECURE_DEV_JWT_SECRET = "local-development-only-secret-never-use-in-production"  # noqa: S105


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+asyncpg://docuvault:docuvault@localhost:5432/docuvault"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    llm_mode: Literal["live", "replay"] = "replay"

    jwt_secret: SecretStr = SecretStr(INSECURE_DEV_JWT_SECRET)
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    guest_tenant_ttl_minutes: int = 60
    guest_logins_per_hour: int = 10

    # Names and places need the spaCy model; formats with checksums work without it.
    ner_enabled: bool = True

    # Upstream LLMs (both speak the OpenAI wire format). Without keys, or with
    # LLM_MODE=replay, the gateway answers from the deterministic replay provider.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    provider_timeout_seconds: float = 30.0
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 30.0

    cache_ttl_seconds: int = 3600
    # Token bucket per API key: burst of `capacity`, then `refill` requests per second.
    rate_limit_capacity: int = 20
    rate_limit_refill_per_second: float = 0.5

    seed_admin_email: str | None = None
    seed_admin_password: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_driver(cls, value: str) -> str:
        """Hosted Postgres providers hand out `postgres://` URLs; SQLAlchemy needs the driver."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @model_validator(mode="after")
    def require_real_secret_in_production(self) -> Self:
        secret = self.jwt_secret.get_secret_value()
        if self.environment == "production" and (
            secret == INSECURE_DEV_JWT_SECRET or len(secret) < 32
        ):
            raise ValueError("JWT_SECRET must be set to a random value of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
