import os
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.dispatch import DailyDispatchRequest, DispatchStatusResponse
from app.domain.models.source import SourceType
from app.domain.schemas.source import SourceCreate
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.digest import DeliveryRecord, Digest, DigestStory
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.digest_service import DigestService
from app.services.dispatch_service import DailyDispatchService
from app.services.email_service import DeliveryService, MockEmailProvider
from app.services.source_service import SourceService
from app.api.routes.dispatch import verify_dispatch_auth, get_dispatch_status


class TestSourceSeedingIdempotency:
    """Test idempotent default source seeding without external dependencies."""

    @pytest.mark.asyncio
    async def test_seed_default_sources_first_and_second_runs(self):
        """Verify seeding adds all default sources initially, and updates them idempotently on re-run."""
        mock_repo = MagicMock()
        existing_store = {}

        async def mock_get_by_url(url):
            return existing_store.get(url)

        async def mock_get_by_slug(slug):
            for s in existing_store.values():
                if s.slug == slug:
                    return s
            return None

        async def mock_add(source):
            source.id = uuid.uuid4()
            existing_store[source.url] = source
            return source

        async def mock_update(source):
            existing_store[source.url] = source
            return source

        mock_repo.get_by_url.side_effect = mock_get_by_url
        mock_repo.get_by_slug.side_effect = mock_get_by_slug
        mock_repo.add.side_effect = mock_add
        mock_repo.update.side_effect = mock_update

        service = SourceService(mock_repo)

        # 1. First run: all 10 baseline sources should be added
        seeded_first = await service.seed_default_sources()
        assert len(seeded_first) == 10
        assert len(existing_store) == 10

        # 2. Second run: all 10 baseline sources should be updated, 0 duplicates added
        seeded_second = await service.seed_default_sources()
        assert len(seeded_second) == 10
        assert len(existing_store) == 10  # Store size unchanged

    @pytest.mark.asyncio
    async def test_seed_preserves_customized_config_and_backfills_missing_keys(self):
        """Verify seeding preserves custom configuration, preserves reliability score, and leaves disabled state intact."""
        mock_repo = MagicMock()
        existing_source = Source(
            id=uuid.uuid4(),
            name="Custom ArXiv AI",
            slug="arxiv-ai",
            type="ARXIV",
            url="https://export.arxiv.org/api/query",
            enabled=False,  # Intentionally disabled by administrator
            reliability_score=0.73,  # Customized reliability score
            config={
                "custom_admin_setting": "preserve-me",
                "max_results_per_page": 99,  # Customized overlapping key
            },
        )
        existing_store = {existing_source.url: existing_source}

        async def mock_get_by_url(url):
            return existing_store.get(url)

        async def mock_get_by_slug(slug):
            for s in existing_store.values():
                if s.slug == slug:
                    return s
            return None

        async def mock_update(source):
            existing_store[source.url] = source
            return source

        async def mock_add(source):
            source.id = uuid.uuid4()
            existing_store[source.url] = source
            return source

        mock_repo.get_by_url.side_effect = mock_get_by_url
        mock_repo.get_by_slug.side_effect = mock_get_by_slug
        mock_repo.update.side_effect = mock_update
        mock_repo.add.side_effect = mock_add

        service = SourceService(mock_repo)

        # Run seeding
        seeded = await service.seed_default_sources()
        arxiv = next(s for s in seeded if s.slug == "arxiv-ai")

        # 1. Custom admin setting preserved
        assert arxiv.config.get("custom_admin_setting") == "preserve-me"
        # 2. Overlapping key preserved
        assert arxiv.config.get("max_results_per_page") == 99
        # 3. Missing baseline keys backfilled
        assert "search_query" in arxiv.config
        assert "categories" in arxiv.config
        # 4. Custom reliability score preserved
        assert arxiv.reliability_score == 0.73
        # 5. Disabled status preserved
        assert arxiv.enabled is False

        # Run repeated seeding to verify idempotency
        seeded_again = await service.seed_default_sources()
        arxiv_again = next(s for s in seeded_again if s.slug == "arxiv-ai")
        assert arxiv_again.config.get("custom_admin_setting") == "preserve-me"
        assert arxiv_again.config.get("max_results_per_page") == 99
        assert arxiv_again.reliability_score == 0.73
        assert arxiv_again.enabled is False

    @pytest.mark.asyncio
    async def test_init_source_registry_persists_and_commits_transaction(self):
        """Verify init_source_registry acquires session, seeds sources, and commits transaction."""
        from app.main import init_source_registry

        mock_session = AsyncMock()
        mock_session_cls = MagicMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_ctx

        mock_seeded = [MagicMock(), MagicMock()]
        with patch("app.infrastructure.database.session.AsyncSessionLocal", mock_session_cls):
            with patch("app.services.source_service.SourceService.seed_default_sources", AsyncMock(return_value=mock_seeded)):
                result = await init_source_registry()

        assert result == mock_seeded
        mock_session.commit.assert_awaited_once()
        mock_session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_init_source_registry_rolls_back_on_exception(self):
        """Verify init_source_registry cleanly rolls back transaction when an exception occurs."""
        from app.main import init_source_registry

        mock_session = AsyncMock()
        mock_session_cls = MagicMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_ctx

        with patch("app.infrastructure.database.session.AsyncSessionLocal", mock_session_cls):
            with patch("app.services.source_service.SourceService.seed_default_sources", AsyncMock(side_effect=RuntimeError("DB write failed"))):
                with pytest.raises(RuntimeError, match="DB write failed"):
                    await init_source_registry()

        mock_session.commit.assert_not_called()
        mock_session.rollback.assert_awaited_once()


