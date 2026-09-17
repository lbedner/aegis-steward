"""Illiana can say a bill or an income exists: ``recurring.declare``.

She could match a payment to a stream that existed and could not say a
new one did - "call it $90 a week and be done with it" died on an
unknown change type. Same act as the Bills page's declare, as a card.
"""

from datetime import date

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.structure import (
    DeclarePayload,
    declare_describe,
    declare_execute,
)
from app.services.finance.service import FinanceService


async def test_the_card_says_what_which_way_how_often_and_the_stream_lands(
    async_db_session: AsyncSession,
) -> None:
    payload = DeclarePayload(
        name="Marisa side work",
        direction="inflow",
        frequency="weekly",
        amount_cents=9000,
        next_expected_date=date(2026, 9, 22),
    )

    said = {
        r.label: r.value
        for r in await declare_describe(async_db_session, payload, None)
    }
    assert said["Stream"] == "Marisa side work"
    assert said["Direction"] == "Income"
    assert said["Every"] == "Weekly"
    assert said["Amount"] == "$90.00"
    assert said["Starting"] == "2026-09-22"

    result = await declare_execute(async_db_session, payload, None)
    await async_db_session.commit()

    stream = await FinanceService(async_db_session).get_recurring(
        result["stream_id"], None
    )
    assert stream is not None
    assert (stream.direction, stream.frequency, stream.expected_amount) == (
        "inflow",
        "weekly",
        9000,
    )
    assert stream.next_expected_date == date(2026, 9, 22)


def test_a_direction_or_rhythm_nobody_defined_is_refused() -> None:
    with pytest.raises(ValidationError):
        DeclarePayload(
            name="x",
            direction="sideways",
            frequency="weekly",
            amount_cents=1,
            next_expected_date=date(2026, 9, 22),
        )
    with pytest.raises(ValidationError):
        DeclarePayload(
            name="x",
            direction="inflow",
            frequency="whenever",
            amount_cents=1,
            next_expected_date=date(2026, 9, 22),
        )
    with pytest.raises(ValidationError):
        DeclarePayload(
            name="x",
            direction="inflow",
            frequency="weekly",
            amount_cents=0,
            next_expected_date=date(2026, 9, 22),
        )


async def test_the_account_and_category_ride_the_card_and_the_stream(
    async_db_session: AsyncSession,
) -> None:
    """ "I need to specify the account and category": both are named on
    the card by name, and both land on the stream."""
    from tests.services._finance_factories import seed_account, seed_category

    svc = FinanceService(async_db_session)
    account = await seed_account(svc, "TOTAL CHECKING")
    category = await seed_category(async_db_session, "Income:Side work")
    payload = DeclarePayload(
        name="Marisa side work",
        direction="inflow",
        frequency="weekly",
        amount_cents=9000,
        next_expected_date=date(2026, 9, 22),
        account_id=account.id,
        category_id=category.id,
    )

    said = {
        r.label: r.value
        for r in await declare_describe(async_db_session, payload, None)
    }
    assert said["Account"] == "TOTAL CHECKING"
    assert said["Category"] == "Income:Side work"

    result = await declare_execute(async_db_session, payload, None)
    await async_db_session.commit()
    stream = await svc.get_recurring(result["stream_id"], None)
    assert (stream.account_id, stream.category_id) == (account.id, category.id)


async def test_a_declared_stream_can_be_sent_to_its_account_later(
    async_db_session: AsyncSession,
) -> None:
    """ "Can you make it go into the chase checking account" had no
    change type: the stream stood without an account and Illiana kept a
    memory instead. recurring.amend sets what is given, nothing else."""
    from app.services.finance.domains.writes.structure import (
        AmendPayload,
        amend_describe,
        amend_execute,
    )
    from tests.services._finance_factories import seed_account

    finance = FinanceService(async_db_session)
    checking = await seed_account(finance, name="TOTAL CHECKING")
    made = await declare_execute(
        async_db_session,
        DeclarePayload(
            name="Marisa side work",
            direction="inflow",
            frequency="weekly",
            amount_cents=9000,
            next_expected_date=date(2026, 9, 22),
        ),
        None,
    )
    payload = AmendPayload(stream_id=made["stream_id"], account_id=int(checking.id))

    said = {
        r.label: r.value for r in await amend_describe(async_db_session, payload, None)
    }
    assert said == {"Stream": "Marisa side work", "Account": "TOTAL CHECKING"}

    await amend_execute(async_db_session, payload, None)
    await async_db_session.commit()
    stream = await finance.get_recurring(made["stream_id"], None)
    assert stream.account_id == checking.id
    assert (stream.frequency, stream.expected_amount) == ("weekly", 9000)


async def test_whose_it_is_is_a_contact_not_a_ledger_id(
    async_db_session: AsyncSession,
) -> None:
    """Illiana sent Marisa's contact id where a ledger subject id went,
    and the approval died on a foreign key. The payload names a CONTACT,
    the way account.create does, and the subject is found or made."""
    from app.services.finance.domains.ledger.subjects import subject_of
    from app.services.matters.service import PartyService

    marisa = await PartyService(async_db_session).create(
        name="Marisa Testcase", kind="person"
    )
    payload = DeclarePayload(
        name="Delta Dental premium",
        direction="outflow",
        frequency="monthly",
        amount_cents=11626,
        next_expected_date=date(2026, 10, 1),
        whose_party_id=int(marisa.id),
    )
    said = {
        r.label: r.value
        for r in await declare_describe(async_db_session, payload, None)
    }
    assert said["Whose"] == "Marisa Testcase"

    made = await declare_execute(async_db_session, payload, None)
    await async_db_session.commit()
    stream = await FinanceService(async_db_session).get_recurring(
        made["stream_id"], None
    )
    subject = await subject_of(async_db_session, int(marisa.id))
    assert subject is not None and stream.subject_id == subject.id


def test_a_ledger_subject_id_is_not_a_field() -> None:
    with pytest.raises(ValidationError):
        DeclarePayload.model_validate(
            {
                "name": "x",
                "direction": "outflow",
                "frequency": "monthly",
                "amount_cents": 1,
                "next_expected_date": "2026-10-01",
                "subject_id": 6,
            }
        )
