from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import (
    DailyDispatchRequest,
    DailyDispatchResponse,
    DispatchStatusResponse,
)
from app.infrastructure.database.session import get_db
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
        if settings.ENVIRONMENT in ("production", "staging"):
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

    if not token or token != secret_token:
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
    active_subs = await subscriber_repo.count_active(frequency="DAILY")

    return DispatchStatusResponse(
        status="READY",
        environment=settings.ENVIRONMENT,
        cron_enabled=settings.DISPATCH_CRON_ENABLED,
        schedule_hour_utc=settings.DISPATCH_SCHEDULE_HOUR_UTC,
        schedule_minute_utc=settings.DISPATCH_SCHEDULE_MINUTE_UTC,
        resend_configured=bool(settings.RESEND_API_KEY),
        webhook_secret_configured=bool(settings.RESEND_WEBHOOK_SECRET),
        dispatch_token_configured=bool(settings.DISPATCH_SECRET_TOKEN),
        latest_digest_date=latest_digest.digest_date if latest_digest else None,
        latest_digest_id=latest_digest.id if latest_digest else None,
        latest_digest_stories=latest_digest.story_count if latest_digest else 0,
        active_daily_subscribers=active_subs,
    )
