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
        preferred_time: str = "08:00",
        timezone_str: str = "Asia/Kolkata",
        preferred_weekday: Optional[str] = "MONDAY",
        topics: Optional[List[str]] = None,
        content_preferences: Optional[dict] = None,
    ) -> Tuple[Subscriber, bool]:
        """
        Create a new subscriber or update an existing record idempotently.
        Reactivates unsubscribed users if they re-subscribe.
        Returns: (subscriber, is_created)
        """
        from app.domain.models.newsletter import (
            normalize_and_validate_timezone,
            validate_frequency_value,
            validate_preferred_time_format,
            validate_preferred_weekday_value,
        )

        clean_email = email.strip().lower()
        clean_tz = normalize_and_validate_timezone(timezone_str)
        clean_time = validate_preferred_time_format(preferred_time)
        clean_freq = validate_frequency_value(frequency)
        clean_weekday = validate_preferred_weekday_value(preferred_weekday) if clean_freq == "WEEKLY" else (preferred_weekday or "MONDAY")

        existing = await self.get_by_email(clean_email)

        if existing:
            was_inactive = not existing.is_active
            # Update fields if provided
            if name is not None:
                existing.name = name.strip() or None
            if age_group is not None:
                existing.age_group = age_group.strip() or None
            if frequency:
                existing.frequency = clean_freq
            if preferred_channel:
                existing.preferred_channel = preferred_channel.upper()
            if preferred_time:
                existing.preferred_time = clean_time
            if timezone_str:
                existing.timezone = clean_tz
            if preferred_weekday is not None:
                existing.preferred_weekday = clean_weekday
            if topics is not None:
                existing.topics = topics
            if content_preferences is not None:
                existing.content_preferences = content_preferences

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
            frequency=clean_freq,
            preferred_channel=preferred_channel.upper(),
            preferred_time=clean_time,
            timezone=clean_tz,
            preferred_weekday=clean_weekday,
            topics=topics or [],
            content_preferences=content_preferences or {},
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
        preferred_time: Optional[str] = None,
        timezone_str: Optional[str] = None,
        preferred_weekday: Optional[str] = None,
        topics: Optional[List[str]] = None,
        content_preferences: Optional[dict] = None,
    ) -> Optional[Subscriber]:
        """Update preferences for an existing subscriber."""
        from app.domain.models.newsletter import (
            normalize_and_validate_timezone,
            validate_frequency_value,
            validate_preferred_time_format,
            validate_preferred_weekday_value,
        )

        subscriber = await self.get_by_id(subscriber_id)
        if not subscriber:
            return None

        if name is not None:
            subscriber.name = name.strip() or None
        if age_group is not None:
            subscriber.age_group = age_group.strip() or None
        if frequency is not None:
            subscriber.frequency = validate_frequency_value(frequency)
        if preferred_channel is not None:
            subscriber.preferred_channel = preferred_channel.upper()
        if preferred_time is not None:
            subscriber.preferred_time = validate_preferred_time_format(preferred_time)
        if timezone_str is not None:
            subscriber.timezone = normalize_and_validate_timezone(timezone_str)
        if preferred_weekday is not None:
            subscriber.preferred_weekday = validate_preferred_weekday_value(preferred_weekday)
        if topics is not None:
            subscriber.topics = topics
        if content_preferences is not None:
            subscriber.content_preferences = content_preferences

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

    async def list_due_subscribers(
        self,
        target_time_utc: Optional[datetime] = None,
        window_minutes: int = 60,
    ) -> List[Subscriber]:
        """
        Evaluate active subscribers against their configured IANA timezone and preferred local delivery time.
        Determines subscribers who are currently due for daily or weekly dispatch.

        Guarantees:
        - IANA timezone evaluation (no hardcoded UTC offsets)
        - Robust due-window mechanism (prevents permanent skipping if scheduler runs slightly late)
        - Day-of-week validation for WEEKLY subscribers
        - Skips inactive or unsubscribed users
        """
        import zoneinfo
        from app.domain.models.newsletter import normalize_and_validate_timezone

        now_utc = target_time_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        active_subs = await self.list_active()
        due: List[Subscriber] = []

        for sub in active_subs:
            freq = (sub.frequency or "DAILY").upper()
            if freq not in ("DAILY", "WEEKLY"):
                continue

            try:
                tz_name = normalize_and_validate_timezone(sub.timezone)
                sub_tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                sub_tz = zoneinfo.ZoneInfo("Asia/Kolkata")

            local_now = now_utc.astimezone(sub_tz)

            # Weekly frequency validation
            if freq == "WEEKLY":
                sub_weekday = (sub.preferred_weekday or "MONDAY").upper()
                current_weekday = local_now.strftime("%A").upper()
                if current_weekday != sub_weekday:
                    continue

            # Local wall-clock time evaluation
            pref_time = sub.preferred_time or "08:00"
            try:
                h_str, m_str = pref_time.split(":")
                pref_h, pref_m = int(h_str), int(m_str)
            except Exception:
                pref_h, pref_m = 8, 0

            target_local = local_now.replace(hour=pref_h, minute=pref_m, second=0, microsecond=0)
            diff_seconds = (local_now - target_local).total_seconds()

            # Subscriber is due if current local time is at or past preferred delivery time
            # within the allowed window_minutes
            if 0 <= diff_seconds < (window_minutes * 60):
                due.append(sub)

        return due
