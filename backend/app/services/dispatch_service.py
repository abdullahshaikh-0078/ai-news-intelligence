import argparse
import asyncio
from datetime import date, datetime, timezone
import sys
import time
from typing import Any, Dict, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import DailyDispatchRequest, DailyDispatchResponse
from app.domain.models.pipeline import PipelineRunRequest
from app.infrastructure.database.session import get_db, AsyncSessionLocal
from app.services.digest_service import DigestService
from app.services.email_service import BaseEmailProvider, DeliveryService
from app.services.pipeline_service import PipelineOrchestratorService


class DailyDispatchService:
    """Production service coordinating end-to-end automated daily ingestion, AI analysis,
    demand-driven embedding, curation, daily digest compilation, and subscriber dispatch.
    
    Guarantees:
    - Bounded, safe AI processing & demand-driven Gemini embeddings (no bulk embedding)
    - Digest idempotency: re-running on the same calendar day reuses existing digest issue
    - Delivery idempotency: subscribers who already received today's issue are safely skipped
    - Graceful isolation: zero crashes on external quota exhaustion or provider timeouts
    """

    def __init__(
        self,
        session: AsyncSession,
        email_provider: Optional[BaseEmailProvider] = None,
        pipeline_service: Optional[PipelineOrchestratorService] = None,
        digest_service: Optional[DigestService] = None,
        delivery_service: Optional[DeliveryService] = None,
    ):
        self.session = session
        self.email_provider = email_provider
        self.pipeline_service = pipeline_service or PipelineOrchestratorService(session=session)
        self.digest_service = digest_service or DigestService(session=session)
        self.delivery_service = delivery_service or DeliveryService(
            session=session, email_provider=email_provider
        )

    async def run_daily_dispatch(
        self,
        request: Optional[DailyDispatchRequest] = None,
    ) -> DailyDispatchResponse:
        """Execute the automated daily workflow end-to-end."""
        t0 = time.perf_counter()
        req = request or DailyDispatchRequest()
        target_date = req.target_date or datetime.now(timezone.utc).date()
        execution_id = uuid.uuid4()

        logger.info(
            f"[Daily Dispatch {execution_id}] Starting daily workflow for date={target_date} "
            f"(dry_run={req.dry_run}, ingest={req.run_ingestion}, force={req.force_regenerate_digest})"
        )

        # -----------------------------------------------------------------
        # Stage 0: Guard that baseline sources exist in registry
        # -----------------------------------------------------------------
        if req.run_ingestion:
            from app.infrastructure.repositories.source_repo import SourceRepository
            from app.services.source_service import SourceService
            source_repo = SourceRepository(self.session)
            enabled_sources = await source_repo.list_enabled()
            if not enabled_sources:
                logger.info(
                    f"[Daily Dispatch {execution_id}] 0 enabled sources found in registry. "
                    "Auto-seeding verified baseline sources before pipeline pass..."
                )
                source_service = SourceService(source_repo)
                await source_service.seed_default_sources()
                await self.session.commit()

        # -----------------------------------------------------------------
        # Stage 1: Pipeline Pass (Ingestion, AI analysis, Embeddings, Ranking, Curation)
        # -----------------------------------------------------------------
        pipeline_req = PipelineRunRequest(
            run_ingestion=req.run_ingestion,
            limit=req.item_limit,
            max_ai_analyses=req.max_ai_analyses,
            max_embeddings=req.max_embeddings,
            curation_limit=req.curation_limit,
        )
        pipeline_res = await self.pipeline_service.run_pipeline(pipeline_req)
        pipeline_summary: Dict[str, Any] = {
            "status": pipeline_res.status,
            "items_processed": pipeline_res.items_processed,
            "ai_analyses_performed": pipeline_res.ai_analyses_performed,
            "embeddings_generated": pipeline_res.embeddings_generated,
            "embeddings_reused": pipeline_res.embeddings_reused,
            "stories_ranked": pipeline_res.stories_ranked,
            "stories_curated": pipeline_res.stories_curated,
            "pipeline_duration_ms": getattr(pipeline_res, "duration_ms", 0.0),
        }

        # -----------------------------------------------------------------
        # Stage 2: Daily Digest Compilation
        # -----------------------------------------------------------------
        logger.info(f"[Daily Dispatch {execution_id}] Compiling Daily Digest for {target_date}...")
        digest = await self.digest_service.generate_daily_digest(
            target_date=target_date,
            force=req.force_regenerate_digest,
        )

        # -----------------------------------------------------------------
        # Stage 3: Newsletter Delivery Dispatch
        # -----------------------------------------------------------------
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
            logger.info(
                f"[Daily Dispatch {execution_id}] Dispatching digest issue {digest.id} "
                f"({digest.story_count} stories) to active subscribers..."
            )
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
        logger.info(
            f"[Daily Dispatch {execution_id}] Completed in {duration}s. "
            f"Status: {overall_status}, Sent: {sent_count}, Skipped: {skipped_count}, Failed: {failed_count}"
        )

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
    parser = argparse.ArgumentParser(description="AI News Daily Digest automated dispatch CLI")
    parser.add_argument("--dry-run", action="store_true", help="Simulate send without emailing")
    parser.add_argument("--force", action="store_true", help="Force regenerate digest if already exists")
    parser.add_argument("--no-ingest", action="store_true", help="Skip RSS ingestion pass")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date) if args.date else None
        req = DailyDispatchRequest(
            target_date=target_date,
            run_ingestion=not args.no_ingest,
            dry_run=args.dry_run,
            force_regenerate_digest=args.force,
        )

        async with AsyncSessionLocal() as session:
            service = DailyDispatchService(session=session)
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
        logger.error(f"Fatal error during CLI daily dispatch execution: {exc}")
        print(f"Daily Dispatch Fatal Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run_cli())
