from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.logging import logger
from app.domain.models.newsletter import PreferencesUpdateRequest, SubscribeRequest
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.email_service import (
    MockEmailProvider,
    ResendEmailProvider,
    WelcomeEmailService,
)


class SubscriberService:
    """Service orchestrating newsletter subscriber lifecycle, subscriptions, and preferences."""

    def __init__(
        self,
        session: AsyncSession,
        welcome_service: Optional[WelcomeEmailService] = None,
    ):
        self.session = session
        self.repo = SubscriberRepository(session)
        if welcome_service:
            self.welcome_service = welcome_service
        elif settings.RESEND_API_KEY:
            self.welcome_service = WelcomeEmailService(email_provider=ResendEmailProvider())
        else:
            self.welcome_service = WelcomeEmailService(email_provider=MockEmailProvider())

    async def subscribe(self, request: SubscribeRequest) -> Tuple[Subscriber, bool]:
        """
        Subscribe or reactivate a user.
        Dispatches an immediate welcome confirmation email for new or reactivated subscribers
        ONLY after successful database persistence.
        Returns: (subscriber, is_created)
        """
        clean_email = str(request.email).strip().lower()
        subscriber, is_created = await self.repo.upsert_subscriber(
            email=clean_email,
            name=request.name,
            age_group=request.age_group,
            frequency=request.frequency or "DAILY",
            preferred_channel=request.preferred_channel or "EMAIL",
            topics=request.topics or [],
        )
        # CRITICAL: Commit transaction FIRST to guarantee persistence before sending email
        await self.session.commit()

        was_reactivated = getattr(subscriber, "_was_reactivated", False)
        logger.info(
            f"Subscriber persisted: id={subscriber.id}, created={is_created}, "
            f"reactivated={was_reactivated}, active={subscriber.is_active}"
        )

        # Dispatch welcome email ONLY for newly created or reactivated subscribers
        # (Duplicate active subscriptions skip the welcome email to prevent inbox spam)
        if is_created or was_reactivated:
            try:
                result = await self.welcome_service.send_welcome_email(subscriber)
                if result.success:
                    logger.info(
                        f"Welcome email successfully sent for subscriber id={subscriber.id} (provider_id={result.provider_message_id})"
                    )
                else:
                    logger.warning(
                        f"Welcome email dispatch failed for subscriber id={subscriber.id}: {result.error_message}"
                    )
            except Exception as exc:
                # Failure isolation: provider failure does NOT rollback or invalidate the saved subscriber
                logger.error(
                    f"Unexpected error while sending welcome email for subscriber id={subscriber.id}: {exc}"
                )

        return subscriber, is_created

    async def unsubscribe(self, token: Optional[str] = None, email: Optional[str] = None) -> bool:
        """
        Deactivate a subscriber by token (preferred) or email.
        Returns True if a subscriber was deactivated, False otherwise.
        """
        success = False
        if token:
            sub = await self.repo.deactivate_by_token(token)
            if sub:
                logger.info(f"Subscriber unsubscribed via token: email={sub.email}")
                success = True
        elif email:
            sub = await self.repo.deactivate_by_email(str(email).strip().lower())
            if sub:
                logger.info(f"Subscriber unsubscribed via email: email={sub.email}")
                success = True

        if success:
            await self.session.commit()
        return success

    async def update_preferences(self, request: PreferencesUpdateRequest) -> Optional[Subscriber]:
        """Update preferences for an existing subscriber by email."""
        clean_email = str(request.email).strip().lower()
        existing = await self.repo.get_by_email(clean_email)
        if not existing:
            return None

        updated = await self.repo.update_preferences(
            subscriber_id=existing.id,
            name=request.name,
            age_group=request.age_group,
            frequency=request.frequency,
            preferred_channel=request.preferred_channel,
            topics=request.topics,
        )
        await self.session.commit()
        logger.info(f"Subscriber preferences updated: email={clean_email}")
        return updated

    async def get_active_subscribers(self, frequency: Optional[str] = "DAILY") -> List[Subscriber]:
        """Retrieve all active subscribers scheduled for delivery."""
        return await self.repo.list_active(frequency=frequency)

    async def get_active_count(self) -> int:
        """Count active subscribers."""
        return await self.repo.count_active()
