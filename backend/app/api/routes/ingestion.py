import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.ingestion import (
    IngestionSummaryResponse,
    SourceIngestionResult,
)
from app.infrastructure.database.session import get_db
from app.ingestion.orchestrator import IngestionOrchestrator

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def get_orchestrator(session: AsyncSession = Depends(get_db)) -> IngestionOrchestrator:
    """Dependency provider injecting IngestionOrchestrator with database session."""
    return IngestionOrchestrator(session)


@router.post(
    "/rss",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion for all enabled RSS sources",
)
async def trigger_rss_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run RSS/Atom ingestion across all active RSS feeds registered in the Source Registry."""
    return await orchestrator.ingest_all_rss_sources()


@router.post(
    "/official",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion for all official AI organization sources",
)
async def trigger_official_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run ingestion across authoritative official AI sources (OpenAI, Anthropic, Google DeepMind)."""
    return await orchestrator.ingest_all_official_sources()


@router.post(
    "/arxiv",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion for all enabled ArXiv research sources",
)
async def trigger_arxiv_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run ArXiv research paper ingestion across all enabled ArXiv sources."""
    return await orchestrator.ingest_all_arxiv_sources()


@router.post(
    "/hacker-news",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion for all enabled Hacker News sources",
)
async def trigger_hacker_news_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run Hacker News community signal ingestion across all active HN sources."""
    return await orchestrator.ingest_all_hacker_news_sources()


@router.post(
    "/youtube",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion for all enabled YouTube sources",
)
async def trigger_youtube_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run YouTube video ingestion across all active YouTube channels registered in the Source Registry."""
    return await orchestrator.ingest_all_youtube_sources()


@router.post(
    "/sources/{source_id}",
    response_model=SourceIngestionResult,
    status_code=status.HTTP_200_OK,
    summary="Trigger ingestion for a single registered source",
)
async def trigger_source_ingestion(
    source_id: uuid.UUID,
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> SourceIngestionResult:
    """Run ingestion for a specific registered source by UUID."""
    return await orchestrator.ingest_source_by_id(source_id)


@router.post(
    "/all",
    response_model=IngestionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger batch ingestion across all active registered sources",
)
async def trigger_all_sources_ingestion(
    orchestrator: IngestionOrchestrator = Depends(get_orchestrator),
) -> IngestionSummaryResponse:
    """Run unified ingestion across all registered active sources (RSS, Web, ArXiv, Hacker News, YouTube)."""
    return await orchestrator.ingest_all_sources()

