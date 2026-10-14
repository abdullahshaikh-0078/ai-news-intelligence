import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import Boolean, DateTime, Index, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class Subscriber(Base, TimestampMixin):
    """Newsletter subscriber model supporting localized delivery schedules and personalization."""

    __tablename__ = "subscribers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    age_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[str] = mapped_column(String(50), default="DAILY", nullable=False)
    preferred_channel: Mapped[str] = mapped_column(String(50), default="EMAIL", nullable=False)
    preferred_time: Mapped[str] = mapped_column(String(10), default="08:00", nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Kolkata", nullable=False)
    preferred_weekday: Mapped[Optional[str]] = mapped_column(String(20), default="MONDAY", nullable=True)
    topics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    content_preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    unsubscribe_token: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    deliveries: Mapped[List["DeliveryRecord"]] = relationship(
        "DeliveryRecord",
        back_populates="subscriber",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_subscribers_active_frequency", "is_active", "frequency"),
    )

    def __repr__(self) -> str:
        return f"<Subscriber(email='{self.email}', is_active={self.is_active})>"
