"""What a finished job says, per job.

The follower (pattern 5) renders one fragment for every job the app
runs, and that fragment was written for the import: "Import complete",
the import's five counts, and a Done button that navigates to Accounts.

Attaching the follower to a document read meant a finished read said
"Import complete" in the document dialog and offered a button that took
the reader somewhere else entirely (2026-09-19).
"""

from typing import Any

from fastapi import Request

from app.components.web_frontend.routes.jobs import render_snapshot
from tests.web.dom import none, one, select, text


def _request() -> Any:
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/"})


def _snapshot(name: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "job-1",
        "name": name,
        "label": None,
        "status": "done",
        "result": result or {},
        "error": None,
    }


class TestAFinishedRead:
    def test_it_says_what_was_read(self) -> None:
        html = render_snapshot(
            _request(),
            _snapshot("documents-extract:12", {"read": 2, "unread": 0, "skipped": 4}),
        )
        said = text(one(html, "[data-job-done]"))
        assert "2" in said
        assert "Import" not in said

    def test_it_takes_nobody_anywhere(self) -> None:
        """The reader is in the document dialog, looking at the document.
        A button that navigates to Accounts is how a finished read landed
        somebody on another page."""
        html = render_snapshot(
            _request(), _snapshot("documents-extract:12", {"read": 2})
        )
        assert not [
            el for el in select(html, "[hx-get]") if "/accounts" in (el.get("hx-get") or "")
        ]

    def test_a_read_that_found_nothing_new_says_so(self) -> None:
        html = render_snapshot(
            _request(),
            _snapshot("documents-extract:12", {"read": 0, "unread": 0, "skipped": 6}),
        )
        assert "already" in text(one(html, "[data-job-done]")).lower()


class TestAFinishedImport:
    def test_it_still_says_import_complete(self) -> None:
        """The import's own frame is unchanged: it is read against the
        review screen that precedes it."""
        html = render_snapshot(
            _request(),
            # Without the counts: those cells have their own tests, and
            # this one is about which FRAME a job gets.
            _snapshot("imports-file:3"),
        )
        one(html, "#import-summary")
        assert "Import complete" in text(one(html, "h3"))


class TestAJobThatIsStillRunning:
    def test_it_shows_its_label(self) -> None:
        html = render_snapshot(
            _request(),
            {
                "id": "job-1",
                "name": "documents-extract:12",
                "label": "Reading page 2 of 6",
                "status": "running",
                "result": {},
                "error": None,
            },
        )
        assert "Reading page 2 of 6" in html
        assert none(html, "[data-job-done]") is None
