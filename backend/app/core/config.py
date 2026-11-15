from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    # Live LLM calls per UTC day shared by all guest sandboxes; beyond it guests get replay.
    guest_daily_llm_calls: int = 200
    # Reverse proxies in front of the app that append to X-Forwarded-For (0 = ignore header).
    trusted_proxy_hops: int = 0
    # /metrics answers only with this bearer token; unset means the endpoint is off.
    metrics_token: SecretStr | None = None
    # Shared with the dashboard's edge proxy, which then may state the visitor address.
    proxy_secret: SecretStr | None = None

    # Names and places need the spaCy model; formats with checksums work without it.
    ner_enabled: bool = True

    # Upstream LLMs (both speak the OpenAI wire format). Without keys, or with
    # LLM_MODE=replay, the gateway answers from the deterministic replay provider.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    provider_timeout_seconds: float = 30.0
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 30.0

    cache_ttl_seconds: int = 3600
    # Token bucket per API key: burst of `capacity`, then `refill` requests per second.
    rate_limit_capacity: int = 20
    rate_limit_refill_per_second: float = 0.5

    # Safety-net wake-up of the stream consumer; normally the publisher wakes it.
    consumer_idle_seconds: float = 300.0
    # Browser origins allowed to call the API (the dashboard).
    cors_origins: list[str] = ["http://localhost:5173"]

    seed_admin_email: str | None = None
    seed_admin_password: SecretStr | None = None

    @field_validator(
        "gemini_api_key", "groq_api_key", "metrics_token", "proxy_secret", mode="before"
    )
    @classmethod
    def blank_means_unset(cls, value: object) -> object:
        """Hosting dashboards store a field left empty as an empty string.

        Without this an empty METRICS_TOKEN would switch /metrics on with an
        empty token, and an empty API key would add a provider that always fails.
        """
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_driver(cls, value: str) -> str:
        """Accept the connection strings hosted Postgres providers hand out.

        They use the `postgres://` scheme and libpq options. SQLAlchemy needs the
        driver in the scheme, and asyncpg spells `sslmode` as `ssl` and rejects
        `channel_binding`.
        """
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                value = "postgresql+asyncpg://" + value[len(prefix) :]
        parts = urlsplit(value)
        options = [
            ("ssl" if name == "sslmode" else name, option)
            for name, option in parse_qsl(parts.query, keep_blank_values=True)
            if name != "channel_binding"
        ]
        return urlunsplit(parts._replace(query=urlencode(options)))

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
