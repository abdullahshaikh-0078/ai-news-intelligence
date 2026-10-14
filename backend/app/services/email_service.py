from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.newsletter import DigestSendResponse
from app.infrastructure.models.digest import Digest
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.digest_service import DigestService


@dataclass
class EmailSendResult:
    """Result from an email provider send attempt."""
    success: bool
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None


class BaseEmailProvider(ABC):
    """Abstract interface for transactional email delivery providers."""

    @abstractmethod
    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> EmailSendResult:
        """Send a single email message."""
        pass


class ResendEmailProvider(BaseEmailProvider):
    """Production email delivery provider using the Resend REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_email: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key or settings.RESEND_API_KEY
        self.from_email = from_email or settings.EMAIL_FROM
        self.timeout = timeout
        self.endpoint = "https://api.resend.com/emails"

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> EmailSendResult:
        """Send email via Resend API with comprehensive failure isolation and idempotency."""
        if not self.api_key:
            logger.error("Resend API key is not configured.")
            return EmailSendResult(
                success=False,
                error_message="RESEND_API_KEY is not configured",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AINewsIntelligence/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        payload: Dict[str, Any] = {
            "from": self.from_email,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        if text_content:
            payload["text"] = text_content
        if reply_to or settings.EMAIL_REPLY_TO:
            payload["reply_to"] = reply_to or settings.EMAIL_REPLY_TO

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.endpoint, json=payload, headers=headers)
                
                if response.status_code in (200, 201):
                    data = response.json()
                    message_id = data.get("id")
                    logger.info(f"Email sent successfully via Resend to recipient (id={message_id})")
                    return EmailSendResult(
                        success=True,
                        provider_message_id=message_id,
                    )
                else:
                    err_msg = f"Resend API error {response.status_code}: {response.text}"
                    logger.warning(err_msg)
                    return EmailSendResult(
                        success=False,
                        error_message=err_msg,
                    )
        except httpx.TimeoutException as exc:
            err_msg = f"Timeout connecting to Resend API: {exc}"
            logger.error(err_msg)
            return EmailSendResult(success=False, error_message=err_msg)
        except Exception as exc:
            err_msg = f"Unexpected error sending email via Resend: {exc}"
            logger.error(err_msg)
            return EmailSendResult(success=False, error_message=err_msg)


class MockEmailProvider(BaseEmailProvider):
    """In-memory mock email provider for unit and regression testing."""

    def __init__(self, should_succeed: bool = True, failure_message: Optional[str] = None):
        self.should_succeed = should_succeed
        self.failure_message = failure_message or "Simulated provider error"
        self.sent_messages: List[Dict[str, Any]] = []

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> EmailSendResult:
        """Simulate email send."""
        if not self.should_succeed:
            return EmailSendResult(
                success=False,
                error_message=self.failure_message,
            )

        mock_id = f"mock_{uuid.uuid4().hex[:12]}"
        self.sent_messages.append({
            "to": to_email,
            "subject": subject,
            "html": html_content,
            "text": text_content,
            "reply_to": reply_to,
            "idempotency_key": idempotency_key,
            "id": mock_id,
            "sent_at": datetime.now(timezone.utc),
        })
        return EmailSendResult(
            success=True,
            provider_message_id=mock_id,
        )


class WelcomeEmailService:
    """Service for compiling and sending immediate confirmation welcome emails upon subscription."""

    def __init__(self, email_provider: Optional[BaseEmailProvider] = None):
        if email_provider:
            self.email_provider = email_provider
        elif settings.RESEND_API_KEY:
            self.email_provider = ResendEmailProvider()
        else:
            self.email_provider = MockEmailProvider()

    def render_welcome_html(
        self,
        subscriber: Subscriber,
        app_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        preferences_url: Optional[str] = None,
    ) -> str:
        """Render responsive, dark-mode HTML welcome email."""
        import html
        effective_app_url = app_url or settings.effective_newsletter_base_url
        effective_unsub_url = (
            unsubscribe_url
            or f"{settings.public_api_v1_url}/newsletter/unsubscribe?token={subscriber.unsubscribe_token}"
        )
        effective_pref_url = preferences_url or f"{effective_app_url}#customize-newsletter"

        topics_display = (
            ", ".join(subscriber.topics)
            if subscriber.topics
            else "All AI Intelligence Topics"
        )
        channel_display = getattr(subscriber, "preferred_channel", "EMAIL") or "EMAIL"
        frequency_display = getattr(subscriber, "frequency", "DAILY") or "DAILY"
        preferred_time_display = getattr(subscriber, "preferred_time", "08:00") or "08:00"
        timezone_display = getattr(subscriber, "timezone", "Asia/Kolkata") or "Asia/Kolkata"
        preferred_weekday_display = getattr(subscriber, "preferred_weekday", "MONDAY") or "MONDAY"

        if frequency_display.upper() == "WEEKLY":
            schedule_display = f"WEEKLY on {preferred_weekday_display} at {preferred_time_display} ({timezone_display})"
        else:
            schedule_display = f"DAILY at {preferred_time_display} ({timezone_display})"

        safe_name = html.escape(subscriber.name) if subscriber.name else "there"
        safe_topics = html.escape(topics_display)
        safe_channel = html.escape(channel_display)
        safe_schedule = html.escape(schedule_display)

        return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to AI News Intelligence</title>
  <style>
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    body {{ height: 100% !important; margin: 0 !important; padding: 0 !important; width: 100% !important; background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e2e8f0; }}
    @media only screen and (max-width: 640px) {{
      .email-container {{ width: 100% !important; border-radius: 0 !important; border: none !important; }}
      .header, .content, .footer {{ padding-left: 20px !important; padding-right: 20px !important; }}
    }}
  </style>
</head>
<body style="background-color: #0b0f19; margin: 0; padding: 0;">
  <!-- Preheader snippet -->
  <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; line-height: 1px; color: #fff; opacity: 0;">
    Your subscription to AI News Intelligence is confirmed. Here is what to expect from your daily briefing.
  </div>
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0b0f19; padding: 24px 0;">
    <tr>
      <td align="center">
        <div class="email-container" style="max-width: 600px; margin: 0 auto; background-color: #0f172a; border-radius: 12px; overflow: hidden; border: 1px solid #1e293b; text-align: left;">
          <!-- Header -->
          <div class="header" style="padding: 36px 32px 24px; border-bottom: 1px solid #1e293b; background: linear-gradient(180deg, #111c35 0%, #0f172a 100%);">
            <div style="display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; background-color: rgba(56, 189, 248, 0.12); color: #38bdf8; margin-bottom: 12px;">
              SUBSCRIPTION CONFIRMED
            </div>
            <h1 style="font-size: 24px; font-weight: 800; color: #f8fafc; margin: 0 0 8px 0; line-height: 1.3;">
              Welcome to AI News Intelligence
            </h1>
            <p style="font-size: 14px; color: #94a3b8; margin: 0; line-height: 1.5;">
              High-signal breakthroughs across Autonomous Agents, Foundation Models, and Engineering Systems.
            </p>
          </div>

            <!-- Content -->
          <div class="content" style="padding: 32px 32px 24px;">
            <p style="font-size: 15px; line-height: 1.6; color: #f1f5f9; margin: 0 0 20px 0;">
              Hello {safe_name},
            </p>
            <p style="font-size: 14px; line-height: 1.6; color: #cbd5e1; margin: 0 0 24px 0;">
              Thank you for subscribing to AI News Intelligence! You'll receive your AI news digest every day at your selected time.
            </p>

            <!-- Preferences Box -->
            <div style="background-color: #1e293b; border-radius: 8px; border: 1px solid #334155; padding: 20px; margin-bottom: 24px;">
              <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #38bdf8; margin-bottom: 12px;">
                Your Active Subscription Profile
              </div>
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td style="padding: 6px 0; font-size: 13px; color: #94a3b8; width: 140px; font-weight: 500;">Delivery Schedule:</td>
                  <td style="padding: 6px 0; font-size: 13px; color: #f8fafc; font-weight: 600;">{safe_schedule}</td>
                </tr>
                <tr>
                  <td style="padding: 6px 0; font-size: 13px; color: #94a3b8; font-weight: 500;">Channel:</td>
                  <td style="padding: 6px 0; font-size: 13px; color: #f8fafc; font-weight: 600;">{safe_channel}</td>
                </tr>
                <tr>
                  <td style="padding: 6px 0; font-size: 13px; color: #94a3b8; font-weight: 500;">Topics of Interest:</td>
                  <td style="padding: 6px 0; font-size: 13px; color: #38bdf8; font-weight: 600;">{safe_topics}</td>
                </tr>
              </table>
            </div>

            <!-- What to expect -->
            <div style="margin-bottom: 28px;">
              <h2 style="font-size: 16px; font-weight: 700; color: #f1f5f9; margin: 0 0 10px 0;">
                When to expect your first newsletter
              </h2>
              <p style="font-size: 14px; line-height: 1.6; color: #94a3b8; margin: 0 0 12px 0;">
                Our autonomous intelligence pipeline runs daily at <strong>03:30 UTC (09:00 AM IST)</strong>, collecting, deduplicating, and synthesizing emerging AI developments.
              </p>
              <p style="font-size: 13px; line-height: 1.5; color: #64748b; margin: 0;">
                If today's daily issue has already been delivered, your first briefing will arrive on tomorrow's scheduled morning cycle. Each issue delivers 5–10 highly relevant stories matching your selected areas of focus.
              </p>
            </div>

            <!-- CTA -->
            <div style="text-align: center; margin: 32px 0 16px;">
              <a href="{effective_app_url}" style="display: inline-block; padding: 12px 28px; background-color: #0284c7; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 600; border-radius: 8px; box-shadow: 0 4px 12px rgba(2, 132, 199, 0.3);" target="_blank" rel="noopener noreferrer">
                Explore AI News / Portfolio &rarr;
              </a>
            </div>
          </div>

          <!-- Footer -->
          <div class="footer" style="padding: 24px 32px; background-color: #090d16; text-align: center; border-top: 1px solid #1e293b;">
            <p style="font-size: 12px; color: #64748b; margin: 0 0 10px 0; line-height: 1.5;">
              AI News Intelligence Platform • Autonomous AI News Pipeline<br>
              Curated by Abdullah Shaikh • AI & Autonomous Systems
            </p>
            <div style="font-size: 12px;">
              <a href="{effective_pref_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">Manage Preferences</a> &bull;
              <a href="{effective_app_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">AI News / Portfolio</a> &bull;
              <a href="{effective_unsub_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">Unsubscribe</a>
            </div>
          </div>
        </div>
      </td>
    </tr>
  </table>
</body>
</html>"""

    def render_welcome_plain_text(
        self,
        subscriber: Subscriber,
        app_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        preferences_url: Optional[str] = None,
    ) -> str:
        """Render plain-text welcome confirmation."""
        effective_app_url = app_url or settings.effective_newsletter_base_url
        effective_unsub_url = (
            unsubscribe_url
            or f"{settings.public_api_v1_url}/newsletter/unsubscribe?token={subscriber.unsubscribe_token}"
        )
        effective_pref_url = preferences_url or f"{effective_app_url}#customize-newsletter"

        topics_display = (
            ", ".join(subscriber.topics)
            if subscriber.topics
            else "All AI Intelligence Topics"
        )
        channel_display = getattr(subscriber, "preferred_channel", "EMAIL") or "EMAIL"
        frequency_display = getattr(subscriber, "frequency", "DAILY") or "DAILY"
        preferred_time_display = getattr(subscriber, "preferred_time", "08:00") or "08:00"
        timezone_display = getattr(subscriber, "timezone", "Asia/Kolkata") or "Asia/Kolkata"
        preferred_weekday_display = getattr(subscriber, "preferred_weekday", "MONDAY") or "MONDAY"
        if frequency_display.upper() == "WEEKLY":
            schedule_display = f"WEEKLY on {preferred_weekday_display} at {preferred_time_display} ({timezone_display})"
        else:
            schedule_display = f"DAILY at {preferred_time_display} ({timezone_display})"

        name_display = subscriber.name if subscriber.name else "there"

        lines = [
            "=== WELCOME TO AI NEWS INTELLIGENCE ===",
            "You're subscribed to AI News Intelligence",
            "=" * 50,
            "",
            f"Hello {name_display},",
            "",
            "Thank you for subscribing to AI News Intelligence.",
            "You'll receive your AI news digest every day at your selected time.",
            "",
            "YOUR SUBSCRIPTION PROFILE:",
            f"- Delivery Schedule: {schedule_display}",
            f"- Channel: {channel_display}",
            f"- Topics: {topics_display}",
            "",
            "WHEN TO EXPECT YOUR FIRST NEWSLETTER:",
            "Our autonomous intelligence pipeline runs daily at 03:30 UTC (09:00 AM IST).",
            "If today's daily issue has already been delivered, your first briefing will arrive",
            "on tomorrow's scheduled morning cycle.",
            "",
            "=" * 50,
            f"Manage Preferences: {effective_pref_url}",
            f"AI News / Portfolio: {effective_app_url}",
            f"Unsubscribe: {effective_unsub_url}",
        ]
        return "\n".join(lines)

    async def send_welcome_email(
        self,
        subscriber: Subscriber,
        app_url: Optional[str] = None,
        unsubscribe_url: Optional[str] = None,
        preferences_url: Optional[str] = None,
    ) -> EmailSendResult:
        """
        Send immediate welcome email confirmation to the subscriber.
        Uses idempotency key based on subscriber UUID to prevent duplicate messages.
        """
        if not subscriber.email:
            return EmailSendResult(success=False, error_message="Subscriber has no email address")

        subject = "You're subscribed to AI News Intelligence"
        html_content = self.render_welcome_html(
            subscriber=subscriber,
            app_url=app_url,
            unsubscribe_url=unsubscribe_url,
            preferences_url=preferences_url,
        )
        text_content = self.render_welcome_plain_text(
            subscriber=subscriber,
            app_url=app_url,
            unsubscribe_url=unsubscribe_url,
            preferences_url=preferences_url,
        )

        idempotency_key = f"welcome-{subscriber.id}"
        return await self.email_provider.send_email(
            to_email=subscriber.email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            idempotency_key=idempotency_key,
        )


