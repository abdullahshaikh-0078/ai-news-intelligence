from abc import ABC, abstractmethod
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
    ) -> EmailSendResult:
        """Send email via Resend API with comprehensive failure isolation."""
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
                    logger.info(f"Email sent successfully via Resend to {to_email} (id={message_id})")
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
            "id": mock_id,
            "sent_at": datetime.now(timezone.utc),
        })
        return EmailSendResult(
            success=True,
            provider_message_id=mock_id,
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

    @staticmethod
    def _filter_stories_for_subscriber(
        stories: List[CuratedStoryResponse],
        subscriber_topics: Optional[List[str]],
    ) -> List[CuratedStoryResponse]:
        """Filter stories to match subscriber topic preferences. If none specified, returns all stories."""
        if not subscriber_topics:
            return stories

        clean_sub_topics = [
            str(t).strip().lower() for t in subscriber_topics if isinstance(t, str) and str(t).strip()
        ]
        if not clean_sub_topics:
            return stories

        matched: List[CuratedStoryResponse] = []
        for s in stories:
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
    ) -> DigestSendResponse:
        """
        Dispatch a digest issue to all active subscribers with concurrency-safe deduplication.

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

        subscribers = await self.subscriber_repo.list_active(frequency="DAILY")
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

            # 2. Enforce subscriber topic personalization
            raw_topics = getattr(sub, "topics", None) or []
            sub_topics = [
                str(t).strip().lower() for t in raw_topics if isinstance(t, str) and str(t).strip()
            ]
            has_topic_preference = bool(sub_topics)

            if has_topic_preference and curated_stories:
                matched_stories = self._filter_stories_for_subscriber(curated_stories, sub_topics)
                if not matched_stories:
                    logger.info(
                        f"Subscriber {sub.email} configured topics {sub_topics} but 0 stories matched. "
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

            # Render customized HTML/text if subscriber has personalized topic subset
            if has_topic_preference and matched_stories and len(matched_stories) < len(curated_stories):
                personalized_html = self.digest_service.render_html_digest(
                    title=title,
                    issue_date=digest.digest_date,
                    stories=matched_stories,
                    unsubscribe_url=unsubscribe_link,
                    preferences_url=preferences_link,
                )
                personalized_text = self.digest_service.render_plain_text_digest(
                    title=title,
                    issue_date=digest.digest_date,
                    stories=matched_stories,
                    unsubscribe_url=unsubscribe_link,
                )
            else:
                personalized_html = (
                    html_content.replace("{{ unsubscribe_url }}", unsubscribe_link)
                    .replace("{{ preferences_url }}", preferences_link)
                )
                personalized_text = (
                    plain_text_content.replace("{{ unsubscribe_url }}", unsubscribe_link)
                    .replace("{{ preferences_url }}", preferences_link)
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
                    self.session.add(fresh_digest)
                    await self.session.commit()

        return DigestSendResponse(
            digest_id=digest.id,
            total_subscribers=total_subscribers,
            sent_count=sent_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            dry_run=dry_run,
        )