class TestIngestionOrchestratorAllSources:
    """Test unified multi-source ingestion with per-source fault isolation."""

    @pytest.mark.asyncio
    async def test_ingest_all_sources_fault_isolation(self):
        """Verify failures in one source do not halt ingestion for subsequent sources."""
        mock_session = AsyncMock()
        orchestrator = IngestionOrchestrator(mock_session)

        # Create two enabled sources: one succeeds, one fails
        source_success = Source(
            id=uuid.uuid4(),
            name="Working RSS Source",
            slug="working-rss",
            type="RSS",
            url="https://example.com/rss.xml",
            enabled=True,
            config={},
        )
        source_failing = Source(
            id=uuid.uuid4(),
            name="Broken Source",
            slug="broken-source",
            type="WEB",
            url="https://broken.invalid/feed",
            enabled=True,
            config={},
        )

        orchestrator.source_repo.list_enabled = AsyncMock(return_value=[source_success, source_failing])

        # Mock ingest_source behavior
        async def mock_ingest_source(source):
            from app.domain.schemas.ingestion import SourceIngestionResult
            if source.slug == "working-rss":
                return SourceIngestionResult(
                    source_id=source.id,
                    source_name=source.name,
                    source_type=source.type,
                    status="SUCCESS",
                    entries_fetched=5,
                    entries_inserted=5,
                )
            else:
                return SourceIngestionResult(
                    source_id=source.id,
                    source_name=source.name,
                    source_type=source.type,
                    status="FAILED",
                    errors=["Simulated 503 Provider Unavailable"],
                )

        orchestrator.ingest_source = AsyncMock(side_effect=mock_ingest_source)

        summary = await orchestrator.ingest_all_sources()
        assert summary.total_sources == 2
        assert summary.succeeded_sources == 1
        assert summary.failed_sources == 1
        assert summary.total_fetched == 5
        assert summary.total_inserted == 5
        assert len(summary.source_results) == 2


