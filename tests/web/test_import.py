"""File import (pattern 5): preview, then a background job followed over
SSE, with the job's frames re-emitted as rendered HTML."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.web.conftest import Ledger, await_job
from tests.web.dom import none, one, select, text

FIXTURES = Path(__file__).parent.parent / "services" / "finance" / "fixtures"
QIF = (
    "sample_quicken.qif",
    (FIXTURES / "sample_quicken.qif").read_bytes(),
    "text/plain",
)


@pytest.fixture
def job_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import job opens its own session; point it at the test's."""
    from app.components.backend.api.finance import imports as api_imports

    @asynccontextmanager
    async def _session():  # noqa: ANN202
        yield async_db_session

    monkeypatch.setattr(api_imports, "_job_session", _session)


class TestDialog:
    def test_button_and_form(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        button = one(page, f'[hx-get="/accounts/import?account_id={ledger.checking}"]')
        assert button.get("hx-target") == "#dialog-body"

        dialog = hx.get(f"/accounts/import?account_id={ledger.checking}").text
        form = one(dialog, "form#import-form")
        assert form.get("hx-encoding") == "multipart/form-data"
        one(form, 'input[type="file"][name="file"]')
        assert one(form, 'select[name="account_id"] option[selected]').get(
            "value"
        ) == str(ledger.checking)
        preview = one(form, 'button[hx-post="/accounts/import/preview"]')
        assert preview.get("hx-target") == "#import-result"
        run = one(form, 'button[hx-post="/accounts/import"]')
        assert run.get("hx-target") == "#import-result"
        one(dialog, "#import-result")


class TestPreview:
    def test_shows_what_a_commit_would_do(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        response = client.post(
            "/accounts/import/preview",
            files={"file": QIF},
            data={"account_id": str(ledger.checking), "lane": "statement"},
        )
        assert response.status_code == 200
        summary = one(response.text, "#import-preview")
        assert "8" in text(one(summary, '[data-count="rows_inserted"]'))
        none(
            response.text, "form"
        )  # the persistent form stays; nothing re-rendered here

    def test_a_qif_without_an_account_asks_for_one(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        response = client.post(
            "/accounts/import/preview",
            files={"file": QIF},
            data={"account_id": "", "lane": "statement"},
        )
        assert response.status_code == 422
        assert "account" in text(one(response.text, '[role="alert"]')).lower()

    def test_unsupported_file_is_a_422(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        response = client.post(
            "/accounts/import/preview",
            files={"file": ("statement.pdf", b"%PDF-1.4", "application/pdf")},
            data={"account_id": str(ledger.checking), "lane": "statement"},
        )
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


def frames_of(client: TestClient, url: str) -> list[str]:
    """Every SSE frame's data (joined lines) from a finished job's stream."""
    frames: list[str] = []
    payload: list[str] = []
    with client.stream("GET", url) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        for line in stream.iter_lines():
            if line.startswith("data:"):
                payload.append(line[5:].strip())
            elif line == "" and payload:
                frames.append("\n".join(payload))
                payload = []
    return frames


class TestImportJob:
    def test_runs_as_a_job_and_the_stream_renders_the_summary(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        response = live_client.post(
            "/accounts/import",
            files={"file": QIF},
            data={"account_id": str(ledger.checking), "lane": "statement"},
        )
        assert response.status_code == 200
        follower = one(response.text, "[sse-connect]")
        assert follower.get("hx-ext") == "sse" and follower.get("sse-swap") == "status"
        stream_url = follower.get("sse-connect") or ""
        job_id = stream_url.removeprefix("/jobs/").removesuffix("/events")

        assert await_job(live_client, job_id)["status"] == "done"
        final = frames_of(live_client, stream_url)[-1]
        assert "8" in text(one(final, '[data-count="rows_inserted"]'))
        assert (
            one(final, 'button[hx-get="/accounts"]').get("hx-target") == "#app-content"
        )
        rows = select(
            live_client.get(f"/accounts/{ledger.checking}").text, "#register tbody tr"
        )
        assert len(rows) == 11

    def test_failure_lands_in_the_stream(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        response = live_client.post(
            "/accounts/import",
            files={"file": QIF},
            data={"account_id": "", "lane": "statement"},
        )
        stream_url = one(response.text, "[sse-connect]").get("sse-connect") or ""
        job_id = stream_url.removeprefix("/jobs/").removesuffix("/events")
        assert await_job(live_client, job_id)["status"] == "failed"
        final = frames_of(live_client, stream_url)[-1]
        assert "account" in text(one(final, '[role="alert"]')).lower()

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/events").status_code == 404


class TestJobFrames:
    """The frame renderer on its own: what each job state looks like."""

    def test_running_shows_the_label(self) -> None:
        from app.components.web_frontend.routes.jobs import render_snapshot, sse_frame

        html = render_snapshot(
            None, {"status": "running", "label": "Importing x...", "name": "j"}
        )  # type: ignore[arg-type]
        assert "Importing x..." in text(one(html, "p"))
        frame = sse_frame("status", html)
        assert frame.startswith("event: status\ndata: ") and frame.endswith("\n\n")
