import re
from typing import List, Optional, Tuple
import uuid
from app.core.exceptions import EntityConflictError, EntityNotFoundError
from app.core.logging import logger
from app.domain.models.source import SourceType
from app.domain.schemas.source import SourceCreate, SourceUpdate
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.source_repo import SourceRepository


def slugify(text: str) -> str:
    """Generate a clean URL-friendly slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return re.sub(r"^-+|-+$", "", text)


class SourceService:
    """Business logic service managing content ingestion sources."""

    def __init__(self, repository: SourceRepository):
        self.repository = repository

    async def create_source(self, data: SourceCreate) -> Source:
        """Register a new source with duplicate validation and slug generation."""
        url_str = str(data.url).rstrip("/")
        existing_by_url = await self.repository.get_by_url(url_str)
        if existing_by_url:
            raise EntityConflictError(f"A source with URL '{url_str}' already exists (ID: {existing_by_url.id})")

        # Slug resolution
        candidate_slug = slugify(data.slug) if data.slug else slugify(data.name)
        if not candidate_slug:
            candidate_slug = f"source-{uuid.uuid4().hex[:8]}"

        existing_by_slug = await self.repository.get_by_slug(candidate_slug)
        if existing_by_slug:
            if data.slug:
                raise EntityConflictError(f"A source with slug '{candidate_slug}' already exists")
            # Auto-disambiguate slug if auto-generated
            candidate_slug = f"{candidate_slug}-{uuid.uuid4().hex[:6]}"

        source = Source(
            name=data.name,
            slug=candidate_slug,
            type=data.type.value if isinstance(data.type, SourceType) else str(data.type),
            url=url_str,
            enabled=data.enabled,
            language=data.language,
            reliability_score=data.reliability_score,
            fetch_interval_minutes=data.fetch_interval_minutes,
            config=data.configuration or {},
        )

        created = await self.repository.add(source)
        logger.info(f"Registered new source '{created.name}' [type={created.type}, id={created.id}]")
        return created

    async def get_source(self, source_id: uuid.UUID) -> Source:
        """Retrieve a source by ID or raise EntityNotFoundError."""
        source = await self.repository.get_by_id(source_id)
        if not source:
            raise EntityNotFoundError("Source", source_id)
        return source

    async def list_sources(
        self,
        source_type: Optional[SourceType] = None,
        enabled: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Source], int]:
        """List paginated sources with optional filtering."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        skip = (page - 1) * page_size

        type_filter = source_type.value if source_type else None
        items, total = await self.repository.list_sources(
            source_type=type_filter,
            enabled=enabled,
            skip=skip,
            limit=page_size,
        )
        return items, total

    async def update_source(self, source_id: uuid.UUID, data: SourceUpdate) -> Source:
        """Apply partial updates to a source after verifying business constraints."""
        source = await self.get_source(source_id)

        # Check URL uniqueness if updated
        if data.url is not None:
            url_str = str(data.url).rstrip("/")
            existing = await self.repository.get_by_url(url_str)
            if existing and existing.id != source.id:
                raise EntityConflictError(f"A source with URL '{url_str}' already exists (ID: {existing.id})")
            source.url = url_str

        if data.name is not None:
            source.name = data.name

        if data.type is not None:
            source.type = data.type.value if isinstance(data.type, SourceType) else str(data.type)

        if data.enabled is not None:
            source.enabled = data.enabled

        if data.language is not None:
            source.language = data.language

        if data.reliability_score is not None:
            source.reliability_score = data.reliability_score

        if data.fetch_interval_minutes is not None:
            source.fetch_interval_minutes = data.fetch_interval_minutes

        if data.configuration is not None:
            source.config = data.configuration

        updated = await self.repository.update(source)
        logger.info(f"Updated source '{updated.name}' [id={updated.id}]")
        return updated

    async def enable_source(self, source_id: uuid.UUID) -> Source:
        """Enable an ingestion source."""
        source = await self.get_source(source_id)
        if not source.enabled:
            source.enabled = True
            await self.repository.update(source)
            logger.info(f"Enabled source '{source.name}' [id={source.id}]")
        return source

    async def disable_source(self, source_id: uuid.UUID) -> Source:
        """Disable an ingestion source."""
        source = await self.get_source(source_id)
        if source.enabled:
            source.enabled = False
            await self.repository.update(source)
            logger.info(f"Disabled source '{source.name}' [id={source.id}]")
        return source

    async def delete_source(self, source_id: uuid.UUID) -> None:
        """Delete an existing source."""
        source = await self.get_source(source_id)
        await self.repository.delete(source)
        logger.info(f"Deleted source '{source.name}' [id={source.id}]")

    async def seed_default_sources(self) -> List[Source]:
        """Seed verified baseline sources across supported source types idempotently."""
        baseline_definitions = [
            SourceCreate(
                name="ArXiv AI Research",
                slug="arxiv-ai",
                type=SourceType.ARXIV,
                url="https://export.arxiv.org/api/query",
                reliability_score=0.95,
                fetch_interval_minutes=360,
                configuration={
                    "api_url": "https://export.arxiv.org/api/query",
                    "search_query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:cs.NE OR cat:stat.ML",
                    "categories": ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "stat.ML"],
                    "max_results_per_page": 25,
                    "max_pages": 1,
                    "sort_by": "submittedDate",
                    "sort_order": "descending",
                    "rate_limit_delay_seconds": 3.0,
                },
            ),
            SourceCreate(
                name="MIT Technology Review AI",
                slug="mit-tech-review-ai",
                type=SourceType.RSS,
                url="https://www.technologyreview.com/topic/artificial-intelligence/feed",
                reliability_score=0.90,
                fetch_interval_minutes=60,
                configuration={"tags": ["AI", "Machine Learning"]},
            ),
            SourceCreate(
                name="OpenAI News & Announcements",
                slug="openai-news",
                type=SourceType.WEB,
                url="https://openai.com/news",
                reliability_score=0.98,
                fetch_interval_minutes=120,
                configuration={"feed_url": "https://openai.com/news/rss.xml", "organization": "OpenAI", "parser_type": "openai"},
            ),
            SourceCreate(
                name="Anthropic News & Research",
                slug="anthropic-news",
                type=SourceType.WEB,
                url="https://www.anthropic.com/news",
                reliability_score=0.98,
                fetch_interval_minutes=120,
                configuration={"organization": "Anthropic", "parser_type": "anthropic"},
            ),
            SourceCreate(
                name="Google DeepMind Research & Blog",
                slug="deepmind-blog",
                type=SourceType.RSS,
                url="https://deepmind.google/blog/rss.xml",
                reliability_score=0.98,
                fetch_interval_minutes=120,
                configuration={"feed_url": "https://deepmind.google/blog/rss.xml", "organization": "Google DeepMind"},
            ),
            SourceCreate(
                name="Two Minute Papers",
                slug="two-minute-papers",
                type=SourceType.YOUTUBE,
                url="https://www.youtube.com/channel/UCbfYPyITQ-7l4upoX8nvctg",
                reliability_score=0.90,
                fetch_interval_minutes=360,
                configuration={"channel_id": "UCbfYPyITQ-7l4upoX8nvctg", "max_results": 10},
            ),
            SourceCreate(
                name="Yannic Kilcher",
                slug="yannic-kilcher",
                type=SourceType.YOUTUBE,
                url="https://www.youtube.com/channel/UCEBm0xLn28l-H0V586dJ7qw",
                reliability_score=0.90,
                fetch_interval_minutes=360,
                configuration={"channel_id": "UCEBm0xLn28l-H0V586dJ7qw", "max_results": 10},
            ),
            SourceCreate(
                name="AI Explained",
                slug="ai-explained",
                type=SourceType.YOUTUBE,
                url="https://www.youtube.com/channel/UCNJ1Ymd5yFuUPtn21xtRbbw",
                reliability_score=0.90,
                fetch_interval_minutes=360,
                configuration={"channel_id": "UCNJ1Ymd5yFuUPtn21xtRbbw", "max_results": 10},
            ),
            SourceCreate(
                name="GitHub Trending Python",
                slug="github-trending-python",
                type=SourceType.GITHUB,
                url="https://github.com/trending/python?since=daily",
                reliability_score=0.85,
                fetch_interval_minutes=360,
                configuration={"language": "python", "since": "daily"},
            ),
            SourceCreate(
                name="Hacker News AI & Tech",
                slug="hacker-news",
                type=SourceType.WEB,
                url="https://news.ycombinator.com",
                reliability_score=0.88,
                fetch_interval_minutes=30,
                configuration={
                    "adapter_type": "hacker_news",
                    "api_base_url": "https://hacker-news.firebaseio.com/v0",
                    "feeds": ["topstories", "beststories"],
                    "max_items_per_feed": 50,
                    "filter_ai_only": True,
                    "min_score": 5,
                },
            ),
        ]

        seeded: List[Source] = []
        for defn in baseline_definitions:
            clean_url = str(defn.url).rstrip("/")
            existing = await self.repository.get_by_url(clean_url)
            if not existing and defn.slug:
                existing = await self.repository.get_by_slug(defn.slug)

            if existing:
                existing.url = clean_url
                # Preserve existing administrator configuration, backfilling only missing default keys
                if defn.configuration:
                    current_config = existing.config if isinstance(existing.config, dict) else {}
                    existing.config = {**defn.configuration, **current_config}
                # Preserve existing reliability score adjustments if already set on the existing record
                if existing.reliability_score is None and defn.reliability_score is not None:
                    existing.reliability_score = defn.reliability_score
                # Intentionally preserved without modification: existing.enabled, existing.slug, existing.id
                await self.repository.update(existing)
                seeded.append(existing)
            else:
                created = await self.create_source(defn)
                seeded.append(created)

        logger.info(f"Verified default sources: {len(seeded)} sources active in registry")
        return seeded
