"""Rename and merge a payee in the UI (#328).

Payee cleanup the API could do, the page can do: a merchant arriving
lowercase or under the wrong brand is renamed once, and two payees for one
merchant are merged - charges, bills and the descriptors it was taught
following the one kept, so the next import files them there too.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import (
    FinanceMerchantAlias,
    FinanceTransactionChangelog,
)
from app.services.finance.service import FinanceService
from tests.web.dom import none, one, table_rows, text, triggers

PAYEES = "/settings/payees"


async def _second_payee(finance: FinanceService, db: AsyncSession) -> int:
    """ "SHELL OIL" on the ledger's grocery charge: a duplicate of Shell."""
    rows, _total = await finance.list_transactions(owner_user_id=None, page_size=50)
    groceries = next(t for t in rows if t.name != "Gas")
    twin = await finance.create_merchant("SHELL OIL", owner_user_id=None)
    assert twin.id is not None and groceries.id is not None
    await finance.assign_merchant([groceries.id], twin.id, owner_user_id=None)
    await db.commit()
    return twin.id


class TestRenamingAPayee:
    def test_each_row_opens_its_edit_dialog(
        self, client: TestClient, hx: TestClient, merchant: int
    ) -> None:
        opener = one(client.get(PAYEES).text, f"#payee-{merchant} [data-edit]")
        assert opener.get("hx-get") == f"{PAYEES}/{merchant}/edit"

        dialog = hx.get(f"{PAYEES}/{merchant}/edit").text
        form = one(dialog, "form[data-rename]")
        assert form.get("hx-post") == f"{PAYEES}/{merchant}/edit"
        assert one(form, 'input[name="name"]').get("value") == "Shell"
        # Nothing else to merge in yet.
        none(dialog, "form[data-merge]")

    def test_a_new_name_shows_on_the_page(
        self, client: TestClient, hx: TestClient, merchant: int
    ) -> None:
        answer = hx.post(f"{PAYEES}/{merchant}/edit", data={"name": "Shell Oil"})

        assert "dialog:close" in triggers(answer)
        names = [
            text(r["Name"])
            for r in table_rows(client.get(PAYEES).text, "#payees table")
        ]
        assert names == ["Shell Oil"]

    def test_a_blank_name_says_why(self, hx: TestClient, merchant: int) -> None:
        answer = hx.post(f"{PAYEES}/{merchant}/edit", data={"name": "  "})

        assert answer.status_code == 422
        assert "A payee needs a name." in answer.text


class TestMergingTwoPayees:
    @pytest.mark.asyncio
    async def test_the_duplicate_folds_in_with_what_it_was_taught(
        self,
        client: TestClient,
        hx: TestClient,
        merchant: int,
        finance: FinanceService,
        async_db_session: AsyncSession,
    ) -> None:
        twin = await _second_payee(finance, async_db_session)
        form = one(hx.get(f"{PAYEES}/{merchant}/edit").text, "form[data-merge]")
        assert form.get("hx-post") == f"{PAYEES}/{merchant}/merge"

        answer = hx.post(f"{PAYEES}/{merchant}/merge", data={"other": str(twin)})

        assert "dialog:close" in triggers(answer)
        rows = table_rows(client.get(PAYEES).text, "#payees table")
        assert [(text(r["Name"]), text(r["Transactions"])) for r in rows] == [
            ("Shell", "2")
        ]
        # The descriptor SHELL OIL was taught names Shell now, so the next
        # import files it there rather than under a payee that is gone.
        taught = (await async_db_session.exec(select(FinanceMerchantAlias))).all()
        assert taught and {a.merchant_id for a in taught} == {merchant}
        # The merge is the payee's action: no charge's payee is logged as
        # an edit, so Undo cannot point one back at the payee merged away.
        logged = (
            await async_db_session.exec(select(FinanceTransactionChangelog))
        ).all()
        assert [r for r in logged if r.field == "merchant_id"] == []

    def test_merging_needs_a_choice(self, hx: TestClient, merchant: int) -> None:
        answer = hx.post(f"{PAYEES}/{merchant}/merge", data={"other": ""})

        assert answer.status_code == 422
        assert "Choose the payee to merge in." in answer.text


class TestIllianaCanProposeIt:
    @pytest.mark.asyncio
    async def test_rename_and_merge_are_cards_by_payee_name(
        self,
        merchant: int,
        finance: FinanceService,
        async_db_session: AsyncSession,
    ) -> None:
        from app.services.finance.domains.writes import executor_for
        from app.services.finance.domains.writes.payees import (
            PayeeMergePayload,
            PayeeRenamePayload,
        )

        twin = await _second_payee(finance, async_db_session)
        rename = executor_for("payee.rename")
        payload = PayeeRenamePayload(payee="shell oil", name="Shell")
        rows = await rename.describe(async_db_session, payload, None)
        assert [(r.label, r.value) for r in rows] == [("Payee", "SHELL OIL → Shell")]

        merge = executor_for("payee.merge")
        done = await merge.execute(
            async_db_session,
            PayeeMergePayload(keep="Shell", merge=["SHELL OIL"]),
            None,
        )

        assert done == {"merchant_id": merchant, "merged": [twin], "moved": 1}
        with pytest.raises(ValueError, match="itself"):
            await merge.execute(
                async_db_session,
                PayeeMergePayload(keep="Shell", merge=["Shell"]),
                None,
            )
