"""A statement's own figure, read off the line that prints it.

The county asked for a balance as of 1 August. The register could offer
what it believed and say plainly that nothing proved it; the statement
that WOULD prove it sat on the shelf, read, with the number on page one
and nobody reading it (2026-09-19).

Labelled lines, the way every other reading here works: "New balance as
of 09/07/26: $7,857.27" is a figure, a date and a label, and a statement
prints it the same way every month.
"""

from datetime import date

import pytest

from app.services.documents.domains.reading.figures import balances


def _pages(*lines: str) -> list[dict]:
    return [{"page": 1, "text": "\n".join(lines)}]


class TestWhatAStatementSaysItHolds:
    def test_a_labelled_balance_with_its_own_date(self) -> None:
        found = balances(_pages("New balance as of 09/07/26: $7,857.27"))
        assert [(one.value_cents, one.as_of) for one in found] == [
            (785727, date(2026, 9, 7))
        ]
        assert found[0].page == 1
        assert found[0].because == "New balance as of 09/07/26: $7,857.27"

    def test_the_labels_a_statement_actually_prints(self) -> None:
        for line, cents in (
            ("New Balance $45,787.53", 4578753),
            ("Ending balance: $3,137.44", 313744),
            ("Closing Balance 1,204.00", 120400),
            ("Current balance $0.00", 0),
            ("Statement balance: $912.10", 91210),
        ):
            found = balances(_pages(line))
            assert [one.value_cents for one in found] == [cents], line

    def test_money_that_is_not_a_balance(self) -> None:
        """A statement is full of money. Only the lines that say they
        are a balance are one - a minimum payment read as a balance is a
        wrong number on a benefits form."""
        for line in (
            "Minimum payment due: $224.57",
            "Payments and credits -$230.00",
            "Late fee $29.00",
            "Credit limit $34,600",
            "Purchases +$0.00",
        ):
            assert balances(_pages(line)) == [], line

    def test_a_line_with_no_money_is_not_a_figure(self) -> None:
        assert balances(_pages("New balance as of 09/07/26:")) == []

    def test_only_the_front_of_the_document(self) -> None:
        pages = [{"page": n, "text": "x"} for n in (1, 2)]
        pages.append({"page": 3, "text": "New balance $10.00"})
        assert balances(pages) == []

    def test_the_first_one_wins(self) -> None:
        """A statement repeats its balance in the summary and again in
        the detail. One figure, read once, from where it is first
        printed."""
        found = balances(
            _pages("New balance: $7,857.27", "New balance as of 09/07/26: $7,857.27")
        )
        assert len(found) == 1
        assert found[0].because == "New balance: $7,857.27"


class TestItReachesTheQueue:
    """A figure read is a figure PROPOSED: nothing here writes, and the
    card carries the page and the line so the reading can be checked
    against the paper rather than taken on faith."""

    async def _statement(self, db, *lines: str):
        from app.services.documents.models import Document, DocumentPage

        text = "\n".join(lines)
        document = Document(
            title="statement-9.pdf",
            filename="statement-9.pdf",
            storage_key=f"testcase/{abs(hash(text))}.pdf",
            media_type="application/pdf",
            content_hash=f"testcase-fig-{abs(hash(text))}",
            size_bytes=9,
        )
        db.add(document)
        await db.flush()
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text=text,
            )
        )
        await db.flush()
        return document

    async def _held_account(self, db, mask: str, whose: str):
        from app.services.finance.domains.ledger.subjects import (
            assign_subject,
            subject_for_party,
        )
        from app.services.finance.service import FinanceService
        from app.services.matters.service import PartyService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(db), name="CLASSIC CHECKING")
        account.mask = mask
        db.add(account)
        party = await PartyService(db).create(name=whose, kind="person")
        await db.flush()
        subject = await subject_for_party(db, party.id, name=party.name)
        await assign_subject(db, account.id, subject.id)
        await db.flush()
        return account, party

    @pytest.mark.asyncio
    async def test_a_statement_for_somebodys_account_proposes_its_balance(
        self, async_db_session
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.finance.domains.writes.queue import list_changes

        account, party = await self._held_account(
            async_db_session, "3639", "James Testcase"
        )
        document = await self._statement(
            async_db_session,
            "HUDSON VALLEY CREDIT UNION",
            "Account ending 3639",
            "Ending balance as of 08/01/26: $3,137.44",
        )

        await propose_reading(async_db_session, document.id)

        figures = [
            change
            for change in await list_changes(async_db_session, status="pending")
            if change.change_type == "fact.record"
        ]
        assert len(figures) == 1
        said = figures[0].payload
        assert said["value_cents"] == 313744
        assert said["attribute"] == "account_balance"
        assert said["subject_party_id"] == party.id
        assert said["account_id"] == account.id
        assert said["as_of"] == "2026-08-01"
        # Cited, the way a figure has to be: provenance document, the
        # page, and the line it was read on.
        assert said["provenance"] == "document"
        assert said["document_id"] == document.id
        assert said["page"] == 1
        assert "3,137.44" in said["source_note"]

    @pytest.mark.asyncio
    async def test_a_statement_for_our_own_account_proposes_nothing(
        self, async_db_session
    ) -> None:
        """A fact is about SOMEBODY. The household's own accounts have no
        party to be about, and a card asking whose this is would be a
        question rather than a proposal."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.finance.domains.writes.queue import list_changes
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(async_db_session), name="Ours")
        account.mask = "1164"
        async_db_session.add(account)
        await async_db_session.flush()
        document = await self._statement(
            async_db_session, "Account ending 1164", "New balance: $10.00"
        )

        await propose_reading(async_db_session, document.id)

        assert not [
            change
            for change in await list_changes(async_db_session, status="pending")
            if change.change_type == "fact.record"
        ]

    @pytest.mark.asyncio
    async def test_a_statement_nobody_can_place_proposes_nothing(
        self, async_db_session
    ) -> None:
        """Without the account, the figure is a number with nobody to be
        about and nothing to be the balance OF."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.finance.domains.writes.queue import list_changes

        document = await self._statement(
            async_db_session, "New balance as of 08/01/26: $3,137.44"
        )

        await propose_reading(async_db_session, document.id)

        assert not [
            change
            for change in await list_changes(async_db_session, status="pending")
            if change.change_type == "fact.record"
        ]
