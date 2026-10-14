import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.base import BaseRepository


class SubscriberRepository(BaseRepository[Subscriber]):
    """Repository managing Subscriber entity persistence, lookups, and lifecycle."""

    def __init__(self, session: AsyncSession):
        super().__init__(Subscriber, session)

    async def get_by_email(self, email: str) -> Optional[Subscriber]:
        """Fetch subscriber by case-insensitive email address."""
        clean_email = email.strip().lower()
        stmt = select(Subscriber).where(func.lower(Subscriber.email) == clean_email)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_token(self, token: str) -> Optional[Subscriber]:
        """Fetch subscriber by their unique unsubscribe token."""
        stmt = select(Subscriber).where(Subscriber.unsubscribe_token == token.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def upsert_subscriber(
        self,
        email: str,
        name: Optional[str] = None,
        age_group: Optional[str] = None,
        frequency: str = "DAILY",
        preferred_channel: str = "EMAIL",
        topics: Optional[List[str]] = None,
    ) -> Tuple[Subscriber, bool]:
        """
        Create a new subscriber or update an existing record idempotently.
        Reactivates unsubscribed users if they re-subscribe.
        Returns: (subscriber, is_created)
        """
        clean_email = email.strip().lower()
        existing = await self.get_by_email(clean_email)

        if existing:
            was_inactive = not existing.is_active
            # Update fields if provided
            if name is not None:
                existing.name = name.strip() or None
            if age_group is not None:
                existing.age_group = age_group.strip() or None
            if frequency:
                existing.frequency = frequency.upper()
            if preferred_channel:
                existing.preferred_channel = preferred_channel.upper()
            if topics is not None:
                existing.topics = topics

            # Reactivate if previously inactive
            if was_inactive:
                existing.is_active = True
                existing.unsubscribed_at = None

            setattr(existing, "_was_reactivated", was_inactive)
            self.session.add(existing)
            await self.session.flush()
            return existing, False

        # Create new subscriber
        token = secrets.token_urlsafe(32)
        subscriber = Subscriber(
            email=clean_email,
            name=name.strip() if name else None,
            age_group=age_group.strip() if age_group else None,
            frequency=frequency.upper(),
            preferred_channel=preferred_channel.upper(),
            topics=topics or [],
            is_active=True,
            unsubscribe_token=token,
        )
        setattr(subscriber, "_was_reactivated", False)
        self.session.add(subscriber)
        await self.session.flush()
        return subscriber, True

    async def update_preferences(
        self,
        subscriber_id: uuid.UUID,
        name: Optional[str] = None,
        age_group: Optional[str] = None,
        frequency: Optional[str] = None,
        preferred_channel: Optional[str] = None,
        topics: Optional[List[str]] = None,
    ) -> Optional[Subscriber]:
        """Update preferences for an existing subscriber."""
        subscriber = await self.get_by_id(subscriber_id)
        if not subscriber:
            return None

        if name is not None:
            subscriber.name = name.strip() or None
        if age_group is not None:
            subscriber.age_group = age_group.strip() or None
        if frequency is not None:
            subscriber.frequency = frequency.upper()
        if preferred_channel is not None:
            subscriber.preferred_channel = preferred_channel.upper()
        if topics is not None:
            subscriber.topics = topics

        self.session.add(subscriber)
        await self.session.flush()
        return subscriber

    async def deactivate_by_token(self, token: str) -> Optional[Subscriber]:
        """Deactivate subscriber via secure unsubscribe token. Returns None if not found or already inactive."""
        subscriber = await self.get_by_token(token)
        if not subscriber or not subscriber.is_active:
            return None

        subscriber.is_active = False
        subscriber.unsubscribed_at = datetime.now(timezone.utc)
        self.session.add(subscriber)
        await self.session.flush()
        return subscriber

    async def deactivate_by_email(self, email: str) -> Optional[Subscriber]:
        """Deactivate subscriber by email address. Returns None if not found or already inactive."""
        subscriber = await self.get_by_email(email)
        if not subscriber or not subscriber.is_active:
            return None

        subscriber.is_active = False
        subscriber.unsubscribed_at = datetime.now(timezone.utc)
        self.session.add(subscriber)
        await self.session.flush()
        return subscriber

    async def list_active(self, frequency: Optional[str] = None) -> List[Subscriber]:
        """List active subscribers, optionally filtered by delivery schedule."""
        stmt = select(Subscriber).where(Subscriber.is_active.is_(True))
        if frequency:
            stmt = stmt.where(Subscriber.frequency == frequency.upper())
        stmt = stmt.order_by(Subscriber.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active(self, frequency: Optional[str] = None) -> int:
        """Count active subscribers, optionally filtered by delivery schedule."""
        stmt = select(func.count(Subscriber.id)).where(Subscriber.is_active.is_(True))
        if frequency:
            stmt = stmt.where(Subscriber.frequency == frequency.upper())
        result = await self.session.execute(stmt)
        return result.scalar_one()
