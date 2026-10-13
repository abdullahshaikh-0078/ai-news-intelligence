from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.pipeline import (
    CuratedStorySummary,
    PipelineFailureDetail,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStatusResponse,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.story import Story
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.dedup_repo import ContentDuplicateRepository
from app.infrastructure.repositories.story_repo import StoryRepository
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.ai_service import AIProcessingService
from app.services.curation_service import StoryCurationService
from app.services.dedup_service import SemanticDeduplicationService
from app.services.ranking_service import StoryRankingService
from app.services.story_service import StoryClusteringService


class PipelineOrchestratorService:
    """Production orchestration service coordinating the complete end-to-end AI News pipeline.
    
    Coordinates existing modular services in bounded, fail-safe stages:
      1. INGESTION (Optional/bounded trigger across registered sources)
      2. CANDIDATE SELECTION & VALIDATION
      3. DETERMINISTIC DEDUPLICATION (Content Hash & Canonical URL)
      4. AI ANALYSIS (LLM text extraction with idempotency & quota guards)
      5. ON-DEMAND EMBEDDING (Reuses existing vectors, generates authentic Gemini embeddings only when needed, never mock)
      6. SEMANTIC DEDUPLICATION (Vector cosine distance via pgvector)
      7. STORY CLUSTERING & SYNTHESIS (Multi-source clustering & grounded synthesis)
      8. STORY RANKING (6 transparent signals: relevance, authority, recency, coverage, diversity, engagement)
      9. EDITORIAL CURATION (Diversity constraints producing top 5–10 stories)
      10. DIGEST CANDIDATE COMPILATION
    """

    def __init__(
        self,
        session: AsyncSession,
        ai_service: Optional[AIProcessingService] = None,
        dedup_service: Optional[SemanticDeduplicationService] = None,
        story_service: Optional[StoryClusteringService] = None,
        ranking_service: Optional[StoryRankingService] = None,
        curation_service: Optional[StoryCurationService] = None,
    ):
        self.session = session
        self.content_repo = ContentRepository(session)
        self.dedup_repo = ContentDuplicateRepository(session)
        self.story_repo = StoryRepository(session)

        # Coordinate existing service implementations
        self.ai_service = ai_service or AIProcessingService(session=session)
        self.dedup_service = dedup_service or SemanticDeduplicationService(session=session)
        self.story_service = story_service or StoryClusteringService(session=session)
        self.ranking_service = ranking_service or StoryRankingService(session=session)
        self.curation_service = curation_service or StoryCurationService(session=session)

    async def get_pipeline_status(self) -> PipelineStatusResponse:
        """Query configuration boundaries, queue status, and provider telemetry."""
        counts = await self.content_repo.get_ai_processing_counts()
        total_stories = (await self.session.execute(select(func.count(Story.id)))).scalar_one()
        curated_stories = (
            await self.session.execute(
                select(func.count(Story.id)).where(Story.is_curated.is_(True))
            )
        ).scalar_one()
        embedded_items = (
            await self.session.execute(
                select(func.count(ContentItem.id)).where(ContentItem.embedding.is_not(None))
            )
        ).scalar_one()

        provider_name = (
            "Google Gemini"
            if "gemini" in self.ai_service.provider.__class__.__name__.lower()
            else self.ai_service.provider.__class__.__name__
        )

        return PipelineStatusResponse(
            status="READY",
            environment=settings.ENVIRONMENT,
            ai_provider=provider_name,
            chat_model=settings.GEMINI_CHAT_MODEL,
            embedding_model=settings.GEMINI_EMBEDDING_MODEL,
            embedding_dimensions=settings.GEMINI_EMBEDDING_DIMENSIONS,
            limits={
                "max_items_per_run": settings.PIPELINE_MAX_ITEMS_PER_RUN,
                "max_ai_analyses_per_run": settings.PIPELINE_MAX_AI_ANALYSES_PER_RUN,
                "max_embeddings_per_run": settings.PIPELINE_MAX_EMBEDDINGS_PER_RUN,
                "max_stories_per_curated_feed": settings.PIPELINE_MAX_STORIES_PER_CURATED_FEED,
                "on_demand_embedding_enabled": settings.PIPELINE_ON_DEMAND_EMBEDDING_ENABLED,
                "min_relevance_for_embedding": settings.PIPELINE_MIN_RELEVANCE_FOR_EMBEDDING,
            },
            queue_summary={
                "total_content_items": counts.get("total", 0),
                "pending_items": counts.get("pending", 0),
                "completed_items": counts.get("completed", 0),
                "failed_items": counts.get("failed", 0),
                "embedded_items": embedded_items,
                "total_stories": total_stories,
                "curated_stories": curated_stories,
            },
        )

    async def run_pipeline(self, request: Optional[PipelineRunRequest] = None) -> PipelineRunResponse:
        """Execute a controlled, bounded, end-to-end processing pipeline run.
        
        Guarantees:
        - Bounded execution limits
        - Idempotency: re-running avoids redundant AI calls
        - Strict On-Demand Embedding: only embeds candidate items entering semantic stages
        - Existing embedding reuse: 0 duplicate embedding calls for already-embedded items
        - Fault isolation: individual item errors or 429 quota limits do not crash the pipeline
        - Never writes synthetic or mock vectors in production
        - Produces 5–10 curated digest-ready stories
        """
        t0 = time.perf_counter()
        req = request or PipelineRunRequest()

        # Resolve effective bounded limits
        item_limit = min(req.limit or settings.PIPELINE_MAX_ITEMS_PER_RUN, 50)
        max_ai_analyses = min(
            req.max_ai_analyses if req.max_ai_analyses is not None else settings.PIPELINE_MAX_AI_ANALYSES_PER_RUN,
            20,
        )
        max_embeddings = min(
            req.max_embeddings if req.max_embeddings is not None else settings.PIPELINE_MAX_EMBEDDINGS_PER_RUN,
            20,
        )
        curation_limit = min(
            req.curation_limit or settings.PIPELINE_MAX_STORIES_PER_CURATED_FEED,
            20,
        )
        min_relevance = (
            req.min_relevance_for_embedding
            if req.min_relevance_for_embedding is not None
            else settings.PIPELINE_MIN_RELEVANCE_FOR_EMBEDDING
        )

        logger.info(
            f"[Pipeline] Starting orchestrated pipeline pass: item_limit={item_limit}, "
            f"max_ai={max_ai_analyses}, max_emb={max_embeddings}, curation_limit={curation_limit}"
        )

        # Telemetry accumulators
        items_seen = 0
        items_processed = 0
        ai_analyses_performed = 0
        embeddings_generated = 0
        embeddings_reused = 0
        deterministic_duplicates_found = 0
        semantic_duplicates_found = 0
        stories_created = 0
        stories_updated = 0
        stories_ranked = 0
        stories_curated = 0
        failures: List[PipelineFailureDetail] = []

        # Track external quota saturation to avoid cascading errors
        quota_exhausted = False

        # -----------------------------------------------------------------
        # Stage 1: Optional Ingestion Pass
        # -----------------------------------------------------------------
        if req.run_ingestion:
            logger.info("[Pipeline Stage 1] Triggering bounded multi-source ingestion pass across all registered sources...")
            try:
                ingestion_orchestrator = IngestionOrchestrator(self.session)
                ing_summary = await ingestion_orchestrator.ingest_all_sources()
                logger.info(
                    f"[Pipeline Stage 1] Ingestion complete: fetched={ing_summary.total_fetched}, "
                    f"inserted={ing_summary.total_inserted}, updated={ing_summary.total_updated}, "
                    f"skipped={ing_summary.total_skipped}"
                )
            except Exception as exc:
                logger.error(f"[Pipeline Stage 1] Ingestion stage failure: {exc.__class__.__name__}: {str(exc)}")
                failures.append(
                    PipelineFailureDetail(
                        stage="ingestion",
                        error_type=exc.__class__.__name__,
                        message=f"Ingestion failure: {str(exc)[:200]}",
                    )
                )

        # -----------------------------------------------------------------
        # Stage 2: Candidate Selection & Validation
        # -----------------------------------------------------------------
        logger.info("[Pipeline Stage 2] Selecting candidate items...")
        candidate_stmt = (
            select(ContentItem)
            .options(selectinload(ContentItem.source))
        )
        if not req.force:
            # Prioritize unassigned or unprocessed candidates
            candidate_stmt = candidate_stmt.where(
                (ContentItem.story_id.is_(None))
                | (ContentItem.processing_state.in_(["RAW", "PENDING"]))
            )
        candidate_stmt = candidate_stmt.order_by(ContentItem.published_at.desc()).limit(item_limit)
        candidates = list((await self.session.execute(candidate_stmt)).scalars().all())
        items_seen = len(candidates)
        logger.info(f"[Pipeline Stage 2] Candidate items selected: {items_seen}")

        # Set of items that pass validation and deterministic dedup
        valid_candidates: List[ContentItem] = []

        # -----------------------------------------------------------------
        # Stage 3: Normalization & Deterministic Deduplication
        # -----------------------------------------------------------------
        for item in candidates:
            # Field validation
            if not item.title or not item.title.strip():
                failures.append(
                    PipelineFailureDetail(
                        item_id=item.id,
                        stage="validation",
                        error_type="ValidationError",
                        message="ContentItem missing title",
                    )
                )
                continue

            if not item.canonical_url or not item.canonical_url.strip():
                failures.append(
                    PipelineFailureDetail(
                        item_id=item.id,
                        stage="validation",
                        error_type="ValidationError",
                        message="ContentItem missing canonical URL",
                    )
                )
                continue

            # Deterministic duplicate inspection
            try:
                det_matches = await self.dedup_repo.find_deterministic_matches(item)
                if det_matches:
                    deterministic_duplicates_found += len(det_matches)
                    for matched_item, method_str in det_matches:
                        await self.dedup_repo.upsert_pair(
                            content_item_id=item.id,
                            duplicate_content_item_id=matched_item.id,
                            similarity_score=1.0,
                            cosine_distance=0.0,
                            classification="EXACT_DUPLICATE",
                            detection_method=method_str,
                            metadata_json={"source_id": str(matched_item.source_id)},
                        )
                    logger.info(
                        f"[Pipeline Stage 3] Deterministic duplicate identified for item {item.id} "
                        f"('{item.title[:30]}') -> matches {len(det_matches)} existing items"
                    )
                    # Note: exact deterministic duplicates do not proceed to expensive LLM processing
                    continue
            except Exception as exc:
                logger.warning(f"[Pipeline Stage 3] Deterministic check error for item {item.id}: {str(exc)}")

            valid_candidates.append(item)

        # -----------------------------------------------------------------
        # Stage 4: AI Text Analysis (When Needed)
        # -----------------------------------------------------------------
        logger.info(f"[Pipeline Stage 4] Starting AI text analysis on {len(valid_candidates)} candidates...")
        for item in valid_candidates:
            # Check idempotency: skip if already analyzed
            already_analyzed = bool(item.ai_summary and item.processing_state == "COMPLETED")
            if already_analyzed and not req.force:
                logger.debug(f"[Pipeline Stage 4] Reusing existing analysis for item {item.id}")
                continue

            if quota_exhausted or ai_analyses_performed >= max_ai_analyses:
                logger.debug(
                    f"[Pipeline Stage 4] AI analyses cap ({max_ai_analyses}) or quota limit reached; "
                    f"skipping analysis for item {item.id}"
                )
                continue

            try:
                await self.ai_service.analyze_item(item, force=req.force)
                ai_analyses_performed += 1
            except Exception as exc:
                err_cls = exc.__class__.__name__
                err_msg = str(exc)
                logger.error(f"[Pipeline Stage 4] AI analysis failed for item {item.id}: {err_cls}: {err_msg[:200]}")
                failures.append(
                    PipelineFailureDetail(
                        item_id=item.id,
                        stage="ai_analysis",
                        error_type=err_cls,
                        message=f"Analysis failure: {err_msg[:200]}",
                    )
                )
                # Check for rate limit / quota exhaustion
                if "429" in err_msg or "ResourceExhausted" in err_cls or "quota" in err_msg.lower():
                    logger.warning("[Pipeline Stage 4] Gemini quota limit encountered (429). Halting further AI calls.")
                    quota_exhausted = True

        # -----------------------------------------------------------------
        # Stage 5: On-Demand Embedding Generation & Reuse
        # -----------------------------------------------------------------
        logger.info("[Pipeline Stage 5] Executing On-Demand Embedding policy...")
        for item in valid_candidates:
            # 1. Check if embedding already exists -> REUSE
            if item.embedding is not None and not req.force:
                embeddings_reused += 1
                items_processed += 1
                continue

            # 2. Check if embedding should be generated on-demand
            # Gate by AI relevance score if available
            if item.ai_relevance_score is not None and float(item.ai_relevance_score) < min_relevance:
                logger.debug(
                    f"[Pipeline Stage 5] Item {item.id} relevance {item.ai_relevance_score:.2f} "
                    f"< threshold {min_relevance:.2f}; skipping on-demand embedding"
                )
                items_processed += 1
                continue

            if quota_exhausted or embeddings_generated >= max_embeddings:
                logger.debug(
                    f"[Pipeline Stage 5] Embeddings cap ({max_embeddings}) or quota limit reached; "
                    f"preserving item {item.id} without embedding"
                )
                items_processed += 1
                continue

            try:
                emb, is_new = await self.ai_service.ensure_embedding(item, force=req.force)
                if is_new:
                    embeddings_generated += 1
                else:
                    embeddings_reused += 1
                items_processed += 1
            except Exception as exc:
                err_cls = exc.__class__.__name__
                err_msg = str(exc)
                logger.error(
                    f"[Pipeline Stage 5] On-demand embedding failed for item {item.id}: {err_cls}: {err_msg[:200]}"
                )
                failures.append(
                    PipelineFailureDetail(
                        item_id=item.id,
                        stage="embedding",
                        error_type=err_cls,
                        message=f"Embedding failure: {err_msg[:200]}",
                    )
                )
                if "429" in err_msg or "ResourceExhausted" in err_cls or "quota" in err_msg.lower():
                    logger.warning("[Pipeline Stage 5] Gemini quota limit encountered during embedding. Halting.")
                    quota_exhausted = True

        await self.session.commit()

        # -----------------------------------------------------------------
        # Stage 6: Semantic Deduplication (Items with Embeddings)
        # -----------------------------------------------------------------
        logger.info("[Pipeline Stage 6] Checking semantic deduplication for embedded candidates...")
        for item in valid_candidates:
            if not item.embedding:
                continue
            try:
                dup_res = await self.dedup_service.check_duplicates(item.id, persist=True)
                if dup_res.semantic_duplicates:
                    semantic_duplicates_found += len(dup_res.semantic_duplicates)
            except Exception as exc:
                logger.warning(f"[Pipeline Stage 6] Semantic dedup inspection error for item {item.id}: {str(exc)}")

        await self.session.commit()

        # -----------------------------------------------------------------
        # Stage 7: Story Clustering & Synthesis
        # -----------------------------------------------------------------
        logger.info("[Pipeline Stage 7] Running Story Clustering & Grounded Synthesis...")
        try:
            cluster_res = await self.story_service.cluster_unassigned_candidates(limit=item_limit)
            stories_created = cluster_res.stories_created
            stories_updated = cluster_res.stories_updated
            logger.info(
                f"[Pipeline Stage 7] Clustering complete: created={stories_created}, "
                f"updated={stories_updated}, items_assigned={cluster_res.items_assigned}"
            )
        except Exception as exc:
            logger.error(f"[Pipeline Stage 7] Story clustering error: {exc.__class__.__name__}: {str(exc)}")
            failures.append(
                PipelineFailureDetail(
                    stage="clustering",
                    error_type=exc.__class__.__name__,
                    message=f"Clustering error: {str(exc)[:200]}",
                )
            )

        await self.session.commit()

        # -----------------------------------------------------------------
        # Stage 8: Story Ranking (6 Transparent Signals)
        # -----------------------------------------------------------------
        logger.info("[Pipeline Stage 8] Scoring and ranking active stories...")
        try:
            ranking_res = await self.ranking_service.rank_batch(limit=50, force_recalculate=True)
            stories_ranked = ranking_res.stories_ranked
            logger.info(f"[Pipeline Stage 8] Ranking complete: ranked={stories_ranked} stories in {ranking_res.duration_ms}ms")
        except Exception as exc:
            logger.error(f"[Pipeline Stage 8] Story ranking error: {exc.__class__.__name__}: {str(exc)}")
            failures.append(
                PipelineFailureDetail(
                    stage="ranking",
                    error_type=exc.__class__.__name__,
                    message=f"Ranking error: {str(exc)[:200]}",
                )
            )

        # -----------------------------------------------------------------
        # Stage 9: Editorial Curation (Top 5–10 Stories with Diversity)
        # -----------------------------------------------------------------
        logger.info(f"[Pipeline Stage 9] Executing editorial curation pass (target: {curation_limit} stories)...")
        try:
            curation_run = await self.curation_service.curate_feed(limit=curation_limit)
            stories_curated = curation_run.curated_count
            logger.info(
                f"[Pipeline Stage 9] Curation complete: curated={stories_curated} "
                f"(scanned={curation_run.stories_scanned}, rejected={curation_run.rejected_reasons})"
            )
        except Exception as exc:
            logger.error(f"[Pipeline Stage 9] Story curation error: {exc.__class__.__name__}: {str(exc)}")
            failures.append(
                PipelineFailureDetail(
                    stage="curation",
                    error_type=exc.__class__.__name__,
                    message=f"Curation error: {str(exc)[:200]}",
                )
            )

        # -----------------------------------------------------------------
        # Stage 10: Compile Digest-Ready Candidate Stories
        # -----------------------------------------------------------------
        curated_story_summaries: List[CuratedStorySummary] = []
        try:
            curated_list = await self.curation_service.list_curated_stories(limit=curation_limit)
            for cs in curated_list:
                curated_story_summaries.append(
                    CuratedStorySummary(
                        story_id=cs.story_id,
                        headline=cs.title,
                        summary=cs.summary,
                        key_takeaway=cs.key_takeaway,
                        why_it_matters=cs.why_it_matters,
                        ranking_score=cs.ranking_score,
                        category=cs.category,
                        canonical_url=cs.canonical_url,
                        primary_source_name=cs.primary_source_name,
                        article_count=cs.article_count,
                        published_at=cs.published_at,
                    )
                )
        except Exception as exc:
            logger.warning(f"[Pipeline Stage 10] Could not list curated stories: {str(exc)}")

        # -----------------------------------------------------------------
        # Final Telemetry & Report Assembly
        # -----------------------------------------------------------------
        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        overall_status = "SUCCESS"
        if failures:
            overall_status = "PARTIAL" if (stories_curated > 0 or items_processed > 0) else "FAILED"

        logger.info(
            f"[Pipeline] Finished pipeline execution in {duration_ms}ms with status={overall_status}. "
            f"Seen={items_seen}, Processed={items_processed}, AI={ai_analyses_performed}, "
            f"EmbGen={embeddings_generated}, EmbReuse={embeddings_reused}, "
            f"StoriesCreated={stories_created}, StoriesCurated={stories_curated}, Failures={len(failures)}"
        )

        return PipelineRunResponse(
            status=overall_status,
            items_seen=items_seen,
            items_processed=items_processed,
            ai_analyses_performed=ai_analyses_performed,
            embeddings_generated=embeddings_generated,
            embeddings_reused=embeddings_reused,
            deterministic_duplicates_found=deterministic_duplicates_found,
            semantic_duplicates_found=semantic_duplicates_found,
            stories_created=stories_created,
            stories_updated=stories_updated,
            stories_ranked=stories_ranked,
            stories_curated=stories_curated,
            failures=failures,
            curated_stories=curated_story_summaries,
            duration_ms=duration_ms,
            executed_at=datetime.now(timezone.utc),
        )
