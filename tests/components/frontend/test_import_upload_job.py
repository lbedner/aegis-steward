"""Every import endpoint answers with a job id at once (#494); the
dashboard posts the file, follows the job, and acts on how it lands."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.components.frontend.controls import jobs
from app.components.frontend.controls.jobs import JobOutcome
from app.components.frontend.controls.loading_overlay import LoadingOverlay
from app.components.frontend.dashboard.modals.finance_modal.transactions_panel.imports_target import (
    ImportTargetMixin,
)
from app.services.finance.domains.imports_job import NEEDS_ACCOUNT


class _Page:
    def __init__(self, api: Any) -> None:
        self.data: dict[str, Any] = {"session_state": SimpleNamespace(api_client=api)}
        self.overlay: list[Any] = []

    def update(self, *controls: Any) -> None:
        pass


class _Api:
    def __init__(self) -> None:
        self.posted: list[tuple[str, dict[str, object]]] = []
        self.last_error: str | None = None

    async def post_multipart(
        self, path: str, *, files: Any, params: dict[str, object]
    ) -> dict[str, Any]:
        self.posted.append((path, params))
        return {"job_id": "j1"}


def _landing(monkeypatch: pytest.MonkeyPatch, outcome: JobOutcome) -> list[str]:
    followed: list[str] = []

    async def follow(api: Any, job_id: str, *, on_label: Any) -> JobOutcome:
        followed.append(job_id)
        return outcome

    monkeypatch.setattr(jobs, "follow_job", follow)
    return followed


def _panel() -> tuple[Any, _Api]:
    api = _Api()
    return SimpleNamespace(page=_Page(api)), api


async def test_the_job_result_is_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    followed = _landing(monkeypatch, JobOutcome(result={"rows_total": 3}))
    panel, api = _panel()

    result = await ImportTargetMixin._upload_job(
        panel, "/api/v1/finance/import/preview", b"x", "a.csv", {"account_id": 7}
    )

    assert result == {"rows_total": 3}
    assert api.posted == [("/api/v1/finance/import/preview", {"account_id": 7})]
    assert followed == ["j1"]
    assert LoadingOverlay.of(panel.page).visible is False


async def test_a_file_naming_no_account_asks_for_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _landing(monkeypatch, JobOutcome(error=NEEDS_ACCOUNT))
    panel, _ = _panel()
    asked: list[bool] = []

    async def ask() -> None:
        asked.append(True)

    result = await ImportTargetMixin._upload_job(
        panel, "/api/v1/finance/import/preview", b"x", "a.csv", needs_account=ask
    )

    assert result is None and asked == [True]


async def test_any_other_failure_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    _landing(monkeypatch, JobOutcome(error="Unknown CSV layout"))
    panel, _ = _panel()
    asked: list[bool] = []

    async def ask() -> None:
        asked.append(True)

    result = await ImportTargetMixin._upload_job(
        panel, "/api/v1/finance/import/preview", b"x", "a.csv", needs_account=ask
    )

    assert result is None and asked == []
    overlay = LoadingOverlay.of(panel.page)
    message = overlay.content.content.controls[1].content.controls[0]
    assert overlay.visible is True and message.value == "Unknown CSV layout"
