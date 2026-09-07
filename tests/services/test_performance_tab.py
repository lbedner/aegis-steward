"""
Smoke tests for the Performance tab in the Backend modal.

The tab paints a loading shell in ``__init__`` and only renders the
summary cards / table after ``_load`` fetches fresh metrics from
``/api/v1/metrics/*``. To avoid needing a Flet page + session +
running backend, we drive ``_render`` directly: it's a pure
transform from (summary, endpoints) dicts to the body's child tree.
"""

import flet as ft

from app.components.frontend.dashboard.modals.backend_modal import (
    PerformanceTab,
)
from app.services.system.models import ComponentStatus, ComponentStatusType


def _make_component(metadata: dict) -> ComponentStatus:
    return ComponentStatus(
        name="backend",
        status=ComponentStatusType.HEALTHY,
        message="ok",
        response_time_ms=None,
        metadata=metadata,
    )


class TestPerformanceTabConstruction:
    def test_initial_content_is_loading_shell(self) -> None:
        """Before _load runs, the tab shows a centered ProgressRing."""
        tab = PerformanceTab(_make_component({}))
        # Outer is the tab itself; inner _body is what we replace.
        assert isinstance(tab.content, ft.Container)

    def test_initial_seed_from_metadata(self) -> None:
        """Snapshot from health-poll metadata is stashed for fallback."""
        tab = PerformanceTab(
            _make_component(
                {
                    "performance": {"total_requests": 7},
                    "performance_endpoints": {"GET /x": {"count": 7}},
                }
            )
        )
        assert tab._initial_summary == {"total_requests": 7}
        assert tab._initial_endpoints == {"GET /x": {"count": 7}}


class TestPerformanceTabRenderEmpty:
    def test_zero_requests_renders_empty_message(self) -> None:
        tab = PerformanceTab(_make_component({}))
        tab._render(
            summary={
                "total_requests": 0,
                "tracked_endpoints": 0,
                "avg_ms": 0.0,
                "p95_ms": 0.0,
                "slowest_endpoint": None,
            },
            endpoints={},
        )
        # Empty-state body is a Column (H3 + secondary text), not a tab layout.
        assert isinstance(tab._body.content, ft.Column)

    def test_no_endpoints_renders_empty_message(self) -> None:
        tab = PerformanceTab(_make_component({}))
        tab._render(summary={"total_requests": 50}, endpoints={})
        assert isinstance(tab._body.content, ft.Column)


class TestPerformanceTabRenderPopulated:
    def test_renders_with_data(self) -> None:
        tab = PerformanceTab(_make_component({}))
        tab._render(
            summary={
                "total_requests": 50,
                "tracked_endpoints": 2,
                "avg_ms": 25.0,
                "p95_ms": 80.0,
                "slowest_endpoint": {
                    "endpoint": "GET /users/{id}",
                    "avg_ms": 38.5,
                },
            },
            endpoints={
                "GET /users/{id}": {
                    "count": 30,
                    "avg_ms": 38.5,
                    "min_ms": 5.0,
                    "max_ms": 120.0,
                    "median_ms": 35.0,
                    "p95_ms": 95.0,
                    "p99_ms": 110.0,
                    "last_request_at": None,
                },
                "GET /health/": {
                    "count": 20,
                    "avg_ms": 5.5,
                    "min_ms": 2.0,
                    "max_ms": 12.0,
                    "median_ms": 5.0,
                    "p95_ms": 10.0,
                    "p99_ms": 11.5,
                    "last_request_at": None,
                },
            },
        )

        # Populated body is a Column with summary row + slowest + spacer +
        # H3 + table container.
        assert isinstance(tab._body.content, ft.Column)
        assert len(tab._body.content.controls) >= 4
