from datetime import datetime, timezone
import time
from typing import Dict, List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityNotFoundError
from app.core.logging import logger
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import (
    IngestionSummaryResponse,
    SourceIngestionResult,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.adapters.arxiv_adapter import ArXivAdapter
from app.ingestion.adapters.hackernews_adapter import HackerNewsAdapter
from app.ingestion.adapters.rss_adapter import RSSAdapter
from app.ingestion.adapters.web_adapter import OfficialWebAdapter
from app.ingestion.adapters.youtube_adapter import YouTubeAdapter
from app.ingestion.base import BaseIngestionAdapter


class IngestionOrchestrator:
    """Central orchestrator managing source ingestion, adapter dispatch, and idempotent persistence."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.source_repo = SourceRepository(session)
        self.content_repo = ContentRepository(session)

        # Register available adapters by key and source type
        self.adapters: Dict[str, BaseIngestionAdapter] = {
            SourceType.RSS.value: RSSAdapter(),
            SourceType.WEB.value: OfficialWebAdapter(),
            SourceType.ARXIV.value: ArXivAdapter(),
            SourceType.YOUTUBE.value: YouTubeAdapter(),
            "HACKER_NEWS": HackerNewsAdapter(),
            "YOUTUBE": YouTubeAdapter(),
        }

    async def ingest_source(self, source: Source) -> SourceIngestionResult:
        """Execute ingestion pipeline for a single source entity."""
        start_time = time.time()
        result = SourceIngestionResult(
            source_id=source.id,
            source_name=source.name,
            source_type=source.type,
            status="SUCCESS",
        )

        config = source.config if isinstance(source.config, dict) else {}
        adapter_key = config.get("adapter_type")
        adapter = None
        if adapter_key:
            adapter = self.adapters.get(adapter_key.upper())
        if not adapter:
            adapter = self.adapters.get(source.type)

        if not adapter:
            result.status = "FAILED"
            result.errors.append(f"No adapter registered for source type '{source.type}' or adapter '{adapter_key}'")
            result.duration_seconds = round(time.time() - start_time, 3)
            return result

        logger.info(f"Starting ingestion for source '{source.name}' [type={source.type}, url={source.url}]")

        try:
            articles = await adapter.fetch_and_parse(source)
            result.entries_fetched = len(articles)

            for article in articles:
                try:
                    # Idempotency lookup: check by canonical URL first
                    existing = await self.content_repo.get_by_canonical_url(article.canonical_url)

                    # If not found by URL and external_id is present, check by (source_id, external_id)
                    if not existing and article.external_id:
                        existing = await self.content_repo.get_by_external_id(
                            source.id, article.external_id
                        )

                    # Cross-source external URL collision guard:
                    # If an existing item with the same canonical URL belongs to a DIFFERENT source,
                    # resolve this community/secondary item to its own submission URL
                    if existing and existing.source_id != source.id:
                        fallback_url = article.metadata_json.get("hn_url") or (
                            f"https://news.ycombinator.com/item?id={article.external_id.replace('hn:', '')}"
                            if article.external_id and "hn:" in article.external_id
                            else None
                        )
                        if fallback_url:
                            article.canonical_url = fallback_url
                            existing = await self.content_repo.get_by_canonical_url(fallback_url)
                            if not existing and article.external_id:
                                existing = await self.content_repo.get_by_external_id(
                                    source.id, article.external_id
                                )

                    if existing:
                        # Check dynamic community/video metrics changes (score, comments, views, likes)
                        metrics_changed = False
                        if isinstance(existing.metadata_json, dict) and isinstance(article.metadata_json, dict):
                            metrics_changed = (
                                article.metadata_json.get("score") != existing.metadata_json.get("score")
                                or article.metadata_json.get("comments") != existing.metadata_json.get("comments")
                                or article.metadata_json.get("view_count") != existing.metadata_json.get("view_count")
                                or article.metadata_json.get("like_count") != existing.metadata_json.get("like_count")
                                or article.metadata_json.get("comment_count") != existing.metadata_json.get("comment_count")
                            )

                        if existing.content_hash == article.content_hash and not metrics_changed:
                            # Content and metrics identical; skip to preserve idempotency
                            result.entries_skipped += 1
                        else:
                            # Content or community metrics have updated; apply modifications
                            existing.title = article.title
                            existing.summary = article.summary
                            existing.raw_content = article.raw_content
                            existing.author = article.author or existing.author
                            existing.content_hash = article.content_hash
                            existing.fetched_at = article.fetched_at
                            existing.metadata_json = article.metadata_json
                            existing.content_type = article.content_type.value if hasattr(article.content_type, "value") else str(article.content_type)
                            if article.thumbnail_url:
                                existing.thumbnail_url = article.thumbnail_url
                            await self.content_repo.update(existing)
                            result.entries_updated += 1
                    else:
                        # Insert new content item
                        item = ContentItem(
                            source_id=article.source_id,
                            canonical_url=article.canonical_url,
                            title=article.title,
                            summary=article.summary,
                            raw_content=article.raw_content,
                            author=article.author,
                            published_at=article.published_at,
                            fetched_at=article.fetched_at,
                            external_id=article.external_id,
                            language=article.language,
                            content_hash=article.content_hash,
                            content_type=article.content_type.value if hasattr(article.content_type, "value") else str(article.content_type),
                            thumbnail_url=article.thumbnail_url,
                            processing_state="RAW",
                            metadata_json=article.metadata_json,
                        )
                        await self.content_repo.add(item)
                        result.entries_inserted += 1

                except Exception as exc:
                    logger.warning(
                        f"Failed to persist article '{article.title[:30]}' from source '{source.name}': {str(exc)}"
                    )
                    result.errors.append(f"Article persistence error ({article.canonical_url}): {str(exc)}")

            # Update source last_fetched_at timestamp
            source.last_fetched_at = datetime.now(timezone.utc)
            await self.source_repo.update(source)

            if result.errors:
                result.status = "PARTIAL" if (result.entries_inserted > 0 or result.entries_skipped > 0) else "FAILED"

            logger.info(
                f"Ingestion completed for '{source.name}': fetched={result.entries_fetched}, "
                f"inserted={result.entries_inserted}, updated={result.entries_updated}, skipped={result.entries_skipped}"
            )

        except Exception as exc:
            logger.error(f"Ingestion failure for source '{source.name}': {str(exc)}")
            result.status = "FAILED"
            result.errors.append(str(exc))

        result.duration_seconds = round(time.time() - start_time, 3)
        return result

    async def ingest_source_by_id(self, source_id: uuid.UUID) -> SourceIngestionResult:
        """Trigger ingestion for a specific registered source by UUID."""
        source = await self.source_repo.get_by_id(source_id)
        if not source:
            raise EntityNotFoundError("Source", source_id)
        return await self.ingest_source(source)

    async def ingest_all_rss_sources(self) -> IngestionSummaryResponse:
        """Ingest all enabled RSS sources from the registry with strict fault isolation."""
        overall_start = time.time()
        rss_sources = await self.source_repo.list_by_type("RSS")
        enabled_sources = [s for s in rss_sources if s.enabled]

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        logger.info(f"Starting batch RSS ingestion for {len(enabled_sources)} enabled sources")

        for source in enabled_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(f"Unhandled error during batch ingestion of '{source.name}': {str(exc)}")
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(enabled_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Batch RSS ingestion finished: {succeeded}/{len(enabled_sources)} succeeded, "
            f"inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

    async def ingest_all_official_sources(self) -> IngestionSummaryResponse:
        """Ingest all registered authoritative official AI organization sources (OpenAI, Anthropic, DeepMind)."""
        overall_start = time.time()
        all_sources, _ = await self.source_repo.list_sources(limit=100)

        # Filter enabled sources that belong to official organizations and have supported adapters
        official_orgs = {"OpenAI", "Anthropic", "Google DeepMind"}
        official_slugs = {"openai-news", "anthropic-news", "deepmind-blog"}

        official_sources = [
            s for s in all_sources
            if s.enabled
            and s.type in self.adapters
            and (
                (isinstance(s.config, dict) and s.config.get("organization") in official_orgs)
                or s.slug in official_slugs
            )
        ]

        logger.info(f"Starting batch official source ingestion for {len(official_sources)} enabled sources")

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        for source in official_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(f"Unhandled error during batch ingestion of '{source.name}': {str(exc)}")
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(official_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Batch official source ingestion finished: {succeeded}/{len(official_sources)} succeeded, "
            f"inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

    async def ingest_all_arxiv_sources(self) -> IngestionSummaryResponse:
        """Ingest all registered, enabled ArXiv AI research sources."""
        overall_start = time.time()
        all_sources, _ = await self.source_repo.list_sources(limit=100)

        arxiv_sources = [
            s for s in all_sources
            if s.enabled and s.type == SourceType.ARXIV.value
        ]

        logger.info(f"Starting batch ArXiv ingestion for {len(arxiv_sources)} enabled sources")

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        for source in arxiv_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(f"Unhandled error during batch ArXiv ingestion of '{source.name}': {str(exc)}")
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(arxiv_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Batch ArXiv ingestion finished: {succeeded}/{len(arxiv_sources)} succeeded, "
            f"inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

    async def ingest_all_hacker_news_sources(self) -> IngestionSummaryResponse:
        """Ingest all registered, enabled Hacker News community signal sources."""
        overall_start = time.time()
        all_sources, _ = await self.source_repo.list_sources(limit=100)

        hn_sources = [
            s for s in all_sources
            if s.enabled and (
                (isinstance(s.config, dict) and s.config.get("adapter_type") == "hacker_news")
                or s.slug == "hacker-news"
            )
        ]

        logger.info(f"Starting batch Hacker News ingestion for {len(hn_sources)} enabled sources")

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        for source in hn_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(f"Unhandled error during batch Hacker News ingestion of '{source.name}': {str(exc)}")
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(hn_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Batch Hacker News ingestion finished: {succeeded}/{len(hn_sources)} succeeded, "
            f"inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

    async def ingest_all_youtube_sources(self) -> IngestionSummaryResponse:
        """Batch ingest all enabled YouTube channels registered in the Source Registry."""
        overall_start = time.time()
        all_sources, _ = await self.source_repo.list_sources(limit=100)
        yt_sources = [
            s for s in all_sources
            if s.enabled and (
                s.type == SourceType.YOUTUBE.value
                or (isinstance(s.config, dict) and s.config.get("adapter_type") == "youtube")
            )
        ]

        logger.info(f"Starting batch YouTube ingestion for {len(yt_sources)} enabled sources")

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        for source in yt_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(f"Unhandled error during batch YouTube ingestion of '{source.name}': {str(exc)}")
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(yt_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Batch YouTube ingestion finished: {succeeded}/{len(yt_sources)} succeeded, "
            f"inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

    async def ingest_all_sources(self) -> IngestionSummaryResponse:
        """Ingest all enabled registered sources across all supported adapters with per-source fault isolation."""
        overall_start = time.time()
        enabled_sources = await self.source_repo.list_enabled()

        results: List[SourceIngestionResult] = []
        succeeded = 0
        failed = 0
        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        logger.info(
            f"Starting unified multi-source ingestion for {len(enabled_sources)} enabled sources across all adapters"
        )

        for source in enabled_sources:
            try:
                res = await self.ingest_source(source)
                results.append(res)
                if res.status in ("SUCCESS", "PARTIAL"):
                    succeeded += 1
                else:
                    failed += 1

                total_fetched += res.entries_fetched
                total_inserted += res.entries_inserted
                total_updated += res.entries_updated
                total_skipped += res.entries_skipped

            except Exception as exc:
                logger.error(
                    f"Unhandled error during unified ingestion of source '{source.name}' [type={source.type}]: {str(exc)}"
                )
                failed += 1
                results.append(
                    SourceIngestionResult(
                        source_id=source.id,
                        source_name=source.name,
                        source_type=source.type,
                        status="FAILED",
                        errors=[str(exc)],
                    )
                )

        summary = IngestionSummaryResponse(
            total_sources=len(enabled_sources),
            succeeded_sources=succeeded,
            failed_sources=failed,
            total_fetched=total_fetched,
            total_inserted=total_inserted,
            total_updated=total_updated,
            total_skipped=total_skipped,
            source_results=results,
            total_duration_seconds=round(time.time() - overall_start, 3),
        )

        logger.info(
            f"Unified multi-source ingestion finished: {succeeded}/{len(enabled_sources)} succeeded, "
            f"fetched={total_fetched}, inserted={total_inserted}, updated={total_updated}, skipped={total_skipped}"
        )
        return summary

