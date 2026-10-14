import hmac
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import (
    DailyDispatchRequest,
    DailyDispatchResponse,
    DispatchStatusResponse,
    ScheduledTickRequest,
    ScheduledTickResponse,
)
from app.infrastructure.database.session import get_db
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.digest import DeliveryRecord
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.dispatch_service import DailyDispatchService

router = APIRouter(prefix="/dispatch", tags=["Daily Dispatch & Automation"])


def verify_dispatch_auth(
    authorization: Optional[str] = Header(None),
    x_dispatch_token: Optional[str] = Header(None),
) -> None:
    """Validate dispatch trigger authorization if DISPATCH_SECRET_TOKEN is configured."""
    secret_token = (settings.DISPATCH_SECRET_TOKEN or "").strip()
    if not secret_token:
        if settings.is_production_like:
            logger.error(
                "Dispatch execution rejected: DISPATCH_SECRET_TOKEN is not configured in production/staging environment."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dispatch endpoint disabled: DISPATCH_SECRET_TOKEN must be configured in production or staging",
            )
        return

    token = None
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
    elif isinstance(x_dispatch_token, str):
        token = x_dispatch_token.strip()

    if not token or not hmac.compare_digest(token, secret_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing dispatch authorization token",
        )


@router.post(
    "/daily",
    response_model=DailyDispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute automated daily ingestion, synthesis, digest generation, and delivery dispatch",
)
async def run_daily_dispatch(
    request: Optional[DailyDispatchRequest] = None,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_dispatch_auth),
) -> DailyDispatchResponse:
    """
    Trigger end-to-end daily intelligence dispatch workflow:
    1. Bounded RSS ingestion & AI processing (Gemini gemini-3.6-flash)
    2. Demand-driven embedding generation (Gemini gemini-embedding-001, 1536-dim, no bulk historical embedding)
    3. Multi-source story clustering, ranking, and editorial curation
    4. Deterministic Daily Digest compilation with date idempotency
    5. Newsletter dispatch to active subscribers with duplicate-send prevention
    """
    req = request or DailyDispatchRequest()
    service = DailyDispatchService(session=session)
    return await service.run_daily_dispatch(req)


@router.post(
    "/scheduler",
    response_model=ScheduledTickResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute decoupled recurring scheduler tick (due delivery + news collection if scheduled)",
)
async def run_scheduled_tick(
    request: Optional[ScheduledTickRequest] = None,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_dispatch_auth),
) -> ScheduledTickResponse:
    """
    Trigger scheduled execution tick:
    - Ingests and curates news only on the designated collection schedule (or forced).
    - Determines subscribers due now based on preferred delivery time and IANA timezone.
    - Dispatches personalized digests reusing the curated story set.
    """
    req = request or ScheduledTickRequest()
    service = DailyDispatchService(session=session)
    return await service.run_scheduled_tick(req)


@router.post(
    "/cleanup",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Execute safe retention cleanup cycle",
)
async def run_retention_cleanup(
    news_retention_days: Optional[int] = None,
    delivery_retention_days: Optional[int] = None,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_dispatch_auth),
) -> Dict[str, Any]:
    """Manually trigger safe, non-destructive retention cleanup."""
    service = DailyDispatchService(session=session)
    return await service.cleanup_old_data(
        news_retention_days=news_retention_days,
        delivery_retention_days=delivery_retention_days,
    )


@router.get(
    "/status",
    response_model=DispatchStatusResponse,
    summary="Get dispatch automation status and latest digest issue telemetry",
)
async def get_dispatch_status(
    session: AsyncSession = Depends(get_db),
) -> DispatchStatusResponse:
    """Inspect dispatch scheduling readiness, configuration flags, and latest issue state."""
    digest_repo = DigestRepository(session)
    subscriber_repo = SubscriberRepository(session)

    latest_digest = await digest_repo.get_latest()
    active_daily = await subscriber_repo.count_active(frequency="DAILY")
    active_weekly = await subscriber_repo.count_active(frequency="WEEKLY")

    # Latest successful delivery
    latest_deliv_stmt = (
        select(DeliveryRecord.delivered_at)
        .where(DeliveryRecord.status == "SENT")
        .order_by(desc(DeliveryRecord.delivered_at))
        .limit(1)
    )
    latest_deliv_res = await session.execute(latest_deliv_stmt)
    latest_delivery_at = latest_deliv_res.scalar_one_or_none()

    # Latest news item collection
    latest_item_stmt = (
        select(ContentItem.fetched_at)
        .order_by(desc(ContentItem.fetched_at))
        .limit(1)
    )
    latest_item_res = await session.execute(latest_item_stmt)
    latest_collection_at = latest_item_res.scalar_one_or_none()

    return DispatchStatusResponse(
        status="READY",
        environment=settings.ENVIRONMENT,
        cron_enabled=settings.DISPATCH_CRON_ENABLED,
        schedule_hour_utc=settings.DISPATCH_SCHEDULE_HOUR_UTC,
        schedule_minute_utc=settings.DISPATCH_SCHEDULE_MINUTE_UTC,
        delivery_scheduler_frequency=settings.DISPATCH_SCHEDULER_FREQUENCY,
        news_collection_schedule_utc=settings.NEWS_COLLECTION_SCHEDULE_UTC,
        resend_configured=bool(settings.RESEND_API_KEY),
        webhook_secret_configured=bool(settings.RESEND_WEBHOOK_SECRET),
        dispatch_token_configured=bool(settings.DISPATCH_SECRET_TOKEN),
        latest_digest_date=latest_digest.digest_date if latest_digest else None,
        latest_digest_id=latest_digest.id if latest_digest else None,
        latest_digest_stories=latest_digest.story_count if latest_digest else 0,
        active_daily_subscribers=active_daily,
        active_weekly_subscribers=active_weekly,
        latest_delivery_at=latest_delivery_at,
        latest_collection_at=latest_collection_at,
        news_retention_days=settings.NEWS_RETENTION_DAYS,
        delivery_record_retention_days=settings.DELIVERY_RECORD_RETENTION_DAYS,
    )
