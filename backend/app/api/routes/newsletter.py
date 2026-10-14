import json
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.newsletter import (
    PreferencesUpdateRequest,
    SubscribeRequest,
    SubscribeResponse,
    SubscriberProfileResponse,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from app.domain.models.webhook import WebhookProcessResult
from app.infrastructure.database.session import get_db
from app.services.subscriber_service import SubscriberService
from app.services.webhook_service import ResendWebhookService, verify_svix_signature

router = APIRouter(prefix="/newsletter", tags=["Newsletter & Subscribers"])


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_200_OK,
    summary="Subscribe to AI News Daily Digest",
)
async def subscribe_to_newsletter(
    request: SubscribeRequest,
    session: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    """Subscribe a new user or update preferences for an existing subscriber idempotently."""
    service = SubscriberService(session=session)
    subscriber, is_created = await service.subscribe(request)
    was_reactivated = getattr(subscriber, "_was_reactivated", False)
    
    if is_created:
        msg = "You're in! Welcome to AI News Intelligence. Your confirmation email is on its way."
    elif was_reactivated:
        msg = "Welcome back! Your subscription to AI News Intelligence has been reactivated."
    else:
        msg = "Welcome back! Your subscription preferences have been updated."

    return SubscribeResponse(
        status="success",
        message=msg,
        email=subscriber.email,
        is_active=subscriber.is_active,
        created=is_created,
        reactivated=was_reactivated,
    )


@router.post(
    "/unsubscribe",
    response_model=UnsubscribeResponse,
    summary="Unsubscribe from newsletter via token or email",
)
async def unsubscribe_post(
    request: UnsubscribeRequest,
    session: AsyncSession = Depends(get_db),
) -> UnsubscribeResponse:
    """Unsubscribe a user from the newsletter."""
    if not request.token and not request.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either token or email must be provided to unsubscribe.",
        )
    service = SubscriberService(session=session)
    success = await service.unsubscribe(token=request.token, email=request.email)
    if not success:
        return UnsubscribeResponse(
            status="not_found",
            message="Subscriber record was not found or already unsubscribed.",
        )
    return UnsubscribeResponse(
        status="success",
        message="You have been successfully unsubscribed from AI News Daily Digest.",
    )


@router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click unsubscribe endpoint for email links",
)
async def unsubscribe_get(
    token: Optional[str] = Query(None, description="Subscriber unsubscribe token"),
    email: Optional[str] = Query(None, description="Subscriber email address"),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Handle one-click unsubscribe links from delivered emails."""
    clean_token = token.strip() if token else None
    clean_email = email.strip() if email else None

    success = False
    if clean_token or clean_email:
        service = SubscriberService(session=session)
        success = await service.unsubscribe(token=clean_token, email=clean_email)

    home_url = settings.effective_newsletter_base_url

    if success:
        page_title = "Unsubscribed — AI News Daily"
        badge_color = "#38bdf8"
        badge_bg = "rgba(56, 189, 248, 0.12)"
        icon_svg = """<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>"""
        heading = "You have been unsubscribed from AI News Daily."
        body_text = "Your subscription has been deactivated. You will no longer receive daily digests at this email address. If this was a mistake, you can re-subscribe at any time."
    else:
        page_title = "Subscription Notice — AI News Daily"
        badge_color = "#f59e0b"
        badge_bg = "rgba(245, 158, 11, 0.12)"
        icon_svg = """<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>"""
        heading = "That unsubscribe link is invalid or has already been used."
        body_text = "We could not find an active subscription for this link. If you have already unsubscribed, no further action is required."

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{page_title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background-color: #0b0f19;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 48px 36px;
      max-width: 520px;
      width: 100%;
      text-align: center;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
    }}
    .icon-container {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 72px;
      height: 72px;
      border-radius: 50%;
      background: {badge_bg};
      margin-bottom: 24px;
    }}
    .brand {{
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: {badge_color};
      margin-bottom: 12px;
    }}
    h1 {{
      font-size: 22px;
      font-weight: 700;
      color: #f1f5f9;
      line-height: 1.4;
      margin-bottom: 16px;
    }}
    p {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.6;
      margin-bottom: 28px;
    }}
    .action-btn {{
      display: inline-block;
      background: #0284c7;
      color: #ffffff;
      text-decoration: none;
      font-weight: 600;
      font-size: 13px;
      padding: 10px 24px;
      border-radius: 8px;
      transition: background 0.2s ease;
    }}
    .action-btn:hover {{
      background: #0369a1;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon-container">
      {icon_svg}
    </div>
    <div class="brand">AI News Intelligence • Daily Digest</div>
    <h1>{heading}</h1>
    <p>{body_text}</p>
    <div>
      <a href="{home_url}" class="action-btn">Return to Homepage &rarr;</a>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.put(
    "/preferences",
    response_model=SubscriberProfileResponse,
    summary="Update subscriber preferences",
)
async def update_preferences(
    request: PreferencesUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> SubscriberProfileResponse:
    """Update topic, frequency, and delivery preferences for an existing subscriber."""
    service = SubscriberService(session=session)
    updated = await service.update_preferences(request)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscriber not found. Please subscribe first.",
        )
    return SubscriberProfileResponse.model_validate(updated)


@router.get(
    "/subscribers/count",
    summary="Get active subscriber count",
)
async def get_subscriber_count(
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return active subscriber count."""
    service = SubscriberService(session=session)
    count = await service.get_active_count()
    return {"active_subscribers": count}


@router.post(
    "/webhooks/resend",
    response_model=Union[WebhookProcessResult, List[WebhookProcessResult]],
    summary="Handle incoming Resend delivery webhooks",
)
async def handle_resend_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Any:
    """
    Process incoming delivery webhooks from Resend (via Svix protocol).
    Verifies cryptographic signatures against RESEND_WEBHOOK_SECRET,
    mitigates replay attacks, and idempotently updates delivery_records.
    """
    raw_body = await request.body()
    svix_id = request.headers.get("svix-id")
    svix_timestamp = request.headers.get("svix-timestamp")
    svix_signature = request.headers.get("svix-signature")

    # If webhook secret is configured, strictly enforce signature verification
    if settings.RESEND_WEBHOOK_SECRET:
        if not svix_id or not svix_timestamp or not svix_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required Svix signature headers (svix-id, svix-timestamp, svix-signature)",
            )
        is_valid, msg = verify_svix_signature(
            secret=settings.RESEND_WEBHOOK_SECRET,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
            body_bytes=raw_body,
            signature_header=svix_signature,
        )
        if not is_valid:
            logger.warning(f"Rejected unauthorized Resend webhook: {msg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid webhook signature: {msg}",
            )
    else:
        logger.info("RESEND_WEBHOOK_SECRET not set; proceeding in unverified mode.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON payload: {exc}",
        )

    service = ResendWebhookService(session=session)
    if isinstance(payload, list):
        results = [await service.process_event(item) for item in payload]
        return results
    elif isinstance(payload, dict):
        result = await service.process_event(payload)
        return result
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload must be a JSON object or array of objects",
        )

