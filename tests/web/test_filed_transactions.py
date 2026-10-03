"""What a case cost, and what was paid a contact (#290, #303).

A transaction filed with a matter or a contact (``transaction.link``)
shows on its page, with the total. Both render paths.

The matters and contacts pages read through their own sessions (the
database app code opens itself, which lives for the whole run), so the
case, the contact and the charges are seeded there and removed after.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlmodel import col, delete

from app.core.db import get_async_session
from app.services.finance.domains.ledger import links
from app.services.finance.models import (
    FinanceAccount,
    FinanceTransaction,
    FinanceTransactionLink,
)
from app.services.finance.service import FinanceService
from app.services.matters.matters import MatterService
from app.services.matters.models import Matter, Party, matter_tag, party_tag
from app.services.matters.service import PartyService
from tests.web.dom import none, one, select, text

# The charges are filed one at a time, on purpose.
pytestmark = pytest.mark.queryspy(threshold=4)


@dataclass(frozen=True)
class Filed:
    matter_id: int
    party_id: int


@pytest.fixture
async def filed() -> AsyncIterator[Filed]:
    """Two charges on the root canal case, the first also the dentist's."""
    async with get_async_session() as db:
        finance = FinanceService(db)
        account = await finance.create_manual_account(
            name="AMEX filed", account_type="credit_card", classification="liability"
        )
        charges = [
            await finance.create_transaction(
                account_id=int(account.id or 0),
                amount=-cents,
                txn_date=date(2026, 9, 30),
                name="BLONDIN ENDODONTICS",
            )
            for cents in (150_000, 30_000)
        ]
        matter = await MatterService(db).open(title="Marisa's Root Canals")
        dentist = await PartyService(db).create(
            name="Blondin Endodontics", kind="organization"
        )
        for charge in charges:
            await links.link(db, int(charge.id or 0), matter_tag(int(matter.id or 0)))
        await links.link(db, int(charges[0].id or 0), party_tag(int(dentist.id or 0)))
        await db.commit()
        ids = [int(c.id or 0) for c in charges]
        seeded = Filed(int(matter.id or 0), int(dentist.id or 0))
        account_id = int(account.id or 0)
    yield seeded
    async with get_async_session() as db:
        for statement in (
            delete(FinanceTransactionLink).where(
                col(FinanceTransactionLink.transaction_id).in_(ids)
            ),
            delete(FinanceTransaction).where(col(FinanceTransaction.id).in_(ids)),
            delete(FinanceAccount).where(col(FinanceAccount.id) == account_id),
            delete(Matter).where(col(Matter.id) == seeded.matter_id),
            delete(Party).where(col(Party.id) == seeded.party_id),
        ):
            await db.exec(statement)
        await db.commit()


def test_a_case_shows_what_it_cost(
    client: TestClient, hx: TestClient, filed: Filed
) -> None:
    card = one(client.get(f"/matters/{filed.matter_id}").text, "#matter-spent")

    assert len(select(card, "tbody tr")) == 2
    assert text(one(card, "[data-total]")) == "-$1,800.00"
    fragment = hx.get(f"/matters/{filed.matter_id}").text
    none(fragment, "html")
    one(fragment, "#matter-spent")


def test_a_contact_shows_what_was_paid_them(client: TestClient, filed: Filed) -> None:
    card = one(client.get(f"/contacts/{filed.party_id}").text, "#contact-paid")

    assert len(select(card, "tbody tr")) == 1
    assert text(one(card, "[data-total]")) == "-$1,500.00"
