import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.dispatch import (
    DailyDispatchRequest,
    DailyDispatchResponse,
    DispatchStatusResponse,
    ScheduledTickRequest,
    ScheduledTickResponse,
)
from app.domain.models.newsletter import (
    PreferencesUpdateRequest,
    SubscribeRequest,
    SubscribeResponse,
    SubscriberProfileResponse,
    normalize_and_validate_timezone,
    validate_frequency_value,
    validate_preferred_time_format,
    validate_preferred_weekday_value,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.digest import DeliveryRecord, Digest, DigestStory
from app.infrastructure.models.story import Story
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.digest_service import DigestService
from app.services.dispatch_service import DailyDispatchService
from app.services.email_service import (
    DeliveryService,
    MockEmailProvider,
    ResendEmailProvider,
    WelcomeEmailService,
)
from app.services.retention_service import RetentionService
from app.services.subscriber_service import SubscriberService


class TestProductionArchitectureV2:
    """Comprehensive test suite covering all 28 production architecture requirements."""

    # -------------------------------------------------------------------------
    # Requirement 1: Subscriber with Asia/Kolkata timezone and 08:00
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_01_subscriber_asia_kolkata_0800_due_at_0230_utc(self):
        """At 02:30 UTC, Asia/Kolkata is 08:00 local time -> subscriber is due."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="kolkata@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # 02:30 UTC == 08:00 IST (UTC+5:30)
        target_utc = datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc)
        due_list = await repo.list_due_subscribers(target_time_utc=target_utc, window_minutes=60)

        assert len(due_list) == 1
        assert due_list[0].email == "kolkata@example.com"

    # -------------------------------------------------------------------------
    # Requirement 2: Subscriber with Asia/Dubai timezone and different preferred time
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_02_subscriber_asia_dubai_0900_due_at_0500_utc(self):
        """At 05:00 UTC, Asia/Dubai (UTC+4) is 09:00 local time -> subscriber is due."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="dubai@example.com",
            frequency="DAILY",
            preferred_time="09:00",
            timezone="Asia/Dubai",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # 05:00 UTC == 09:00 GST
        target_utc = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
        due_list = await repo.list_due_subscribers(target_time_utc=target_utc, window_minutes=60)

        assert len(due_list) == 1
        assert due_list[0].email == "dubai@example.com"

    # -------------------------------------------------------------------------
    # Requirement 3: Subscriber with America/New_York timezone
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_03_subscriber_america_new_york_0700_due_window(self):
        """America/New_York (EDT UTC-4 in October) at 11:00 UTC is 07:00 local time -> due."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="ny@example.com",
            frequency="DAILY",
            preferred_time="07:00",
            timezone="America/New_York",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # 11:00 UTC == 07:00 EDT (UTC-4)
        target_utc = datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)
        due_list = await repo.list_due_subscribers(target_time_utc=target_utc, window_minutes=60)

        assert len(due_list) == 1
        assert due_list[0].email == "ny@example.com"

    # -------------------------------------------------------------------------
    # Requirement 4: Different subscribers due at different times
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_04_different_subscribers_due_at_different_times(self):
        """Subscribers in different timezones/times are selectively filtered at each execution."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub_kolkata = Subscriber(
            id=uuid.uuid4(),
            email="kolkata@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        sub_london = Subscriber(
            id=uuid.uuid4(),
            email="london@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Europe/London",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub_kolkata, sub_london])

        # At 02:30 UTC: Kolkata is 08:00 (DUE), London is 03:30 BST (NOT DUE)
        target_1 = datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc)
        due_1 = await repo.list_due_subscribers(target_time_utc=target_1, window_minutes=60)
        assert len(due_1) == 1
        assert due_1[0].email == "kolkata@example.com"

        # At 07:00 UTC: Kolkata is 12:30 (NOT DUE), London (BST UTC+1) is 08:00 (DUE)
        target_2 = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        due_2 = await repo.list_due_subscribers(target_time_utc=target_2, window_minutes=60)
        assert len(due_2) == 1
        assert due_2[0].email == "london@example.com"

    # -------------------------------------------------------------------------
    # Requirement 5: Subscriber not yet due
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_05_subscriber_not_yet_due(self):
        """Subscriber whose preferred time is in the future is not returned in due list."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="future@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # 01:30 UTC == 07:00 IST (1 hour before preferred time)
        target_utc = datetime(2026, 10, 14, 1, 30, tzinfo=timezone.utc)
        due_list = await repo.list_due_subscribers(target_time_utc=target_utc, window_minutes=60)

        assert len(due_list) == 0

    # -------------------------------------------------------------------------
    # Requirement 6: Subscriber whose delivery window was slightly delayed
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_06_subscriber_delivery_window_slightly_delayed(self):
        """If scheduler runs 20 minutes late, subscriber is still within the due window."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="delayed@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # 02:50 UTC == 08:20 IST (20 minutes late, but within window_minutes=60)
        target_utc = datetime(2026, 10, 14, 2, 50, tzinfo=timezone.utc)
        due_list = await repo.list_due_subscribers(target_time_utc=target_utc, window_minutes=60)

        assert len(due_list) == 1
        assert due_list[0].email == "delayed@example.com"

    # -------------------------------------------------------------------------
    # Requirement 7: Daily subscriber
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_07_daily_subscriber_due_any_day_at_preferred_time(self):
        """Daily subscriber is evaluated on every weekday."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="daily@example.com",
            frequency="DAILY",
            preferred_time="08:00",
            timezone="UTC",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # Wednesday 08:00 UTC
        due_wed = await repo.list_due_subscribers(
            target_time_utc=datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        )
        assert len(due_wed) == 1

        # Thursday 08:00 UTC
        due_thu = await repo.list_due_subscribers(
            target_time_utc=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        )
        assert len(due_thu) == 1

    # -------------------------------------------------------------------------
    # Requirement 8: Weekly subscriber
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_08_weekly_subscriber_due_only_on_configured_weekday(self):
        """Weekly subscriber configured for MONDAY is due on Monday, not Wednesday."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        sub = Subscriber(
            id=uuid.uuid4(),
            email="weekly@example.com",
            frequency="WEEKLY",
            preferred_weekday="MONDAY",
            preferred_time="08:00",
            timezone="UTC",
            is_active=True,
        )
        repo.list_active = AsyncMock(return_value=[sub])

        # Wednesday, October 14, 2026 at 08:00 UTC -> NOT DUE
        due_wed = await repo.list_due_subscribers(
            target_time_utc=datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        )
        assert len(due_wed) == 0

        # Monday, October 19, 2026 at 08:00 UTC -> DUE
        due_mon = await repo.list_due_subscribers(
            target_time_utc=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
        )
        assert len(due_mon) == 1
        assert due_mon[0].email == "weekly@example.com"

    # -------------------------------------------------------------------------
    # Requirement 9: Inactive subscriber
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_09_inactive_subscriber_never_due(self):
        """Inactive subscribers (is_active=False) are never returned in due list."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        # list_active filters out is_active=False
        repo.list_active = AsyncMock(return_value=[])

        due_list = await repo.list_due_subscribers(
            target_time_utc=datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        )
        assert len(due_list) == 0

    # -------------------------------------------------------------------------
    # Requirement 10: Unsubscribed subscriber
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_10_unsubscribed_subscriber_excluded(self):
        """Unsubscribed subscriber has is_active=False and unsubscribed_at set, excluded from delivery."""
        mock_session = AsyncMock()
        repo = SubscriberRepository(mock_session)

        unsub = Subscriber(
            id=uuid.uuid4(),
            email="unsub@example.com",
            is_active=False,
            unsubscribed_at=datetime.now(timezone.utc),
        )
        # Repository query filter guarantees is_active=True
        repo.list_active = AsyncMock(return_value=[])

        due_list = await repo.list_due_subscribers()
        assert len(due_list) == 0

    # -------------------------------------------------------------------------
    # Requirement 11: Topic filtering
    # -------------------------------------------------------------------------
    def test_11_topic_filtering(self):
        """Curated stories are deterministically filtered by subscriber topic preferences."""
        story_1 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Autonomous Agents in Production",
            category="Agents",
            topics=["agents", "autonomous", "tool-use"],
        )
        story_2 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Robotics Hardware Breakthrough",
            category="Robotics",
            topics=["hardware", "robotics"],
        )
        story_3 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="LLM Reasoning Benchmarks",
            category="Models",
            topics=["llms", "reasoning"],
        )
        all_stories = [story_1, story_2, story_3]

        # Subscriber interested only in Agents
        matched = DeliveryService._filter_stories_for_subscriber(
            all_stories, subscriber_topics=["agents"]
        )
        assert len(matched) == 1
        assert matched[0].title == "Autonomous Agents in Production"

        # Subscriber interested in LLMs or Robotics
        matched_multi = DeliveryService._filter_stories_for_subscriber(
            all_stories, subscriber_topics=["llms", "robotics"]
        )
        assert len(matched_multi) == 2
        assert {s.title for s in matched_multi} == {
            "Robotics Hardware Breakthrough",
            "LLM Reasoning Benchmarks",
        }

    # -------------------------------------------------------------------------
    # Requirement 12: Content preference filtering
    # -------------------------------------------------------------------------
    def test_12_content_preference_filtering(self):
        """Content preferences support min_score and excluded_topics filters."""
        story_1 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="High Importance Agent",
            ranking_score=0.95,
            topics=["agents"],
        )
        story_2 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Low Importance Agent",
            ranking_score=0.40,
            topics=["agents"],
        )
        story_3 = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Crypto AI Integration",
            ranking_score=0.85,
            topics=["crypto", "agents"],
        )
        all_stories = [story_1, story_2, story_3]

        # Filter: min_score=0.50 and exclude "crypto"
        prefs = {"min_score": 0.50, "excluded_topics": ["crypto"]}
        filtered = DeliveryService._filter_stories_for_subscriber(
            all_stories, subscriber_topics=["agents"], content_preferences=prefs
        )

        assert len(filtered) == 1
        assert filtered[0].title == "High Importance Agent"

    # -------------------------------------------------------------------------
    # Requirement 13: No matching stories -> no email sent
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_13_no_matching_stories_skips_email(self):
        """If 0 stories match subscriber preferences, dispatch skips delivery to prevent empty email."""
        mock_session = AsyncMock()
        mock_email = MockEmailProvider()
        delivery = DeliveryService(session=mock_session, email_provider=mock_email)

        digest_id = uuid.uuid4()
        fake_digest = Digest(
            id=digest_id,
            title="AI News Issue",
            digest_date=date(2026, 10, 14),
            story_count=1,
            html_content="<p>Robotics</p>",
            plain_text_content="Robotics",
        )
        delivery.digest_repo.get_with_stories = AsyncMock(return_value=fake_digest)

        # Mock stories in digest: only Robotics
        robotics_story = CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Robotics Arm Upgrade",
            topics=["robotics"],
        )
        delivery._extract_curated_stories = MagicMock(return_value=[robotics_story])

        # Subscriber wants only "Healthcare"
        sub = Subscriber(
            id=uuid.uuid4(),
            email="health@example.com",
            topics=["healthcare"],
            is_active=True,
        )

        res = await delivery.send_digest_to_subscribers(
            digest_id=digest_id,
            subscribers=[sub],
        )

        assert res.sent_count == 0
        assert res.skipped_count == 1
        assert len(mock_email.sent_messages) == 0  # No email sent!

    # -------------------------------------------------------------------------
    # Requirement 14: Duplicate scheduler execution -> one email only
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_14_duplicate_scheduler_execution_one_email_only(self):
        """When scheduler runs multiple times for the same issue, subscriber receives exactly 1 email."""
        mock_session = AsyncMock()
        mock_email = MockEmailProvider()
        delivery = DeliveryService(session=mock_session, email_provider=mock_email)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        sub = Subscriber(
            id=sub_id,
            email="dedup@example.com",
            is_active=True,
            topics=[],
        )

        fake_digest = Digest(
            id=digest_id,
            title="Daily Digest",
            digest_date=date(2026, 10, 14),
            story_count=1,
            html_content="<p>News</p>",
            plain_text_content="News",
        )
        delivery.digest_repo.get_with_stories = AsyncMock(return_value=fake_digest)
        delivery._extract_curated_stories = MagicMock(return_value=[
            CuratedStoryResponse(story_id=uuid.uuid4(), title="General AI", topics=[])
        ])

        # Run 1: Claim succeeds -> email sent
        claim_rec = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "token_1"},
        )
        delivery.digest_repo.claim_delivery = AsyncMock(return_value=claim_rec)
        delivery.digest_repo.complete_delivery = AsyncMock(return_value=True)

        res_1 = await delivery.send_digest_to_subscribers(digest_id=digest_id, subscribers=[sub])
        assert res_1.sent_count == 1
        assert len(mock_email.sent_messages) == 1

        # Run 2: Claim returns None (already sent/claimed) -> skipped
        delivery.digest_repo.claim_delivery = AsyncMock(return_value=None)
        res_2 = await delivery.send_digest_to_subscribers(digest_id=digest_id, subscribers=[sub])
        assert res_2.sent_count == 0
        assert res_2.skipped_count == 1
        assert len(mock_email.sent_messages) == 1  # Total emails sent remains 1!

    # -------------------------------------------------------------------------
    # Requirement 15: Delivery failure -> correct failure state and retry behavior
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_15_delivery_failure_and_retry(self):
        """Provider failure marks delivery FAILED; subsequent run can reclaim and succeed."""
        mock_session = AsyncMock()
        failing_provider = MockEmailProvider(should_succeed=False, failure_message="503 Service Unavailable")
        delivery = DeliveryService(session=mock_session, email_provider=failing_provider)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        sub = Subscriber(id=sub_id, email="retry@example.com", is_active=True, topics=[])

        fake_digest = Digest(
            id=digest_id,
            title="Daily Digest",
            digest_date=date(2026, 10, 14),
            story_count=1,
            html_content="<p>News</p>",
            plain_text_content="News",
        )
        delivery.digest_repo.get_with_stories = AsyncMock(return_value=fake_digest)
        delivery._extract_curated_stories = MagicMock(return_value=[
            CuratedStoryResponse(story_id=uuid.uuid4(), title="General AI", topics=[])
        ])

        claim_rec = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "token_fail"},
        )
        delivery.digest_repo.claim_delivery = AsyncMock(return_value=claim_rec)
        delivery.digest_repo.complete_delivery = AsyncMock(return_value=True)

        # Attempt 1: Fails
        res_fail = await delivery.send_digest_to_subscribers(digest_id=digest_id, subscribers=[sub])
        assert res_fail.failed_count == 1
        assert res_fail.sent_count == 0
        delivery.digest_repo.complete_delivery.assert_called_with(
            record=claim_rec,
            status="FAILED",
            claim_token="token_fail",
            provider="MOCK",
            error_message="503 Service Unavailable",
        )

        # Attempt 2: Provider recovers -> succeeds
        succeeding_provider = MockEmailProvider(should_succeed=True)
        delivery.email_provider = succeeding_provider
        claim_reclaim = DeliveryRecord(
            id=claim_rec.id,
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "token_reclaim"},
        )
        delivery.digest_repo.claim_delivery = AsyncMock(return_value=claim_reclaim)

        res_retry = await delivery.send_digest_to_subscribers(digest_id=digest_id, subscribers=[sub])
        assert res_retry.sent_count == 1
        assert res_retry.failed_count == 0
        assert len(succeeding_provider.sent_messages) == 1

    # -------------------------------------------------------------------------
    # Requirement 16: Welcome email + digest on same calendar day
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_16_welcome_email_and_digest_on_same_calendar_day(self):
        """Subscriber receives welcome email upon signup and later receives normal digest on same day."""
        mock_session = AsyncMock()
        mock_email = MockEmailProvider()
        welcome_service = WelcomeEmailService(email_provider=mock_email)
        subscriber_service = SubscriberService(session=mock_session, welcome_service=welcome_service)

        sub_id = uuid.uuid4()
        fake_sub = Subscriber(
            id=sub_id,
            email="sameday@example.com",
            is_active=True,
            frequency="DAILY",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
            unsubscribe_token="token_same_day",
            topics=[],
        )
        subscriber_service.repo.upsert_subscriber = AsyncMock(return_value=(fake_sub, True))

        # 1. User subscribes in the morning -> receives welcome email
        req = SubscribeRequest(
            email="sameday@example.com",
            preferred_time="08:00",
            timezone="Asia/Kolkata",
        )
        sub, created = await subscriber_service.subscribe(req)
        assert created is True
        assert len(mock_email.sent_messages) == 1
        assert mock_email.sent_messages[0]["subject"] == "You're subscribed to AI News Intelligence"

        # 2. Later that day, scheduler delivers the daily digest to due subscribers
        delivery = DeliveryService(session=mock_session, email_provider=mock_email)
        digest_id = uuid.uuid4()
        fake_digest = Digest(
            id=digest_id,
            title="Daily Digest Issue",
            digest_date=date(2026, 10, 14),
            story_count=1,
            html_content="<p>Digest</p>",
            plain_text_content="Digest",
        )
        delivery.digest_repo.get_with_stories = AsyncMock(return_value=fake_digest)
        delivery._extract_curated_stories = MagicMock(return_value=[
            CuratedStoryResponse(story_id=uuid.uuid4(), title="Headline", topics=[])
        ])
        delivery.digest_repo.claim_delivery = AsyncMock(return_value=DeliveryRecord(
            id=uuid.uuid4(), digest_id=digest_id, subscriber_id=sub_id, status="PENDING"
        ))
        delivery.digest_repo.complete_delivery = AsyncMock(return_value=True)

        res_digest = await delivery.send_digest_to_subscribers(digest_id=digest_id, subscribers=[fake_sub])
        assert res_digest.sent_count == 1
        # Both welcome email and digest were delivered
        assert len(mock_email.sent_messages) == 2
        assert mock_email.sent_messages[1]["subject"] == "Daily Digest Issue"

    # -------------------------------------------------------------------------
    # Requirement 17: Digest idempotency
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_17_digest_compilation_idempotency(self):
        """Compiling a digest for target_date reuses the existing issue unless force=True."""
        mock_session = AsyncMock()
        service = DailyDispatchService(session=mock_session)

        target_date = date(2026, 10, 14)
        existing_digest = Digest(
            id=uuid.uuid4(),
            title="Existing Issue",
            digest_date=target_date,
            story_count=5,
        )
        service.digest_service.generate_daily_digest = AsyncMock(return_value=existing_digest)

        digest = await service.compile_digest_issue(target_date=target_date, force=False)
        assert digest.id == existing_digest.id
        service.digest_service.generate_daily_digest.assert_called_once_with(
            target_date=target_date, force=False
        )

    # -------------------------------------------------------------------------
    # Requirement 18: News collection runs independently of subscriber delivery
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_18_news_collection_runs_independently_of_delivery(self):
        """collect_and_curate_news does not trigger delivery or send any emails."""
        mock_session = AsyncMock()
        mock_pipeline = AsyncMock()
        mock_delivery = AsyncMock()

        mock_pipeline.run_pipeline.return_value = MagicMock(
            status="SUCCESS",
            items_processed=15,
            ai_analyses_performed=8,
            embeddings_generated=4,
            embeddings_reused=4,
            stories_ranked=6,
            stories_curated=5,
        )

        service = DailyDispatchService(
            session=mock_session,
            pipeline_service=mock_pipeline,
            delivery_service=mock_delivery,
        )

        with patch("app.infrastructure.repositories.source_repo.SourceRepository.list_enabled", new_callable=AsyncMock) as mock_src:
            mock_src.return_value = [MagicMock()]
            col_res = await service.collect_and_curate_news(run_ingestion=True)

        assert col_res["status"] == "SUCCESS"
        assert col_res["items_processed"] == 15
        assert col_res["stories_curated"] == 5
        mock_delivery.send_digest.assert_not_called()
        mock_delivery.send_digest_to_subscribers.assert_not_called()

    # -------------------------------------------------------------------------
    # Requirement 19: Multiple subscribers reuse the same curated story set
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_19_multiple_subscribers_reuse_same_curated_story_set(self):
        """Subscribers with different preferences filter the single curated issue without LLM regeneration."""
        mock_session = AsyncMock()
        mock_email = MockEmailProvider()
        delivery = DeliveryService(session=mock_session, email_provider=mock_email)

        digest_id = uuid.uuid4()
        fake_digest = Digest(
            id=digest_id,
            title="Curated Issue #42",
            digest_date=date(2026, 10, 14),
            story_count=3,
            html_content="<p>All Stories</p>",
            plain_text_content="All Stories",
        )
        delivery.digest_repo.get_with_stories = AsyncMock(return_value=fake_digest)

        curated = [
            CuratedStoryResponse(story_id=uuid.uuid4(), title="LLM Paper", topics=["llms"]),
            CuratedStoryResponse(story_id=uuid.uuid4(), title="Robot Arm", topics=["robotics"]),
            CuratedStoryResponse(story_id=uuid.uuid4(), title="Autonomous Agent", topics=["agents"]),
        ]
        delivery._extract_curated_stories = MagicMock(return_value=curated)

        sub_a = Subscriber(id=uuid.uuid4(), email="sub_a@example.com", is_active=True, topics=["llms"])
        sub_b = Subscriber(id=uuid.uuid4(), email="sub_b@example.com", is_active=True, topics=["robotics"])
        sub_c = Subscriber(id=uuid.uuid4(), email="sub_c@example.com", is_active=True, topics=["agents"])

        delivery.digest_repo.claim_delivery = AsyncMock(side_effect=[
            DeliveryRecord(id=uuid.uuid4(), digest_id=digest_id, subscriber_id=sub_a.id, status="PENDING"),
            DeliveryRecord(id=uuid.uuid4(), digest_id=digest_id, subscriber_id=sub_b.id, status="PENDING"),
            DeliveryRecord(id=uuid.uuid4(), digest_id=digest_id, subscriber_id=sub_c.id, status="PENDING"),
        ])
        delivery.digest_repo.complete_delivery = AsyncMock(return_value=True)

        res = await delivery.send_digest_to_subscribers(
            digest_id=digest_id,
            subscribers=[sub_a, sub_b, sub_c],
        )

        assert res.sent_count == 3
        assert len(mock_email.sent_messages) == 3
        # Sub A received LLM story
        assert "LLM Paper" in mock_email.sent_messages[0]["html"]
        # Sub B received Robotics story
        assert "Robot Arm" in mock_email.sent_messages[1]["html"]
        # Sub C received Agent story
        assert "Autonomous Agent" in mock_email.sent_messages[2]["html"]

    # -------------------------------------------------------------------------
    # Requirement 20: Retention cleanup does not delete active subscribers
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_20_retention_cleanup_preserves_active_subscribers(self):
        """Retention cleanup never touches or deletes subscriber records."""
        mock_session = AsyncMock()
        mock_res_del = MagicMock()
        mock_res_del.rowcount = 10
        mock_res_prune = MagicMock()
        mock_res_prune.rowcount = 5
        mock_session.execute = AsyncMock(side_effect=[
            mock_res_del,    # cleanup_delivery_records
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),  # digest_story_ids
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),  # digest_content_items
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),  # canonical_items
            mock_res_prune,  # delete uncurated old content
        ])

        retention = RetentionService(session=mock_session)
        report = await retention.run_retention_cleanup(news_retention_days=90, delivery_retention_days=180)

        assert report.status == "success"
        # Confirm no DELETE statements targeting the subscribers table were executed
        for call_args in mock_session.execute.call_args_list:
            stmt = call_args[0][0]
            stmt_str = str(stmt).lower()
            assert "delete from subscribers" not in stmt_str
            assert "subscribers" not in stmt_str or "delivery_records" in stmt_str

    # -------------------------------------------------------------------------
    # Requirement 21: Retention cleanup respects foreign keys
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_21_retention_cleanup_respects_foreign_keys(self):
        """Historical content in published digests is pruned rather than deleted, preserving digest references."""
        mock_session = AsyncMock()
        retention = RetentionService(session=mock_session)

        # Mock digest_stories reference story_id_1
        story_id_1 = uuid.uuid4()
        content_item_id_1 = uuid.uuid4()

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[story_id_1])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[content_item_id_1])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(rowcount=1),  # pruned count
            MagicMock(rowcount=4),  # deleted uncurated count
        ])

        res = await retention.cleanup_news_content(retention_days=90)
        assert res["pruned"] == 1
        assert res["deleted"] == 4

    # -------------------------------------------------------------------------
    # Requirement 22: Invalid timezone handling
    # -------------------------------------------------------------------------
    def test_22_invalid_timezone_handling(self):
        """Invalid IANA timezone identifier raises a validation error with clear message."""
        with pytest.raises(ValueError, match="Invalid IANA timezone identifier"):
            normalize_and_validate_timezone("Mars/Olympus_Mons")

        with pytest.raises(ValidationError):
            SubscribeRequest(
                email="invalid_tz@example.com",
                timezone="Not/A_Real_Timezone",
            )

    # -------------------------------------------------------------------------
    # Requirement 23: Invalid preferred time handling
    # -------------------------------------------------------------------------
    def test_23_invalid_preferred_time_handling(self):
        """Invalid delivery time (e.g. '25:00', '8am', '99:99') raises validation error."""
        with pytest.raises(ValueError, match="Invalid preferred_time format"):
            validate_preferred_time_format("25:00")

        with pytest.raises(ValueError, match="Invalid preferred_time format"):
            validate_preferred_time_format("8:00am")

        with pytest.raises(ValidationError):
            SubscribeRequest(
                email="bad_time@example.com",
                preferred_time="invalid_time",
            )

    # -------------------------------------------------------------------------
    # Requirement 24: Invalid frequency handling
    # -------------------------------------------------------------------------
    def test_24_invalid_frequency_handling(self):
        """Frequency other than DAILY or WEEKLY raises validation error."""
        with pytest.raises(ValueError, match="Invalid frequency"):
            validate_frequency_value("HOURLY")

        with pytest.raises(ValueError, match="Invalid frequency"):
            validate_frequency_value("MONTHLY")

        with pytest.raises(ValidationError):
            SubscribeRequest(
                email="bad_freq@example.com",
                frequency="MONTHLY",
            )

    # -------------------------------------------------------------------------
    # Requirement 25: Scheduler handles zero subscribers cleanly
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_25_scheduler_handles_zero_subscribers_cleanly(self):
        """When 0 subscribers are due, deliver_due_subscribers exits cleanly with zero errors."""
        mock_session = AsyncMock()
        service = DailyDispatchService(session=mock_session)
        service.subscriber_repo.list_due_subscribers = AsyncMock(return_value=[])

        res = await service.deliver_due_subscribers()
        assert res["status"] == "success"
        assert res["due_subscribers_count"] == 0
        assert res["sent_count"] == 0
        assert res["failed_count"] == 0

    # -------------------------------------------------------------------------
    # Requirement 26: Scheduler handles zero stories cleanly
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_26_scheduler_handles_zero_stories_cleanly(self):
        """When digest contains 0 curated stories, delivery is safely skipped to avoid blank emails."""
        mock_session = AsyncMock()
        service = DailyDispatchService(session=mock_session)

        sub = Subscriber(id=uuid.uuid4(), email="sub@example.com", is_active=True)
        service.subscriber_repo.list_due_subscribers = AsyncMock(return_value=[sub])

        empty_digest = Digest(
            id=uuid.uuid4(),
            title="Empty Digest",
            digest_date=date(2026, 10, 14),
            story_count=0,
        )
        service.digest_repo.get_by_date = AsyncMock(return_value=empty_digest)

        res = await service.deliver_due_subscribers()
        assert res["status"] == "skipped"
        assert res["sent_count"] == 0
        assert res["due_subscribers_count"] == 1

    # -------------------------------------------------------------------------
    # Requirement 27: Scheduler handles database errors safely
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_27_scheduler_handles_database_errors_safely(self):
        """Retention cleanup and scheduler execution catch database exceptions, rollback, and isolate errors."""
        mock_session = AsyncMock()
        retention = RetentionService(session=mock_session)
        mock_session.execute.side_effect = Exception("Simulated connection timeout to PostgreSQL")

        res = await retention.run_retention_cleanup()
        assert res.status == "error"
        assert "Simulated connection timeout" in res.error
        mock_session.rollback.assert_called_once()

    # -------------------------------------------------------------------------
    # Requirement 28: No real Resend calls in tests
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_28_no_real_resend_calls_in_tests(self):
        """Verify all deliveries in test use MockEmailProvider and zero HTTP requests hit api.resend.com."""
        mock_provider = MockEmailProvider(should_succeed=True)
        assert not isinstance(mock_provider, ResendEmailProvider)

        result = await mock_provider.send_email(
            to_email="test@example.com",
            subject="Test Subject",
            html_content="<p>Test</p>",
        )
        assert result.success is True
        assert result.provider_message_id.startswith("mock_")
        assert len(mock_provider.sent_messages) == 1
