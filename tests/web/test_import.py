"""File import (pattern 5): preview, then a background job followed over
SSE, with the job's frames re-emitted as rendered HTML."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.web.conftest import Ledger, await_job
from tests.web.dom import none, one, select, stat, text

FIXTURES = Path(__file__).parent.parent / "services" / "finance" / "fixtures"
QIF = (
    "sample_quicken.qif",
    (FIXTURES / "sample_quicken.qif").read_bytes(),
    "text/plain",
)
# A multi-account report: two accounts neither of which the ledger has, so
# the review has a ranking, named new accounts and new categories to show.
CSV = (
    "sample_quicken_all.csv",
    (FIXTURES / "sample_quicken_all.csv").read_bytes(),
    "text/csv",
)


def review(client: TestClient, upload: tuple, **data: str) -> str:
    """The review panel Import opens for ``upload``."""
    response = client.post(
        "/accounts/import/preview",
        files={"file": upload},
        data={"lane": "statement", **data},
    )
    assert response.status_code == 200
    return response.text


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
        # One button: Import opens the review, and the review commits.
        # A file is never written straight from this form.
        none(form, 'button[hx-post="/accounts/import"]')
        run = one(form, 'button[hx-post="/accounts/import/preview"]')
        assert run.get("hx-target") == "#import-result"
        assert text(run) == "Import"
        one(dialog, "#import-result")

    def test_the_chooser_steps_aside_once_a_review_is_up(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        """Reported live 2026-09-11: while the review was on screen the
        file picker sat above it, so another file could be chosen against
        a review of the last one.

        Hidden, never removed: the review's own Import button re-sends the
        very bytes it reviewed via ``hx-include="#import-form"``, and the
        batch dedup ties the two requests together by file hash. Take the
        form out of the DOM and the commit has nothing to post.
        """
        dialog = hx.get(f"/accounts/import?account_id={ledger.checking}").text
        scope = one(dialog, "#import-dialog")
        assert "busy" in (scope.get("x-data") or "")

        chooser = one(dialog, "#import-chooser")
        assert chooser.get("x-show") == "!busy"
        # The file input is inside what gets hidden - that is the point.
        one(chooser, 'input[type="file"][name="file"]')

        result = one(dialog, "#import-result")
        watched = result.get("@htmx:after-swap") or ""
        assert "firstElementChild" in watched, (
            "reported live 2026-09-12: the chooser watched for the review "
            "alone, so it came back over a running import and again over "
            "the summary - a live file picker above the import just done"
        )
        assert "data-import-retry" in watched, (
            "a rejected file is the one case where picking another one is "
            "the next step"
        )

    def test_only_a_rejected_file_puts_the_chooser_back(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """A running job, and the summary after it, each carry their own
        next step; only a refused file wants the picker again."""
        from app.components.web_frontend.rendering import templates

        def render(name: str, **ctx: object) -> str:
            return templates.get_template(name).render(**ctx)

        running = render(
            "partials/jobs/status.html",
            job={"status": "running", "label": "Importing x.csv", "name": "import"},
        )
        done = render(
            "partials/jobs/status.html",
            job={"status": "completed", "label": "", "name": "import", "result": {}},
        )
        refused = render("partials/imports/error.html", errors=["Not a statement."])

        assert "data-import-retry" not in running
        assert "data-import-retry" not in done
        assert "data-import-retry" in refused

    def test_the_review_still_posts_the_form_it_hid(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The guard on the fix: hiding must not become removing."""
        panel = review(client, QIF, account_id=str(ledger.checking))
        run = one(panel, 'button[hx-post="/accounts/import"]')
        assert run.get("hx-include") == "#import-form"


class TestReview:
    """What Import opens: the same five counts, the same words, and the
    same named detail the Flet review dialog showed."""

    def test_names_the_file_and_counts_what_would_happen(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        panel = review(client, QIF, account_id=str(ledger.checking))
        assert "sample_quicken.qif" in text(one(panel, "#import-preview"))
        assert stat(panel, "To add") == "8"
        assert stat(panel, "To update") == "0"
        assert stat(panel, "Already have") == "0"
        # The two outcomes that do NOT reach the ledger stay dots, so a
        # skipped row can never be read as an incoming one.
        dots = [text(d) for d in select(panel, "#import-dots [data-tone]")]
        assert "0 scheduled" in dots and "0 errors" in dots
        assert "Nothing has been written yet." in text(one(panel, "#import-preview"))

    def test_the_confirm_button_commits_the_reviewed_file(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        panel = review(client, QIF, account_id=str(ledger.checking))
        run = one(panel, 'button[hx-post="/accounts/import"]')
        assert run.get("hx-target") == "#import-result"
        assert run.get("hx-include") == "#import-form"
        assert text(run) == "Import 8 changes"  # inserted + updated
        # This button lives OUTSIDE the form, so it inherits nothing from
        # it: without its own encoding htmx sends the file urlencoded and
        # the API sees the string "[object File]".
        assert run.get("hx-encoding") == "multipart/form-data"

    def test_names_what_a_commit_would_mint(
        self, client: TestClient, ledger: Ledger, csv_profiles: None
    ) -> None:
        """An import that quietly invents an account is the surprise this
        panel exists to head off, so each one is named, not counted."""
        panel = review(client, CSV)
        creates = text(one(panel, "#import-creates"))
        assert "CHECKING" in creates and "AMEX CARD" in creates
        assert "Groceries" in creates

    def test_ranks_where_the_new_rows_land(
        self, client: TestClient, ledger: Ledger, csv_profiles: None
    ) -> None:
        rows = select(review(client, CSV), "#import-lands .ranked > li")
        # Biggest first, and an account that does not exist yet says so.
        assert [text(one(row, "span.truncate")) for row in rows] == [
            "AMEX CARD (new)",
            "CHECKING (new)",
        ]
        assert [text(one(row, "span.font-medium")) for row in rows] == ["3", "2"]

    def test_one_account_is_not_a_ranking(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """One bar is not a ranking, it is "To add" restated."""
        none(review(client, QIF, account_id=str(ledger.checking)), "#import-lands")

    def test_an_already_imported_file_offers_no_button(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        """The dead end keeps the family's words and loses the chrome that
        means "this reaches your ledger"."""
        started = live_client.post(
            "/accounts/import",
            files={"file": QIF},
            data={"account_id": str(ledger.checking), "lane": "statement"},
        )
        stream = one(started.text, "[sse-connect]").get("sse-connect") or ""
        job = stream.removeprefix("/jobs/").removesuffix("/events")
        assert await_job(live_client, job)["status"] == "done"
        panel = review(live_client, QIF, account_id=str(ledger.checking))
        none(panel, 'button[hx-post="/accounts/import"]')
        none(panel, "#import-counts")
        assert "already" in text(one(panel, "#import-preview")).lower()

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
        assert stat(final, "Added") == "8"  # the review's layout, past tense
        assert (
            one(final, 'button[hx-get="/accounts"]').get("hx-target") == "#app-content"
        )
        rows = select(
            live_client.get(f"/accounts/{ledger.checking}").text, "#register tbody tr"
        )
        assert len(rows) == 11

    def test_a_failed_job_shows_its_error(self) -> None:
        from app.components.web_frontend.routes.jobs import render_snapshot

        html = render_snapshot(
            None, {"status": "failed", "error": "boom", "label": "x", "name": "j"}
        )  # type: ignore[arg-type]
        assert "boom" in text(one(html, '[role="alert"]'))

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
