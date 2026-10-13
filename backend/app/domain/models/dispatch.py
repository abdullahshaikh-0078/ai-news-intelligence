from datetime import date, datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class DailyDispatchRequest(BaseModel):
    """Execution parameters for daily pipeline run, digest compilation, and delivery dispatch."""
    target_date: Optional[date] = Field(None, description="Calendar date for digest (defaults to UTC today)")
    run_ingestion: bool = Field(True, description="Whether to trigger an RSS ingestion pass prior to processing")
    dry_run: bool = Field(False, description="Simulate sending without dispatching real emails or updating status")
    force_regenerate_digest: bool = Field(False, description="Force regeneration of digest if already created for target_date")
    item_limit: int = Field(20, ge=1, le=50, description="Max candidate items to consider for AI pipeline pass")
    max_ai_analyses: int = Field(10, ge=0, le=20, description="Max AI text extractions to run in this pass")
    max_embeddings: int = Field(10, ge=0, le=20, description="Max on-demand authentic Gemini embeddings to compute")
    curation_limit: int = Field(10, ge=1, le=20, description="Max curated stories in digest feed")


class DailyDispatchResponse(BaseModel):
    """Telemetry report of an orchestrated daily dispatch execution."""
    status: str = Field(..., description="'success', 'partial', 'skipped', or 'error'")
    execution_id: uuid.UUID
    target_date: date
    digest_id: Optional[uuid.UUID] = None
    digest_title: Optional[str] = None
    digest_stories_count: int = 0
    total_subscribers: int = 0
    sent_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    dry_run: bool = False
    pipeline_summary: Dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    executed_at: datetime


class DispatchStatusResponse(BaseModel):
    """Operational status of the automated daily dispatch subsystem."""
    status: str
    environment: str
    cron_enabled: bool
    schedule_hour_utc: int
    schedule_minute_utc: int = 30
    resend_configured: bool
    webhook_secret_configured: bool
    dispatch_token_configured: bool = False
    latest_digest_date: Optional[date] = None
    latest_digest_id: Optional[uuid.UUID] = None
    latest_digest_stories: int = 0
    active_daily_subscribers: int = 0
