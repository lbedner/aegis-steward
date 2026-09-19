"""The answer sheet: what to write on the county's form.

ST-09's deliverable, and the one step in the whole matter that still
happens by hand. The app knows what was asked, what answers it and
where each figure came from; a person then re-reads all of that on
screen and transcribes it into a paper form, which is exactly where a
wrong number or a missed item gets in.

So: one page, each ask with its answer and the source behind it, laid
out to be printed and read beside the form. Explicitly NOT form-filling
and not submission - the output is a sheet a human transcribes.

Both render paths, selectors not substrings.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.web.dom import none, one, select, text
from tests.web.matters import forget_matter, matter_referenced


@pytest.fixture
def renewal(client: Any) -> Any:
    """A matter with an answered ask, an unanswered one, and a waived
    one - the three states the sheet has to tell apart.

    Built through the app's OWN routes, not a session fixture: matters
    routes open their own sessions against the app-owned engine, so data
    written to ``async_db_session`` lands in a different database than
    the page reads. Going through the routes also means this exercises
    the real wiring rather than a hand-built shape.

    The title is unique per test because the app-owned database is
    SESSION scoped - matters accumulate across tests, and "the only
    matter" is true for about one test.
    """
    # The REFERENCE is the unique mark, not the title: a long title
    # is truncated in the list and a match on it fails silently.
    reference = f"MA{uuid4().hex[:6].upper()}"
    client.post(
        "/contacts/new",
        data={"name": "James Testcase", "kind": "person", "sort_name": "", "note": ""},
    )
    client.post(
        "/matters/new",
        data={
            "title": "Medicaid renewal",
            "kind": "benefits",
            "reference": reference,
            "subject_party_id": "",
            "new_subject": "",
            "counterparty_party_id": "",
            "new_counterparty": "",
            "note": "",
        },
    )
    matter_id = matter_referenced(client, reference)
    client.post(
        f"/matters/{matter_id}/requests/new",
        data={
            "asked": (
                "Proof of gross monthly income\n"
                "A copy of the power of attorney\n"
                "Burial fund statement"
            ),
            "due_on": "",
            "received_on": "",
            "requester_party_id": "",
            "new_requester": "",
        },
    )
    items = _item_ids(client, matter_id)

    # The first ask names an ATTRIBUTE, which is what lets a recorded
    # figure answer it. Nothing is guessed from the sentence.
    client.post(
        f"/matters/requests/items/{items[0]}/edit",
        data={
            "asked": "Proof of gross monthly income",
            "kind": "figure",
            "ask": "gross_income",
            "as_of": "",
        },
    )
    client.post(
        f"/matters/{matter_id}/facts/new",
        data={
            "subject_party_id": "",
            "new_subject": "James Testcase",
            "attribute": "gross_income",
            "label": "",
            "amount": "2178.94",
            "period": "month",
            "text_value": "",
            "as_of": "",
            "provenance": "stated",
            "document_id": "",
            "source_party_id": "",
        },
    )
    client.post(
        f"/matters/requests/items/{items[0]}/attach",
        files={
            "file": (
                "NYSLRS Monthly Statement.pdf",
                b"%PDF-1.4 statement",
                "application/pdf",
            )
        },
    )
    client.post(f"/matters/requests/items/{items[2]}/mark/waived")
    yield {"matter_id": matter_id, "items": items, "reference": reference}

    # Put the shared database back. The app-owned engine is SESSION
    # scoped, so a matter left behind is a matter every later test sees -
    # and "no matters yet" is an assertion somebody else already wrote.
    forget_matter(matter_id)


def _item_ids(client: Any, matter_id: int) -> list[int]:
    page = client.get(f"/matters/{matter_id}").text
    return [
        int(el.get("data-item"))
        for el in select(page, "[data-item]")
        if (el.get("data-item") or "").isdigit()
    ]


def test_the_sheet_renders(client: Any, renewal: dict[str, Any]) -> None:
    page = client.get(f"/matters/{renewal['matter_id']}/answers").text
    one(page, "#answer-sheet")


def test_the_fragment_carries_no_shell(hx: Any, renewal: dict[str, Any]) -> None:
    page = hx.get(f"/matters/{renewal['matter_id']}/answers").text
    none(page, "html")
    one(page, "#answer-sheet")


class TestWhatTheSheetSays:
    def test_every_ask_is_on_it(self, client: Any, renewal: dict[str, Any]) -> None:
        """Including the ones with no answer. A sheet that lists only
        what is done is a sheet that hides what is not."""
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        assert len(select(page, "[data-answer]")) == 3

    def test_an_answered_ask_carries_its_figure(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        answered = select(page, '[data-answer][data-state="answered"]')
        assert len(answered) == 1
        said = text(answered[0])
        assert "$2,178.94" in said
        assert "a month" in said

    def test_it_names_the_source_it_came_from(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        """The whole point. A figure on a form with no source behind it
        is a number somebody will be asked to justify later."""
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        sources = select(page, "[data-source]")
        said = " ".join(text(s) for s in sources)
        assert "NYSLRS Monthly Statement.pdf" in said

    def test_a_missing_ask_says_what_to_go_and_get(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        missing = select(page, '[data-answer][data-state="missing"]')
        assert len(missing) == 1
        assert "power of attorney" in text(missing[0]).casefold()

    def test_a_waived_ask_is_shown_as_settled_not_missing(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        """Somebody said this one does not apply. Printing it as
        outstanding sends them chasing paper the county did not ask
        for."""
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        assert len(select(page, '[data-answer][data-state="waived"]')) == 1
        assert len(select(page, '[data-answer][data-state="missing"]')) == 1

    def test_the_reference_is_on_it(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        """A sheet beside a form needs the case number the county files
        it under, or it is a page about nothing in particular.

        It reads off the header every face of the matter wears, not a
        line of this page's own: the sheet had its own copy, so the same
        case announced itself one way here and another on the case page.
        """
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        assert renewal["reference"] in text(one(page, "header[data-matter] [data-facts]"))

    def test_it_says_how_many_are_still_outstanding(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        assert "1" in text(one(page, "[data-outstanding]"))


class TestItIsMeantToBePrinted:
    def test_the_chrome_is_marked_to_drop_out_of_print(
        self, client: Any, renewal: dict[str, Any]
    ) -> None:
        """Navigation and buttons on a printed sheet are ink somebody
        pays for and reads past."""
        page = client.get(f"/matters/{renewal['matter_id']}/answers").text
        assert select(page, ".print\\:hidden")

    def test_a_matter_that_does_not_exist_is_404(self, client: Any) -> None:
        assert client.get("/matters/999999/answers").status_code == 404
