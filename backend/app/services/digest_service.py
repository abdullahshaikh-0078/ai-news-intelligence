from datetime import date, datetime, timezone
import html
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.newsletter import DigestResponse, DigestStoryItem
from app.infrastructure.models.digest import Digest, DigestStory
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.services.curation_service import StoryCurationService


class DigestService:
    """Service managing deterministic Daily Digest generation, HTML compilation, and idempotency."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.digest_repo = DigestRepository(session)
        self.curation_service = StoryCurationService(session=session)

    async def generate_daily_digest(
        self,
        target_date: Optional[date] = None,
        force: bool = False,
    ) -> Digest:
        """
        Generate a daily newsletter issue from top-ranked curated stories.
        Enforces idempotency: returns existing issue if already generated for target_date unless force=True.
        """
        issue_date = target_date or datetime.now(timezone.utc).date()

        # Check existing digest for date
        existing = await self.digest_repo.get_by_date(issue_date)
        if existing and not force:
            logger.info(f"Digest for {issue_date} already exists (id={existing.id}). Returning existing issue.")
            return existing

        # Fetch curated stories from StoryCurationService
        curated_candidates = await self.curation_service.list_curated_stories(
            limit=settings.DIGEST_MAX_STORIES
        )

        # Handle story count boundaries:
        # We cap at DIGEST_MAX_STORIES (10) and gracefully handle < 5 or 0 stories
        selected_stories = curated_candidates[: settings.DIGEST_MAX_STORIES]
        story_count = len(selected_stories)

        formatted_date = issue_date.strftime("%B %d, %Y")
        title = f"AI News Daily Digest — {formatted_date}"

        # Compile HTML and Plain Text
        html_content = self.render_html_digest(title, issue_date, selected_stories)
        plain_text_content = self.render_plain_text_digest(title, issue_date, selected_stories)

        if existing and force:
            # Update existing
            existing.title = title
            existing.story_count = story_count
            existing.html_content = html_content
            existing.plain_text_content = plain_text_content
            existing.metadata_json = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "forced_regeneration": True,
                "story_count": story_count,
            }
            # Remove old story associations
            existing.digest_stories.clear()
            for idx, s in enumerate(selected_stories, start=1):
                digest_story = DigestStory(
                    digest_id=existing.id,
                    story_id=s.story_id,
                    position=idx,
                    ranking_score_snapshot=s.ranking_score,
                    headline_override=s.title,
                    summary_override=s.summary,
                    why_it_matters_override=s.why_it_matters,
                )
                self.session.add(digest_story)
            self.session.add(existing)
            await self.session.commit()
            return existing

        # Create new digest
        digest = await self.digest_repo.create_digest(
            title=title,
            digest_date=issue_date,
            html_content=html_content,
            plain_text_content=plain_text_content,
            story_count=story_count,
            status="PUBLISHED" if story_count > 0 else "DRAFT",
            metadata_json={
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "story_count": story_count,
            },
        )

        for idx, s in enumerate(selected_stories, start=1):
            await self.digest_repo.add_story_to_digest(
                digest_id=digest.id,
                story_id=s.story_id,
                position=idx,
                ranking_score_snapshot=s.ranking_score,
                headline_override=s.title,
                summary_override=s.summary,
                why_it_matters_override=s.why_it_matters,
            )

        await self.session.commit()
        # Reload with stories populated
        return await self.digest_repo.get_with_stories(digest.id)

    async def get_latest_digest(self) -> Optional[Digest]:
        """Fetch the latest generated digest."""
        return await self.digest_repo.get_latest()

    async def get_digest_by_id(self, digest_id: uuid.UUID) -> Optional[Digest]:
        """Fetch digest by ID."""
        return await self.digest_repo.get_with_stories(digest_id)

    def render_html_digest(
        self,
        title: str,
        issue_date: date,
        stories: List[CuratedStoryResponse],
        unsubscribe_url: str = "{{ unsubscribe_url }}",
        preferences_url: str = "{{ preferences_url }}",
        app_url: str = "{{ app_url }}",
    ) -> str:
        """Render responsive, accessible HTML daily digest email."""
        effective_app_url = app_url or settings.effective_newsletter_base_url
        formatted_date = issue_date.strftime("%B %d, %Y")
        story_count = len(stories)
        
        # Extract preheader snippet from first story or fallback
        first_summary = stories[0].summary if stories and stories[0].summary else ""
        preheader_text = html.escape(
            (first_summary[:140] + "...") if len(first_summary) > 140 else (first_summary or "Today's high-signal AI developments and research briefings.")
        )

        stories_html = []
        if not stories:
            stories_html.append(
                """<div style="padding: 32px 24px; text-align: center; color: #94a3b8; font-size: 14px; background-color: #1e293b; border-radius: 8px;">
                    No eligible stories reached the editorial curation threshold for this digest date.
                </div>"""
            )
        else:
            for idx, s in enumerate(stories, start=1):
                safe_title = html.escape(s.title or "Untitled Story")
                safe_summary = html.escape(s.summary or s.key_takeaway or "")
                safe_category = html.escape(s.category or "AI Intel")
                safe_source = html.escape(s.primary_source_name or "Official Source")
                why_it_matters = s.why_it_matters
                
                why_html = ""
                if why_it_matters:
                    why_html = f"""
                    <div style="background-color: #1e293b; border-left: 3px solid #38bdf8; padding: 12px 16px; border-radius: 0 8px 8px 0; margin-bottom: 16px;">
                      <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #38bdf8; margin: 0 0 4px 0;">Why it matters</div>
                      <p style="font-size: 13px; line-height: 1.5; color: #94a3b8; margin: 0;">{html.escape(why_it_matters)}</p>
                    </div>
                    """
                
                cta_html = ""
                if s.canonical_url:
                    cta_html = f"""
                    <div style="margin-top: 14px;">
                      <a href="{html.escape(s.canonical_url)}" style="display: inline-block; padding: 8px 18px; background-color: #0284c7; color: #ffffff; text-decoration: none; font-size: 12px; font-weight: 600; border-radius: 6px; box-shadow: 0 2px 6px rgba(2, 132, 199, 0.25);" target="_blank" rel="noopener noreferrer">Read Full Source &rarr;</a>
                    </div>
                    """

                card = f"""
                <div style="margin-bottom: 32px; padding-bottom: 32px; border-bottom: 1px solid #1e293b;">
                  <div style="margin-bottom: 10px;">
                    <span style="display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; background-color: rgba(56, 189, 248, 0.12); color: #38bdf8; margin-right: 8px;">{safe_category}</span>
                    <span style="font-size: 12px; color: #94a3b8; font-weight: 500;">Source: <strong style="color: #cbd5e1;">{safe_source}</strong></span>
                  </div>
                  <h2 style="font-size: 18px; font-weight: 700; color: #f8fafc; margin: 0 0 12px 0; line-height: 1.4;">
                    <span style="color: #64748b; font-weight: 500; margin-right: 6px;">#{idx}</span>{safe_title}
                  </h2>
                  <p style="font-size: 14px; line-height: 1.6; color: #cbd5e1; margin: 0 0 14px 0;">{safe_summary}</p>
                  {why_html}
                  {cta_html}
                </div>
                """
                stories_html.append(card)

        rendered_stories = "\n".join(stories_html)

        return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    body {{ height: 100% !important; margin: 0 !important; padding: 0 !important; width: 100% !important; background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e2e8f0; }}
    @media only screen and (max-width: 640px) {{
      .email-container {{ width: 100% !important; border-radius: 0 !important; border: none !important; }}
      .header, .intro, .content, .footer {{ padding-left: 20px !important; padding-right: 20px !important; }}
    }}
  </style>
</head>
<body style="background-color: #0b0f19; margin: 0; padding: 0;">
  <!-- Preheader preview text -->
  <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; line-height: 1px; color: #fff; opacity: 0;">
    {preheader_text}
  </div>
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0b0f19; padding: 24px 0;">
    <tr>
      <td align="center">
        <div class="email-container" style="max-width: 640px; margin: 0 auto; background-color: #0f172a; border-radius: 12px; overflow: hidden; border: 1px solid #1e293b; text-align: left;">
          <!-- Header -->
          <div class="header" style="padding: 36px 32px 24px; border-bottom: 1px solid #1e293b; background: linear-gradient(180deg, #111c35 0%, #0f172a 100%);">
            <div style="display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; background-color: rgba(56, 189, 248, 0.12); color: #38bdf8; margin-bottom: 10px;">
              AI NEWS INTELLIGENCE • DAILY BRIEFING
            </div>
            <h1 style="font-size: 24px; font-weight: 800; color: #f8fafc; margin: 0 0 8px 0; line-height: 1.3;">{html.escape(title)}</h1>
            <p style="font-size: 14px; color: #94a3b8; margin: 0;">{formatted_date} • {story_count} Curated Intelligence Developments</p>
          </div>

          <!-- Introduction Section -->
          <div class="intro" style="padding: 24px 32px 20px; border-bottom: 1px solid #1e293b; background-color: #0b1120;">
            <p style="font-size: 14px; line-height: 1.6; color: #94a3b8; margin: 0;">
              Welcome to today's intelligence briefing. Our autonomous pipeline has monitored, deduplicated, and ranked the latest breakthroughs across AI research labs, engineering preprints, and developer ecosystems.
            </p>
          </div>
          
          <!-- Content -->
          <div class="content" style="padding: 32px 32px 16px;">
            {rendered_stories}
          </div>
          
          <!-- Footer -->
          <div class="footer" style="padding: 32px; background-color: #090d16; text-align: center; border-top: 1px solid #1e293b;">
            <p style="font-size: 12px; color: #64748b; margin: 0 0 12px 0; line-height: 1.6;">
              You are receiving this briefing because you subscribed to AI News Intelligence on Abdullah Shaikh's AI Systems platform.<br>
              Curating high-signal intelligence across AI Agents, RAG, Foundation Models, and Systems.
            </p>
            <div style="font-size: 12px;">
              <a href="{preferences_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">Manage Preferences</a> &bull;
              <a href="{effective_app_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">AI News / Portfolio</a> &bull;
              <a href="{unsubscribe_url}" style="color: #38bdf8; text-decoration: none; margin: 0 8px;">Unsubscribe</a>
            </div>
          </div>
        </div>
      </td>
    </tr>
  </table>
</body>
</html>"""

    def render_plain_text_digest(
        self,
        title: str,
        issue_date: date,
        stories: List[CuratedStoryResponse],
        unsubscribe_url: str = "{{ unsubscribe_url }}",
        preferences_url: str = "{{ preferences_url }}",
        app_url: str = "{{ app_url }}",
    ) -> str:
        """Render plain text fallback for email clients without HTML support."""
        effective_app_url = app_url or settings.effective_newsletter_base_url
        formatted_date = issue_date.strftime("%B %d, %Y")
        story_count = len(stories)
        lines = [
            f"=== {title.upper()} ===",
            f"{formatted_date} | {story_count} Curated AI Intelligence Developments",
            "=" * 50,
            "",
            "Welcome to today's intelligence briefing. Curated across top AI research labs,",
            "engineering blogs, and preprints.",
            "",
            "-" * 50,
            "",
        ]

        if not stories:
            lines.append("No eligible stories reached the editorial curation threshold for this digest date.\n")
        else:
            for idx, s in enumerate(stories, start=1):
                lines.append(f"#{idx} {s.title}")
                if s.primary_source_name or s.category:
                    lines.append(f"   [{s.category or 'AI'}] Source: {s.primary_source_name or 'Official Source'}")
                if s.summary:
                    lines.append(f"   Summary: {s.summary}")
                if s.why_it_matters:
                    lines.append(f"   Why it matters: {s.why_it_matters}")
                if s.canonical_url:
                    lines.append(f"   Read article: {s.canonical_url}")
                lines.append("")

        lines.extend([
            "=" * 50,
            "You received this because you subscribed to AI News Intelligence Daily Digest.",
            f"Manage Preferences: {preferences_url}",
            f"AI News / Portfolio: {effective_app_url}",
            f"Unsubscribe: {unsubscribe_url}",
        ])
        return "\n".join(lines)