class DeliveryService:
    """Service orchestrating newsletter delivery to active subscribers with strict idempotency."""

    def __init__(
        self,
        session: AsyncSession,
        email_provider: Optional[BaseEmailProvider] = None,
        digest_service: Optional[Any] = None,
    ):
        self.session = session
        self.digest_repo = DigestRepository(session)
        self.subscriber_repo = SubscriberRepository(session)
        self.digest_service = digest_service or DigestService(session=session)
        # Default to Resend if key exists, otherwise Mock provider with graceful logging
        if email_provider:
            self.email_provider = email_provider
        elif settings.RESEND_API_KEY:
            self.email_provider = ResendEmailProvider()
        else:
            self.email_provider = MockEmailProvider()

    @staticmethod
    def _extract_curated_stories(digest: Digest) -> List[CuratedStoryResponse]:
        """Convert digest story associations into normalized CuratedStoryResponse models."""
        curated: List[CuratedStoryResponse] = []
        stories_assoc = getattr(digest, "digest_stories", None) or []
        for ds in stories_assoc:
            story = getattr(ds, "story", None)
            source_name = None
            content_type = "ARTICLE"
            canonical_url = None
            published_at = None
            category = None
            topics: List[str] = []

            if story:
                category = getattr(story, "category", None)
                published_at = getattr(story, "published_at", None)
                canonical_item = getattr(story, "canonical_content_item", None)
                if canonical_item:
                    canonical_url = getattr(canonical_item, "canonical_url", None)
                    content_type = getattr(canonical_item, "content_type", "ARTICLE")
                    ai_topics = getattr(canonical_item, "ai_topics", None)
                    if ai_topics and isinstance(ai_topics, list):
                        topics = [str(t) for t in ai_topics]
                    src = getattr(canonical_item, "source", None)
                    if src:
                        source_name = getattr(src, "name", None)
                if not topics and category:
                    topics = [category]

            curated.append(
                CuratedStoryResponse(
                    story_id=ds.story_id,
                    title=getattr(ds, "headline_override", None) or (story.headline if story else "Untitled"),
                    summary=getattr(ds, "summary_override", None) or (story.summary if story else None),
                    key_takeaway=getattr(story, "key_takeaway", None) if story else None,
                    why_it_matters=getattr(ds, "why_it_matters_override", None) or (getattr(story, "why_it_matters", None) if story else None),
                    ranking_score=getattr(ds, "ranking_score_snapshot", 0.0) or (getattr(story, "ranking_score", 0.0) if story else 0.0),
                    category=category,
                    topics=topics,
                    canonical_url=canonical_url,
                    content_type=content_type or "ARTICLE",
                    primary_source_name=source_name,
                    published_at=published_at,
                )
            )
        return curated

    @classmethod
    def _filter_stories_for_subscriber(
        cls,
        stories: List[CuratedStoryResponse],
        subscriber_topics: Optional[List[str]],
        content_preferences: Optional[Dict[str, Any]] = None,
    ) -> List[CuratedStoryResponse]:
        """Filter stories to match subscriber topic and content preferences.
        If none specified, returns all stories.
        """
        candidate_stories = list(stories)

        # 1. Content preferences: min_score filter and excluded_topics filter
        pref_dict = content_preferences or {}
        if isinstance(pref_dict, dict):
            min_score = pref_dict.get("min_score")
            if min_score is not None:
                try:
                    min_score_float = float(min_score)
                    candidate_stories = [s for s in candidate_stories if s.ranking_score >= min_score_float]
                except (ValueError, TypeError):
                    pass

            excluded = pref_dict.get("excluded_topics") or []
            if isinstance(excluded, list) and excluded:
                clean_excluded = [str(x).strip().lower() for x in excluded if str(x).strip()]
                candidate_stories = [
                    s for s in candidate_stories
                    if not any(
                        ex in (s.category or "").lower() or any(ex in str(t).lower() for t in s.topics)
                        for ex in clean_excluded
                    )
                ]

        # 2. Topic preference matching
        if not subscriber_topics:
            return candidate_stories

        clean_sub_topics = [
            str(t).strip().lower() for t in subscriber_topics if isinstance(t, str) and str(t).strip()
        ]
        if not clean_sub_topics:
            return candidate_stories

        matched: List[CuratedStoryResponse] = []
        for s in candidate_stories:
            story_tokens = []
            if s.category:
                story_tokens.append(s.category.strip().lower())
            for t in s.topics:
                if isinstance(t, str) and t.strip():
                    story_tokens.append(t.strip().lower())

            is_match = any(
                sub_t in st or st in sub_t
                for sub_t in clean_sub_topics
                for st in story_tokens
            )
            if is_match:
                matched.append(s)

        return matched

    async def send_digest(
        self,
        digest_id: uuid.UUID,
        dry_run: bool = False,
        frequency: Optional[str] = "DAILY",
    ) -> DigestSendResponse:
        """Dispatch a digest issue to all active subscribers matching the frequency filter."""
        subscribers = await self.subscriber_repo.list_active(frequency=frequency)
        return await self.send_digest_to_subscribers(
            digest_id=digest_id,
            subscribers=subscribers,
            dry_run=dry_run,
        )

    async def send_digest_to_subscribers(
        self,
        digest_id: uuid.UUID,
        subscribers: List[Subscriber],
        dry_run: bool = False,
    ) -> DigestSendResponse:
        """
        Dispatch a digest issue to a specific list of due subscribers with concurrency-safe deduplication.

        Concurrency & Idempotency Architecture:
        - Atomic Claims: Acquires an atomic claim (status='PENDING') backed by the unique constraint
          uq_delivery_records_digest_subscriber before making external network calls.
        - Lock Isolation: Claims are committed immediately before calling the email provider so NO
          database locks or transactions are held across slow external HTTP requests.
        - Two-Phase Reality: External HTTP email providers do not participate in two-phase commit (2PC).
          Therefore, exactly-once delivery cannot be theoretically guaranteed over unreliable networks;
          this architecture guarantees at-most-once initiation per worker and safe at-least-once retryability.
        - Provider Confirmation: Deliveries are only marked status='SENT' after provider confirms success.
        - Fault Isolation: Network/provider failures mark the delivery status='FAILED' for safe retry.
        - Stale Claim Recovery: Claims stalled in status='PENDING' for >15 minutes can be safely reclaimed.
        """
        digest = await self.digest_repo.get_with_stories(digest_id)
        if not digest:
            raise ValueError(f"Digest issue {digest_id} not found.")

        total_subscribers = len(subscribers)
        sent_count = 0
        failed_count = 0
        skipped_count = 0

        # Safety: Empty digest guard prevents delivering blank newsletter issues
        if digest.story_count == 0:
            logger.warning(
                f"Digest issue {digest_id} has 0 curated stories. Skipping delivery to avoid blank newsletters."
            )
            return DigestSendResponse(
                digest_id=digest.id,
                total_subscribers=total_subscribers,
                sent_count=0,
                failed_count=0,
                skipped_count=total_subscribers,
                dry_run=dry_run,
            )

        title = digest.title
        html_content = digest.html_content
        plain_text_content = digest.plain_text_content
        curated_stories = self._extract_curated_stories(digest)

        provider_name = (
            "RESEND" if isinstance(self.email_provider, ResendEmailProvider) else "MOCK"
        )

        for sub in subscribers:
            # 1. Enforce channel preference: skip non-EMAIL preferences
            channel = getattr(sub, "preferred_channel", "EMAIL") or "EMAIL"
            if str(channel).upper() != "EMAIL":
                logger.info(
                    f"Subscriber {sub.email} preferred channel is '{channel}' (not EMAIL). Skipping email dispatch."
                )
                skipped_count += 1
                continue

            # 2. Enforce subscriber topic and content preference personalization
            raw_topics = getattr(sub, "topics", None) or []
            content_prefs = getattr(sub, "content_preferences", None) or {}
            sub_topics = [
                str(t).strip().lower() for t in raw_topics if isinstance(t, str) and str(t).strip()
            ]
            has_preference = bool(sub_topics) or bool(content_prefs)

            if has_preference and curated_stories:
                matched_stories = self._filter_stories_for_subscriber(
                    curated_stories, sub_topics, content_preferences=content_prefs
                )
                if not matched_stories:
                    logger.info(
                        f"Subscriber {sub.email} configured topics {sub_topics} and prefs {content_prefs} but 0 stories matched. "
                        "Skipping delivery to prevent delivering irrelevant or empty content."
                    )
                    skipped_count += 1
                    continue
            else:
                matched_stories = curated_stories

            if dry_run:
                existing_delivery = await self.digest_repo.get_delivery_record(digest.id, sub.id)
                if existing_delivery and existing_delivery.status == "SENT":
                    logger.info(f"Subscriber {sub.email} already received digest {digest.id}. Skipping.")
                    skipped_count += 1
                    continue
                logger.info(f"[DRY RUN] Would send digest {digest.id} to {sub.email} ({len(matched_stories)} stories)")
                sent_count += 1
                continue

            # Atomically claim authorization to deliver to this subscriber
            claim = await self.digest_repo.claim_delivery(
                digest_id=digest.id,
                subscriber_id=sub.id,
                recipient_email=sub.email,
                provider=provider_name,
            )
            if not claim:
                logger.info(
                    f"Delivery to subscriber {sub.email} for digest {digest.id} skipped (already sent or claimed by another worker)."
                )
                skipped_count += 1
                continue

            claim_token = (
                claim.metadata_json.get("claim_token")
                if isinstance(claim.metadata_json, dict)
                else None
            )

            # Persist the PENDING claim immediately so concurrent workers see it
            # and no DB transactions/locks are held open across the external email HTTP call
            await self.session.commit()

            # Personalize links
            unsubscribe_link = (
                f"{settings.public_api_v1_url}/newsletter/unsubscribe?token={sub.unsubscribe_token}"
            )
            preferences_link = f"{settings.effective_newsletter_base_url}#customize-newsletter"
            app_link = settings.effective_newsletter_base_url

            # Render customized HTML/text if subscriber has personalized topic subset
            if has_preference and matched_stories and len(matched_stories) < len(curated_stories):
                personalized_html = self.digest_service.render_html_digest(
                    title=title,
                    issue_date=digest.digest_date,
                    stories=matched_stories,
                    unsubscribe_url=unsubscribe_link,
                    preferences_url=preferences_link,
                    app_url=app_link,
                )
                personalized_text = self.digest_service.render_plain_text_digest(
                    title=title,
                    issue_date=digest.digest_date,
                    stories=matched_stories,
                    unsubscribe_url=unsubscribe_link,
                    preferences_url=preferences_link,
                    app_url=app_link,
                )
            else:
                personalized_html = (
                    html_content.replace("{{ unsubscribe_url }}", unsubscribe_link)
                    .replace("{{ preferences_url }}", preferences_link)
                    .replace("{{ app_url }}", app_link)
                )
                personalized_text = (
                    plain_text_content.replace("{{ unsubscribe_url }}", unsubscribe_link)
                    .replace("{{ preferences_url }}", preferences_link)
                    .replace("{{ app_url }}", app_link)
                )

            # Attempt external email send with fault isolation
            try:
                send_result = await self.email_provider.send_email(
                    to_email=sub.email,
                    subject=title,
                    html_content=personalized_html,
                    text_content=personalized_text,
                )
            except Exception as exc:
                logger.error(f"Unexpected error calling email provider for {sub.email}: {exc}")
                send_result = EmailSendResult(
                    success=False,
                    error_message=f"Provider exception: {str(exc)}",
                )

            if send_result.success:
                completed = await self.digest_repo.complete_delivery(
                    record=claim,
                    status="SENT",
                    claim_token=claim_token,
                    provider=provider_name,
                    provider_message_id=send_result.provider_message_id,
                    delivered_at=datetime.now(timezone.utc),
                )
                if completed:
                    sent_count += 1
                else:
                    skipped_count += 1
            else:
                completed = await self.digest_repo.complete_delivery(
                    record=claim,
                    status="FAILED",
                    claim_token=claim_token,
                    provider=provider_name,
                    error_message=send_result.error_message,
                )
                if completed:
                    failed_count += 1
                else:
                    skipped_count += 1
            await self.session.commit()

        if not dry_run:
            if sent_count > 0:
                fresh_digest = await self.digest_repo.get_with_stories(digest_id)
                if fresh_digest:
                    fresh_digest.status = "SENT"
                    add_res = self.session.add(fresh_digest)
                    if asyncio.iscoroutine(add_res):
                        await add_res
                    await self.session.commit()

        return DigestSendResponse(
            digest_id=digest.id,
            total_subscribers=total_subscribers,
            sent_count=sent_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            dry_run=dry_run,
        )