class TestDigestGenerationAndDispatch:
    """Test digest compilation and dispatch handling with and without eligible stories."""

    @pytest.mark.asyncio
    async def test_digest_with_zero_stories_skips_delivery(self):
        """When 0 curated stories exist, digest status is DRAFT and delivery is skipped."""
        mock_session = AsyncMock()

        # Mock digest service
        mock_digest_service = MagicMock()
        empty_digest = Digest(
            id=uuid.uuid4(),
            title="AI News Daily Digest — Test Date",
            digest_date=date(2026, 10, 13),
            html_content="<div>No stories</div>",
            plain_text_content="No stories",
            story_count=0,
            status="DRAFT",
        )
        mock_digest_service.generate_daily_digest = AsyncMock(return_value=empty_digest)

        # Mock pipeline service
        mock_pipeline_service = MagicMock()
        mock_pipeline_response = MagicMock(
            status="SUCCESS",
            items_processed=0,
            ai_analyses_performed=0,
            embeddings_generated=0,
            embeddings_reused=0,
            stories_ranked=0,
            stories_curated=0,
            duration_ms=50.0,
        )
        mock_pipeline_service.run_pipeline = AsyncMock(return_value=mock_pipeline_response)

        # Mock delivery service
        mock_delivery_service = MagicMock()
        mock_delivery_service.send_digest = AsyncMock()

        dispatch = DailyDispatchService(
            session=mock_session,
            pipeline_service=mock_pipeline_service,
            digest_service=mock_digest_service,
            delivery_service=mock_delivery_service,
        )

        req = DailyDispatchRequest(
            target_date=date(2026, 10, 13),
            run_ingestion=False,
            dry_run=False,
        )
        res = await dispatch.run_daily_dispatch(req)

        # Must report status skipped and 0 delivery calls
        assert res.status == "skipped"
        assert res.digest_stories_count == 0
        assert res.sent_count == 0
        mock_delivery_service.send_digest.assert_not_called()

    @pytest.mark.asyncio
    async def test_digest_with_curated_stories_dispatches_successfully(self):
        """When stories exist, digest status is PUBLISHED and delivery proceeds."""
        mock_session = AsyncMock()

        mock_digest_service = MagicMock()
        populated_digest = Digest(
            id=uuid.uuid4(),
            title="AI News Daily Digest — Test Date",
            digest_date=date(2026, 10, 13),
            html_content="<div>Stories here</div>",
            plain_text_content="Stories here",
            story_count=5,
            status="PUBLISHED",
        )
        mock_digest_service.generate_daily_digest = AsyncMock(return_value=populated_digest)

        mock_pipeline_service = MagicMock()
        mock_pipeline_response = MagicMock(
            status="SUCCESS",
            items_processed=5,
            ai_analyses_performed=5,
            embeddings_generated=5,
            embeddings_reused=0,
            stories_ranked=5,
            stories_curated=5,
            duration_ms=120.0,
        )
        mock_pipeline_service.run_pipeline = AsyncMock(return_value=mock_pipeline_response)

        mock_delivery_service = MagicMock()
        from app.domain.models.newsletter import DigestSendResponse
        mock_delivery_service.send_digest = AsyncMock(
            return_value=DigestSendResponse(
                digest_id=populated_digest.id,
                total_subscribers=2,
                sent_count=2,
                failed_count=0,
                skipped_count=0,
                dry_run=False,
            )
        )

        dispatch = DailyDispatchService(
            session=mock_session,
            pipeline_service=mock_pipeline_service,
            digest_service=mock_digest_service,
            delivery_service=mock_delivery_service,
        )

        req = DailyDispatchRequest(
            target_date=date(2026, 10, 13),
            run_ingestion=False,
            dry_run=False,
        )
        res = await dispatch.run_daily_dispatch(req)

        assert res.status == "success"
        assert res.digest_stories_count == 5
        assert res.sent_count == 2
        mock_delivery_service.send_digest.assert_called_once()


