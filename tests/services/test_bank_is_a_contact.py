"""Every account sits under its bank, and the bank is a contact (#410).

The layer was there - account -> institution -> contact - and mostly
empty: 13 of 21 accounts had no institution, three institutions had no
contact, none had a domain, and SimpleFIN's org name and URL for every
bank it links were read for a logo and dropped. The reader files paper
by a contact's website or phone, so an empty org level is why the M1
statement sat unfiled (#409).

Now an institution is never made without its contact, a link puts each
account under the bank that reported it, and the bank's URL lands where
the logo and the reader look for it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import placing
from app.services.finance.domains.ledger import institutions
from app.services.finance.models import FinanceAccount, FinanceInstitution
from app.services.finance.service import FinanceService
from app.services.matters.service import PartyService
from tests.services.test_finance_simplefin import FakeSimpleFINClient, _connect


def _bridge() -> dict[str, Any]:
    """Two banks behind one SimpleFIN link, as v2 reports them."""
    return {
        "errlist": [],
        "connections": [
            {"conn_id": "C1", "name": "Chase Bank", "org_url": "https://www.chase.com"},
            {"conn_id": "C2", "name": "M1 Finance", "org_url": "https://m1.com"},
        ],
        "accounts": [
            {
                "id": "acct-chk",
                "conn_id": "C1",
                "name": "TOTAL CHECKING (3639)",
                "currency": "USD",
                "balance": "100.00",
                "transactions": [],
            },
            {
                "id": "acct-sav",
                "conn_id": "C1",
                "name": "SAVINGS (0001)",
                "currency": "USD",
                "balance": "50.00",
                "transactions": [],
            },
            {
                "id": "acct-inv",
                "conn_id": "C2",
                "name": "Invest",
                "currency": "USD",
                "balance": "20000.00",
                "transactions": [],
            },
        ],
    }


async def _organizations(db: AsyncSession) -> dict[str, Any]:
    return {p.name: p for p in await PartyService(db).find(kind="organization")}


class TestABankIsAContact:
    @pytest.mark.asyncio
    async def test_naming_a_bank_makes_its_contact_with_it(
        self, async_db_session: AsyncSession
    ) -> None:
        bank = await FinanceService(async_db_session).get_or_create_institution(
            name="Fidelity", owner_user_id=1, url="https://www.fidelity.com"
        )

        contact = (await _organizations(async_db_session))["Fidelity"]
        assert bank.party_id == contact.id
        # The homepage lives on the contact, and only there (#412).
        assert contact.contact == {"website": "https://www.fidelity.com"}

    @pytest.mark.asyncio
    async def test_a_contact_already_named_is_the_one(
        self, async_db_session: AsyncSession
    ) -> None:
        """The address book had Chase before the ledger did: one body."""
        party = await PartyService(async_db_session).create(
            name="Chase Bank", kind="organization", owner_user_id=1
        )

        bank = await FinanceService(async_db_session).get_or_create_institution(
            name="chase bank", owner_user_id=1
        )

        assert bank.party_id == party.id
        assert len(await _organizations(async_db_session)) == 1

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two passes
    async def test_a_bank_without_a_contact_gets_one_when_asked(
        self, async_db_session: AsyncSession
    ) -> None:
        """The one-time pass over what is already there (#410): the three
        institutions made before this rule."""
        orphan = FinanceInstitution(
            owner_user_id=1, provider="manual", name="HVCU", normalized_name="HVCU"
        )
        async_db_session.add(orphan)
        await async_db_session.flush()

        made = await institutions.ensure_contacts(async_db_session, owner_user_id=1)
        again = await institutions.ensure_contacts(async_db_session, owner_user_id=1)

        await async_db_session.refresh(orphan)
        assert (made, again) == (1, 0)
        assert orphan.party_id == (await _organizations(async_db_session))["HVCU"].id


class TestALinkPutsEachAccountUnderItsBank:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the directory
    async def test_accounts_sit_under_the_bank_that_reported_them(
        self, async_db_session: AsyncSession
    ) -> None:
        await _connect(async_db_session, FakeSimpleFINClient(_bridge()))

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        banks = {
            b.id: b
            for b in await FinanceService(async_db_session).list_institutions(
                owner_user_id=1
            )
        }
        under = {a.name: banks[a.institution_id].name for a in accounts}
        assert under == {
            "TOTAL CHECKING (3639)": "Chase Bank",
            "SAVINGS (0001)": "Chase Bank",
            "Invest": "M1 Finance",
        }
        # One row per bank, each with its contact and the homepage on it.
        sites = await institutions.websites(async_db_session, list(banks))
        assert sorted((b.name, sites.get(b.id)) for b in banks.values()) == [
            ("Chase Bank", "https://www.chase.com"),
            ("M1 Finance", "https://m1.com"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then a place
    async def test_a_placed_account_takes_the_bank_it_had_none_of(
        self, async_db_session: AsyncSession
    ) -> None:
        """Yours, from a Quicken export with no bank named: placing the
        bank's account on it says which bank it is at."""
        yours = await FinanceService(async_db_session).create_manual_account(
            owner_user_id=1,
            name="Brokerage",
            account_type="brokerage",
            classification="asset",
        )
        connection = await _connect(async_db_session, FakeSimpleFINClient(_bridge()))
        (held,) = [h for h in placing.unplaced(connection) if h["name"] == "Invest"]
        assert held["institution_id"] is not None

        assert not await placing.place(
            async_db_session, connection, {held["id"]: str(yours.id)}
        )

        await async_db_session.refresh(yours)
        bank = await async_db_session.get(FinanceInstitution, yours.institution_id)
        assert bank is not None and bank.name == "M1 Finance"


class TestOneBankNamedTwoWays:
    """Live, 2026-10-06: SimpleFIN said "Chase Bank", "Citizens Bank" and
    "M1" where the ledger said "JPMorgan Chase Bank, N.A.", "Citizens" and
    "M1 Finance", and the sync made a second row and contact for each."""

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=5)  # two syncs, two looks at the banks
    async def test_the_bank_its_accounts_already_sit_under_is_the_one(
        self, async_db_session: AsyncSession
    ) -> None:
        connection = await _connect(async_db_session, FakeSimpleFINClient(_bridge()))
        mine = next(
            b
            for b in await FinanceService(async_db_session).list_institutions(
                owner_user_id=1
            )
            if b.name == "M1 Finance"
        )
        # Named your way, with nothing on file to reach them by yet.
        mine.name, mine.normalized_name = "M1 Holdings", "M1 HOLDINGS"
        party = await PartyService(async_db_session).get(mine.party_id)
        assert party is not None
        party.contact = None
        async_db_session.add_all([mine, party])
        await async_db_session.flush()

        await connections.sync_simplefin_connection(
            async_db_session, connection, client=FakeSimpleFINClient(_bridge())
        )

        names = [
            b.name
            for b in await FinanceService(async_db_session).list_institutions(
                owner_user_id=1
            )
        ]
        assert sorted(names) == ["Chase Bank", "M1 Holdings"]
        # What the bank says of itself lands on YOUR bank's contact.
        assert party.contact == {"website": "https://m1.com"}

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a bank named, then a link
    async def test_a_bank_with_the_same_website_is_the_one(
        self, async_db_session: AsyncSession
    ) -> None:
        mine = await FinanceService(async_db_session).get_or_create_institution(
            name="JPMorgan Chase Bank, N.A.",
            owner_user_id=1,
            url="https://www.chase.com/personal",
        )

        await _connect(async_db_session, FakeSimpleFINClient(_bridge()))

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        checking = next(a for a in accounts if a.name == "TOTAL CHECKING (3639)")
        assert checking.institution_id == mine.id
        names = [
            b.name
            for b in await FinanceService(async_db_session).list_institutions(
                owner_user_id=1
            )
        ]
        assert "Chase Bank" not in names


