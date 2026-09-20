"""Review: four queues as sibling routes under one sub-nav.

Approvals resolves pending changes (pattern 2) and moves the counts out
of band; Uncategorized and No payee are the register with one filter
fixed, plus the auto-categorize preview and the payee-group assignment;
Attention lists the new insights with a dismiss.
"""

import json

from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from tests.web.conftest import REGISTER, Ledger, Review
from tests.web.dom import none, one, oob, select, table_rows, text, triggers


def nav(page: str) -> dict[str, str]:
    return {text(a).rsplit(" (", 1)[0]: text(a) for a in select(page, "#review-nav a")}


class TestNav:
    def test_four_queues_with_counts(self, client: TestClient, review: Review) -> None:
        page = client.get("/review").text
        assert nav(page) == {
            "Approvals": "Approvals (3)",
            "Uncategorized": "Uncategorized (2)",  # the mystery charge and payroll
            "No payee": "No payee (5)",
            "Attention": "Attention (1)",
        }
        assert text(one(page, '#review-nav a[aria-current="page"]')).startswith(
            "Approvals"
        )
        for path in ("/review/uncategorized", "/review/no-payee", "/review/attention"):
            current = one(client.get(path).text, '#review-nav a[aria-current="page"]')
            assert current.get("href") == path

    def test_fragment_has_no_shell(self, hx: TestClient, review: Review) -> None:
        none(hx.get("/review").text, "html")

    def test_the_sidebar_fetches_a_mark_while_cards_are_waiting(
        self, client: TestClient, review: Review
    ) -> None:
        """The same hook Matters uses for an overdue deadline: a card
        nobody has answered is the thing a person opening the app should
        see before they go looking (2026-09-21)."""
        nav = one(
            client.get("/review").text, '[data-attention][hx-get="/review/waiting"]'
        )
        assert nav.get("hx-get") == "/review/waiting"
        mark = one(client.get("/review/waiting").text, "[data-dot]")
        assert text(mark).strip().endswith("to review")


class TestApprovals:
    def test_single_card_then_approve(self, client: TestClient, review: Review) -> None:
        page = client.get("/review").text
        card = one(page, f"#change-{review.change}")
        assert "Categorize a transaction" in text(one(card, "h3"))
        one(card, "[data-subject]")  # the subject line, then the facts
        approve = one(card, f'[hx-post="/review/changes/{review.change}/approve"]')
        assert approve.get("hx-target") == f"#change-{review.change}"
        assert approve.get("hx-swap") == "outerHTML"

        response = client.post(f"/review/changes/{review.change}/approve")
        primary, siblings = oob(response.text)
        assert primary == []
        ids = {s.get("id") for s in siblings}
        assert {"pending-changes", "review-nav"} <= ids
        assert "Approved" in triggers(response)["toast"]["text"]
        assert nav(client.get("/review").text)["Approvals"] == "Approvals (2)"
        assert "Food:Groceries" in text(
            one(
                client.get(REGISTER).text,
                f"#txn-{review.mystery} select option[selected]",
            )
        )

    def test_reject(self, client: TestClient, review: Review) -> None:
        response = client.post(f"/review/changes/{review.change}/reject")
        assert oob(response.text)[0] == []
        none(client.get("/review").text, f"#change-{review.change}")

    def test_batch_card_with_vetoes(self, client: TestClient, review: Review) -> None:
        page = client.get("/review").text
        card = one(page, f"#batch-{review.batch}")
        rows = select(card, 'input[name="exclude_ids"]')
        assert len(rows) == 2 and all(r.get("checked") is None for r in rows)
        approve = one(card, f'[hx-post="/review/changes/batch/{review.batch}/approve"]')
        assert approve.get("hx-include") == f"#batch-{review.batch}"

        response = client.post(
            f"/review/changes/batch/{review.batch}/approve",
            data={"exclude_ids": [rows[1].get("value")]},
        )
        assert oob(response.text)[0] == []
        toast = triggers(response)["toast"]["text"]
        assert "1 approved" in toast and "1 rejected" in toast
        none(client.get("/review").text, f"#batch-{review.batch}")

    def test_reject_batch(self, client: TestClient, review: Review) -> None:
        response = client.post(f"/review/changes/batch/{review.batch}/reject")
        assert "2 rejected" in triggers(response)["toast"]["text"]

    def test_empty_queue_says_so(self, client: TestClient, ledger: Ledger) -> None:
        assert "Nothing waiting" in text(one(client.get("/review").text, "#approvals"))


