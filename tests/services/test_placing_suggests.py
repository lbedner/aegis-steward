"""Placing a linked account suggests which of yours it is (#404).

Linking SimpleFIN beside Quicken held ten accounts as blank pickers,
though most were obvious: the last four sat in the name, the charges were
the same charges, the names said the same bank. Each is evidence the
dialog now offers, pre-selected, with its reason - never attached on a
guess.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import placing
from app.services.finance.adapters.providers.connections.simplefin_sync.mapping import (
    simplefin_accounts,
)
from app.services.finance.models import FinanceAccount, FinanceConnection
from app.services.finance.service import FinanceService
from tests.services.test_finance_simplefin import SETUP_TOKEN, FakeSimpleFINClient

DAY = date(2026, 9, 10)


def _unix(day: date) -> int:
    import calendar

    return calendar.timegm(day.timetuple())


def _bridge(*accounts: dict[str, Any]) -> dict[str, Any]:
    return {
        "errlist": [],
        "connections": [
            {
                "conn_id": "CON-CHASE",
                "name": "Chase Bank",
                "org_url": "https://www.chase.com",
            }
        ],
        "accounts": [
            {"conn_id": "CON-CHASE", "currency": "USD", **a} for a in accounts
        ],
    }


def _charges(*cents: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"t{i}",
            "posted": _unix(DAY - timedelta(days=i)),
            "amount": f"{amount / 100:.2f}",
            "description": "Charge",
        }
        for i, amount in enumerate(cents)
    ]


CHECKING = {
    "id": "acct-1",
    "name": "TOTAL CHECKING (3639)",
    "balance": "100.00",
    "transactions": _charges(-1200, -500, -4300),
}


async def _yours(db: AsyncSession, name: str, **fields: Any) -> FinanceAccount:
    account = await FinanceService(db).create_manual_account(
        owner_user_id=1, name=name, account_type="checking", classification="asset"
    )
    for field, value in fields.items():
        setattr(account, field, value)
    db.add(account)
    await db.flush()
    return account


async def _connect(db: AsyncSession, payload: dict[str, Any]) -> FinanceConnection:
    await connections.connect_simplefin(
        db,
        owner_user_id=1,
        setup_token=SETUP_TOKEN,
        client=FakeSimpleFINClient(payload),
    )
    from sqlmodel import select

    return (
        await db.exec(
            select(FinanceConnection).where(FinanceConnection.provider == "simplefin")
        )
    ).one()


class TestWhatTheBridgeSays:
    def test_the_last_four_and_the_bank_are_read(self) -> None:
        (account,) = simplefin_accounts(_bridge(CHECKING))

        assert account.mask == "3639"
        assert (account.bank, account.bank_url) == (
            "Chase Bank",
            "https://www.chase.com",
        )

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a bank named, then a link
    async def test_the_last_four_in_the_name_attaches_on_its_own(
        self, async_db_session: AsyncSession
    ) -> None:
        """Even where your account names its bank: a SimpleFIN link knows
        no institution to compare it with."""
        institution = await FinanceService(async_db_session).get_or_create_institution(
            name="Chase", owner_user_id=1
        )
        yours = await _yours(
            async_db_session,
            "TOTAL CHECKING (CHASE)",
            mask="3639",
            institution_id=institution.id,
        )

        connection = await _connect(async_db_session, _bridge(CHECKING))

        await async_db_session.refresh(yours)
        assert yours.connection_id == connection.id
        assert placing.unplaced(connection) == []


class TestTheSuggestion:
    @pytest.mark.asyncio
    async def test_a_held_account_keeps_its_recent_charges(
        self, async_db_session: AsyncSession
    ) -> None:
        await _yours(async_db_session, "Checking")
        unnumbered = {**CHECKING, "name": "TOTAL CHECKING"}

        connection = await _connect(async_db_session, _bridge(unnumbered))

        (held,) = placing.unplaced(connection)
        assert sorted(amount for _day, amount, _name in held["charges"]) == [
            -4300,
            -1200,
            -500,
        ]
        assert held["bank"] == "Chase Bank"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the choice
    async def test_the_account_sharing_its_charges_is_suggested(
        self, async_db_session: AsyncSession
    ) -> None:
        service = FinanceService(async_db_session)
        shared = await _yours(async_db_session, "Everyday")
        await _yours(async_db_session, "Rainy day")
        for i, cents in enumerate((-1200, -500, -4300)):
            await service.create_transaction(
                owner_user_id=1,
                account_id=shared.id,
                amount=cents,
                txn_date=DAY - timedelta(days=i + 1),  # a day apart: still the same
                name="Charge",
                source="qif",
            )
        connection = await _connect(
            async_db_session, _bridge({**CHECKING, "name": "TOTAL CHECKING"})
        )

        offered = await placing.choices(async_db_session, connection)
        suggested = await placing.suggestions(async_db_session, offered)

        (pick,) = suggested.values()
        assert pick.account_id == shared.id
        assert pick.reason == "3 of 3 recent charges match"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the choice
    async def test_a_one_off_charge_outweighs_a_repeating_one(
        self, async_db_session: AsyncSession
    ) -> None:
        """A subscription could sit on any card you have; a one-off charge,
        its amount on its day, is the fingerprint. Which lines matched is
        named, to be checked at a glance."""
        service = FinanceService(async_db_session)
        repeating = await _yours(async_db_session, "Everyday")
        fingerprint = await _yours(async_db_session, "Rainy day")
        # Held, newest first: -1200 -500 -4300 -700 -800 -900.
        cents = (-1200, -500, -4300, -700, -800, -900)
        rows = [
            await service.create_transaction(
                owner_user_id=1,
                account_id=(repeating if i < 3 else fingerprint).id,
                amount=amount,
                txn_date=DAY - timedelta(days=i),
                name="Charge",
                source="qif",
            )
            for i, amount in enumerate(cents)
        ]
        connection = await _connect(
            async_db_session,
            _bridge(
                {**CHECKING, "name": "TOTAL CHECKING", "transactions": _charges(*cents)}
            ),
        )
        # A detected stream (Spotify, Microsoft) - marked after the link,
        # whose sync re-detects streams and would clear a made-up one.
        for row in rows[:4]:
            row.recurring_stream_id = 1
            async_db_session.add(row)
        await async_db_session.flush()

        suggested = await placing.suggestions(
            async_db_session, await placing.choices(async_db_session, connection)
        )

        (pick,) = suggested.values()
        assert pick.account_id == fingerprint.id
        assert pick.reason == "3 of 6 recent charges match, 1 of them repeating"
        assert pick.matched == (3, 4, 5)

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the choice
    async def test_with_no_history_the_names_suggest_one(
        self, async_db_session: AsyncSession
    ) -> None:
        citi = await FinanceService(async_db_session).create_manual_account(
            owner_user_id=1,
            name="Citi Double Cash® Card",
            account_type="credit_card",
            classification="liability",
        )
        await FinanceService(async_db_session).create_manual_account(
            owner_user_id=1,
            name="AMEX",
            account_type="credit_card",
            classification="liability",
        )
        card = {
            "id": "acct-2",
            "name": "Citi Double Cash® Card-1886 (1886)",
            "balance": "-50.00",
            "transactions": [],
        }

        connection = await _connect(async_db_session, _bridge(card))

        suggested = await placing.suggestions(
            async_db_session, await placing.choices(async_db_session, connection)
        )
        (pick,) = suggested.values()
        assert (pick.account_id, pick.reason) == (citi.id, "by name")

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the choice
    async def test_with_nothing_to_go_on_nothing_is_suggested(
        self, async_db_session: AsyncSession
    ) -> None:
        await _yours(async_db_session, "Rainy day")
        connection = await _connect(
            async_db_session,
            _bridge({**CHECKING, "name": "Account", "transactions": []}),
        )

        suggested = await placing.suggestions(
            async_db_session, await placing.choices(async_db_session, connection)
        )
        assert suggested == {}
