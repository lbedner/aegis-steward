"""
HTTP-surface tests for ``/api/v1/metrics/summary`` and ``/endpoints``.

Seeds ``metrics_service`` directly (bypassing the middleware) so the
test doesn't depend on which routes happen to exist in this stack —
all we care about here is that the API serializes the service.
"""

from fastapi import status
from fastapi.testclient import TestClient
import pytest

from app.components.backend.middleware.performance import metrics_service
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset_metrics_service():
    metrics_service.reset()
    yield
    metrics_service.reset()


@pytest.fixture(autouse=True)
def _enable_auth():
    """Auth on — these tests cover the auth-gated branch."""
    prior = settings.AUTH_ENABLED
    settings.AUTH_ENABLED = True
    try:
        yield
    finally:
        settings.AUTH_ENABLED = prior


class TestMetricsEndpointsAuthGated:
    def test_summary_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/summary")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_endpoints_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/endpoints")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_summary_returns_shape(self, authenticated_client: TestClient) -> None:
        metrics_service.record("GET", "/users/{id}", 100.0)
        metrics_service.record("GET", "/users/{id}", 200.0)
        metrics_service.record("POST", "/users", 50.0)

        response = authenticated_client.get("/api/v1/metrics/summary")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["total_requests"] == 3
        assert body["tracked_endpoints"] == 2
        assert body["avg_ms"] > 0
        # Slowest endpoint by avg is ``GET /users/{id}`` (150ms vs 50ms).
        assert body["slowest_endpoint"]["endpoint"] == "GET /users/{id}"

    def test_endpoints_returns_per_route_detail(
        self, authenticated_client: TestClient
    ) -> None:
        metrics_service.record("GET", "/users/{id}", 100.0)
        metrics_service.record("GET", "/users/{id}", 200.0)

        response = authenticated_client.get("/api/v1/metrics/endpoints")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "GET /users/{id}" in body
        bucket = body["GET /users/{id}"]
        assert bucket["count"] == 2
        assert bucket["avg_ms"] == 150.0
        assert bucket["min_ms"] == 100.0
        assert bucket["max_ms"] == 200.0
