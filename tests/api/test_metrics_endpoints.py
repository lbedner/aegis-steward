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


@pytest.fixture(autouse=True)
def _reset_metrics_service():
    metrics_service.reset()
    yield
    metrics_service.reset()


class TestMetricsEndpointsOpen:
    """Auth service not enabled — endpoints are open, like ``/health/``."""

    def test_summary_returns_shape(self, client: TestClient) -> None:
        metrics_service.record("GET", "/users/{id}", 100.0)
        metrics_service.record("GET", "/users/{id}", 200.0)
        metrics_service.record("POST", "/users", 50.0)

        response = client.get("/api/v1/metrics/summary")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["total_requests"] == 3
        assert body["tracked_endpoints"] == 2
        assert body["slowest_endpoint"]["endpoint"] == "GET /users/{id}"

    def test_endpoints_returns_per_route_detail(self, client: TestClient) -> None:
        metrics_service.record("GET", "/users/{id}", 100.0)
        metrics_service.record("GET", "/users/{id}", 200.0)

        response = client.get("/api/v1/metrics/endpoints")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "GET /users/{id}" in body
        bucket = body["GET /users/{id}"]
        assert bucket["count"] == 2
        assert bucket["avg_ms"] == 150.0
