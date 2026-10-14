from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict, Optional
from sqlalchemy import and_, delete, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.digest import DeliveryRecord, DigestStory
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem


@dataclass
class RetentionCleanupResult:
    """Audit outcome of an executed safe retention cleanup cycle."""
    status: str
    news_retention_days: int
    delivery_record_retention_days: int
    deleted_delivery_records: int
    deleted_content_items: int
    pruned_content_items: int
    duration_seconds: float
    executed_at: datetime
    error: Optional[str] = None


class RetentionService:
    """Production service managing safe, idempotent data lifecycle retention.

    Guarantees:
    - Zero subscriber deletion: NEVER touches subscribers or subscriber preferences.
    - Preserves digest history: Retains canonical story metadata and digest provenance.
    - Safe foreign key handling: Cleans uncurated orphan items while pruning bulky vectors/content
      on historical curated stories.
    - Safe terminal state: Only prunes terminal delivery logs (SENT or FAILED), never in-flight claims.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def cleanup_delivery_records(
        self,
        retention_days: Optional[int] = None,
        batch_size: Optional[int] = None,
    ) -> int:
        """Delete old terminal delivery audit records past the retention horizon."""
        days = retention_days if retention_days is not None else settings.DELIVERY_RECORD_RETENTION_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = (
            delete(DeliveryRecord)
            .where(
                and_(
                    DeliveryRecord.created_at < cutoff,
                    DeliveryRecord.status.in_(["SENT", "FAILED"]),
                )
            )
        )
        res = await self.session.execute(stmt)
        return int(res.rowcount or 0)

    async def cleanup_news_content(
        self,
        retention_days: Optional[int] = None,
        batch_size: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Safely clean old news articles older than the retention cutoff:
        1. For articles referenced in published digest issues: prune bulky raw_content and
           vector embeddings to reclaim storage while keeping story titles and provenance intact.
        2. For uncurated articles not linked to any digest issue: safely delete the row.
        """
        days = retention_days if retention_days is not None else settings.NEWS_RETENTION_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Identify all story IDs that belong to a published digest
        digest_story_ids_stmt = select(DigestStory.story_id).distinct()
        digest_story_ids_res = await self.session.execute(digest_story_ids_stmt)
        curated_digest_story_ids = set(digest_story_ids_res.scalars().all())

        # Subquery for content items associated with digest stories
        digest_content_item_ids_stmt = (
            select(StoryContentItem.content_item_id)
            .where(StoryContentItem.story_id.in_(curated_digest_story_ids))
            .distinct()
        )
        digest_content_item_ids_res = await self.session.execute(digest_content_item_ids_stmt)
        protected_content_item_ids = set(digest_content_item_ids_res.scalars().all())

        # Also protect canonical items of curated stories
        canonical_items_stmt = (
            select(Story.canonical_content_item_id)
            .where(
                and_(
                    Story.id.in_(curated_digest_story_ids),
                    Story.canonical_content_item_id.isnot(None),
                )
            )
            .distinct()
        )
        canonical_res = await self.session.execute(canonical_items_stmt)
        for c_id in canonical_res.scalars().all():
            if c_id:
                protected_content_item_ids.add(c_id)

        # 1. Prune bulky text/embeddings on protected historical digest articles
        pruned_count = 0
        if protected_content_item_ids:
            prune_stmt = (
                update(ContentItem)
                .where(
                    and_(
                        ContentItem.id.in_(protected_content_item_ids),
                        ContentItem.published_at < cutoff,
                        or_(
                            ContentItem.raw_content.isnot(None),
                            ContentItem.embedding.isnot(None),
                        ),
                    )
                )
                .values(
                    raw_content=None,
                    embedding=None,
                )
            )
            prune_res = await self.session.execute(prune_stmt)
            pruned_count = int(prune_res.rowcount or 0)

        # 2. Safely delete uncurated old content items not in protected set
        delete_condition = [ContentItem.published_at < cutoff]
        if protected_content_item_ids:
            delete_condition.append(not_(ContentItem.id.in_(protected_content_item_ids)))

        delete_stmt = delete(ContentItem).where(and_(*delete_condition))
        delete_res = await self.session.execute(delete_stmt)
        deleted_count = int(delete_res.rowcount or 0)

        return {
            "pruned": pruned_count,
            "deleted": deleted_count,
        }

    async def run_retention_cleanup(
        self,
        news_retention_days: Optional[int] = None,
        delivery_retention_days: Optional[int] = None,
    ) -> RetentionCleanupResult:
        """Execute unified, transactional retention cleanup cycle."""
        t0 = time.perf_counter()
        news_days = (
            news_retention_days
            if news_retention_days is not None
            else settings.NEWS_RETENTION_DAYS
        )
        delivery_days = (
            delivery_retention_days
            if delivery_retention_days is not None
            else settings.DELIVERY_RECORD_RETENTION_DAYS
        )

        logger.info(
            f"[Retention] Starting retention cleanup: news_days={news_days}, delivery_days={delivery_days}"
        )

        try:
            del_records = await self.cleanup_delivery_records(retention_days=delivery_days)
            content_results = await self.cleanup_news_content(retention_days=news_days)
            await self.session.commit()

            duration = round(time.perf_counter() - t0, 3)
            logger.info(
                f"[Retention] Cleanup completed in {duration}s: "
                f"deleted_delivery={del_records}, deleted_content={content_results['deleted']}, "
                f"pruned_content={content_results['pruned']}"
            )

            return RetentionCleanupResult(
                status="success",
                news_retention_days=news_days,
                delivery_record_retention_days=delivery_days,
                deleted_delivery_records=del_records,
                deleted_content_items=content_results["deleted"],
                pruned_content_items=content_results["pruned"],
                duration_seconds=duration,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            await self.session.rollback()
            duration = round(time.perf_counter() - t0, 3)
            logger.error(f"[Retention] Cleanup failed: {exc}", exc_info=True)
            return RetentionCleanupResult(
                status="error",
                news_retention_days=news_days,
                delivery_record_retention_days=delivery_days,
                deleted_delivery_records=0,
                deleted_content_items=0,
                pruned_content_items=0,
                duration_seconds=duration,
                executed_at=datetime.now(timezone.utc),
                error=str(exc),
            )