class TestTheWebsiteLivesOnTheContact:
    """One home (#412): a website set on the contact - by a card in chat,
    the Contacts page or a sync - is the bank's logo and link with
    nothing else done. It used to sit on the bank row too, and a card
    that set the contact's left the accounts without a logo."""

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the logo, then the websites read directly
    async def test_a_website_on_the_contact_is_the_banks_logo(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.finance.domains.ledger import merchant_icon

        bank = await FinanceService(async_db_session).get_or_create_institution(
            name="HVCU", owner_user_id=1
        )
        assert bank.party_id is not None
        # What approving Illiana's contact.amend card does.
        await PartyService(async_db_session).update(
            bank.party_id, {"contact": {"website": "https://www.hvcu.org"}}
        )
        asked: list[dict[str, str]] = []

        async def _keys(_db: Any, names: list[str], overrides: dict[str, str]) -> dict:
            asked.append(overrides)
            return {}

        monkeypatch.setattr(merchant_icon, "resolve_icon_keys", _keys)

        await merchant_icon.institution_icons(async_db_session, [bank])

        assert asked == [{"HVCU": "hvcu.org"}]
        assert await institutions.websites(async_db_session, [bank.id]) == {
            bank.id: "https://www.hvcu.org"
        }


class TestNothingIsLostOnTheWay:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a bank named, then a link
    async def test_a_bank_someone_picked_is_kept(
        self, async_db_session: AsyncSession
    ) -> None:
        """A kept account at the bank you named stays at it: the link's
        bank only fills a blank."""
        mine = await FinanceService(async_db_session).get_or_create_institution(
            name="Chase", owner_user_id=1
        )
        account = await FinanceService(async_db_session).create_manual_account(
            owner_user_id=1,
            name="TOTAL CHECKING (CHASE)",
            account_type="checking",
            classification="asset",
        )
        account.mask, account.institution_id = "3639", mine.id
        async_db_session.add(account)
        await async_db_session.flush()

        await _connect(async_db_session, FakeSimpleFINClient(_bridge()))

        await async_db_session.refresh(account)
        assert account.institution_id == mine.id
        assert isinstance(account, FinanceAccount)
