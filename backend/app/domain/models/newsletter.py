import zoneinfo
from datetime import date, datetime
import re
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
VALID_FREQUENCIES = {"DAILY", "WEEKLY"}
VALID_WEEKDAYS = {"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"}

TIMEZONE_ALIASES = {
    "asia/calcutta": "Asia/Kolkata",
    "calcutta": "Asia/Kolkata",
    "utc": "UTC",
    "gmt": "UTC",
}


def normalize_and_validate_timezone(tz_name: Optional[str]) -> str:
    """Normalize and validate IANA timezone identifier with safe legacy alias translation."""
    if not tz_name or not str(tz_name).strip():
        return "Asia/Kolkata"
    raw = str(tz_name).strip()
    aliased = TIMEZONE_ALIASES.get(raw.lower(), raw)
    try:
        zi = zoneinfo.ZoneInfo(aliased)
        return TIMEZONE_ALIASES.get(zi.key.lower(), zi.key)
    except Exception:
        raise ValueError(f"Invalid IANA timezone identifier: '{tz_name}'")


def validate_preferred_time_format(val: Optional[str]) -> str:
    """Validate 24-hour HH:MM wall-clock delivery time."""
    if not val or not str(val).strip():
        return "08:00"
    cleaned = str(val).strip()
    if not TIME_REGEX.match(cleaned):
        raise ValueError(f"Invalid preferred_time format: '{val}'. Expected 'HH:MM' (24-hour, e.g. '08:00').")
    return cleaned


def validate_frequency_value(val: Optional[str]) -> str:
    """Validate subscriber delivery cadence."""
    if not val or not str(val).strip():
        return "DAILY"
    cleaned = str(val).strip().upper()
    if cleaned not in VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency: '{val}'. Must be 'DAILY' or 'WEEKLY'.")
    return cleaned


def validate_preferred_weekday_value(val: Optional[str]) -> Optional[str]:
    """Validate subscriber weekly delivery day."""
    if val is None or not str(val).strip():
        return "MONDAY"
    cleaned = str(val).strip().upper()
    if cleaned not in VALID_WEEKDAYS:
        raise ValueError(f"Invalid preferred_weekday: '{val}'. Must be MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, or SUNDAY.")
    return cleaned


class SubscribeRequest(BaseModel):
    """Payload for newsletter subscription."""
    email: str = Field(..., max_length=255, description="Subscriber email address")
    name: Optional[str] = Field(None, max_length=255)
    age_group: Optional[str] = Field(None, max_length=50)
    frequency: Optional[str] = Field("DAILY", max_length=50)
    preferred_channel: Optional[str] = Field("EMAIL", max_length=50)
    preferred_time: Optional[str] = Field("08:00", max_length=10, description="Preferred local delivery time (HH:MM)")
    timezone: Optional[str] = Field("Asia/Kolkata", max_length=100, description="Subscriber IANA timezone")
    preferred_weekday: Optional[str] = Field("MONDAY", max_length=20, description="Preferred delivery weekday for weekly subscribers")
    topics: Optional[List[str]] = Field(default_factory=list)
    content_preferences: Optional[Dict[str, Any]] = Field(default_factory=dict)

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

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> str:
        return normalize_and_validate_timezone(v)

    @field_validator("preferred_time")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> str:
        return validate_preferred_time_format(v)

    @field_validator("frequency")
    @classmethod
    def validate_freq(cls, v: Optional[str]) -> str:
        return validate_frequency_value(v)

    @field_validator("preferred_weekday")
    @classmethod
    def validate_weekday(cls, v: Optional[str]) -> Optional[str]:
        return validate_preferred_weekday_value(v)


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
    preferred_time: Optional[str] = Field(None, max_length=10)
    timezone: Optional[str] = Field(None, max_length=100, description="Subscriber timezone")
    preferred_weekday: Optional[str] = Field(None, max_length=20)
    topics: Optional[List[str]] = None
    content_preferences: Optional[Dict[str, Any]] = None

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

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return normalize_and_validate_timezone(v)
        return v

    @field_validator("preferred_time")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_preferred_time_format(v)
        return v

    @field_validator("frequency")
    @classmethod
    def validate_freq(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_frequency_value(v)
        return v

    @field_validator("preferred_weekday")
    @classmethod
    def validate_weekday(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_preferred_weekday_value(v)
        return v


class SubscriberProfileResponse(BaseModel):
    """Public subscriber profile representation without internal tokens."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: Optional[str] = None
    age_group: Optional[str] = None
    frequency: str = "DAILY"
    preferred_channel: str = "EMAIL"
    preferred_time: str = "08:00"
    timezone: str = "Asia/Kolkata"
    preferred_weekday: Optional[str] = "MONDAY"
    topics: List[str] = Field(default_factory=list)
    content_preferences: Dict[str, Any] = Field(default_factory=dict)
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