class TestDuplicateDeliveryPrevention:
    """Test concurrency-safe delivery claims and duplicate send prevention."""

    @pytest.mark.asyncio
    async def test_subscriber_already_sent_is_skipped(self):
        """Subscribers who already have a delivery record with status='SENT' are skipped."""
        mock_session = AsyncMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()

        digest = Digest(
            id=digest_id,
            title="Daily Digest",
            html_content="<p>Body</p>",
            plain_text_content="Body",
            story_count=3,
        )
        subscriber = Subscriber(
            id=sub_id,
            email="recipient@example.com",
            is_active=True,
            unsubscribe_token="token_abc",
        )
        existing_record = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="SENT",
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[subscriber])
        delivery_service.digest_repo.get_delivery_record = AsyncMock(return_value=existing_record)

        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock()
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 1
        assert res.sent_count == 0
        assert res.skipped_count == 1
        mock_provider.send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_claim_delivery_acquires_pending_when_new(self):
        """When no prior delivery record exists, claim_delivery acquires an atomic PENDING claim."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_nested = MagicMock()
        mock_nested.__aenter__ = AsyncMock(return_value=mock_nested)
        mock_nested.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin_nested = MagicMock(return_value=mock_nested)

        digest_repo = DigestRepository(mock_session)
        digest_repo.get_delivery_record = AsyncMock(return_value=None)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()

        claim = await digest_repo.claim_delivery(
            digest_id=digest_id,
            subscriber_id=sub_id,
            recipient_email="sub@example.com",
            provider="RESEND",
        )

        assert claim is not None
        assert claim.status == "PENDING"
        assert claim.recipient_email == "sub@example.com"
        assert claim.provider == "RESEND"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_claim_delivery_integrity_error_returns_none(self):
        """When concurrent worker races and triggers unique constraint IntegrityError, claim_delivery returns None."""
        mock_session = AsyncMock()
        mock_nested = MagicMock()
        mock_nested.__aenter__ = AsyncMock(side_effect=IntegrityError("duplicate key", None, None))
        mock_nested.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin_nested = MagicMock(return_value=mock_nested)

        digest_repo = DigestRepository(mock_session)
        digest_repo.get_delivery_record = AsyncMock(return_value=None)

        claim = await digest_repo.claim_delivery(
            digest_id=uuid.uuid4(),
            subscriber_id=uuid.uuid4(),
            recipient_email="sub@example.com",
        )

        assert claim is None

    @pytest.mark.asyncio
    async def test_claim_delivery_active_pending_skipped(self):
        """Active PENDING delivery claimed recently (< 15 min) returns None to prevent concurrent send."""
        mock_session = AsyncMock()
        digest_repo = DigestRepository(mock_session)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        active_record = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            attempted_at=datetime.now(timezone.utc),  # Just now
        )
        digest_repo.get_delivery_record = AsyncMock(return_value=active_record)

        claim = await digest_repo.claim_delivery(
            digest_id=digest_id,
            subscriber_id=sub_id,
            recipient_email="sub@example.com",
            claim_timeout_seconds=900,
        )

        assert claim is None

    @pytest.mark.asyncio
    async def test_claim_delivery_failed_record_concurrent_reclaim_only_one_acquires(self):
        """When two workers concurrently attempt to reclaim a FAILED record, atomic UPDATE ensures only one succeeds."""
        mock_session_1 = AsyncMock()
        mock_session_2 = AsyncMock()
        repo_1 = DigestRepository(mock_session_1)
        repo_2 = DigestRepository(mock_session_2)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        failed_record = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="FAILED",
            error_message="503 Service Unavailable",
        )
        repo_1.get_delivery_record = AsyncMock(return_value=failed_record)
        repo_2.get_delivery_record = AsyncMock(return_value=failed_record)

        # Worker 1's atomic UPDATE succeeds and returns updated row
        reclaimed_1 = DeliveryRecord(
            id=failed_record.id,
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "token_worker_1"},
        )
        mock_res_1 = MagicMock()
        mock_res_1.scalars.return_value.first.return_value = reclaimed_1
        mock_session_1.execute = AsyncMock(return_value=mock_res_1)

        # Worker 2's atomic UPDATE finds 0 matching rows (overtaken)
        mock_res_2 = MagicMock()
        mock_res_2.scalars.return_value.first.return_value = None
        mock_session_2.execute = AsyncMock(return_value=mock_res_2)

        claim_1 = await repo_1.claim_delivery(digest_id=digest_id, subscriber_id=sub_id, recipient_email="sub@example.com")
        claim_2 = await repo_2.claim_delivery(digest_id=digest_id, subscriber_id=sub_id, recipient_email="sub@example.com")

        assert claim_1 is not None
        assert claim_1.status == "PENDING"
        assert claim_1.metadata_json["claim_token"] == "token_worker_1"

        assert claim_2 is None

    @pytest.mark.asyncio
    async def test_claim_delivery_stale_pending_concurrent_reclaim_only_one_acquires(self):
        """When two workers concurrently attempt to reclaim a stale PENDING record (>15m), only one succeeds."""
        mock_session_1 = AsyncMock()
        mock_session_2 = AsyncMock()
        repo_1 = DigestRepository(mock_session_1)
        repo_2 = DigestRepository(mock_session_2)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        stale_record = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            attempted_at=datetime.now(timezone.utc) - timedelta(minutes=25),
        )
        repo_1.get_delivery_record = AsyncMock(return_value=stale_record)
        repo_2.get_delivery_record = AsyncMock(return_value=stale_record)

        # Worker 1 wins atomic update
        reclaimed_1 = DeliveryRecord(
            id=stale_record.id,
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "token_worker_1"},
        )
        mock_res_1 = MagicMock()
        mock_res_1.scalars.return_value.first.return_value = reclaimed_1
        mock_session_1.execute = AsyncMock(return_value=mock_res_1)

        # Worker 2 matches 0 rows
        mock_res_2 = MagicMock()
        mock_res_2.scalars.return_value.first.return_value = None
        mock_session_2.execute = AsyncMock(return_value=mock_res_2)

        claim_1 = await repo_1.claim_delivery(digest_id=digest_id, subscriber_id=sub_id, recipient_email="sub@example.com")
        claim_2 = await repo_2.claim_delivery(digest_id=digest_id, subscriber_id=sub_id, recipient_email="sub@example.com")

        assert claim_1 is not None
        assert claim_2 is None

    @pytest.mark.asyncio
    async def test_complete_delivery_expired_worker_cannot_mark_newer_claim_sent(self, caplog):
        """An expired worker with an old token cannot mark a newer worker's claim SENT."""
        mock_session = AsyncMock()
        repo = DigestRepository(mock_session)

        record_id = uuid.uuid4()
        old_record = DeliveryRecord(
            id=record_id,
            status="PENDING",
            metadata_json={"claim_token": "old_expired_token"},
        )

        # Database returns 0 matching rows because row in DB has newer claim_token
        mock_res = MagicMock()
        mock_res.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_res)

        completed = await repo.complete_delivery(
            record=old_record,
            status="SENT",
            claim_token="old_expired_token",
            provider_message_id="msg_old",
        )

        assert completed is None
        # Assert token is not exposed in log warning
        assert "old_expired_token" not in caplog.text

    @pytest.mark.asyncio
    async def test_complete_delivery_expired_worker_cannot_overwrite_newer_sent_with_failed(self):
        """An expired worker finishing with an error cannot overwrite a newer SENT state with FAILED."""
        mock_session = AsyncMock()
        repo = DigestRepository(mock_session)

        record_id = uuid.uuid4()
        old_record = DeliveryRecord(
            id=record_id,
            status="PENDING",
            metadata_json={"claim_token": "old_expired_token"},
        )

        # Database returns 0 matching rows because status is already SENT or token is newer
        mock_res = MagicMock()
        mock_res.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_res)

        completed = await repo.complete_delivery(
            record=old_record,
            status="FAILED",
            claim_token="old_expired_token",
            error_message="Late timeout error",
        )

        assert completed is None

    @pytest.mark.asyncio
    async def test_complete_delivery_valid_owner_successfully_completes(self):
        """Current claim owner with matching token successfully updates delivery record."""
        mock_session = AsyncMock()
        repo = DigestRepository(mock_session)

        record_id = uuid.uuid4()
        current_record = DeliveryRecord(
            id=record_id,
            status="PENDING",
            metadata_json={"claim_token": "active_valid_token"},
        )

        completed_record = DeliveryRecord(
            id=record_id,
            status="SENT",
            provider_message_id="re_valid_123",
            delivered_at=datetime.now(timezone.utc),
            metadata_json={"claim_token": "active_valid_token"},
        )
        mock_res = MagicMock()
        mock_res.scalars.return_value.first.return_value = completed_record
        mock_session.execute = AsyncMock(return_value=mock_res)

        completed = await repo.complete_delivery(
            record=current_record,
            status="SENT",
            claim_token="active_valid_token",
            provider_message_id="re_valid_123",
        )

        assert completed is not None
        assert completed.status == "SENT"
        assert completed.provider_message_id == "re_valid_123"

    @pytest.mark.asyncio
    async def test_claim_tokens_refreshed_on_reclaim_and_never_logged(self, caplog):
        """Claim tokens are refreshed on reclaim and are never exposed in log output."""
        mock_session = AsyncMock()
        repo = DigestRepository(mock_session)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        existing_record = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="FAILED",
            metadata_json={"claim_token": "original_token_abc"},
        )
        repo.get_delivery_record = AsyncMock(return_value=existing_record)

        # Mock execute returning updated record
        mock_res = MagicMock()
        reclaimed_record = DeliveryRecord(
            id=existing_record.id,
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "new_refreshed_token_xyz"},
        )
        mock_res.scalars.return_value.first.return_value = reclaimed_record
        mock_session.execute = AsyncMock(return_value=mock_res)

        claim = await repo.claim_delivery(digest_id=digest_id, subscriber_id=sub_id, recipient_email="sub@example.com")
        assert claim is not None
        token = claim.metadata_json.get("claim_token")
        assert token is not None
        assert token != "original_token_abc"
        assert token not in caplog.text

    @pytest.mark.asyncio
    async def test_concurrent_worker_simulation_only_one_sends(self):
        """Simulate two workers dispatching same digest/subscriber: Worker 1 wins claim and sends, Worker 2 skips."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        sub_id = uuid.uuid4()
        digest = Digest(
            id=digest_id,
            title="AI News Issue",
            html_content="<p>Test</p>",
            plain_text_content="Test",
            story_count=1,
        )
        sub = Subscriber(
            id=sub_id,
            email="racing@example.com",
            is_active=True,
            unsubscribe_token="token_xyz",
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[sub])

        # Worker 1 claims successfully
        claim_rec = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_id,
            status="PENDING",
            metadata_json={"claim_token": "worker_1_tok"},
        )
        delivery_service.digest_repo.claim_delivery = AsyncMock(return_value=claim_rec)
        delivery_service.digest_repo.complete_delivery = AsyncMock(return_value=claim_rec)

        mock_provider = MockEmailProvider(should_succeed=True)
        delivery_service.email_provider = mock_provider

        res1 = await delivery_service.send_digest(digest_id=digest_id)
        assert res1.sent_count == 1
        assert res1.skipped_count == 0
        assert len(mock_provider.sent_messages) == 1

        # Worker 2 attempts same subscriber: claim_delivery returns None (already claimed/sent)
        delivery_service.digest_repo.claim_delivery = AsyncMock(return_value=None)
        res2 = await delivery_service.send_digest(digest_id=digest_id)
        assert res2.sent_count == 0
        assert res2.skipped_count == 1
        # Provider still only called once across both workers
        assert len(mock_provider.sent_messages) == 1


class TestDispatchAuthentication:
    """Test dispatch trigger security and authorization headers."""

    def test_verify_dispatch_auth_when_token_configured(self):
        """When DISPATCH_SECRET_TOKEN is set, valid Bearer and X-Dispatch-Token headers pass; others fail."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", "prod-secret-token-123"):
            # 1. Valid Bearer token
            verify_dispatch_auth(authorization="Bearer prod-secret-token-123")

            # 2. Valid X-Dispatch-Token
            verify_dispatch_auth(x_dispatch_token="prod-secret-token-123")

            # 3. Invalid token raises 401
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(authorization="Bearer wrong-token")
            assert exc_info.value.status_code == 401

            # 4. Missing token raises 401
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth()
            assert exc_info.value.status_code == 401

    def test_verify_dispatch_auth_unconfigured_in_development(self):
        """When DISPATCH_SECRET_TOKEN is unset in development, it allows execution without exception."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", None):
            with patch.object(settings, "ENVIRONMENT", "development"):
                verify_dispatch_auth()  # Should not raise exception

    def test_verify_dispatch_auth_fails_closed_in_production(self):
        """When DISPATCH_SECRET_TOKEN is unset in production, it fails closed with HTTP 503."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", None):
            with patch.object(settings, "ENVIRONMENT", "production"):
                with pytest.raises(HTTPException) as exc_info:
                    verify_dispatch_auth()
                assert exc_info.value.status_code == 503
                assert "DISPATCH_SECRET_TOKEN must be configured" in exc_info.value.detail

    def test_verify_dispatch_auth_fails_closed_in_staging(self):
        """When DISPATCH_SECRET_TOKEN is whitespace or empty in staging, it fails closed with HTTP 503."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", "   "):
            with patch.object(settings, "ENVIRONMENT", "staging"):
                with pytest.raises(HTTPException) as exc_info:
                    verify_dispatch_auth()
                assert exc_info.value.status_code == 503
                assert "DISPATCH_SECRET_TOKEN must be configured" in exc_info.value.detail

    def test_verify_dispatch_auth_unconfigured_in_test(self):
        """When DISPATCH_SECRET_TOKEN is unset in test environment, it allows execution without exception."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", None):
            with patch.object(settings, "ENVIRONMENT", "test"):
                verify_dispatch_auth()  # Should not raise exception

    def test_verify_dispatch_auth_rejects_whitespace_or_malformed(self):
        """When token is configured, rejecting whitespace-only, malformed prefix, or empty credentials with 401."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", "my-secret-token"):
            # Whitespace Bearer
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(authorization="Bearer    ")
            assert exc_info.value.status_code == 401

            # Whitespace header
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(x_dispatch_token="   ")
            assert exc_info.value.status_code == 401

            # Malformed prefix
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(authorization="Basic my-secret-token")
            assert exc_info.value.status_code == 401

    def test_verify_dispatch_auth_fails_closed_on_render_even_if_environment_is_development(self, monkeypatch):
        """When DISPATCH_SECRET_TOKEN is unset on Render hosting, fail closed with HTTP 503 regardless of ENVIRONMENT setting."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", None):
            with patch.object(settings, "ENVIRONMENT", "development"):
                monkeypatch.setenv("RENDER", "true")
                with pytest.raises(HTTPException) as exc_info:
                    verify_dispatch_auth()
                assert exc_info.value.status_code == 503
                assert "DISPATCH_SECRET_TOKEN must be configured" in exc_info.value.detail

    def test_verify_dispatch_auth_fails_closed_on_render_external_url(self, monkeypatch):
        """When RENDER_EXTERNAL_URL is set and DISPATCH_SECRET_TOKEN is unset, fail closed with HTTP 503."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", None):
            with patch.object(settings, "ENVIRONMENT", "development"):
                monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://ai-news-intelligence-vvh5.onrender.com")
                with pytest.raises(HTTPException) as exc_info:
                    verify_dispatch_auth()
                assert exc_info.value.status_code == 503

    def test_verify_dispatch_auth_constant_time_comparison(self):
        """Verify token comparison handles matching and non-matching tokens with constant-time defense."""
        with patch.object(settings, "DISPATCH_SECRET_TOKEN", "constant-time-secret-key-12345"):
            # Invalid token of different length
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(authorization="Bearer short")
            assert exc_info.value.status_code == 401

            # Invalid token of identical length
            with pytest.raises(HTTPException) as exc_info:
                verify_dispatch_auth(authorization="Bearer constant-time-secret-key-99999")
            assert exc_info.value.status_code == 401

            # Valid token passes
            verify_dispatch_auth(authorization="Bearer constant-time-secret-key-12345")


class TestSchedulerTimezoneAndURLConfiguration:
    """Test 9:00 AM IST to 03:30 UTC conversion and environment URL resolution."""

    def test_schedule_hour_and_minute_ist_utc_conversion(self):
        """Verify 9:00 AM Asia/Kolkata (IST = UTC+5:30) corresponds to 03:30 UTC."""
        ist_hour = 9
        ist_minute = 0
        offset_minutes = 5 * 60 + 30  # 330 minutes

        total_ist_minutes = ist_hour * 60 + ist_minute
        total_utc_minutes = (total_ist_minutes - offset_minutes) % (24 * 60)

        utc_hour = total_utc_minutes // 60
        utc_minute = total_utc_minutes % 60

        assert utc_hour == 3
        assert utc_minute == 30
        assert settings.DISPATCH_SCHEDULE_HOUR_UTC == 3
        assert settings.DISPATCH_SCHEDULE_MINUTE_UTC == 30

    def test_render_external_url_resolution(self):
        """Verify public_api_v1_url adopts RENDER_EXTERNAL_URL when default localhost is configured."""
        with patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://ai-news-intelligence-vvh5.onrender.com"}):
            with patch.object(settings, "API_BASE_URL", "http://127.0.0.1:8000"):
                url = settings.public_api_v1_url
                assert url == "https://ai-news-intelligence-vvh5.onrender.com/api/v1"

    def test_effective_newsletter_base_url_in_production(self):
        """Verify effective_newsletter_base_url defaults to portfolio origin in production environment."""
        with patch.object(settings, "ENVIRONMENT", "production"):
            with patch.object(settings, "NEWSLETTER_BASE_URL", "http://127.0.0.1:5500"):
                url = settings.effective_newsletter_base_url
                assert url == "https://abdullah-shaikh-portfolio.vercel.app"


class TestSubscriberPreferenceEnforcement:
    """Test subscriber channel filtering, topic personalization, and safe defaults."""

    @pytest.mark.asyncio
    async def test_non_email_preferred_channel_is_skipped(self):
        """Subscriber with preferred_channel != 'EMAIL' (e.g. 'RSS', 'SLACK') is skipped from email delivery."""
        mock_session = AsyncMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        digest = Digest(
            id=digest_id,
            title="Daily Digest",
            html_content="<p>Body</p>",
            plain_text_content="Body",
            story_count=1,
            digest_stories=[],
        )
        subscriber_rss = Subscriber(
            id=uuid.uuid4(),
            email="rss-user@example.com",
            is_active=True,
            preferred_channel="RSS",
            unsubscribe_token="token_rss",
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[subscriber_rss])
        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock()
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 1
        assert res.sent_count == 0
        assert res.skipped_count == 1
        mock_provider.send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_topic_preference_filters_stories_and_personalizes_digest(self):
        """Subscriber with topic preferences receives only matching stories in their personalized email."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        story_ml = Story(
            id=uuid.uuid4(),
            headline="Breakthrough in LLM Reasoning",
            summary="New reasoning model outperforms baselines.",
            why_it_matters="Accelerates agentic coding workflows.",
            category="Machine Learning",
            ranking_score=0.95,
        )
        story_bio = Story(
            id=uuid.uuid4(),
            headline="Biotech AI Protein Folding",
            summary="New model maps cellular pathways.",
            why_it_matters="Accelerates drug discovery.",
            category="Biotech",
            ranking_score=0.88,
        )

        ds_ml = DigestStory(
            id=uuid.uuid4(),
            digest_id=digest_id,
            story_id=story_ml.id,
            position=1,
            headline_override=story_ml.headline,
            summary_override=story_ml.summary,
            why_it_matters_override=story_ml.why_it_matters,
            ranking_score_snapshot=0.95,
            story=story_ml,
        )
        ds_bio = DigestStory(
            id=uuid.uuid4(),
            digest_id=digest_id,
            story_id=story_bio.id,
            position=2,
            headline_override=story_bio.headline,
            summary_override=story_bio.summary,
            why_it_matters_override=story_bio.why_it_matters,
            ranking_score_snapshot=0.88,
            story=story_bio,
        )

        digest = Digest(
            id=digest_id,
            title="Daily Digest",
            digest_date=date(2026, 10, 13),
            html_content="<p>Default All Stories</p>",
            plain_text_content="Default All Stories",
            story_count=2,
            digest_stories=[ds_ml, ds_bio],
        )

        sub_ml = Subscriber(
            id=uuid.uuid4(),
            email="ml-fan@example.com",
            is_active=True,
            preferred_channel="EMAIL",
            topics=["machine learning"],
            unsubscribe_token="token_ml",
        )

        claim = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_ml.id,
            status="PENDING",
            metadata_json={"claim_token": "token-123"},
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[sub_ml])
        delivery_service.digest_repo.claim_delivery = AsyncMock(return_value=claim)
        delivery_service.digest_repo.complete_delivery = AsyncMock(return_value=True)

        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock(return_value=MagicMock(success=True, provider_message_id="msg_1"))
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 1
        assert res.sent_count == 1
        assert res.skipped_count == 0
        mock_provider.send_email.assert_called_once()
        sent_html = mock_provider.send_email.call_args.kwargs["html_content"]
        assert "Breakthrough in LLM Reasoning" in sent_html
        assert "Biotech AI Protein Folding" not in sent_html

    @pytest.mark.asyncio
    async def test_topic_preference_no_matches_safely_skipped(self):
        """Subscriber whose topic preferences match 0 stories in today's digest is skipped with 0 emails sent."""
        mock_session = AsyncMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        story_ml = Story(
            id=uuid.uuid4(),
            headline="AI Systems Update",
            category="Machine Learning",
            ranking_score=0.9,
        )
        ds = DigestStory(
            id=uuid.uuid4(),
            digest_id=digest_id,
            story_id=story_ml.id,
            position=1,
            headline_override=story_ml.headline,
            ranking_score_snapshot=0.9,
            story=story_ml,
        )
        digest = Digest(
            id=digest_id,
            title="Daily Digest",
            story_count=1,
            digest_stories=[ds],
        )
        sub_quantum = Subscriber(
            id=uuid.uuid4(),
            email="quantum@example.com",
            is_active=True,
            preferred_channel="EMAIL",
            topics=["Quantum Computing"],
            unsubscribe_token="token_q",
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[sub_quantum])
        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock()
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 1
        assert res.sent_count == 0
        assert res.skipped_count == 1
        mock_provider.send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_topics_receives_complete_digest(self):
        """Subscriber with empty topics list receives all stories without filtering."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        digest = Digest(
            id=digest_id,
            title="Daily Digest",
            html_content="<p>Full General Digest</p>",
            plain_text_content="Full General Digest",
            story_count=3,
            digest_stories=[],
        )
        sub_general = Subscriber(
            id=uuid.uuid4(),
            email="general@example.com",
            is_active=True,
            preferred_channel="EMAIL",
            topics=[],
            unsubscribe_token="token_gen",
        )
        claim = DeliveryRecord(
            id=uuid.uuid4(),
            digest_id=digest_id,
            subscriber_id=sub_general.id,
            status="PENDING",
            metadata_json={"claim_token": "token-gen"},
        )

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[sub_general])
        delivery_service.digest_repo.claim_delivery = AsyncMock(return_value=claim)
        delivery_service.digest_repo.complete_delivery = AsyncMock(return_value=True)

        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock(return_value=MagicMock(success=True, provider_message_id="msg_gen"))
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 1
        assert res.sent_count == 1
        mock_provider.send_email.assert_called_once()
        sent_html = mock_provider.send_email.call_args.kwargs["html_content"]
        assert "Full General Digest" in sent_html


class TestEmptyDigestDeliveryGuard:
    """Test protection against delivering blank newsletter issues."""

    @pytest.mark.asyncio
    async def test_digest_story_count_zero_skips_all_subscribers(self):
        """When digest issue has 0 stories, send_digest immediately returns 0 sent and skips all subscribers."""
        mock_session = AsyncMock()
        delivery_service = DeliveryService(session=mock_session)

        digest_id = uuid.uuid4()
        digest = Digest(
            id=digest_id,
            title="Empty Digest",
            html_content="<p>Empty</p>",
            plain_text_content="Empty",
            story_count=0,
            digest_stories=[],
        )
        sub1 = Subscriber(id=uuid.uuid4(), email="sub1@example.com", is_active=True, unsubscribe_token="t1")
        sub2 = Subscriber(id=uuid.uuid4(), email="sub2@example.com", is_active=True, unsubscribe_token="t2")

        delivery_service.digest_repo.get_with_stories = AsyncMock(return_value=digest)
        delivery_service.subscriber_repo.list_active = AsyncMock(return_value=[sub1, sub2])
        mock_provider = MagicMock()
        mock_provider.send_email = AsyncMock()
        delivery_service.email_provider = mock_provider

        res = await delivery_service.send_digest(digest_id=digest_id)

        assert res.total_subscribers == 2
        assert res.sent_count == 0
        assert res.skipped_count == 2
        mock_provider.send_email.assert_not_called()
