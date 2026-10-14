import argparse
import asyncio
from datetime import date, datetime, timezone
import sys
import time
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import (
    DailyDispatchRequest,
    DailyDispatchResponse,
    ScheduledTickRequest,
    ScheduledTickResponse,
)
from app.domain.models.pipeline import PipelineRunRequest
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.digest import Digest
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.digest_service import DigestService
from app.services.email_service import BaseEmailProvider, DeliveryService
from app.services.pipeline_service import PipelineOrchestratorService
from app.services.retention_service import RetentionService


class DailyDispatchService:
    """Production service coordinating decoupled news collection, digest compilation,
    localized subscriber delivery dispatch, and safe retention cleanup.
    
    Architecture:
    1. News Collection & Curation: Ingests, deduplicates, and curates news independently of subscribers.
    2. Digest Compilation: Generates an immutable, idempotent daily digest issue once per day.
    3. Subscriber Delivery: Evaluates which subscribers are due at current execution time based on their
       preferred time and IANA timezone, and sends personalized stories from the curated set.
    4. Retention Cleanup: Safely and idempotently prunes old data respecting foreign keys without
       deleting active subscribers.
    """

    def __init__(
        self,
        session: AsyncSession,
        email_provider: Optional[BaseEmailProvider] = None,
        pipeline_service: Optional[PipelineOrchestratorService] = None,
        digest_service: Optional[DigestService] = None,
        delivery_service: Optional[DeliveryService] = None,
        retention_service: Optional[RetentionService] = None,
    ):
        self.session = session
        self.email_provider = email_provider
        self.pipeline_service = pipeline_service or PipelineOrchestratorService(session=session)
        self.digest_service = digest_service or DigestService(session=session)
        self.delivery_service = delivery_service or DeliveryService(
            session=session, email_provider=email_provider
        )
        self.retention_service = retention_service or RetentionService(session=session)
        self.subscriber_repo = SubscriberRepository(session=session)
        self.digest_repo = DigestRepository(session=session)

    # -------------------------------------------------------------------------
    # 1. Independent News Collection & Curation Pass
    # -------------------------------------------------------------------------
    async def collect_and_curate_news(
        self,
        run_ingestion: bool = True,
        item_limit: int = 20,
        max_ai_analyses: int = 10,
        max_embeddings: int = 10,
        curation_limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Execute bounded ingestion, AI extraction, deduplication, ranking, and curation.
        Collects news independently from subscriber delivery (never rerun per subscriber).
        """
        t0 = time.perf_counter()
        logger.info("[News Collection] Starting news ingestion and curation pass...")

        # Ensure verified baseline sources are seeded if registry is currently empty
        if run_ingestion:
            from app.infrastructure.repositories.source_repo import SourceRepository
            from app.services.source_service import SourceService
            source_repo = SourceRepository(self.session)
            enabled_sources = await source_repo.list_enabled()
            if not enabled_sources:
                logger.info("[News Collection] 0 enabled sources in registry. Seeding default sources...")
                source_service = SourceService(source_repo)
                await source_service.seed_default_sources()
                await self.session.commit()

        pipeline_req = PipelineRunRequest(
            run_ingestion=run_ingestion,
            limit=item_limit,
            max_ai_analyses=max_ai_analyses,
            max_embeddings=max_embeddings,
            curation_limit=curation_limit,
        )
        pipeline_res = await self.pipeline_service.run_pipeline(pipeline_req)
        duration = round(time.perf_counter() - t0, 3)

        summary = {
            "status": pipeline_res.status,
            "items_processed": pipeline_res.items_processed,
            "ai_analyses_performed": pipeline_res.ai_analyses_performed,
            "embeddings_generated": pipeline_res.embeddings_generated,
            "embeddings_reused": pipeline_res.embeddings_reused,
            "stories_ranked": pipeline_res.stories_ranked,
            "stories_curated": pipeline_res.stories_curated,
            "duration_seconds": duration,
        }
        logger.info(f"[News Collection] Completed in {duration}s. Status={pipeline_res.status}")
        return summary

    # -------------------------------------------------------------------------
    # 2. Independent Daily Digest Compilation
    # -------------------------------------------------------------------------
    async def compile_digest_issue(
        self,
        target_date: Optional[date] = None,
        force: bool = False,
    ) -> Digest:
        """
        Compile curated stories into an immutable Daily Digest issue.
        Re-running on the same date reuses the existing issue unless force=True.
        """
        effective_date = target_date or datetime.now(timezone.utc).date()
        logger.info(f"[Digest Compilation] Compiling digest issue for date={effective_date} (force={force})...")
        digest = await self.digest_service.generate_daily_digest(
            target_date=effective_date,
            force=force,
        )
        logger.info(f"[Digest Compilation] Digest issue {digest.id} ready with {digest.story_count} curated stories.")
        return digest

    # -------------------------------------------------------------------------
    # 3. Localized Subscriber Delivery Dispatch (Due-Window Architecture)
    # -------------------------------------------------------------------------
    async def deliver_due_subscribers(
        self,
        target_time_utc: Optional[datetime] = None,
        window_minutes: Optional[int] = None,
        dry_run: bool = False,
        digest: Optional[Digest] = None,
    ) -> Dict[str, Any]:
        """
        Determine which subscribers are due at current UTC execution time and dispatch personalized digests.

        Guarantees:
        - Evaluates preferred_time (HH:MM) against subscriber's IANA timezone.
        - Respects DAILY and WEEKLY cadences.
        - Robust due-window prevents skipping if scheduler execution was slightly delayed.
        - Reuses the single curated story set without invoking LLMs per subscriber.
        - Skips subscribers with zero matching stories (never delivers empty emails).
        - Idempotent: atomic claim ensures subscribers who already received today's issue are skipped.
        """
        t0 = time.perf_counter()
        now_utc = target_time_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        due_window = window_minutes if window_minutes is not None else settings.DELIVERY_DUE_WINDOW_MINUTES

        logger.info(f"[Delivery Dispatch] Evaluating due subscribers at UTC={now_utc.isoformat()} (window={due_window}m, dry_run={dry_run})...")

        # 1. Identify subscribers due in the current time window
        due_subscribers = await self.subscriber_repo.list_due_subscribers(
            target_time_utc=now_utc,
            window_minutes=due_window,
        )
        total_due = len(due_subscribers)
        logger.info(f"[Delivery Dispatch] Found {total_due} subscriber(s) currently due.")

        if total_due == 0:
            return {
                "status": "success",
                "due_subscribers_count": 0,
                "sent_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "digest_id": None,
                "duration_seconds": round(time.perf_counter() - t0, 3),
            }

        # 2. Obtain today's digest issue (or most recent published issue)
        target_digest = digest
        if not target_digest:
            today_date = now_utc.date()
            target_digest = await self.digest_repo.get_by_date(today_date)
            if not target_digest:
                target_digest = await self.digest_repo.get_latest()

        if not target_digest or target_digest.story_count == 0:
            logger.warning(
                f"[Delivery Dispatch] No valid digest issue with curated stories available for delivery at {now_utc.date()}. "
                "Skipping dispatch to prevent sending blank newsletters."
            )
            return {
                "status": "skipped",
                "due_subscribers_count": total_due,
                "sent_count": 0,
                "skipped_count": total_due,
                "failed_count": 0,
                "digest_id": getattr(target_digest, "id", None),
                "duration_seconds": round(time.perf_counter() - t0, 3),
            }

        # 3. Dispatch to due subscribers with deterministic personalization and atomic claims
        delivery_res = await self.delivery_service.send_digest_to_subscribers(
            digest_id=target_digest.id,
            subscribers=due_subscribers,
            dry_run=dry_run,
        )

        overall_status = "success"
        if delivery_res.failed_count > 0 and delivery_res.sent_count == 0:
            overall_status = "error"
        elif delivery_res.failed_count > 0:
            overall_status = "partial"

        duration = round(time.perf_counter() - t0, 3)
        logger.info(
            f"[Delivery Dispatch] Completed in {duration}s. Status={overall_status}, "
            f"Sent={delivery_res.sent_count}, Skipped={delivery_res.skipped_count}, Failed={delivery_res.failed_count}"
        )

        return {
            "status": overall_status,
            "due_subscribers_count": total_due,
            "sent_count": delivery_res.sent_count,
            "skipped_count": delivery_res.skipped_count,
            "failed_count": delivery_res.failed_count,
            "digest_id": target_digest.id,
            "duration_seconds": duration,
        }

    # -------------------------------------------------------------------------
    # 4. Safe Retention Cleanup Pass
    # -------------------------------------------------------------------------
    async def cleanup_old_data(
        self,
        news_retention_days: Optional[int] = None,
        delivery_retention_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute safe, non-destructive retention cleanup respecting foreign keys and active subscribers."""
        res = await self.retention_service.run_retention_cleanup(
            news_retention_days=news_retention_days,
            delivery_retention_days=delivery_retention_days,
        )
        return {
            "status": res.status,
            "deleted_delivery_records": res.deleted_delivery_records,
            "deleted_content_items": res.deleted_content_items,
            "pruned_content_items": res.pruned_content_items,
            "duration_seconds": res.duration_seconds,
            "error": res.error,
        }

    # -------------------------------------------------------------------------
    # 5. Unified Scheduler Tick (Primary Recurring Cron Entrypoint)
    # -------------------------------------------------------------------------
    async def run_scheduled_tick(
        self,
        request: Optional[ScheduledTickRequest] = None,
    ) -> ScheduledTickResponse:
        """
        Primary execution entrypoint for recurring scheduler runs (e.g. hourly cron).

        Evaluates:
        1. Whether news collection pass is due (e.g. scheduled at 03:30 UTC or forced).
        2. Always evaluates and delivers due subscribers in the current time window.
        3. Runs daily retention cleanup if scheduled or forced.
        """
        t0 = time.perf_counter()
        req = request or ScheduledTickRequest()
        execution_id = uuid.uuid4()
        now_utc = req.target_time_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # Check if news collection should run
        # By default runs if explicitly requested OR during the daily collection window (03:30 UTC)
        is_collection_hour = (
            now_utc.hour == settings.DISPATCH_SCHEDULE_HOUR_UTC
            and abs(now_utc.minute - settings.DISPATCH_SCHEDULE_MINUTE_UTC) <= 30
        )
        should_collect = req.run_pipeline if req.run_pipeline is not None else is_collection_hour

        collection_executed = False
        digest_compiled = False
        digest = None

        if should_collect:
            logger.info(f"[Scheduler Tick {execution_id}] Running scheduled news collection & curation pass...")
            await self.collect_and_curate_news()
            collection_executed = True
            digest = await self.compile_digest_issue(target_date=now_utc.date())
            digest_compiled = True

        # Always deliver subscribers who are due at this specific wall-clock time
        delivery_summary = await self.deliver_due_subscribers(
            target_time_utc=now_utc,
            window_minutes=req.window_minutes,
            dry_run=req.dry_run,
            digest=digest,
        )

        # Retention cleanup: runs if explicitly requested OR once daily around 04:00 UTC
        is_cleanup_hour = (now_utc.hour == 4 and now_utc.minute < 30)
        should_cleanup = req.run_cleanup if req.run_cleanup is not None else is_cleanup_hour
        cleanup_executed = False
        cleanup_summary: Dict[str, Any] = {}

        if should_cleanup:
            logger.info(f"[Scheduler Tick {execution_id}] Running retention cleanup pass...")
            cleanup_summary = await self.cleanup_old_data()
            cleanup_executed = True

        duration = round(time.perf_counter() - t0, 3)
        return ScheduledTickResponse(
            status=delivery_summary.get("status", "success"),
            execution_id=execution_id,
            target_time_utc=now_utc,
            collection_executed=collection_executed,
            digest_compiled=digest_compiled,
            digest_id=delivery_summary.get("digest_id"),
            due_subscribers_count=delivery_summary.get("due_subscribers_count", 0),
            sent_count=delivery_summary.get("sent_count", 0),
            skipped_count=delivery_summary.get("skipped_count", 0),
            failed_count=delivery_summary.get("failed_count", 0),
            cleanup_executed=cleanup_executed,
            cleanup_summary=cleanup_summary,
            duration_seconds=duration,
            executed_at=datetime.now(timezone.utc),
        )

    # -------------------------------------------------------------------------
    # 6. Legacy End-to-End Daily Dispatch Workflow (Backward Compatible)
    # -------------------------------------------------------------------------
    async def run_daily_dispatch(
        self,
        request: Optional[DailyDispatchRequest] = None,
    ) -> DailyDispatchResponse:
        """Execute end-to-end workflow: collection -> digest -> delivery to all active daily subscribers."""
        t0 = time.perf_counter()
        req = request or DailyDispatchRequest()
        target_date = req.target_date or datetime.now(timezone.utc).date()
        execution_id = uuid.uuid4()

        logger.info(
            f"[Daily Dispatch {execution_id}] Starting daily workflow for date={target_date} "
            f"(dry_run={req.dry_run}, ingest={req.run_ingestion}, force={req.force_regenerate_digest})"
        )

        # Stage 1: Pipeline Pass
        pipeline_summary = await self.collect_and_curate_news(
            run_ingestion=req.run_ingestion,
            item_limit=req.item_limit,
            max_ai_analyses=req.max_ai_analyses,
            max_embeddings=req.max_embeddings,
            curation_limit=req.curation_limit,
        )

        # Stage 2: Digest Compilation
        digest = await self.compile_digest_issue(
            target_date=target_date,
            force=req.force_regenerate_digest,
        )

        # Stage 3: Delivery Dispatch
        sent_count = 0
        skipped_count = 0
        failed_count = 0
        total_subscribers = 0
        overall_status = "success"

        if digest.story_count == 0:
            logger.warning(
                f"[Daily Dispatch {execution_id}] Digest for {target_date} contains 0 curated stories. "
                "Skipping email delivery to prevent sending empty digests."
            )
            overall_status = "skipped"
        else:
            delivery_res = await self.delivery_service.send_digest(
                digest_id=digest.id,
                dry_run=req.dry_run,
            )
            total_subscribers = delivery_res.total_subscribers
            sent_count = delivery_res.sent_count
            skipped_count = delivery_res.skipped_count
            failed_count = delivery_res.failed_count

            if failed_count > 0 and sent_count == 0:
                overall_status = "error"
            elif failed_count > 0:
                overall_status = "partial"

        duration = round(time.perf_counter() - t0, 3)
        return DailyDispatchResponse(
            status=overall_status,
            execution_id=execution_id,
            target_date=target_date,
            digest_id=digest.id,
            digest_title=digest.title,
            digest_stories_count=digest.story_count,
            total_subscribers=total_subscribers,
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            dry_run=req.dry_run,
            pipeline_summary=pipeline_summary,
            duration_seconds=duration,
            executed_at=datetime.now(timezone.utc),
        )


async def _run_cli():
    """CLI execution entrypoint for cron or scheduled workflows."""
    parser = argparse.ArgumentParser(description="AI News Intelligence automated scheduler & dispatch CLI")
    parser.add_argument(
        "--mode",
        choices=["scheduler", "deliver", "collect", "digest", "cleanup", "all"],
        default="all",
        help="Execution mode (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate send without emailing")
    parser.add_argument("--force", action="store_true", help="Force regenerate digest if already exists")
    parser.add_argument("--no-ingest", action="store_true", help="Skip RSS ingestion pass")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--window", type=int, default=60, help="Due window in minutes for delivery")
    args = parser.parse_args()

    try:
        async with AsyncSessionLocal() as session:
            service = DailyDispatchService(session=session)

            if args.mode == "scheduler":
                tick_res = await service.run_scheduled_tick(
                    ScheduledTickRequest(dry_run=args.dry_run, window_minutes=args.window)
                )
                print(f"Scheduler Tick Outcome: {tick_res.status}")
                print(f"Collection Run: {tick_res.collection_executed}, Digest: {tick_res.digest_compiled}")
                print(f"Due Subscribers: {tick_res.due_subscribers_count}, Sent: {tick_res.sent_count}, Skipped: {tick_res.skipped_count}")
                print(f"Cleanup Run: {tick_res.cleanup_executed}")
                print(f"Duration: {tick_res.duration_seconds}s")
                if tick_res.status == "error":
                    sys.exit(1)

            elif args.mode == "deliver":
                del_res = await service.deliver_due_subscribers(dry_run=args.dry_run, window_minutes=args.window)
                print(f"Delivery Outcome: {del_res.get('status')}")
                print(f"Due Subscribers: {del_res.get('due_subscribers_count')}, Sent: {del_res.get('sent_count')}, Skipped: {del_res.get('skipped_count')}")
                print(f"Duration: {del_res.get('duration_seconds')}s")

            elif args.mode == "collect":
                col_res = await service.collect_and_curate_news(run_ingestion=not args.no_ingest)
                print(f"Collection Outcome: {col_res.get('status')}")
                print(f"Items: {col_res.get('items_processed')}, Curated: {col_res.get('stories_curated')}")

            elif args.mode == "digest":
                target_date = date.fromisoformat(args.date) if args.date else None
                digest = await service.compile_digest_issue(target_date=target_date, force=args.force)
                print(f"Digest Issue: {digest.id} ({digest.digest_date}), Stories: {digest.story_count}")

            elif args.mode == "cleanup":
                clean_res = await service.cleanup_old_data()
                print(f"Cleanup Outcome: {clean_res.get('status')}")
                print(f"Deleted Delivery Records: {clean_res.get('deleted_delivery_records')}")
                print(f"Deleted Content Items: {clean_res.get('deleted_content_items')}")
                print(f"Pruned Content Items: {clean_res.get('pruned_content_items')}")

            else:  # mode == "all"
                target_date = date.fromisoformat(args.date) if args.date else None
                req = DailyDispatchRequest(
                    target_date=target_date,
                    run_ingestion=not args.no_ingest,
                    dry_run=args.dry_run,
                    force_regenerate_digest=args.force,
                )
                res = await service.run_daily_dispatch(req)
                print(f"Daily Dispatch Outcome: {res.status}")
                print(f"Digest ID: {res.digest_id} ({res.digest_stories_count} stories)")
                print(f"Subscribers: total={res.total_subscribers}, sent={res.sent_count}, skipped={res.skipped_count}, failed={res.failed_count}")
                print(f"Duration: {res.duration_seconds}s")
                if res.status == "error":
                    sys.exit(1)

    except SystemExit:
        raise
    except Exception as exc:
        logger.error(f"Fatal error during CLI dispatch execution: {exc}")
        print(f"Daily Dispatch Fatal Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run_cli())
