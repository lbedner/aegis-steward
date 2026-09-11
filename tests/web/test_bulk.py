"""Bulk actions on the register: select rows, act on the selection.

Selection is plain checkboxes named ``transaction_ids``; each action
includes the checked ones. Responses swap nothing at the trigger and
send every touched row out of band, so one contract serves single and
bulk alike.
"""

from fastapi.testclient import TestClient

from tests.web.conftest import REGISTER, Ledger
from tests.web.dom import none, one, oob, select, text, triggers
from tests.web.test_row_actions import payee_cell, txn_id


def payee_of(row) -> str:  # noqa: ANN001
    return text(payee_cell(row)).split(" ")[0]


class TestSelection:
    def test_every_row_has_a_checkbox(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(REGISTER).text
        boxes = select(
            page, '#register tbody input[type="checkbox"][name="transaction_ids"]'
        )
        assert len(boxes) == 5
        assert text(select(page, "#register thead th")[0]) == "Select"

    def test_action_bar_acts_on_the_checked_rows(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        bar = one(client.get(REGISTER).text, "#bulk-actions")
        include = "[name='transaction_ids']:checked"
        payee = one(bar, '[hx-get="/transactions/payee"]')
        assert (
            payee.get("hx-include") == include
            and payee.get("hx-target") == "#dialog-body"
        )
        tag = one(bar, '[hx-get="/transactions/tag"]')
        assert (
            tag.get("hx-include") == include and tag.get("hx-target") == "#dialog-body"
        )
        remove = one(bar, '[hx-post="/transactions/delete"]')
        assert remove.get("hx-include") == include
        assert remove.get("hx-swap") == "none" and remove.get("hx-confirm")


class TestBulkDelete:
    def test_deletes_the_selection_out_of_band(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Gas"), txn_id(page, "Mystery charge")]
        response = client.post("/transactions/delete", data={"transaction_ids": ids})
        primary, siblings = oob(response.text)
        assert primary == []
        deleted = [s for s in siblings if s.get("hx-swap-oob") == "delete"]
        assert sorted(s.get("id") for s in deleted) == sorted(f"txn-{i}" for i in ids)
        assert text(one(response.text, "#uncategorized-count")) == "1 uncategorized"
        after = client.get(REGISTER).text
        none(after, f"#txn-{ids[0]}")
        none(after, f"#txn-{ids[1]}")


class TestAssignPayee:
    def test_the_dialog_is_a_searchable_list_that_can_name_a_new_one(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """A ledger has hundreds of payees, so the choice is a search over
        them, not a select to scroll. Naming a new one happens right here,
        where you discovered you needed it."""
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Shell"), txn_id(page, "Payroll")]
        dialog = hx.get("/transactions/payee", params={"transaction_ids": ids}).text
        picker = one(dialog, "#payee-picker")
        assert one(picker, "input[type=search]").get("placeholder") == (
            "Search or name a payee"
        )
        form = one(picker, "form[hx-post='/transactions/payee']")
        assert sorted(
            h.get("value") for h in select(form, 'input[name="transaction_ids"]')
        ) == sorted(ids)
        # Every option is a way to submit the form, so picking is one click.
        assert [b.get("value") for b in select(form, "button[name=merchant_id]")]

    def test_typing_never_leaves_the_dialog_and_never_resizes_it(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """Narrowing a list of a hundred names is not worth a round trip
        each keystroke: the whole list is here, and typing hides rows. The
        list keeps a fixed height, so the dialog does not collapse to one
        row and jump under the cursor."""
        page = client.get(REGISTER).text
        gas = txn_id(page, "Shell")
        dialog = hx.get("/transactions/payee", params={"transaction_ids": [gas]}).text
        picker = one(dialog, "#payee-picker")
        none(picker, "form[hx-get]")  # nothing to wait for
        assert one(picker, "input[type=search]").get("x-model") == "query"
        options = one(picker, "ul#payee-picker-options")
        assert "h-[22rem]" in (options.get("class") or "")
        rows = select(options, "li[data-name]")
        assert rows and all(row.get("x-show") for row in rows)

    def test_the_dialog_calls_a_transaction_what_you_renamed_it_to(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """Once a payee is named, that is the transaction's name: the raw
        bank descriptor is what you were trying to get away from, and
        seeing it again reads as though the rename never took."""
        page = client.get(REGISTER).text
        gas = txn_id(page, "Shell")
        dialog = hx.get("/transactions/payee", params={"transaction_ids": [gas]}).text
        assert "Shell" in text(one(dialog, "h2"))

    def test_the_picker_refuses_the_payee_it_already_has(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """Setting it to what it already is does nothing, so the row that
        would do nothing cannot be pressed. Marked and disabled, not
        hidden: you still need to see what it is."""
        page = client.get(REGISTER).text
        gas = txn_id(page, "Shell")
        picker = one(
            hx.get("/transactions/payee", params={"transaction_ids": [gas]}).text,
            "#payee-picker",
        )
        current = one(picker, 'button[aria-current="true"]')
        assert current.get("value") == str(merchant)
        assert current.get("disabled") is not None
        assert "Already" in (current.get("title") or "")
        # Every other payee is still one click away.
        assert not [
            b
            for b in select(picker, "button[name=merchant_id]")
            if b is not current and b.get("disabled") is not None
        ]

    def test_a_mixed_selection_marks_nothing(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Shell"), txn_id(page, "Payroll")]
        picker = one(
            hx.get("/transactions/payee", params={"transaction_ids": ids}).text,
            "#payee-picker",
        )
        none(picker, 'button[aria-current="true"]')
        none(picker, "button[name=merchant_id][disabled]")

    def test_every_payee_is_here_to_be_searched_and_a_new_one_named(
        self, client: TestClient, hx: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """The rows carry the name typing matches on, and the create row
        posts whatever was typed — it shows itself only when nothing in
        the list is that name already."""
        page = client.get(REGISTER).text
        gas = txn_id(page, "Shell")
        picker = one(
            hx.get("/transactions/payee", params={"transaction_ids": [gas]}).text,
            "#payee-picker",
        )
        assert "shell" in [
            row.get("data-name") for row in select(picker, "li[data-name]")
        ]
        create = one(picker, "button[name=new_name]")
        assert create.get(":value") == "query.trim()"
        assert "names" in (one(picker, "[x-data]").get("x-data") or "")

    def test_new_payee_is_created_and_rows_come_back_out_of_band(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(REGISTER).text
        gas = txn_id(page, "Gas")
        response = client.post(
            "/transactions/payee", data={"transaction_ids": [gas], "new_name": "Shell"}
        )
        primary, siblings = oob(response.text)
        row = one(response.text, f"tr#txn-{gas}")
        assert row.get("hx-swap-oob") == "outerHTML"
        assert payee_of(row) == "Shell"
        assert "dialog:close" in triggers(response)

    def test_offers_the_similar_rows_after_a_single_assignment(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Two payee-less "Market" rows: naming one offers the other, a
        suggestion the user confirms, never applied on its own. Every
        lookalike is a checkbox, ticked, because the match is a heuristic
        and a wrong one should be unticked, not force all or nothing."""
        page = client.get(REGISTER).text
        markets = [
            (tr.get("id") or "").removeprefix("txn-")
            for tr in select(page, "#register tbody tr")
            if text(payee_cell(tr)) == "Market"
        ]
        first, other = markets
        response = client.post(
            "/transactions/payee",
            data={"transaction_ids": [first], "new_name": "Market Inc"},
        )
        assert "dialog:close" not in triggers(response)
        offer = one(response.text, "form#similar-offer")
        assert "1 similar transaction" in text(one(response.text, "p"))
        boxes = select(offer, 'input[name="transaction_ids"][type=checkbox]')
        assert [b.get("value") for b in boxes] == [other]
        assert all(b.get("checked") is not None for b in boxes)
        merchant = one(offer, 'input[name="merchant_id"]').get("value")

        accepted = client.post(
            "/transactions/payee",
            data={"transaction_ids": [other], "merchant_id": merchant},
        )
        assert payee_of(one(accepted.text, f"tr#txn-{other}")) == "Market"
        assert "dialog:close" in triggers(accepted)

    def test_the_rows_ride_home_in_a_template_when_a_dialog_stays_open(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """A ``<tr>`` at the top of a response bound for a ``<div>`` is
        dropped by the parser and its cells spill into the dialog, which
        is what put a stray transaction under the Apply button. Wrapped in
        a template it survives to be swapped where it belongs."""
        page = client.get(REGISTER).text
        loose = txn_id(page, "Mystery charge")
        response = client.post(
            "/transactions/payee",
            data={"transaction_ids": [loose], "merchant_id": merchant},
        )
        one(response.text, "form#similar-offer")
        wrapper = one(response.text, f"template#txn-{loose}-oob")
        assert one(wrapper, f"tr#txn-{loose}").get("hx-swap-oob") == "outerHTML"
        # Nothing loose: every row in the answer is inside its template.
        assert not [
            tr for tr in select(response.text, "tr") if tr.getparent().tag != "template"
        ]

    def test_apply_is_dead_until_there_is_something_to_apply(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """Untick every lookalike and the category and Apply would write
        nothing, so it cannot be pressed."""
        page = client.get(REGISTER).text
        loose = txn_id(page, "Mystery charge")
        offer = one(
            client.post(
                "/transactions/payee",
                data={"transaction_ids": [loose], "merchant_id": merchant},
            ).text,
            "form#similar-offer",
        )
        assert offer.get("@change")
        assert one(offer, 'button[type="submit"]').get(":disabled") == "nothing"

    def test_the_offer_asks_about_the_category_when_the_payee_disagrees(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """A payee filed two ways, or not at all, is the only reason to
        ask. Shell already owns a categorised charge; give it an
        uncategorised one and the payee is arguing with itself."""
        page = client.get(REGISTER).text
        loose = txn_id(page, "Mystery charge")
        response = client.post(
            "/transactions/payee",
            data={"transaction_ids": [loose], "merchant_id": merchant},
        )
        offer = one(response.text, "form#similar-offer")
        assert one(offer, 'input[name="apply_category"][type=checkbox]') is not None
        one(offer, 'select[name="category_id"]')
        assert "not all filed the same way" in text(select(response.text, "p")[0])

    def test_a_settled_payee_is_not_asked_about(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """Re-confirming what is already true is a click to dismiss."""
        page = client.get(REGISTER).text
        response = client.post(
            "/transactions/payee",
            data={"transaction_ids": [txn_id(page, "Shell")], "merchant_id": merchant},
        )
        none(response.text, "form#similar-offer")
        assert "dialog:close" in triggers(response)

    def test_nothing_chosen_is_a_422(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(REGISTER).text
        gas = txn_id(page, "Gas")
        response = client.post("/transactions/payee", data={"transaction_ids": [gas]})
        assert response.status_code == 422
        one(response.text, '[role="alert"]')


class TestBulkTag:
    def test_tags_the_selection(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Gas"), txn_id(page, "Payroll")]
        response = client.post(
            "/transactions/tag", data={"transaction_ids": ids, "name": "audit"}
        )
        for i in ids:
            row = one(response.text, f"tr#txn-{i}")
            assert row.get("hx-swap-oob") == "outerHTML"
            assert text(one(row, ".tag")).startswith("audit")


class TestTheCategoryHalfOfTheFollowUp:
    """Applying the category after a BULK assign.

    The follow-up form carries one checkbox per LOOKALIKE, and lookalikes
    are gathered only after a SINGLE-row assign. So after naming several
    rows at once the form posts back with no ``transaction_ids`` at all -
    just the payee and the category. Reported live 2026-09-11: several ATM
    withdrawals renamed to one payee, the category offered and confirmed,
    and both rows kept the category they already had.
    """

    def test_the_category_lands_on_rows_named_in_bulk(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Mystery charge"), txn_id(page, "Payroll")]
        offer = one(
            client.post(
                "/transactions/payee",
                data={"transaction_ids": ids, "merchant_id": merchant},
            ).text,
            "form#similar-offer",
        )
        # No lookalikes after a bulk assign: the form has a payee and a
        # category and nothing else to send.
        assert not select(offer, 'input[name="transaction_ids"]')
        chosen = one(offer, 'select[name="category_id"]')

        client.post(
            "/transactions/payee",
            data={
                "merchant_id": merchant,
                "apply_category": "true",
                "category_id": chosen.get("value") or _first_option(chosen),
            },
        )

        after = client.get(REGISTER).text
        for txn in ids:
            assert select(after, f"#txn-{txn} select option[selected]"), (
                f"row {txn} came away with no category at all"
            )

    def test_the_rows_it_refiled_come_back_rendered(
        self, client: TestClient, ledger: Ledger, merchant: int
    ) -> None:
        """The write was never the broken half. The follow-up re-files the
        payee's rows and then renders whatever is in ``transaction_ids`` -
        which after a bulk assign is empty, so the reply carried no rows
        and the screen kept showing the categories it had."""
        page = client.get(REGISTER).text
        ids = [txn_id(page, "Mystery charge"), txn_id(page, "Payroll")]
        offer = one(
            client.post(
                "/transactions/payee",
                data={"transaction_ids": ids, "merchant_id": merchant},
            ).text,
            "form#similar-offer",
        )
        carried = [i.get("value") for i in select(offer, 'input[name="named_ids"]')]
        assert sorted(carried) == sorted(str(i) for i in ids), (
            "the offer must carry the rows it was opened on"
        )

        chosen = _first_option(one(offer, 'select[name="category_id"]'))
        applied = client.post(
            "/transactions/payee",
            data={
                "merchant_id": merchant,
                "named_ids": ids,
                "apply_category": "true",
                "category_id": chosen,
            },
        )

        for txn in ids:
            row = one(applied.text, f"template#txn-{txn}-oob")
            assert one(row, "select option[selected]").get("value") == chosen, (
                f"row {txn} came back without the category it was just given"
            )


def _first_option(sel) -> str:  # noqa: ANN001
    return next(o.get("value") for o in sel.findall(".//option") if o.get("value"))
