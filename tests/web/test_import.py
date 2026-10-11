"""File import (pattern 5): preview, then a background job followed over
SSE, with the job's frames re-emitted as rendered HTML."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.web.conftest import HX, Ledger, follow
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
OPTUM = (
    "optum_hsa_sample.tsv",
    (FIXTURES / "optum_hsa_sample.tsv").read_bytes(),
    "text/tab-separated-values",
)


def review(client: TestClient, upload: tuple, **data: str) -> str:
    """The review panel Import opens for ``upload``: the request answers
    with a follower, and the review is the job's last frame."""
    data = {"lane": "statement", **data}
    response = client.post(
        "/accounts/import/preview", files={"file": upload}, data=data
    )
    assert response.status_code == 200
    return follow(client, response.text)[1]


@pytest.fixture
def job_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import job opens its own session; point it at the test's."""
    from app.services.finance.domains import imports_job

    @asynccontextmanager
    async def _session():  # noqa: ANN202
        yield async_db_session

    monkeypatch.setattr(imports_job, "get_async_session", _session)


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
            "a rejected file is the one case where picking another one is the next step"
        )

    def test_the_dialog_shrinks_to_what_it_is_holding(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        """Reported live 2026-09-15: a run in flight is one line of text
        in a shell sized for a four-field form, so the file being
        imported floated in a box six times its height.

        Bound rather than set, because the chooser comes back when a file
        is rejected and it needs the room again.
        """
        dialog = hx.get(f"/accounts/import?account_id={ledger.checking}").text
        scope = one(dialog, "#import-dialog")

        narrow = scope.get(":data-narrow") or ""
        assert "busy" in narrow, "the dialog only narrows while a run is in flight"
        assert "null" in narrow, "and widens again when the chooser returns"
        # The hook the dialog shell actually reads.
        assert scope.get("data-narrow") is None

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
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        """The guard on the fix: hiding must not become removing."""
        panel = review(live_client, QIF, account_id=str(ledger.checking))
        run = one(panel, 'button[hx-post="/accounts/import"]')
        assert run.get("hx-include") == "#import-form"


class TestReview:
    """What Import opens: the same five counts, the same words, and the
    same named detail the Flet review dialog showed."""

    def test_names_the_file_and_counts_what_would_happen(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        panel = review(live_client, QIF, account_id=str(ledger.checking))
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
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        panel = review(live_client, QIF, account_id=str(ledger.checking))
        run = one(panel, 'button[hx-post="/accounts/import"]')
        assert run.get("hx-target") == "#import-result"
        assert run.get("hx-include") == "#import-form"
        assert text(run) == "Import 8 changes"  # inserted + updated
        # This button lives OUTSIDE the form, so it inherits nothing from
        # it: without its own encoding htmx sends the file urlencoded and
        # the API sees the string "[object File]".
        assert run.get("hx-encoding") == "multipart/form-data"

    def test_names_what_a_commit_would_mint(
        self,
        live_client: TestClient,
        ledger: Ledger,
        csv_profiles: None,
        job_session: None,
    ) -> None:
        """An import that quietly invents an account is the surprise this
        panel exists to head off, so each one is named, not counted."""
        panel = review(live_client, CSV)
        creates = text(one(panel, "#import-creates"))
        assert "CHECKING" in creates and "AMEX CARD" in creates
        assert "Groceries" in creates

    def test_ranks_where_the_new_rows_land(
        self,
        live_client: TestClient,
        ledger: Ledger,
        csv_profiles: None,
        job_session: None,
    ) -> None:
        rows = select(review(live_client, CSV), "#import-lands .ranked > li")
        # Biggest first, and an account that does not exist yet says so.
        assert [text(one(row, "span.truncate")) for row in rows] == [
            "AMEX CARD (new)",
            "CHECKING (new)",
        ]
        assert [text(one(row, "span.font-medium")) for row in rows] == ["3", "2"]

    def test_one_account_is_not_a_ranking(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        """One bar is not a ranking, it is "To add" restated."""
        none(review(live_client, QIF, account_id=str(ledger.checking)), "#import-lands")

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
        assert follow(live_client, started.text)[0] == "done"
        panel = review(live_client, QIF, account_id=str(ledger.checking))
        none(panel, 'button[hx-post="/accounts/import"]')
        none(panel, "#import-counts")
        assert "already" in text(one(panel, "#import-preview")).lower()

    def test_a_qif_without_an_account_asks_for_one(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        """The file is read on the worker, so the question arrives as the
        job's frame - and puts the chooser back, since picking the account
        is the next step."""
        frame = review(live_client, QIF, account_id="")
        alert = one(frame, "[data-import-retry] [role='alert']")
        assert "account" in text(alert).lower()
        none(frame, "#import-preview")

    def test_unsupported_file_fails_the_job_with_a_retry(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        frame = review(
            live_client,
            ("statement.pdf", b"%PDF-1.4", "application/pdf"),
            account_id=str(ledger.checking),
        )
        one(frame, "[data-import-retry] [role='alert']")


class TestReviewCap:
    def test_the_job_keeps_only_what_the_frame_shows(self, client: TestClient) -> None:
        """The review's job record lives in Redis: it carries the capped
        detail the frame names, and the frame counts the rest from the
        totals, not from the lists it was handed."""
        from datetime import date

        from app.components.web_frontend.routes.jobs import render_snapshot
        from app.services.finance.constants import PREVIEW_DETAIL_CAP
        from app.services.finance.domains import imports_job
        from app.services.finance.schemas import (
            ImportPreviewEdit,
            ImportPreviewResponse,
        )

        cap = PREVIEW_DETAIL_CAP
        n = cap + 3
        response = ImportPreviewResponse(
            rows_total=n,
            rows_updated=n,
            new_categories=[f"Category {i}" for i in range(n)],
            edits=[
                ImportPreviewEdit(
                    transaction_id=i, date=date(2026, 1, 1), amount=100, changes=["x"]
                )
                for i in range(n)
            ],
        )
        result = imports_job.capped_review(response)
        assert len(result["edits"]) == cap
        assert len(result["new_categories"]) == cap

        html = render_snapshot(
            None,  # type: ignore[arg-type]
            {
                "status": "done",
                "name": imports_job.job_name("preview", "x.csv"),
                "label": "",
                "result": result,
            },
        )
        assert len(select(html, "#import-edits [data-edit]")) == cap
        assert "and 3 more updates" in text(one(html, "#import-edits"))
        assert "and 3 more categories" in text(one(html, "#import-creates"))


class TestPreviewOffTheRequest:
    """#487: an 18k-row plan measured 253 MiB inside the web process. The
    request stores the upload and answers with a follower; the plan is
    made where the import itself runs."""

    def test_the_request_answers_with_a_follower_not_a_review(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        response = live_client.post(
            "/accounts/import/preview",
            files={"file": QIF},
            data={"account_id": str(ledger.checking), "lane": "statement"},
            headers=HX,
        )
        assert response.status_code == 200
        none(response.text, "html")  # a fragment, never the shell
        follower = one(response.text, "[sse-connect]")
        assert follower.get("hx-ext") == "sse" and follower.get("sse-swap") == "status"
        none(response.text, "#import-preview")

        panel = follow(live_client, response.text)[1]
        assert stat(panel, "To add") == "8"
        one(panel, 'button[hx-post="/accounts/import"]')

    def test_an_oversized_file_does_not_plan_inline(
        self, client: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.finance.domains import imports_job
        from app.services.finance.service import FinanceService

        async def _plans_inline(*a: object, **k: object) -> None:
            raise AssertionError("the request planned the file itself")

        handed: dict[str, object] = {}

        async def _start(storage_key: str, **kwargs: object) -> str:
            handed.update(storage_key=storage_key, **kwargs)
            return "queued-job"

        monkeypatch.setattr(FinanceService, "preview_file", _plans_inline)
        monkeypatch.setattr(imports_job, "start_import", _start)
        big = ("big.qif", QIF[1] * 2000, "text/plain")

        response = client.post(
            "/accounts/import/preview",
            files={"file": big},
            data={"account_id": str(ledger.checking), "lane": "statement"},
        )

        assert response.status_code == 200
        follower = one(response.text, "[sse-connect]")
        assert follower.get("sse-connect") == "/jobs/queued-job/events"
        assert handed["kind"] == "preview"
        assert handed["file_name"] == "big.qif"


class TestInvestments:
    """The investment lane runs where the statement import runs, too: its
    review and its commit are both jobs the dialog follows."""

    def test_the_review_arrives_via_the_job(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        panel = review(live_client, OPTUM, lane="investments")
        heading = text(one(panel, "#import-preview h3"))
        assert heading.startswith("7 activities")

    def test_each_position_names_its_security_and_shares(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        panel = review(live_client, OPTUM, lane="investments")
        rows = select(panel, "#import-preview tbody tr")
        assert rows
        for row in rows:
            name, shares = (text(cell) for cell in select(row, "td")[:2])
            assert "-" not in (name, shares)  # the table's blank cell

    def test_the_import_runs_as_a_job(
        self, live_client: TestClient, ledger: Ledger, job_session: None
    ) -> None:
        response = live_client.post(
            "/accounts/import",
            files={"file": OPTUM},
            data={"account_id": str(ledger.checking), "lane": "investments"},
            headers=HX,
        )
        assert response.status_code == 200
        none(response.text, "#import-summary")  # nothing ran inline
        summary = follow(live_client, response.text)[1]
        assert "7 activities" in text(one(summary, "#import-summary"))
        trades = live_client.get(f"/api/v1/finance/accounts/{ledger.checking}/trades")
        assert trades.json()["total"] == 7

    def test_an_import_with_no_account_is_refused_before_it_is_stored(
        self, client: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ledger has nowhere to land without an account, and the form
        says so up front: no file parked, no job queued to fail."""
        from app.core import storage
        from app.services.finance.domains import imports_job

        def _never(*a: object, **k: object) -> None:
            raise AssertionError("a ledger with no account was stored or queued")

        monkeypatch.setattr(storage, "get_storage", _never)
        monkeypatch.setattr(imports_job, "start_import", _never)

        response = client.post(
            "/accounts/import",
            files={"file": OPTUM},
            data={"account_id": "", "lane": "investments"},
        )
        none(response.text, "[sse-connect]")
        alert = one(response.text, "[data-import-retry] [role='alert']")
        assert imports_job.NEEDS_ACCOUNT in text(alert)


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
        status, final = follow(live_client, response.text)
        assert status == "done"
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

    def test_a_job_the_app_no_longer_has_says_so_and_offers_the_file_again(
        self, client: TestClient
    ) -> None:
        """Reported live 2026-09-15: the app restarted mid-import, and
        the dialog followed a job that no longer existed. A 404 on an SSE
        stream is invisible to the extension - no frame arrives, the
        spinner turns forever, and the reader believes an import is
        still going.

        One terminal frame instead, carrying the retry marker so the file
        picker comes back.
        """
        answer = client.get("/jobs/nope/events")

        assert answer.status_code == 200
        assert "event: status" in answer.text
        assert "restarted" in answer.text
        assert "data-import-retry" in answer.text


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


class TestWhereTheImportRuns:
    """A run inside the web process dies with a reload. The worker lane
    is the default now; this one stays as the fallback for a stack
    without a worker, and the caller cannot tell which it got."""

    @pytest.mark.asyncio
    async def test_it_goes_to_the_worker_with_the_key_not_the_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.finance.domains import imports_job

        sent: dict[str, object] = {}

        async def _enqueue(job_id, storage_key, file_name, account_id, owner, kind):
            sent.update(
                job_id=job_id,
                storage_key=storage_key,
                file_name=file_name,
                account_id=account_id,
                kind=kind,
            )

        class _Store:
            async def create(self, *a, **k) -> None: ...
            async def fail(self, *a, **k) -> None: ...
            async def aclose(self) -> None: ...

        monkeypatch.setattr(imports_job, "_enqueue", _enqueue)
        monkeypatch.setattr(imports_job, "RedisJobStore", None, raising=False)
        monkeypatch.setattr(
            "app.services.system.job_store.RedisJobStore.from_url",
            lambda url: _Store(),
        )

        job_id = await imports_job.start_import(
            "store/abc123",
            file_name="ledger.csv",
            account_id=7,
            owner_user_id=None,
            kind="import",
        )

        assert sent["job_id"] == job_id
        # The file travels through storage; the queue carries its key.
        assert sent["storage_key"] == "store/abc123"
        assert sent["file_name"] == "ledger.csv"
        assert sent["account_id"] == 7
        assert sent["kind"] == "import"

    @pytest.mark.asyncio
    async def test_no_worker_still_imports_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stack without a worker is not a stack that cannot import."""
        from app.services.finance.domains import imports_job

        async def _no_worker(*a, **k):
            raise RuntimeError("no redis here")

        started: dict[str, object] = {}

        def _in_process(storage_key, **kwargs):
            started.update(storage_key=storage_key, **kwargs)
            return "local-job"

        monkeypatch.setattr(imports_job, "_hand_to_worker", _no_worker)
        monkeypatch.setattr(imports_job, "start_import_in_process", _in_process)

        job_id = await imports_job.start_import(
            "store/abc123",
            file_name="ledger.csv",
            account_id=None,
            owner_user_id=None,
            kind="import",
        )

        assert job_id == "local-job"
        assert started["storage_key"] == "store/abc123"
