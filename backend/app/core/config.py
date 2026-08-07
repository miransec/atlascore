"""
Application configuration with startup validation.

All secrets are injected from environment variables.
Startup fails hard if REPLACE_* placeholder values are present,
if required secrets are too short, or if production mode is insecure.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_PATTERN = re.compile(r"REPLACE_", re.IGNORECASE)
_MIN_SECRET_BYTES = 32  # 256-bit minimum for all secret keys


def _reject_placeholder(value: str, field_name: str) -> str:
    """Raise ValueError if value is still a REPLACE_* placeholder."""
    if _PLACEHOLDER_PATTERN.search(value):
        raise ValueError(
            f"{field_name} contains a REPLACE_* placeholder value. "
            "Replace it with a real secret before starting the application."
        )
    return value


def _require_min_length(value: str, field_name: str, min_bytes: int = _MIN_SECRET_BYTES) -> str:
    if len(value.encode()) < min_bytes:
        raise ValueError(
            f"{field_name} is too short ({len(value.encode())} bytes). "
            f"Minimum {min_bytes} bytes required."
        )
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # Application
    # -----------------------------------------------------------------------
    APP_ENV: Literal["development", "test", "staging", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    APP_NAME: str = "AtlasCore"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"
    REQUEST_ID_HEADER: str = "X-Request-ID"

    # -----------------------------------------------------------------------
    # Backend
    # -----------------------------------------------------------------------
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8100
    BACKEND_WORKERS: int = 1
    BACKEND_RELOAD: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:3100"
    SECURE_COOKIES: bool = False

    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://atlascore:change_in_production@localhost:5433/atlascore"
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_ECHO: bool = False

    DATABASE_SYNC_URL: str = "postgresql://atlascore:change_in_production@localhost:5433/atlascore"

    # -----------------------------------------------------------------------
    # Redis
    # -----------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6380/0"
    REDIS_KEY_PREFIX_SESSION: str = "sess:"
    REDIS_KEY_PREFIX_LOCK: str = "lock:"
    REDIS_KEY_PREFIX_RATE: str = "rate:"
    REDIS_KEY_PREFIX_CACHE: str = "cache:"
    REDIS_DEFAULT_TTL_SECONDS: int = 3600
    TEST_REDIS_KEY_PREFIX: str = "test:"

    # -----------------------------------------------------------------------
    # Auth secrets — REQUIRED; startup fails on REPLACE_* or short values
    # -----------------------------------------------------------------------
    JWT_SECRET_KEY: str = Field(
        default="REPLACE_WITH_SECURE_RANDOM_KEY_64_BYTES",
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_TOKEN_PEPPER: str = Field(
        default="REPLACE_WITH_SECURE_RANDOM_KEY",
    )

    ARGON2_PEPPER: str = Field(
        default="REPLACE_WITH_SECURE_RANDOM_KEY",
    )
    ARGON2_PEPPER_VERSION: int = 1

    CSRF_SECRET: str = Field(
        default="REPLACE_WITH_SECURE_RANDOM_KEY",
    )

    PRE_AUTH_SESSION_PEPPER: str = Field(
        default="REPLACE_WITH_SECURE_RANDOM_KEY",
    )
    PRE_AUTH_SESSION_EXPIRE_MINUTES: int = 5

    # Phase 1B secrets — validated at startup, same rules as Phase 1A secrets
    API_KEY_PEPPER: str = Field(default="REPLACE_WITH_SECURE_RANDOM_KEY")
    API_KEY_PREFIX_LENGTH: int = 8
    INVITATION_TOKEN_PEPPER: str = Field(default="REPLACE_WITH_SECURE_RANDOM_KEY")

    # Phase 2+ (validated when connectors module is active)
    ENCRYPTION_KEY: str = Field(default="REPLACE_WITH_FERNET_KEY")
    ENCRYPTION_KEY_VERSION: int = 1

    # -----------------------------------------------------------------------
    # Rate limiting
    # -----------------------------------------------------------------------
    RATE_LIMIT_DEFAULT: int = 60
    RATE_LIMIT_AUTH: int = 10
    RATE_LIMIT_WRITE: int = 20

    # -----------------------------------------------------------------------
    # AI Providers
    # -----------------------------------------------------------------------
    LLM_PROVIDER: str = "mock"
    EMBEDDING_PROVIDER: str = "mock"
    RERANKING_PROVIDER: str = "mock"
    MODERATION_PROVIDER: str = "mock"

    OPENAI_API_KEY: str = ""
    OPENAI_DEFAULT_CHAT_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_MAX_RETRIES: int = 3
    OPENAI_TIMEOUT: int = 60
    # OpenAI-compatible chat completions URL. Override for gateways that speak
    # the OpenAI Chat Completions API (e.g. a private proxy). Never log keys.
    OPENAI_BASE_URL: str = "https://api.openai.com/v1/chat/completions"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_DEFAULT_CHAT_MODEL: str = "claude-opus-4-5"
    ANTHROPIC_MAX_RETRIES: int = 3
    ANTHROPIC_TIMEOUT: int = 60

    GEMINI_API_KEY: str = ""
    GEMINI_DEFAULT_CHAT_MODEL: str = "gemini-2.0-flash"
    GEMINI_TIMEOUT: int = 60

    COHERE_API_KEY: str = ""
    COHERE_RERANKING_MODEL: str = "rerank-english-v3.0"

    # -----------------------------------------------------------------------
    # Model budgets
    # -----------------------------------------------------------------------
    DEFAULT_MAX_MODEL_CALLS: int = 20
    DEFAULT_MAX_TOOL_CALLS: int = 30
    DEFAULT_MAX_STEPS: int = 50
    DEFAULT_COST_BUDGET_USD: float = 1.00
    DEFAULT_TIMEOUT_SECONDS: int = 300

    # -----------------------------------------------------------------------
    # Knowledge system — Phase 2A
    # -----------------------------------------------------------------------
    # Root directory for BlobStore storage.
    # Must be an absolute path on the server filesystem.
    # The application creates it on startup if it does not exist.
    # SECURITY: Never expose this path to clients; server-generated keys only.
    KNOWLEDGE_STORAGE_ROOT: str = "/tmp/atlascore_knowledge"

    # Maximum upload size in bytes.  Default: 50 MiB.
    # This is also enforced in the BlobStore layer independently of this setting.
    KNOWLEDGE_MAX_UPLOAD_BYTES: int = 52_428_800

    CHUNK_SIZE_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64
    EMBEDDING_DIMENSIONS: int = 1536
    RETRIEVAL_TOP_K: int = 10
    RERANKING_TOP_K: int = 5
    RETRIEVAL_MIN_CONFIDENCE: float = 0.3
    MAX_DOCUMENT_SIZE_BYTES: int = 52_428_800

    # -----------------------------------------------------------------------
    # Phase 2C — Grounded answering
    # -----------------------------------------------------------------------
    ANSWER_PROVIDER: str = "deterministic-test"
    # "deterministic-test" — no network, no API key (for testing/CI).
    # "openai"             — requires OPENAI_API_KEY + OPENAI_DEFAULT_CHAT_MODEL.
    # "anthropic"          — requires ANTHROPIC_API_KEY + ANTHROPIC_DEFAULT_CHAT_MODEL.

    ANSWER_MAX_EVIDENCE_ITEMS: int = 10
    # Maximum evidence items passed to the AnswerProvider prompt context.

    ANSWER_MAX_CHARS_PER_CHUNK: int = 1500
    # Maximum characters of each evidence chunk included in the provider prompt.
    # Provenance is always preserved; only the provider input representation is bounded.

    ANSWER_MIN_HYBRID_SCORE: float = 0.0
    # Minimum retrieval hybrid score to include a chunk as evidence.

    ANSWER_REQUIRE_MEDIUM_BAND: bool = True
    # If True, LOW evidence band triggers abstention (ABSTAIN_WEAK_EVIDENCE).
    # Set False in development to allow answers with weaker evidence.

    ANSWER_MAX_EXCERPT_CHARS: int = 200
    # Maximum characters in the citation excerpt returned in the API response.

    ANSWER_DEMO_MODE: bool = False
    # When True, forces DeterministicTestAnswerProvider regardless of ANSWER_PROVIDER.
    # Useful for staging environments where real LLM credentials are not available.
    # Real provider is NEVER used when this is True.

    WEB_INGESTION_ALLOWED_DOMAINS: str = ""
    WEB_INGESTION_TIMEOUT: int = 15
    WEB_INGESTION_MAX_RESPONSE_BYTES: int = 5_242_880

    # -----------------------------------------------------------------------
    # Audit
    # -----------------------------------------------------------------------
    AUDIT_LOG_RETENTION_DAYS: int = 0
    AUDIT_EXPORT_MAX_ROWS: int = 100_000

    # -----------------------------------------------------------------------
    # Observability
    # -----------------------------------------------------------------------
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "atlascore-backend"
    COST_TRACKING_ENABLED: bool = True
    COST_ALERT_THRESHOLD_USD: float = 10.00

    # -----------------------------------------------------------------------
    # Development / testing
    # -----------------------------------------------------------------------
    TEST_DATABASE_URL: str = (
        "postgresql+asyncpg://atlascore:change_in_production@localhost:5433/atlascore_test"
    )
    FAKER_SEED: int = 42

    # -----------------------------------------------------------------------
    # Computed properties
    # -----------------------------------------------------------------------
    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def ENVIRONMENT(self) -> str:
        """Backward-compatible alias for older deployment/test configuration."""
        return self.APP_ENV

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    @field_validator("JWT_SECRET_KEY", mode="before")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        _reject_placeholder(v, "JWT_SECRET_KEY")
        _require_min_length(v, "JWT_SECRET_KEY", 64)
        return v

    @field_validator("REFRESH_TOKEN_PEPPER", mode="before")
    @classmethod
    def validate_refresh_pepper(cls, v: str) -> str:
        _reject_placeholder(v, "REFRESH_TOKEN_PEPPER")
        _require_min_length(v, "REFRESH_TOKEN_PEPPER")
        return v

    @field_validator("ARGON2_PEPPER", mode="before")
    @classmethod
    def validate_argon2_pepper(cls, v: str) -> str:
        _reject_placeholder(v, "ARGON2_PEPPER")
        _require_min_length(v, "ARGON2_PEPPER")
        return v

    @field_validator("CSRF_SECRET", mode="before")
    @classmethod
    def validate_csrf_secret(cls, v: str) -> str:
        _reject_placeholder(v, "CSRF_SECRET")
        _require_min_length(v, "CSRF_SECRET")
        return v

    @field_validator("PRE_AUTH_SESSION_PEPPER", mode="before")
    @classmethod
    def validate_pre_auth_pepper(cls, v: str) -> str:
        _reject_placeholder(v, "PRE_AUTH_SESSION_PEPPER")
        _require_min_length(v, "PRE_AUTH_SESSION_PEPPER")
        return v

    @field_validator("API_KEY_PEPPER", mode="before")
    @classmethod
    def validate_api_key_pepper(cls, v: str) -> str:
        _reject_placeholder(v, "API_KEY_PEPPER")
        _require_min_length(v, "API_KEY_PEPPER")
        return v

    @field_validator("INVITATION_TOKEN_PEPPER", mode="before")
    @classmethod
    def validate_invitation_token_pepper(cls, v: str) -> str:
        _reject_placeholder(v, "INVITATION_TOKEN_PEPPER")
        _require_min_length(v, "INVITATION_TOKEN_PEPPER")
        return v

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        """Reject insecure combinations in production."""
        if self.is_production:
            # Wildcard CORS + credentialed requests is forbidden.
            for origin in self.allowed_origins_list:
                if origin == "*":
                    raise ValueError(
                        "ALLOWED_ORIGINS wildcard '*' is forbidden when credentialed requests "
                        "are enabled. List explicit origins."
                    )
            if not self.SECURE_COOKIES:
                raise ValueError("SECURE_COOKIES must be true in production.")
        return self

    @model_validator(mode="after")
    def validate_chunk_settings(self) -> Settings:
        """Chunk overlap must be strictly less than chunk size."""
        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_SIZE_TOKENS:
            raise ValueError(
                f"CHUNK_OVERLAP_TOKENS ({self.CHUNK_OVERLAP_TOKENS}) must be "
                f"strictly less than CHUNK_SIZE_TOKENS ({self.CHUNK_SIZE_TOKENS})."
            )
        if self.CHUNK_SIZE_TOKENS <= 0:
            raise ValueError("CHUNK_SIZE_TOKENS must be positive.")
        if self.CHUNK_OVERLAP_TOKENS < 0:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be non-negative.")
        return self


def get_settings() -> Settings:
    """Return the application settings singleton.

    Call this from FastAPI Depends() — never import settings directly
    in module scope so tests can override via dependency injection.
    """
    return _settings


# Module-level singleton — instantiated once at import time.
# Startup validation runs here; a misconfigured application fails immediately.
_settings = Settings()
