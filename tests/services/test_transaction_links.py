"""A transaction filed against a matter or a contact (#290, #303).

"I don't have a tool to link it directly to a specific matter" - the
$1,500 root canal charge belonged on "Marisa's Root Canals", and the
dentist's charges with the dentist's record. Transactions are filed by
the labels documents already are (``matter_tag``, ``party_tag``), many
to many, and can be unfiled.
"""

from datetime import date

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import links
from app.services.finance.domains.writes.linking import (
    LinkTransactionPayload,
    link_transaction_describe,
    link_transaction_execute,
)
from app.services.finance.service import FinanceService
from app.services.matters.matters import MatterService
from app.services.matters.models import matter_tag, party_tag
from app.services.matters.service import PartyService
from tests._session import opens
from tests.services._finance_factories import seed_account, seed_txn

# Each test files a few charges one at a time, on purpose.
pytestmark = pytest.mark.queryspy(threshold=4)


async def _charge(svc: FinanceService, cents: int = 150_000) -> int:
    account = await seed_account(svc, name="AMEX", account_type="credit_card")
    txn = await seed_txn(
        svc, account.id, -cents, date(2026, 9, 30), name="BLONDIN ENDODONTICS"
    )
    assert txn.id is not None
    return txn.id


class TestFiling:
    @pytest.mark.asyncio
    async def test_a_charge_can_belong_to_two_cases_and_leave_one(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        charge = await _charge(svc)
        care, claim = matter_tag(1), matter_tag(2)

        assert await links.link(async_db_session, charge, care) is True
        assert await links.link(async_db_session, charge, care) is False  # once
        await links.link(async_db_session, charge, claim)
        await links.unlink(async_db_session, charge, claim)

        filed = await links.linked(async_db_session, [care, claim])
        assert [t.id for t in filed[care]] == [charge]
        assert filed[claim] == []


class TestTheCard:
    @pytest.mark.asyncio
    async def test_it_files_the_charge_with_the_case(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        charge = await _charge(svc)
        matter = await MatterService(async_db_session).open(
            title="Marisa's Root Canals"
        )
        payload = LinkTransactionPayload(
            transaction_id=charge, matter_id=int(matter.id or 0)
        )

        said = {
            r.label: r.value
            for r in await link_transaction_describe(async_db_session, payload, None)
        }
        await link_transaction_execute(async_db_session, payload, None)

        assert said["Link to"] == "Marisa's Root Canals"
        assert "BLONDIN ENDODONTICS" in said["Transaction"]
        filed = await links.linked(async_db_session, [matter_tag(int(matter.id or 0))])
        assert [t.id for t in filed[matter_tag(int(matter.id or 0))]] == [charge]

    @pytest.mark.asyncio
    async def test_unlinking_takes_it_back_off(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        charge = await _charge(svc)
        dentist = await PartyService(async_db_session).create(
            name="Magdalena Goralczyk, DDS", kind="organization"
        )
        place = {"transaction_id": charge, "party_id": int(dentist.id or 0)}
        await link_transaction_execute(
            async_db_session, LinkTransactionPayload(**place), None
        )

        unlink = LinkTransactionPayload(**place, unlink=True)
        said = {
            r.label: r.value
            for r in await link_transaction_describe(async_db_session, unlink, None)
        }
        await link_transaction_execute(async_db_session, unlink, None)

        assert "Unlink from" in said
        tag = party_tag(int(dentist.id or 0))
        assert (await links.linked(async_db_session, [tag]))[tag] == []

    def test_it_goes_to_exactly_one_place(self) -> None:
        with pytest.raises(ValidationError):
            LinkTransactionPayload(transaction_id=1)
        with pytest.raises(ValidationError):
            LinkTransactionPayload(transaction_id=1, matter_id=1, party_id=2)


class TestHerReads:
    @pytest.fixture(autouse=True)
    def _her_session(
        self, monkeypatch: pytest.MonkeyPatch, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters import ai_tools

        monkeypatch.setattr(ai_tools, "get_async_session", opens(async_db_session))

    @pytest.mark.asyncio
    async def test_a_case_says_what_it_cost(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters import ai_tools

        charge = await _charge(svc, 150_000)
        other = await _charge(svc, 30_000)
        matter = await MatterService(async_db_session).open(title="Root canals")
        for txn in (charge, other):
            await links.link(async_db_session, txn, matter_tag(int(matter.id or 0)))
        await async_db_session.commit()

        found = next(
            m for m in (await ai_tools.matters())["matters"] if m["id"] == matter.id
        )

        assert found["transactions"]["total_cents"] == -180_000
        assert {t["id"] for t in found["transactions"]["items"]} == {charge, other}

    @pytest.mark.asyncio
    async def test_a_contact_says_what_was_paid_them(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters import ai_tools

        charge = await _charge(svc, 18_500)
        dentist = await PartyService(async_db_session).create(
            name="Magdalena Goralczyk, DDS", kind="organization"
        )
        await links.link(async_db_session, charge, party_tag(int(dentist.id or 0)))
        await async_db_session.commit()

        found = next(
            p for p in (await ai_tools.parties())["parties"] if p["id"] == dentist.id
        )

        assert found["transactions"]["total_cents"] == -18_500
