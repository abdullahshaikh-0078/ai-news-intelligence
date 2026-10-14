from datetime import date, datetime
import re
from typing import Any, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscribeRequest(BaseModel):
    """Payload for newsletter subscription."""
    email: str = Field(..., max_length=255, description="Subscriber email address")
    name: Optional[str] = Field(None, max_length=255)
    age_group: Optional[str] = Field(None, max_length=50)
    frequency: Optional[str] = Field("DAILY", max_length=50)
    preferred_channel: Optional[str] = Field("EMAIL", max_length=50)
    topics: Optional[List[str]] = Field(default_factory=list)
    timezone: Optional[str] = Field(None, max_length=100, description="Subscriber timezone")

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Accept 'channel' alias if 'preferred_channel' is not explicitly provided
            if "channel" in data and "preferred_channel" not in data:
                data["preferred_channel"] = data["channel"]
        return data

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        clean = v.strip().lower()
        if not EMAIL_REGEX.match(clean):
            raise ValueError("Invalid email address format")
        return clean


class SubscribeResponse(BaseModel):
    """Response returned upon successful subscription."""
    status: str = "success"
    message: str
    email: str
    is_active: bool
    created: bool
    reactivated: bool = False


class UnsubscribeRequest(BaseModel):
    """Payload for unsubscribing via token or email."""
    token: Optional[str] = None
    email: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip().lower()
            if not EMAIL_REGEX.match(clean):
                raise ValueError("Invalid email address format")
            return clean
        return v


class UnsubscribeResponse(BaseModel):
    """Response returned upon unsubscribing."""
    status: str = "success"
    message: str


class PreferencesUpdateRequest(BaseModel):
    """Payload to customize newsletter preferences."""
    email: str = Field(..., max_length=255)
    name: Optional[str] = Field(None, max_length=255)
    age_group: Optional[str] = Field(None, max_length=50)
    frequency: Optional[str] = Field(None, max_length=50)
    preferred_channel: Optional[str] = Field(None, max_length=50)
    topics: Optional[List[str]] = None
    timezone: Optional[str] = Field(None, max_length=100, description="Subscriber timezone")

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "channel" in data and "preferred_channel" not in data:
                data["preferred_channel"] = data["channel"]
        return data

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        clean = v.strip().lower()
        if not EMAIL_REGEX.match(clean):
            raise ValueError("Invalid email address format")
        return clean



class SubscriberProfileResponse(BaseModel):
    """Public subscriber profile representation without internal tokens."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: Optional[str] = None
    age_group: Optional[str] = None
    frequency: str
    preferred_channel: str
    topics: List[str]
    is_active: bool
    created_at: datetime


class DigestStoryItem(BaseModel):
    """Story representation within a digest issue."""
    position: int
    story_id: uuid.UUID
    title: str
    source: Optional[str] = None
    content_type: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    why_it_matters: Optional[str] = None
    canonical_url: Optional[str] = None
    published_at: Optional[datetime] = None
    ranking_score: float = 0.0


class DigestResponse(BaseModel):
    """Digest issue response model."""
    id: uuid.UUID
    title: str
    digest_date: date
    status: str
    story_count: int
    stories: List[DigestStoryItem] = Field(default_factory=list)
    generated_at: datetime


class DigestGenerateRequest(BaseModel):
    """Request to trigger daily digest generation."""
    target_date: Optional[date] = None
    force: bool = False


class DigestSendRequest(BaseModel):
    """Request to trigger email delivery of a digest issue."""
    dry_run: bool = False


class DigestSendResponse(BaseModel):
    """Outcome summary of digest email delivery."""
    digest_id: uuid.UUID
    total_subscribers: int
    sent_count: int
    failed_count: int
    skipped_count: int
    dry_run: bool


class DeliveryRecordResponse(BaseModel):
    """Audit log item for newsletter dispatch."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient_email: str
    channel: str
    status: str
    provider: str
    provider_message_id: Optional[str] = None
    attempted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    error_message: Optional[str] = None
