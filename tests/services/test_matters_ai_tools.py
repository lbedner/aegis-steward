"""Matter host tools: the case surface Illiana works from.

Each tool opens its own session in production; tests point that at the
transactional test session so seeded rows are visible and rolled back.
"""

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.chat.tools import registered_tool_names
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
import app.services.matters.ai_tools as ai_tools
from app.services.matters.facts import FactService
from app.services.matters.matters import MatterService
from app.services.matters.requests import RequestService
from app.services.matters.service import PartyService

EXPECTED_TOOLS = {"parties", "matters", "requests", "facts"}


@pytest.fixture(autouse=True)
def _tools_use_test_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def test_session():
        yield async_db_session

    monkeypatch.setattr(ai_tools, "get_async_session", test_session)


async def _renewal(db: AsyncSession) -> tuple[int, int]:
    parties = PartyService(db)
    james = await parties.create(name="James Bedner", kind="person")
    county = await parties.create(name="Dutchess County DSS", kind="organization")
    matters = MatterService(db)
    matter = await matters.open(
        title="Medicaid renewal",
        kind="medicaid",
        reference="MA-TOOLS-1",
        subject_party_id=james.id,
        counterpart_party_id=county.id,
    )
    await matters.add_participant(matter.id, james.id, "subject")
    request = await RequestService(db).record(
        matter_id=matter.id,
        due_on=current_date() - timedelta(days=6),
        items=[
            {"asked": "A copy of the power of attorney"},
            {"asked": "Proof of GROSS monthly income"},
        ],
    )
    await db.commit()
    return matter.id, request.id


def test_matter_tools_register_on_import() -> None:
    """Importing the module makes every matter tool grantable by name."""
    assert EXPECTED_TOOLS <= set(registered_tool_names())


async def test_matters_reports_the_case_and_what_is_outstanding(
    async_db_session: AsyncSession,
) -> None:
    matter_id, _request = await _renewal(async_db_session)

    rows = (await ai_tools.matters())["matters"]
    case = next(row for row in rows if row["id"] == matter_id)

    assert case["reference"] == "MA-TOOLS-1"
    assert case["subject"] == "James Bedner"
    assert case["requests"]["open"] == 1
    # Overdue is read off the clock here too, so the tool and the page
    # cannot disagree about whether a deadline has passed.
    assert case["requests"]["overdue_count"] == 1


async def test_requests_says_what_is_answered_and_what_is_missing(
    async_db_session: AsyncSession,
) -> None:
    matter_id, request_id = await _renewal(async_db_session)
    service = RequestService(async_db_session)
    items = await service.items(request_id)
    await service.attach(items[0].id, 99, "Power of attorney.pdf")
    await async_db_session.commit()

    rows = (await ai_tools.requests(matter_id=matter_id))["requests"]

    assert len(rows) == 1
    assert rows[0]["overdue"] is True
    assert rows[0]["settled"] == 1
    answered = {item["asked"]: item["document_id"] for item in rows[0]["items"]}
    assert answered["A copy of the power of attorney"] == 99
    assert answered["Proof of GROSS monthly income"] is None


async def test_facts_carry_their_source_and_the_rate_read_as_a_month(
    async_db_session: AsyncSession,
) -> None:
    """A figure without its provenance is not fit for a government form,
    and a daily rate is not a monthly answer until somebody multiplies."""
    matter_id, _request = await _renewal(async_db_session)
    subject = (await ai_tools.parties())["parties"]
    james = next(p for p in subject if p["name"] == "James Bedner")
    await FactService(async_db_session).record(
        subject_party_id=james["id"],
        matter_id=matter_id,
        attribute="gross_income",
        label="IBEW pension",
        value_cents=5000,
        period="day",
        provenance="stated",
        source_note="Read off the pension portal",
    )
    await async_db_session.commit()

    rows = (await ai_tools.facts(matter_id=matter_id))["facts"]

    assert rows[0]["value_cents"] == 5000
    assert rows[0]["period"] == "day"
    assert rows[0]["monthly_cents"] == 152188
    assert rows[0]["provenance"] == "stated"
    assert rows[0]["source_note"] == "Read off the pension portal"
    assert rows[0]["verified"] is False


class TestWhoseMoneyThroughIlliana:
    """She can be asked about a parent's account. She is not handed it
    when the question was about ours."""

    @staticmethod
    async def _their_account(db: AsyncSession, name: str) -> tuple[int, int]:
        from app.services.finance.domains.ledger.accounts import create_manual_account
        from app.services.finance.domains.ledger.subjects import (
            assign_subject,
            subject_for_party,
        )

        party = await PartyService(db).create(name=name, kind="person")
        subject = await subject_for_party(db, party.id, name=name)
        await create_manual_account(
            db, name="Ours Checking", account_type="checking", classification="asset"
        )
        theirs = await create_manual_account(
            db, name="Their Pension", account_type="other_asset", classification="asset"
        )
        await assign_subject(db, theirs.id, subject.id)
        await db.commit()
        return party.id, subject.id

    @pytest.mark.asyncio
    async def test_the_default_answer_is_our_money(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import asynccontextmanager

        import app.services.finance.ai_account_tools as account_tools
        import app.services.finance.ai_tools as finance_tools

        @asynccontextmanager
        async def test_session():
            yield async_db_session

        monkeypatch.setattr(finance_tools, "get_async_session", test_session)
        monkeypatch.setattr(account_tools, "get_async_session", test_session)
        _party_id, subject_id = await self._their_account(
            async_db_session, "Tool Whose One"
        )

        ours = await account_tools.accounts()
        theirs = await account_tools.accounts(whose=str(subject_id))
        everybody = await account_tools.accounts(whose="all")

        assert "Their Pension" not in [a["name"] for a in ours["accounts"]]
        assert [a["name"] for a in theirs["accounts"]] == ["Their Pension"]
        assert "Their Pension" in [a["name"] for a in everybody["accounts"]]

    @pytest.mark.asyncio
    async def test_a_proposal_can_put_an_account_in_someones_name(
        self, async_db_session: AsyncSession
    ) -> None:
        """Working a matter adds change types, not tools: the account is
        proposed like every other write and approved by a person."""
        from app.services.finance.domains.writes.accounts import (
            CreateAccountPayload,
            create_account_describe,
            create_account_execute,
        )

        party = await PartyService(async_db_session).create(
            name="Tool Whose Two", kind="person"
        )
        await async_db_session.commit()
        payload = CreateAccountPayload(
            name="Tool NYSLRS Pension",
            account_type="other_asset",
            whose_party_id=party.id,
        )

        card = await create_account_describe(async_db_session, payload, None)
        result = await create_account_execute(async_db_session, payload, None)
        await async_db_session.commit()

        # The card says whose, because "add an account" and "add an
        # account that is not ours" are different approvals.
        assert any("Tool Whose Two" in row.value for row in card)
        assert result["whose"] == "Tool Whose Two"
        ours, _ = await FinanceService(async_db_session).list_accounts()
        assert "Tool NYSLRS Pension" not in [a.name for a in ours]
