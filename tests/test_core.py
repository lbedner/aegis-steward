# tests/test_core.py
"""
Core functionality tests.

These basic tests ensure the fundamental application components work correctly.
"""

from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest

from app.integrations.main import create_integrated_app


@pytest.fixture
def client() -> Generator[TestClient]:
    """Create a test client for the FastAPI app."""
    app = create_integrated_app()
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient) -> None:
    """Test that the health endpoint works."""
    response = client.get("/health/")
    # Should return 200 for healthy or 503 for unhealthy, both are valid responses
    assert response.status_code in [200, 503]

    data = response.json()
    # Check the enhanced health endpoint format
    assert isinstance(data["healthy"], bool)
    assert "status" in data
    assert "components" in data
    assert "timestamp" in data


def test_flet_app_mount(client: TestClient) -> None:
    """Test that the Flet frontend is mounted at /dashboard."""
    response = client.get("/dashboard", follow_redirects=False)
    # Should serve the Flet app (might be redirect or direct serve)
    assert response.status_code in [200, 301, 302, 307, 308]


@pytest.mark.asyncio
async def test_app_creation() -> None:
    """Test that the app can be created without errors."""
    app = create_integrated_app()
    assert app is not None
    assert hasattr(app, "router")
