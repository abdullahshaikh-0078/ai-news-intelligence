from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CuratedStoryResponse(BaseModel):
    """Normalized response item for an editorially curated story."""

    story_id: UUID
    title: str
    summary: Optional[str] = None
    key_takeaway: Optional[str] = None
    why_it_matters: Optional[str] = None
    ranking_score: float = Field(default=0.0, ge=0.0, le=1.0)
    category: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    canonical_url: Optional[str] = None
    content_type: str = "ARTICLE"
    primary_source_name: Optional[str] = None
    article_count: int = 1
    published_at: Optional[datetime] = None
    curated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CuratedStoryListResponse(BaseModel):
    """Standardized paginated list container for curated stories."""

    items: List[CuratedStoryResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class StoryCurationRunRequest(BaseModel):
    """Parameters for running deterministic editorial curation."""

    limit: int = Field(default=20, ge=1, le=100, description="Target number of stories to curate")
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Minimum ranking score cutoff")
    max_per_topic: Optional[int] = Field(default=None, ge=1, le=20, description="Max stories from the same topic/category")
    max_per_source: Optional[int] = Field(default=None, ge=1, le=20, description="Max stories with same primary source")
    diversity_enabled: Optional[bool] = Field(default=None, description="Whether to enforce topic & source diversity caps")


class StoryCurationRunResponse(BaseModel):
    """Telemetry returned after executing a story curation pass."""

    stories_scanned: int
    eligible_count: int
    curated_count: int
    rejected_reasons: Dict[str, int] = Field(
        default_factory=dict,
        description="Breakdown of rejection causes (below_min_score, topic_cap, source_cap, archived, etc.)",
    )
    duration_ms: float
