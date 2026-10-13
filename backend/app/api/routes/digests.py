from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.newsletter import (
    DeliveryRecordResponse,
    DigestGenerateRequest,
    DigestResponse,
    DigestSendRequest,
    DigestSendResponse,
    DigestStoryItem,
)
from app.core.config import settings
from app.infrastructure.database.session import get_db
from app.infrastructure.models.digest import Digest
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.digest_service import DigestService
from app.services.email_service import DeliveryService

router = APIRouter(prefix="/digests", tags=["Daily Digests"])


def _format_digest_response(digest: Digest) -> DigestResponse:
    """Helper to transform Digest entity into public DigestResponse schema."""
    stories: List[DigestStoryItem] = []
    for ds in digest.digest_stories:
        story = ds.story
        source_name = None
        content_type = "ARTICLE"
        canonical_url = None
        published_at = None
        category = None

        if story:
            category = story.category
            published_at = story.published_at
            if story.canonical_content_item:
                canonical_url = story.canonical_content_item.canonical_url
                content_type = story.canonical_content_item.content_type
                if story.canonical_content_item.source:
                    source_name = story.canonical_content_item.source.name

        stories.append(
            DigestStoryItem(
                position=ds.position,
                story_id=ds.story_id,
                title=ds.headline_override or (story.headline if story else "Untitled"),
                source=source_name,
                content_type=content_type,
                category=category,
                summary=ds.summary_override or (story.summary if story else None),
                why_it_matters=ds.why_it_matters_override or (story.why_it_matters if story else None),
                canonical_url=canonical_url,
                published_at=published_at,
                ranking_score=ds.ranking_score_snapshot,
            )
        )

    return DigestResponse(
        id=digest.id,
        title=digest.title,
        digest_date=digest.digest_date,
        status=digest.status,
        story_count=digest.story_count,
        stories=stories,
        generated_at=digest.generated_at,
    )


@router.post(
    "/generate",
    response_model=DigestResponse,
    summary="Generate or retrieve daily digest issue",
)
async def generate_digest(
    request: DigestGenerateRequest = DigestGenerateRequest(),
    session: AsyncSession = Depends(get_db),
) -> DigestResponse:
    """Deterministically compile or retrieve the daily digest issue from curated stories."""
    service = DigestService(session=session)
    digest = await service.generate_daily_digest(
        target_date=request.target_date,
        force=request.force,
    )
    return _format_digest_response(digest)


@router.get(
    "/latest",
    response_model=DigestResponse,
    summary="Get latest digest issue",
)
async def get_latest_digest(
    session: AsyncSession = Depends(get_db),
) -> DigestResponse:
    """Retrieve the most recent daily digest issue."""
    service = DigestService(session=session)
    digest = await service.get_latest_digest()
    if not digest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No digest issues found. Please generate one first.",
        )
    return _format_digest_response(digest)


@router.get(
    "/{digest_id}",
    response_model=DigestResponse,
    summary="Get digest issue by ID",
)
async def get_digest_by_id(
    digest_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> DigestResponse:
    """Fetch a specific digest issue by ID."""
    service = DigestService(session=session)
    digest = await service.get_digest_by_id(digest_id)
    if not digest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Digest issue {digest_id} not found.",
        )
    return _format_digest_response(digest)


@router.get(
    "/{digest_id}/preview",
    response_class=HTMLResponse,
    summary="Preview raw HTML email template for a digest issue",
)
async def preview_digest_html(
    digest_id: uuid.UUID,
    token: Optional[str] = Query(None, description="Optional subscriber token for personalized preview"),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Return responsive HTML newsletter template for browser/iframe preview."""
    service = DigestService(session=session)
    digest = await service.get_digest_by_id(digest_id)
    if not digest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Digest issue {digest_id} not found.",
        )

    sub_token = "preview-placeholder-token"
    if token:
        subscriber_repo = SubscriberRepository(session)
        subscriber = await subscriber_repo.get_by_token(token.strip())
        if subscriber and subscriber.is_active:
            sub_token = subscriber.unsubscribe_token

    preview_unsub = f"{settings.public_api_v1_url}/newsletter/unsubscribe?token={sub_token}"
    preview_pref = f"{settings.effective_newsletter_base_url}#customize-newsletter"

    # Render with preview links
    preview_html = (
        digest.html_content.replace("{{ unsubscribe_url }}", preview_unsub)
        .replace("{{ preferences_url }}", preview_pref)
    )
    return HTMLResponse(content=preview_html)


@router.post(
    "/{digest_id}/send",
    response_model=DigestSendResponse,
    summary="Dispatch digest issue to active subscribers",
)
async def send_digest(
    digest_id: uuid.UUID,
    request: DigestSendRequest = DigestSendRequest(),
    session: AsyncSession = Depends(get_db),
) -> DigestSendResponse:
    """Dispatch the digest issue to active subscribers with strict idempotency."""
    service = DeliveryService(session=session)
    try:
        return await service.send_digest(digest_id=digest_id, dry_run=request.dry_run)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/{digest_id}/deliveries",
    response_model=List[DeliveryRecordResponse],
    summary="List delivery audit records for a digest",
)
async def list_digest_deliveries(
    digest_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> List[DeliveryRecordResponse]:
    """Audit delivery logs for a given digest issue."""
    repo = DigestRepository(session=session)
    records = await repo.list_deliveries_for_digest(digest_id)
    return [DeliveryRecordResponse.model_validate(r) for r in records]
