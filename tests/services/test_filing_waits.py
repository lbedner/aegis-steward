"""A document arriving before its account is known still gets filed (#409).

The M1 statement printed "Account: M1 Individual Brokerage Account" and
"Account number: XXXX2774"; the household had that account with no last
four, so the reader - which files only by what is on file - filed it
under nothing, and nothing ever tried again. Three parts, cheapest
first: the paper supplies the last four, a new fact re-reads the
unfiled pile, and a nightly sweep is the net.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import filing, propose_reading
from app.services.documents.models import DocumentPage
from app.services.documents.service import DocumentService
from app.services.finance.constants import account_tag
from app.services.finance.domains.ledger import numbers
from app.services.finance.domains.writes.queue import list_changes
from app.services.finance.models import FinanceAccount
from app.services.finance.service import FinanceService
from tests._session import opens

M1_FRONT = """Sophisticated wealth building, simplified.
Statement
Statement period: 08/01/2026 to 08/31/2026
Account number: XXXX2774
Account: M1 Individual Brokerage Account
Type: Margin
"""


async def _paper(db: AsyncSession, title: str, *texts: str) -> int:
    document = await DocumentService(db).ingest(
        b"%PDF-1.4 " + title.encode(), title=title, media_type="application/pdf"
    )
    for number, text in enumerate(texts, start=1):
        db.add(
            DocumentPage(
                document_id=int(document.id),
                page_number=number,
                status="read",
                method="text_layer",
                text=text,
            )
        )
    await db.flush()
    return int(document.id)


async def _account(db: AsyncSession, name: str, mask: str | None = None) -> int:
    account = await FinanceService(db).create_manual_account(
        owner_user_id=1,
        name=name,
        account_type="brokerage",
        classification="asset",
    )
    account.mask = mask
    db.add(account)
    await db.flush()
    assert account.id is not None
    return account.id


async def _card(db: AsyncSession, document_id: int) -> Any:
    cards = [
        c
        for c in await list_changes(db, status="pending")
        if c.change_type == "document.metadata"
        and c.payload["document_id"] == document_id
    ]
    return cards[0] if cards else None


class TestThePaperSuppliesTheFact:
    @pytest.mark.asyncio
    async def test_a_statement_naming_an_account_with_no_last_four_offers_it(
        self, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account(async_db_session, "M1 Individual Brokerage Account")
        document_id = await _paper(async_db_session, "m1_aug.pdf", M1_FRONT)

        await propose_reading(opens(async_db_session), document_id)

        card = await _card(async_db_session, document_id)
        assert card.payload["account"] == {
            "value": str(account_id),
            "page": 1,
            "because": "Account: M1 Individual Brokerage Account",
        }
        assert card.payload["last_four"] == {
            "value": "2774",
            "page": 1,
            "because": "Account number: XXXX2774",
        }

    @pytest.mark.asyncio
    async def test_approving_it_sets_the_last_four_and_files_the_paper(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        account_id = await _account(async_db_session, "M1 Individual Brokerage Account")
        document_id = await _paper(async_db_session, "m1_aug.pdf", M1_FRONT)

        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=document_id,
                account=ReadValue(value=str(account_id), page=1, because="Account:"),
                last_four=ReadValue(value="2774", page=1, because="Account number:"),
            ),
            None,
        )

        account = await async_db_session.get(FinanceAccount, account_id)
        assert account is not None and account.mask == "2774"
        assert account_tag(account_id) in await DocumentService(
            async_db_session
        ).tags_for(document_id)

    @pytest.mark.asyncio
    async def test_an_account_with_another_last_four_is_not_changed(
        self, async_db_session: AsyncSession
    ) -> None:
        """Named on the page but numbered otherwise on file: somebody said
        so, and a reading does not argue."""
        await _account(async_db_session, "M1 Individual Brokerage Account", mask="1111")
        document_id = await _paper(async_db_session, "m1_aug.pdf", M1_FRONT)

        await propose_reading(opens(async_db_session), document_id)

        card = await _card(async_db_session, document_id)
        assert card.payload.get("last_four") is None
        assert card.payload.get("account") is None

    @pytest.mark.asyncio
    async def test_two_accounts_named_on_the_page_is_no_answer(
        self, async_db_session: AsyncSession
    ) -> None:
        await _account(async_db_session, "M1 Individual Brokerage Account")
        await _account(async_db_session, "Brokerage Account")
        document_id = await _paper(async_db_session, "m1_aug.pdf", M1_FRONT)

        await propose_reading(opens(async_db_session), document_id)

        card = await _card(async_db_session, document_id)
        assert card.payload.get("last_four") is None


class TestANewFactReadsTheUnfiledPileAgain:
    @pytest.mark.asyncio
    async def test_the_last_four_set_by_hand_files_the_paper_that_prints_it(
        self, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account(async_db_session, "Rainy day")
        # Nothing on the front but the number: no kind, no date, so no
        # card at all until the number means something.
        document_id = await _paper(
            async_db_session, "stmt.pdf", "Account ending 3639\n"
        )
        await propose_reading(opens(async_db_session), document_id)
        assert (await _card(async_db_session, document_id)) is None
        await numbers.set_last_four(async_db_session, account_id, "3639")

        proposed = await filing.reread_unfiled(async_db_session, owner_user_id=None)

        assert proposed == 1
        card = await _card(async_db_session, document_id)
        assert card.payload["account"]["value"] == str(account_id)

    @pytest.mark.asyncio
    async def test_filed_paper_and_paper_already_asked_about_are_left_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account(async_db_session, "Rainy day", mask="3639")
        filed = await _paper(async_db_session, "old.pdf", "Account ending 3639")
        await DocumentService(async_db_session).tag(filed, account_tag(account_id))
        asked = await _paper(async_db_session, "new.pdf", "Account ending 3639")
        await propose_reading(opens(async_db_session), asked)
        assert (await _card(async_db_session, asked)) is not None

        assert await filing.reread_unfiled(async_db_session, owner_user_id=None) == 0

    @pytest.mark.asyncio
    async def test_an_approved_fact_reads_the_pile_again(
        self, async_db_session: AsyncSession
    ) -> None:
        """The fact lands by a card: approving the M1 statement's last four
        files the OTHER statement that prints it, in the same breath."""
        from app.services.finance.domains.writes.queue import approve, propose

        account_id = await _account(async_db_session, "M1 Individual Brokerage Account")
        first = await _paper(async_db_session, "m1_aug.pdf", M1_FRONT)
        later = await _paper(
            async_db_session, "m1_sep.pdf", "Statement\nAccount ending 2774\n"
        )
        await propose_reading(opens(async_db_session), first)
        assert (await _card(async_db_session, later)) is None
        card = await _card(async_db_session, first)

        await approve(async_db_session, card.id)

        account = await async_db_session.get(FinanceAccount, account_id)
        assert account is not None and account.mask == "2774"
        assert (await _card(async_db_session, later)).payload["account"][
            "value"
        ] == str(account_id)
        assert propose is not None


class TestTheNightlySweep:
    @pytest.mark.asyncio
    async def test_the_job_opens_its_session_sweeps_and_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import asynccontextmanager

        seen: list[str] = []

        class Session:
            async def commit(self) -> None:
                seen.append("commit")

        @asynccontextmanager
        async def _open():
            seen.append("open")
            yield Session()

        async def _pass(db: Any, *, owner_user_id: int | None) -> int:
            seen.append("swept")
            return 0

        monkeypatch.setattr(filing, "get_async_session", _open)

        async def _receipts(db: Any, *, owner_user_id: int | None) -> int:
            seen.append("receipts")
            return 0

        from app.services.documents.domains.reading import receipts

        monkeypatch.setattr(filing, "reread_unfiled", _pass)
        monkeypatch.setattr(receipts, "match_waiting", _receipts)

        await filing.reread_unfiled_job()

        assert seen == ["open", "swept", "receipts", "commit"]

    def test_the_job_is_registered_nightly_before_the_joins(self) -> None:
        from pathlib import Path

        source = Path("app/components/scheduler/main.py").read_text()
        assert "reread_unfiled_job" in source
        assert 'id="reread_unfiled"' in source
        assert source.index('id="reread_unfiled"') < source.index('id="join_arrivals"')
        worker = Path("app/components/worker/tasks/service_jobs.py").read_text()
        assert "as_task(reread_unfiled_job)" in worker
