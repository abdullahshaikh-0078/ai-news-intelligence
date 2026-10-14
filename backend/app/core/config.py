from pathlib import Path
from typing import List, Optional, Union
import json
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Determine project root and authoritative .env location
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ROOT_ENV_FILE = PROJECT_ROOT / ".env" if (PROJECT_ROOT / ".env").is_file() else (BACKEND_DIR / ".env")


class Settings(BaseSettings):
    """Application settings with unified environment-based configuration."""

    # Project metadata
    APP_NAME: str = "AI News Intelligence Platform"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    API_BASE_URL: str = "http://127.0.0.1:8000"

    @property
    def public_api_v1_url(self) -> str:
        """Canonical public backend API base URL with /api/v1 prefix."""
        import os
        effective_base = self.API_BASE_URL
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url and ("127.0.0.1" in effective_base or "localhost" in effective_base):
            effective_base = render_url
        base = effective_base.rstrip("/")
        if base.endswith("/api/v1"):
            return base
        api_prefix = self.API_V1_STR.strip("/")
        return f"{base}/{api_prefix}"

    @property
    def is_production_like(self) -> bool:
        """Return True if the runtime environment is production, staging, or hosted on Render."""
        import os
        return (
            self.ENVIRONMENT in ("production", "staging")
            or bool(os.environ.get("RENDER"))
            or bool(os.environ.get("RENDER_EXTERNAL_URL"))
            or bool(os.environ.get("RENDER_SERVICE_ID"))
        )

    @property
    def effective_newsletter_base_url(self) -> str:
        """Effective frontend portfolio URL for newsletter action and preference links."""
        import os
        if self.NEWSLETTER_BASE_URL and "127.0.0.1" not in self.NEWSLETTER_BASE_URL and "localhost" not in self.NEWSLETTER_BASE_URL:
            return self.NEWSLETTER_BASE_URL
        if self.is_production_like:
            return "https://abdullah-shaikh-portfolio.vercel.app"
        return self.NEWSLETTER_BASE_URL

    # Server binding
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Production frontend origins guaranteed to be allowed
    PRODUCTION_ORIGINS: List[str] = [
        "https://abdullah-shaikh-portfolio.vercel.app",
        "https://abdullah-ai-systems-portfolio.vercel.app",
    ]

    # CORS configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://abdullah-shaikh-portfolio.vercel.app",
        "https://abdullah-ai-systems-portfolio.vercel.app",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        origins: List[str] = []
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                try:
                    origins = json.loads(v)
                except Exception:
                    origins = []
            else:
                origins = [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            origins = list(v)

        canonical_production = [
            "https://abdullah-shaikh-portfolio.vercel.app",
            "https://abdullah-ai-systems-portfolio.vercel.app",
        ]
        cleaned: List[str] = []
        for origin in origins:
            if isinstance(origin, str) and origin.strip():
                normalized = origin.strip().rstrip("/")
                if normalized and normalized not in cleaned:
                    cleaned.append(normalized)

        for prod in canonical_production:
            if prod not in cleaned:
                cleaned.append(prod)

        return cleaned

    # Canonical Database Configuration (Single Source of Truth, compatible with Neon PostgreSQL)
    DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5433/ai_news_intel"
    DIRECT_DATABASE_URL: Optional[str] = None
    TEST_DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5433/ai_news_intel_test"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: float = 30.0
    DB_POOL_RECYCLE: int = 300  # 5 minutes, matching Neon auto-suspend window
    DB_ECHO: bool = False

    @property
    def async_database_url(self) -> str:
        """
        Derive asyncpg connection URL (postgresql+asyncpg://) compatible with asyncpg.
        Normalizes postgres:// and postgresql:// protocols and translates query parameter sslmode -> ssl.
        """
        url = self.DATABASE_URL
        if not url:
            return url
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        elif url.startswith("postgresql+psycopg://"):
            url = "postgresql+asyncpg://" + url[len("postgresql+psycopg://"):]
        elif url.startswith("postgresql+psycopg2://"):
            url = "postgresql+asyncpg://" + url[len("postgresql+psycopg2://"):]

        # Translate sslmode to ssl for asyncpg compatibility (preventing 'unexpected keyword argument sslmode')
        url = url.replace("sslmode=require", "ssl=require")
        url = url.replace("sslmode=verify-full", "ssl=verify-full")
        url = url.replace("sslmode=verify-ca", "ssl=verify-ca")
        url = url.replace("sslmode=prefer", "ssl=prefer")
        url = url.replace("sslmode=disable", "ssl=disable")

        # Strip libpq-specific channel_binding parameter not supported by asyncpg
        import re
        url = re.sub(r"([?&])channel_binding=[^&]*(&|$)", r"\1", url)
        url = re.sub(r"[?&]$", "", url)
        if "?" not in url and "&" in url:
            url = url.replace("&", "?", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """Derive standard psycopg/sync connection URL for Alembic or sync tools."""
        url = self.DATABASE_URL
        if not url:
            return url
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]

        # Ensure libpq/psycopg sync drivers retain sslmode parameter
        url = url.replace("ssl=require", "sslmode=require")
        url = url.replace("ssl=verify-full", "sslmode=verify-full")
        url = url.replace("ssl=verify-ca", "sslmode=verify-ca")
        return url

    @property
    def migration_database_url(self) -> str:
        """
        Derive direct connection URL for Alembic migrations.
        Uses DIRECT_DATABASE_URL if configured, or automatically strips '-pooler' from Neon host
        to bypass PgBouncer transaction pooling during DDL migrations.
        """
        raw = self.DIRECT_DATABASE_URL or self.DATABASE_URL
        if not raw:
            return self.async_database_url
        if not self.DIRECT_DATABASE_URL and "-pooler." in raw:
            raw = raw.replace("-pooler.", ".")

        url = raw
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        elif url.startswith("postgresql+psycopg://"):
            url = "postgresql+asyncpg://" + url[len("postgresql+psycopg://"):]
        elif url.startswith("postgresql+psycopg2://"):
            url = "postgresql+asyncpg://" + url[len("postgresql+psycopg2://"):]

        url = url.replace("sslmode=require", "ssl=require")
        url = url.replace("sslmode=verify-full", "ssl=verify-full")
        url = url.replace("sslmode=verify-ca", "ssl=verify-ca")

        # Strip libpq-specific channel_binding parameter not supported by asyncpg
        import re
        url = re.sub(r"([?&])channel_binding=[^&]*(&|$)", r"\1", url)
        url = re.sub(r"[?&]$", "", url)
        if "?" not in url and "&" in url:
            url = url.replace("&", "?", 1)
        return url

    # AI / LLM Configuration (Google Gemini)
    GEMINI_API_KEY: str | None = None
    GEMINI_CHAT_MODEL: str = "gemini-3.6-flash"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    GEMINI_EMBEDDING_DIMENSIONS: int = 1536
    AI_PROCESSING_TIMEOUT: float = 30.0
    AI_PROCESSING_MAX_RETRIES: int = 3
    AI_PROCESSING_BATCH_SIZE: int = 50
    AI_PROCESSING_CONCURRENCY: int = 5

    # Semantic Deduplication Configuration (Phase 10)
    SEMANTIC_DEDUP_SIMILARITY_THRESHOLD: float = 0.82
    SEMANTIC_DEDUP_HIGH_CONFIDENCE_THRESHOLD: float = 0.92
    SEMANTIC_DEDUP_BATCH_SIZE: int = 50

    # Story Clustering & Synthesis Configuration (Phase 11)
    STORY_CLUSTER_SIMILARITY_THRESHOLD: float = 0.85
    STORY_CLUSTER_WINDOW_HOURS: int = 72
    STORY_CLUSTER_BATCH_SIZE: int = 50

    # Story Ranking Configuration (Phase 12)
    STORY_RANK_RELEVANCE_WEIGHT: float = 0.25
    STORY_RANK_AUTHORITY_WEIGHT: float = 0.20
    STORY_RANK_RECENCY_WEIGHT: float = 0.25
    STORY_RANK_COVERAGE_WEIGHT: float = 0.15
    STORY_RANK_DIVERSITY_WEIGHT: float = 0.05
    STORY_RANK_ENGAGEMENT_WEIGHT: float = 0.10
    STORY_RANK_RECENCY_HALF_LIFE_HOURS: float = 48.0
    STORY_RANK_BATCH_SIZE: int = 50

    # Story Curation Configuration (Phase 13)
    CURATION_DEFAULT_LIMIT: int = 20
    CURATION_MIN_SCORE: float = 0.35
    CURATION_MAX_PER_TOPIC: int = 3
    CURATION_MAX_PER_SOURCE: int = 3
    CURATION_DIVERSITY_ENABLED: bool = True

    # Pipeline Orchestration Configuration (Phase 16)
    PIPELINE_MAX_ITEMS_PER_RUN: int = 20
    PIPELINE_MAX_AI_ANALYSES_PER_RUN: int = 10
    PIPELINE_MAX_EMBEDDINGS_PER_RUN: int = 10
    PIPELINE_MAX_STORIES_PER_CURATED_FEED: int = 10
    PIPELINE_ON_DEMAND_EMBEDDING_ENABLED: bool = True
    PIPELINE_MIN_RELEVANCE_FOR_EMBEDDING: float = 0.4

    ANTHROPIC_API_KEY: str | None = None

    # Email / Resend (Phase 17)
    RESEND_API_KEY: str | None = None
    EMAIL_FROM: str = "AI News <onboarding@resend.dev>"
    EMAIL_REPLY_TO: str | None = None
    NEWSLETTER_BASE_URL: str = "http://127.0.0.1:5500"
    DIGEST_MIN_STORIES: int = 5
    DIGEST_MAX_STORIES: int = 10
    DELIVERY_CLAIM_TIMEOUT_SECONDS: int = 900

    # Retention / Safe Cleanup Configuration
    NEWS_RETENTION_DAYS: int = 90
    DELIVERY_RECORD_RETENTION_DAYS: int = 180
    RETENTION_BATCH_SIZE: int = 500

    # Due Window & Scheduling Defaults
    DELIVERY_DUE_WINDOW_MINUTES: int = 60
    DEFAULT_DELIVERY_TIME: str = "08:00"
    DEFAULT_TIMEZONE: str = "Asia/Kolkata"

    # Resend Webhooks & Dispatch Configuration (Phase 18)
    RESEND_WEBHOOK_SECRET: str | None = None
    DISPATCH_SECRET_TOKEN: str | None = None
    DISPATCH_CRON_ENABLED: bool = False
    DISPATCH_SCHEDULE_HOUR_UTC: int = 3
    DISPATCH_SCHEDULE_MINUTE_UTC: int = 30
    DISPATCH_SCHEDULER_FREQUENCY: str = "0 * * * *"  # Recommended hourly scheduler
    NEWS_COLLECTION_SCHEDULE_UTC: str = "03:30"

    # External Service Keys
    YOUTUBE_API_KEY: str | None = None

    # Ingestion Configuration
    INGESTION_HTTP_TIMEOUT: float = 15.0
    INGESTION_MAX_RETRIES: int = 3
    INGESTION_BACKOFF_FACTOR: float = 0.5
    INGESTION_USER_AGENT: str = "AINewsIntelligence/1.0 (+https://ainewsplatform.local/bot)"
    INGESTION_MAX_RESPONSE_BYTES: int = 10485760  # 10 MB

    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
