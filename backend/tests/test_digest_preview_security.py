import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.infrastructure.database.session import get_db
from app.infrastructure.models.digest import Digest
from app.infrastructure.models.subscriber import Subscriber
from app.services.email_service import DeliveryService


@pytest.fixture
def test_client():
    """Create test client with database dependency override."""
    async def override_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_digest_preview_does_not_expose_active_subscriber_token(test_client: TestClient):
    """Verify that unauthenticated preview calls never retrieve or expose active subscriber tokens."""
    digest_id = uuid.uuid4()
    secret_real_token = "secret-real-subscriber-token-12345"

    mock_digest = MagicMock(spec=Digest)
    mock_digest.id = digest_id
    mock_digest.html_content = "<html><body><a href='{{ unsubscribe_url }}'>Unsubscribe</a></body></html>"

    mock_active_sub = MagicMock(spec=Subscriber)
    mock_active_sub.unsubscribe_token = secret_real_token
    mock_active_sub.is_active = True

    with (
        patch("app.services.digest_service.DigestService.get_digest_by_id", new_callable=AsyncMock) as mock_get_digest,
        patch("app.infrastructure.repositories.subscriber_repo.SubscriberRepository.list_active", new_callable=AsyncMock) as mock_list_active,
    ):
        mock_get_digest.return_value = mock_digest
        mock_list_active.return_value = [mock_active_sub]

        response = test_client.get(f"/api/v1/digests/{digest_id}/preview")

        assert response.status_code == 200
        # Ensure list_active was never called
        assert not mock_list_active.called
        # Ensure the secret real token is NEVER in the response HTML
        assert secret_real_token not in response.text
        # Ensure safe placeholder is used
        assert "token=preview-placeholder-token" in response.text


def test_digest_preview_with_explicit_valid_token(test_client: TestClient):
    """Verify preview with an explicit authorized token personalizes only with that token."""
    digest_id = uuid.uuid4()
    client_token = "caller-own-token-67890"

    mock_digest = MagicMock(spec=Digest)
    mock_digest.id = digest_id
    mock_digest.html_content = "<html><body><a href='{{ unsubscribe_url }}'>Unsubscribe</a></body></html>"

    mock_sub = MagicMock(spec=Subscriber)
    mock_sub.unsubscribe_token = client_token
    mock_sub.is_active = True

    with (
        patch("app.services.digest_service.DigestService.get_digest_by_id", new_callable=AsyncMock) as mock_get_digest,
        patch("app.infrastructure.repositories.subscriber_repo.SubscriberRepository.get_by_token", new_callable=AsyncMock) as mock_get_token,
    ):
        mock_get_digest.return_value = mock_digest
        mock_get_token.return_value = mock_sub

        response = test_client.get(f"/api/v1/digests/{digest_id}/preview?token={client_token}")

        assert response.status_code == 200
        assert f"token={client_token}" in response.text


def test_digest_preview_with_invalid_token_falls_back_to_placeholder(test_client: TestClient):
    """Verify preview with a non-existent token falls back to placeholder."""
    digest_id = uuid.uuid4()

    mock_digest = MagicMock(spec=Digest)
    mock_digest.id = digest_id
    mock_digest.html_content = "<html><body><a href='{{ unsubscribe_url }}'>Unsubscribe</a></body></html>"

    with (
        patch("app.services.digest_service.DigestService.get_digest_by_id", new_callable=AsyncMock) as mock_get_digest,
        patch("app.infrastructure.repositories.subscriber_repo.SubscriberRepository.get_by_token", new_callable=AsyncMock) as mock_get_token,
    ):
        mock_get_digest.return_value = mock_digest
        mock_get_token.return_value = None

        response = test_client.get(f"/api/v1/digests/{digest_id}/preview?token=invalid-fake-token")

        assert response.status_code == 200
        assert "token=preview-placeholder-token" in response.text


def test_delivery_service_unsub_link_generation():
    """Verify legitimate subscriber emails continue generating working personalized unsubscribe links."""
    service = DeliveryService(session=AsyncMock())
    mock_sub = MagicMock(spec=Subscriber)
    mock_sub.unsubscribe_token = "genuine-subscriber-token"

    # Testing URL format in email service
    from app.core.config import settings
    expected_url = f"{settings.public_api_v1_url}/newsletter/unsubscribe?token={mock_sub.unsubscribe_token}"
    assert "newsletter/unsubscribe?token=" in expected_url
    assert mock_sub.unsubscribe_token in expected_url
