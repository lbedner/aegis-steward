"""Smoke tests for the Traffic tab in the Backend modal.

The tab paints a loading shell in ``__init__`` and only renders after
``_load`` fetches ``/api/v1/traffic/sources``. To avoid needing a Flet page +
session + running backend, we drive ``_render`` directly: it's a pure
transform from the snapshot dict to the body's child tree.
"""

import flet as ft

from app.components.frontend.dashboard.modals.backend_modal import TrafficTab

_SNAPSHOT = {
    "backend": "memory",
    "window_hours": 24,
    "total_requests": 1000,
    "sources": [
        {"ip": "9.9.9.9", "requests": 800, "share": 0.8},
        {"ip": "1.1.1.1", "requests": 200, "share": 0.2},
    ],
    "dominant": {"ip": "9.9.9.9", "requests": 800, "share": 0.8},
}


def test_initial_content_is_loading_shell() -> None:
    tab = TrafficTab()
    # Before _load runs, the body shows a progress ring (loading shell).
    assert isinstance(tab._body.content, ft.Row)


def test_render_populated_snapshot_builds_column() -> None:
    tab = TrafficTab()
    tab._render(_SNAPSHOT)
    assert isinstance(tab._body.content, ft.Column)


def test_render_empty_snapshot_shows_empty_state() -> None:
    tab = TrafficTab()
    tab._render({})
    assert tab._body.content is not None
    assert tab._body.alignment == ft.alignment.center


def test_render_tolerates_partial_snapshot() -> None:
    # Missing budget/dominant/backend keys must not raise (all .get-guarded).
    tab = TrafficTab()
    tab._render(
        {
            "total_requests": 5,
            "sources": [{"ip": "2.2.2.2", "requests": 5, "share": 1.0}],
        }
    )
    assert tab._body.content is not None
