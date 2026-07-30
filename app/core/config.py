"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production"]

# Sentinel default for ``jwt_secret_key`` in development. The model validator
# below refuses to start the app in production if this value is still in use.
_INSECURE_DEV_JWT_SECRET = "dev-insecure-CHANGE-ME-before-production"


class Settings(BaseSettings):
    """Application settings.

    Values are read from environment variables (case-insensitive) and from a
    local ``.env`` file when present. Override any field by exporting the
    matching env var, e.g. ``ENVIRONMENT=production``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime ---------------------------------------------------------
    environment: Environment = "development"

    # --- Database --------------------------------------------------------
    # Async SQLAlchemy URL, e.g.:
    #   postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/compiquest-demo"
    # Echo SQL statements to stdout (useful in development).
    db_echo: bool = False
    # Connection pool sizing.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- JWT / Auth ------------------------------------------------------
    # Secret used to sign access tokens. MUST be overridden in production
    # (the validator below refuses to start otherwise). Generate a strong
    # value with:
    #   python -c "import secrets; print(secrets.token_urlsafe(64))"
    jwt_secret_key: str = _INSECURE_DEV_JWT_SECRET
    # HS256 is fine for symmetric, single-service setups. Switch to RS256
    # / EdDSA if you ever need asymmetric signing across services.
    jwt_algorithm: str = "HS256"
    # How long an access token remains valid after issue.
    access_token_expire_minutes: int = 30
    # How long a refresh token remains valid. Refresh tokens are
    # exchanged for new access tokens via ``/auth/refresh``; rotating
    # them on every refresh limits the blast radius of a leaked token.
    refresh_token_expire_days: int = 14

    # --- Token deny-list (Redis or in-memory) ---------------------------
    # Optional Redis URL for the JWT deny-list. When set, revoked
    # ``jti``s are stored in Redis with TTL = remaining token lifetime,
    # and every authenticated request checks against it. When unset
    # (typical for local dev / single-instance), the deny-list falls
    # back to an in-process set — fine for one process but useless
    # across replicas, so production deployments MUST set this.
    redis_url: str | None = None

    # --- AWS Bedrock / iQuest AI ----------------------------------------
    # Region and model for the JVRE rationale streaming endpoint.
    # Credentials can be provided here (loaded from .env) or via the
    # standard AWS chain (~/.aws/credentials, instance profile, etc.).
    # When set here they take explicit precedence over the ambient chain.
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = "deepseek.v3.2"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    # Max tokens and temperature for the JVRE rationale generation prompt.
    bedrock_max_tokens: int = 350
    bedrock_temperature: float = 0.3

    # --- Ollama / local SLM ---------------------------------------------
    # Used when the Ollama block is active in iquest_streaming_service._token_stream.
    # Switch backends by commenting/uncommenting that block; no env var needed.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:latest"

    # --- AI Chat provider -----------------------------------------------
    # Controls which LLM backend the new iQuest AI Chat agent uses.
    # "ollama" → OllamaLLMClient (local dev)
    # "bedrock" → BedrockLLMClient with DeepSeek (prod)
    ai_provider: str = "ollama"

    # --- CompChat (compensation chat assistant pipeline) ----------------
    # Model used for the Layer-4 intent classifier and the narrator. Kept
    # separate from ``ollama_model`` so the chat pipeline can run a model
    # with stronger JSON/instruction discipline than the rationale
    # endpoint's model. gpt-oss:120b-cloud (Ollama's cloud-offloaded
    # variant — the weights don't fit local VRAM) replaced llama3.1:latest
    # after it proved unreliable at exact-digit narration of large numbers
    # (see budget_headroom follow-up questions: correct ~50% of the time,
    # garbled digit-grouping or false "insufficient information" declines
    # the rest, despite the fact being present and grounded in context).
    compchat_model: str = "gpt-oss:120b-cloud"
    # Fallback fiscal year when neither the question nor the Tessot data
    # implies one. Tessot master data is anchored to FY2026.
    compchat_default_fiscal_year: int = 2026

    # --- CD&A report generator (Compensation Discussion & Analysis) -----
    # Local model used to narrate the CD&A report's data-driven sections.
    # Runs through the same Ollama endpoint (``ollama_base_url``). Numbers
    # are always deterministic (parsed from the uploaded workbook); the
    # model only writes connective prose grounded in those numbers plus the
    # bundled domain knowledge base — it never authors a figure. Defaults to
    # ``qwen3.5:9b`` (the tag pulled locally). Override with ``CDA_MODEL`` if
    # you pull a different tag. If the model is unreachable the generator
    # falls back to a deterministic narrative, so a running Ollama is
    # optional for the endpoint to succeed.
    cda_model: str = "qwen3.5:9b"
    # Ceiling on narration length per section (num_predict).
    cda_max_tokens: int = 500
    cda_temperature: float = 0.2

    # --- Logging ---------------------------------------------------------
    # Root log level. Accepts standard Python logging names.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- CORS ------------------------------------------------------------
    # Comma-separated list of allowed origins. Empty = no cross-origin access.
    # In production, set explicitly (e.g. "https://app.example.com").
    #
    # The ``NoDecode`` annotation tells pydantic-settings to skip its
    # default JSON-decode step for these fields, so a plain CSV (or
    # empty) string from .env reaches the ``_split_csv`` validator
    # below intact.
    cors_allow_origins: Annotated[list[str], NoDecode] = []
    cors_allow_credentials: bool = True
    cors_allow_methods: Annotated[list[str], NoDecode] = [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    cors_allow_headers: Annotated[list[str], NoDecode] = ["*"]

    # --- Rate limiting ---------------------------------------------------
    # ``memory://`` (per-process) is fine for single-instance deploys; use
    # ``redis://host:6379/0`` for multi-instance / production.
    rate_limit_storage_uri: str = "memory://"
    # Per-IP limits applied to the most-abused endpoints. slowapi syntax:
    # "<count>/<period>" where period is second / minute / hour / day.
    rate_limit_login: str = "5/15minutes"
    rate_limit_register: str = "10/hour"

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment == "production"

    # ``pydantic-settings`` defaults to JSON-decoding ``list[str]`` env
    # values. That fails on plain comma-separated strings (and on empty
    # strings) which are far more ergonomic in a ``.env`` file. This
    # ``mode="before"`` validator runs ahead of JSON parsing and turns
    # comma-separated input into a real list.
    @field_validator(
        "cors_allow_origins",
        "cors_allow_methods",
        "cors_allow_headers",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v

    # An empty REDIS_URL in .env should be treated as "not configured"
    # (the in-memory deny-list backend) rather than as the literal
    # empty string, which would later fail when handed to redis.from_url.
    @field_validator("redis_url", mode="before")
    @classmethod
    def _empty_string_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _refuse_insecure_jwt_secret_in_production(self) -> Settings:
        """Fail-fast in production if the dev JWT secret was not overridden."""
        if self.is_production and self.jwt_secret_key == _INSECURE_DEV_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value when "
                "ENVIRONMENT=production. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