class TestEdit:
    """A card that came off a From header says "Optum" because that is
    the domain; the person reading it knows it is Optum Financial. The
    card is a proposal, and a proposal can be corrected before it is
    approved - for the types that opt in, through the one dialog, with
    every field prefilled (2026-09-21)."""

    @pytest_asyncio.fixture
    async def offered(self, finance, async_db_session) -> int:
        row = await finance.propose_change(
            "contact.create",
            {
                "name": "Optum",
                "kind": "organization",
                "email": "of-service@of.optum.com",
            },
            owner_user_id=None,
            proposed_by_agent="mail",
        )
        await async_db_session.commit()
        return int(row.id)

    def test_an_editable_card_offers_edit_and_others_do_not(
        self, client: TestClient, review: Review, offered: int
    ) -> None:
        page = client.get("/review").text
        opener = one(page, f"#change-{offered} [data-edit]")
        assert opener.get("hx-get") == f"/review/changes/{offered}/edit"
        none(page, f"#change-{review.change} [data-edit]")

    def test_the_dialog_is_prefilled_from_the_card(
        self, client: TestClient, offered: int
    ) -> None:
        dialog = client.get(f"/review/changes/{offered}/edit").text
        assert one(dialog, 'input[name="name"]').get("value") == "Optum"
        assert (
            one(dialog, 'input[name="email"]').get("value") == "of-service@of.optum.com"
        )
        kind = one(dialog, 'select[name="kind"] option[selected]')
        assert kind.get("value") == "organization"
        form = one(dialog, "form")
        assert form.get("hx-post") == f"/review/changes/{offered}/edit"

    def test_saving_puts_your_words_on_the_card(
        self, client: TestClient, offered: int
    ) -> None:
        answer = client.post(
            f"/review/changes/{offered}/edit",
            data={
                "name": "Optum Financial",
                "kind": "organization",
                "email": "of-service@of.optum.com",
            },
            headers={"HX-Current-URL": "http://t/review"},
        )
        assert answer.status_code == 200
        assert "dialog:close" in triggers(answer)
        card = one(client.get("/review").text, f"#change-{offered}")
        assert text(one(card, "[data-subject]")).strip() == "Optum Financial"

    def test_a_bad_revision_comes_back_with_the_error(
        self, client: TestClient, offered: int
    ) -> None:
        answer = client.post(
            f"/review/changes/{offered}/edit", data={"name": "", "kind": "organization"}
        )
        assert answer.status_code == 422
        one(answer.text, "[role=alert]")
        assert (
            one(answer.text, 'select[name="kind"] option[selected]').get("value")
            == "organization"
        )


class TestUncategorized:
    def test_is_the_register_with_the_filter_fixed(
        self, client: TestClient, review: Review
    ) -> None:
        page = client.get("/review/uncategorized").text
        rows = table_rows(page, "#register table")
        assert [text(r["Payee"]) for r in rows] == ["Mystery charge", "Payroll"]
        form = one(page, "form#register-filters")
        assert form.get("hx-get") == "/review/uncategorized"
        one(form, 'input[name="uncategorized"][value="on"]')
        none(form, 'select[name="category_id"]')
        one(page, "#bulk-actions")  # the same selection bar as the register

    def test_auto_categorize_previews_without_writing(
        self, client: TestClient, review: Review
    ) -> None:
        page = client.get("/review/uncategorized").text
        button = one(page, '[hx-post="/review/uncategorized/suggest"]')
        assert button.get("hx-include") == "[name='transaction_ids']:checked"
        response = client.post("/review/uncategorized/suggest")
        assert response.status_code == 200
        primary, siblings = oob(response.text)
        assert primary == []
        chips = select(response.text, ".suggestion")
        if chips:
            accept = one(chips[0], "button[hx-post$='/categorize']")
            assert json.loads(accept.get("hx-vals") or "{}")["category_id"]
        else:
            assert "No suggestions" in triggers(response)["toast"]["text"]
        # Nothing written: the row is still uncategorised.
        still = table_rows(client.get("/review/uncategorized").text, "#register table")
        assert len(still) == 2


