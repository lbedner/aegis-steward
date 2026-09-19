"""ST-10: the matter reads as a case, not a pile of paper.

Three years from now the papers will still be there and the story will
not. The timeline is DERIVED from dated rows that already exist, so it
cannot disagree with them; the only thing it stores is the part that
leaves no paper - the call, the mailing, the visit.

Both render paths, selectors not substrings.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.web.dom import none, one, select, text
from tests.web.matters import forget_matter, matter_referenced


@pytest.fixture
def case(client: Any) -> Any:
    """A matter with a letter that asked for something and is due."""
    reference = f"TL{uuid4().hex[:6].upper()}"
    client.post(
        "/matters/new",
        data={
            "title": "Medicaid renewal",
            "kind": "benefits",
            "reference": reference,
            "opened_on": "2026-08-20",
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
            "asked": "Proof of gross monthly income",
            "due_on": "2026-09-21",
            "received_on": "2026-08-27",
            "requester_party_id": "",
            "new_requester": "",
        },
    )
    yield {"matter_id": matter_id, "reference": reference}
    forget_matter(matter_id)


def _moments(page: str) -> list[str]:
    return [el.get("data-kind") for el in select(page, "[data-moment]")]


def test_the_timeline_renders(client: Any, case: dict[str, Any]) -> None:
    page = client.get(f"/matters/{case['matter_id']}/timeline").text
    one(page, "#timeline")


def test_the_fragment_carries_no_shell(hx: Any, case: dict[str, Any]) -> None:
    page = hx.get(f"/matters/{case['matter_id']}/timeline").text
    none(page, "html")
    one(page, "#timeline")


class TestWhatItSays:
    def test_the_dated_rows_are_there_without_anybody_retyping_them(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        page = client.get(f"/matters/{case['matter_id']}/timeline").text
        assert _moments(page) == ["opened", "asked", "due"]

    def test_a_case_with_only_its_opening_says_so(self, client: Any) -> None:
        """Every matter has at least the day it opened, so the empty
        state is about the events nobody has typed, not about the page."""
        reference = f"TL{uuid4().hex[:6].upper()}"
        client.post(
            "/matters/new",
            data={
                "title": "Bare case",
                "kind": "",
                "reference": reference,
                "subject_party_id": "",
                "new_subject": "",
                "counterparty_party_id": "",
                "new_counterparty": "",
                "note": "",
            },
        )
        matter_id = matter_referenced(client, reference)
        page = client.get(f"/matters/{matter_id}/timeline").text
        assert _moments(page) == ["opened"]
        forget_matter(matter_id)


class TestTheEventsThatLeaveNoPaper:
    def test_a_call_is_typed_in_and_places_itself_by_date(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        matter_id = case["matter_id"]
        opened = client.get(f"/matters/{matter_id}/events/new")
        assert opened.status_code == 200
        one(opened.text, "form")

        added = client.post(
            f"/matters/{matter_id}/events",
            data={
                "occurred_at": "2026-08-25",
                "kind": "call",
                "summary": "Called DSS, confirmed receipt",
            },
        )
        assert added.status_code == 200

        page = client.get(f"/matters/{matter_id}/timeline").text
        assert _moments(page) == ["opened", "call", "asked", "due"]
        assert "Called DSS, confirmed receipt" in text(
            select(page, "[data-moment][data-kind='call']")[0]
        )

    def test_an_event_saying_nothing_is_refused_in_place(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        refused = client.post(
            f"/matters/{case['matter_id']}/events",
            data={"occurred_at": "2026-08-25", "kind": "call", "summary": "   "},
        )
        assert refused.status_code == 422
        one(refused.text, "form")

    def test_the_remove_button_goes_somewhere_that_exists(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        """The participants dialog shipped with a button and no route and
        404'd from the day the page existed. A control nobody clicked in
        a test looks exactly like this one."""
        matter_id = case["matter_id"]
        client.post(
            f"/matters/{matter_id}/events",
            data={
                "occurred_at": "2026-08-25",
                "kind": "visit",
                "summary": "Walked the packet in",
            },
        )
        page = client.get(f"/matters/{matter_id}/timeline").text
        button = select(page, "[data-moment][data-kind='visit'] [data-forget]")[0]
        opened = client.get(button.get("hx-get"))
        assert opened.status_code == 200
        assert "Walked the packet in" in opened.text

    def test_removing_it_changes_nothing_else(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        matter_id = case["matter_id"]
        client.post(
            f"/matters/{matter_id}/events",
            data={
                "occurred_at": "2026-08-25",
                "kind": "mailed",
                "summary": "Mailed the POA",
            },
        )
        page = client.get(f"/matters/{matter_id}/timeline").text
        event_id = select(page, "[data-moment][data-kind='mailed']")[0].get("data-event")

        client.delete(f"/matters/{matter_id}/events/{event_id}")

        after = client.get(f"/matters/{matter_id}/timeline").text
        assert _moments(after) == ["opened", "asked", "due"]


def test_every_face_wears_the_same_header(client: Any, case: dict[str, Any]) -> None:
    """Cycling the tabs must not redraw the title block. Three pages had
    three headers - one with the case's kind and reference, one with its
    due date, one with neither - so which case you were looking at
    changed shape as you moved between its own tabs. The accounts detail
    settled this already (one ``account_header``, asserted identical on
    all three faces); matters now reads the same way."""
    matter_id = case["matter_id"]
    faces = {
        face: one(client.get(f"/matters/{matter_id}{face}").text, "header[data-matter]")
        for face in ("", "/timeline", "/answers")
    }
    assert len({text(one(h, "h1")) for h in faces.values()}) == 1
    assert len({text(one(h, "[data-facts]")) for h in faces.values()}) == 1
    for header in faces.values():
        one(header, "[data-back]")


def test_each_moment_wears_what_it_means(client: Any, case: dict[str, Any]) -> None:
    """One rule, not a colour per kind: amber is what can still bite
    you, teal is what we settled, accent is what arrived from outside.
    Read off the map that defines it rather than restated here, or the
    test is a second home for the rule."""
    from app.services.matters.words import MOMENT_TONES

    page = client.get(f"/matters/{case['matter_id']}/timeline").text
    for moment in select(page, "[data-moment]"):
        kind = moment.get("data-kind")
        assert one(moment, "[data-tone]").get("data-tone") == MOMENT_TONES[kind]


class TestWhatIsALinkAndWhatIsNot:
    """Paper is the only moment that goes anywhere.

    Everything else on the story is a row on the case page, one tab
    over, and a link that moves the reader to another face of the thing
    they are already looking at is navigation dressed up as information:
    the header is the same, the tabs are the same, and following one
    reads as having gone nowhere (2026-09-18)."""

    def test_a_figure_is_not_a_link(self, client: Any, case: dict[str, Any]) -> None:
        matter_id = case["matter_id"]
        client.post(
            f"/matters/{matter_id}/facts/new",
            data={
                "subject_party_id": "",
                "new_subject": "Timeline Fact Subject",
                "attribute": "account_balance",
                "label": "Checking",
                "amount": "3137.44",
                "period": "once",
                "as_of": "2026-08-01",
                "provenance": "ledger",
            },
        )
        page = client.get(f"/matters/{matter_id}/timeline").text
        assert none(page, "[data-moment][data-kind='figure'] a") is None

    def test_an_ask_is_not_a_link(self, client: Any, case: dict[str, Any]) -> None:
        page = client.get(f"/matters/{case['matter_id']}/timeline").text
        assert none(page, "[data-moment][data-kind='asked'] a") is None

    def test_paper_opens_its_own_page_and_looks_like_a_door(
        self, client: Any, case: dict[str, Any]
    ) -> None:
        matter_id = case["matter_id"]
        client.post(
            f"/matters/{matter_id}/documents/new",
            files={
                "file": ("County letter.pdf", b"%PDF-1.4 letter", "application/pdf")
            },
        )
        page = client.get(f"/matters/{matter_id}/timeline").text
        link = one(page, "[data-moment][data-kind='paper'] a")
        assert link.get("href").startswith(f"/matters/{matter_id}/documents/")
        # Teal, like every other door: they were drawn in the text colour
        # and nobody knew they could be clicked.
        assert "text-aegis-teal" in (link.get("class") or "")


def test_the_tab_is_on_every_face_of_the_matter(
    client: Any, case: dict[str, Any]
) -> None:
    """A story reachable only from one page is a story nobody finds."""
    for path in ("", "/answers", "/timeline"):
        page = client.get(f"/matters/{case['matter_id']}{path}").text
        hrefs = [el.get("href") for el in select(page, "#matter-tabs a")]
        assert f"/matters/{case['matter_id']}/timeline" in hrefs
