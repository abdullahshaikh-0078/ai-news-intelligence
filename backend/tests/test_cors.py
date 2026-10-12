from typing import Generator
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.core.config import Settings, settings
from app.infrastructure.database.session import get_db


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Test client fixture configured with database dependency override."""
    async def override_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_cors_preflight_production_origin(client: TestClient):
    """Test preflight OPTIONS request to /api/v1/newsletter/subscribe from production origin."""
    origin = "https://abdullah-shaikh-portfolio.vercel.app"
    response = client.options(
        "/api/v1/newsletter/subscribe",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
    allow_methods = response.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods or "*" in allow_methods
    allow_headers = response.headers.get("access-control-allow-headers", "")
    assert "content-type" in allow_headers.lower() or "*" in allow_headers


def test_cors_preflight_production_alias_origin(client: TestClient):
    """Test preflight OPTIONS request to /api/v1/newsletter/subscribe from production alias."""
    origin = "https://abdullah-ai-systems-portfolio.vercel.app"
    response = client.options(
        "/api/v1/newsletter/subscribe",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
    allow_methods = response.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods or "*" in allow_methods


def test_cors_preflight_disallowed_origin(client: TestClient):
    """Test preflight OPTIONS request from an unauthorized origin is rejected without allow-origin."""
    disallowed_origin = "https://unauthorized-domain.com"
    response = client.options(
        "/api/v1/newsletter/subscribe",
        headers={
            "Origin": disallowed_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_post_validation_error_preserves_cors_headers(client: TestClient):
    """Test that a POST request that fails schema validation still returns CORS headers."""
    origin = "https://abdullah-shaikh-portfolio.vercel.app"
    # Sending invalid payload (missing required email field)
    response = client.post(
        "/api/v1/newsletter/subscribe",
        json={"invalid_field": 123},
        headers={"Origin": origin},
    )
    assert response.status_code == 422
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_post_successful_subscribe(client: TestClient):
    """Test successful POST /api/v1/newsletter/subscribe from production origin returns CORS headers."""
    origin = "https://abdullah-shaikh-portfolio.vercel.app"
    mock_subscriber = MagicMock()
    mock_subscriber.email = "test@example.com"
    mock_subscriber.is_active = True

    with patch("app.services.subscriber_service.SubscriberService.subscribe", new_callable=AsyncMock) as mock_sub:
        mock_sub.return_value = (mock_subscriber, True)
        response = client.post(
            "/api/v1/newsletter/subscribe",
            json={"email": "test@example.com", "name": "Test User"},
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin
        assert response.headers.get("access-control-allow-credentials") == "true"
        data = response.json()
        assert data["status"] == "success"
        assert data["email"] == "test@example.com"


def test_cors_post_disallowed_origin_omits_allow_origin(client: TestClient):
    """Test POST request from disallowed origin does not include access-control-allow-origin."""
    disallowed_origin = "https://unauthorized-domain.com"
    response = client.post(
        "/api/v1/newsletter/subscribe",
        json={"invalid_field": 123},
        headers={"Origin": disallowed_origin},
    )
    assert response.status_code == 422
    assert "access-control-allow-origin" not in response.headers


def test_assemble_cors_origins_validator():
    """Verify Settings.assemble_cors_origins parses formats and guarantees canonical origins."""
    # From JSON string with trailing slashes
    res1 = Settings.assemble_cors_origins('["http://localhost:3000/", "https://custom.app/"]')
    assert "http://localhost:3000" in res1
    assert "https://custom.app" in res1
    assert "https://abdullah-shaikh-portfolio.vercel.app" in res1
    assert "https://abdullah-ai-systems-portfolio.vercel.app" in res1

    # From comma-separated string
    res2 = Settings.assemble_cors_origins("http://127.0.0.1:8080, https://another.dev")
    assert "http://127.0.0.1:8080" in res2
    assert "https://another.dev" in res2
    assert "https://abdullah-shaikh-portfolio.vercel.app" in res2

    # From list
    res3 = Settings.assemble_cors_origins(["http://localhost:5173/"])
    assert "http://localhost:5173" in res3
    assert "https://abdullah-shaikh-portfolio.vercel.app" in res3
    assert "https://abdullah-ai-systems-portfolio.vercel.app" in res3


def test_active_settings_contain_production_origins():
    """Ensure the active loaded settings object includes the production origins."""
    assert "https://abdullah-shaikh-portfolio.vercel.app" in settings.CORS_ORIGINS
    assert "https://abdullah-ai-systems-portfolio.vercel.app" in settings.CORS_ORIGINS