class TestNoPayee:
    def test_is_the_register_without_merchants(
        self, client: TestClient, review: Review
    ) -> None:
        page = client.get("/review/no-payee").text
        assert len(table_rows(page, "#register table")) == 5
        form = one(page, "form#register-filters")
        one(form, 'input[name="without_merchant"][value="on"]')
        none(form, 'select[name="merchant_id"]')
        one(page, '#bulk-actions [hx-get="/transactions/payee"]')

    def test_groups_view_and_assignment(
        self, client: TestClient, hx: TestClient, review: Review
    ) -> None:
        page = client.get("/review/no-payee?view=groups").text
        groups = table_rows(page, "#payee-groups table")
        market = next(
            g for g in groups if text(g["Suggested name"]).startswith("Market")
        )
        assert text(market["Count"]) == "2"
        key = one(market["Actions"], 'input[name="keys"]').get("value")
        button = one(page, '[hx-get="/review/no-payee/assign"]')
        assert button.get("hx-include") == "[name='keys']:checked"

        form = one(
            hx.get("/review/no-payee/assign", params={"keys": [key]}).text, "form"
        )
        assert form.get("hx-post") == "/review/no-payee/assign"
        one(form, 'select[name="merchant_id"]')
        response = client.post(
            "/review/no-payee/assign", data={"keys": [key], "new_name": "Market Inc"}
        )
        assert (
            json.loads(response.headers["HX-Location"])["path"]
            == "/review/no-payee?view=groups"
        )
        # A navigation is followed, never swapped, so the close must ride
        # the plain trigger header: an after-settle one would never fire
        # and the dialog would stay open over the new page.
        assert "dialog:close" in json.loads(response.headers["HX-Trigger"])
        assert (
            len(table_rows(client.get("/review/no-payee").text, "#register table")) == 3
        )

    def test_assign_needs_a_payee(self, client: TestClient, review: Review) -> None:
        response = client.post(
            "/review/no-payee/assign", data={"keys": ["x"], "new_name": ""}
        )
        assert response.status_code == 422


class TestAttention:
    def test_card_then_dismiss(self, client: TestClient, review: Review) -> None:
        page = client.get("/review/attention").text
        card = one(page, f"#insight-{review.insight}")
        assert "Netflix went up" in text(card)
        assert one(card, "[data-tone]").get("data-tone") == "warn"
        dismiss = one(card, f'[hx-post="/review/insights/{review.insight}/dismiss"]')
        assert dismiss.get("hx-target") == f"#insight-{review.insight}"

        response = client.post(f"/review/insights/{review.insight}/dismiss")
        primary, siblings = oob(response.text)
        assert primary == [] and any(s.get("id") == "review-nav" for s in siblings)
        assert "Dismissed" in triggers(response)["toast"]["text"]
        after = client.get("/review/attention").text
        assert "Nothing needs attention" in text(one(after, "#attention"))

    def test_unknown_insight_is_404(self, client: TestClient, ledger: Ledger) -> None:
        assert client.post("/review/insights/999999/dismiss").status_code == 404


class TestACardShowsWhatItCites:
    """A citation proves where text came from, not that the reading was
    right - and the moment somebody is deciding is the one moment they
    could check it. A card that cites a page draws that page."""

    async def _proposal(self, finance, async_db_session) -> tuple[int, int]:
        from app.services.documents.models import Document, DocumentPage
        from app.services.matters.matters import MatterService
        from app.services.matters.requests import RequestService

        document = Document(
            title="NYSLRS Monthly Statement",
            storage_key="testcase/nyslrs.pdf",
            media_type="application/pdf",
            content_hash="testcase-card-hash",
            size_bytes=17,
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentPage(
                document_id=document.id,
                page_number=2,
                status="read",
                method="text",
                text="Monthly Pension Benefit: $2,178.94",
                image_key="testcase/nyslrs-2.png",
            )
        )
        matter = await MatterService(async_db_session).open(
            title="Medicaid renewal", reference="CARD-1"
        )
        request = await RequestService(async_db_session).record(
            matter_id=matter.id, items=[{"asked": "Proof of gross monthly income"}]
        )
        items = await RequestService(async_db_session).items(request.id)
        change = await finance.propose_change(
            "document.evidence_link",
            {
                "document_id": document.id,
                "request_item_id": items[0].id,
                "page": 2,
                "because": "Monthly Pension Benefit: $2,178.94",
            },
            owner_user_id=None,
            proposed_by_agent="steward",
        )
        await async_db_session.flush()
        return change.id, document.id

    @pytest.mark.asyncio
    async def test_the_page_is_drawn_beside_the_claim(
        self, client: TestClient, finance, async_db_session, review: Review
    ) -> None:
        change_id, document_id = await self._proposal(finance, async_db_session)

        card = one(client.get("/review").text, f"#change-{change_id}")
        shown = one(card, "[data-page]")
        assert shown.get("src") == f"/api/v1/documents/{document_id}/pages/2/image"

        # And it is a door: a thumbnail answers "which paper is this",
        # and the next question is always "what else does it say".
        # READING, not editing: somebody checking a proposed title
        # against the letterhead must not be able to type a different
        # one behind the card that is about to overwrite it.
        #
        # The address only: the documents section opens its own session
        # against the app-owned engine, so a document written to this
        # test's session is not there to be fetched. Its own tests cover
        # that the route answers.
        assert shown.getparent().get("hx-get") == f"/documents/{document_id}?reading=1"

    @pytest.mark.asyncio
    async def test_a_card_citing_no_page_draws_none(
        self, client: TestClient, review: Review
    ) -> None:
        card = one(client.get("/review").text, f"#change-{review.change}")
        assert none(card, "[data-page]") is None
