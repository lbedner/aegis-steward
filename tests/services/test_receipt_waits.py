"""A receipt that arrives before its charge waits for it (#330).

Matching ran once, when a reading was made: a receipt read the day of
the purchase, before the card's charge posted, was never matched. A
receipt on the shelf with no transaction now waits - every sync, import
and nightly sweep tries again - and after ``WAIT_DAYS`` on the shelf it
becomes an Attention item instead of waiting forever in silence.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlmodel import select as sql_select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import receipts
from app.services.documents.models import DocumentPage
from app.services.documents.queries import tagged_with
from app.services.documents.service import DocumentService
from app.services.finance.constants import transaction_tag
from app.services.finance.domains.writes.queue import approve, list_changes, reject
from app.services.finance.models import FinanceInsight
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account

BOUGHT = date(2026, 9, 3)
CVS = """CVS pharmacy
Store 4471  09/03/2026
ADVIL 24CT          9.99
SUBTOTAL           10.00
TAX                 0.80
TOTAL              10.80
VISA ************1234  10.80
"""


async def _receipt(db: AsyncSession, text: str = CVS, *, dated: date = BOUGHT) -> int:
    document = await DocumentService(db).ingest(
        b"%PDF-1.4 " + text.encode(),
        title="CVS receipt",
        media_type="application/pdf",
        kind="receipt",
    )
    document.document_date = dated
    db.add(document)
    db.add(
        DocumentPage(
            document_id=int(document.id or 0),
            page_number=1,
            status="read",
            method="text_layer",
            text=text,
        )
    )
    await db.flush()
    return int(document.id or 0)


async def _charge(
    db: AsyncSession, cents: int, day: date, name: str = "CVS/PHARMACY"
) -> int:
    svc = FinanceService(db)
    account = await seed_account(svc, name=f"VISA {name} {day}")
    row = await svc.create_transaction(
        owner_user_id=1,
        account_id=int(account.id or 0),
        amount=cents,
        txn_date=day,
        name=name,
        source="csv",
    )
    return int(row.id or 0)


async def _cards(db: AsyncSession, status: str | None = "pending") -> list[Any]:
    return [
        c
        for c in await list_changes(db, status=status)
        if c.change_type == receipts.RECEIPT
    ]


class TestReadingTheTotal:
    def test_the_total_is_the_total_not_the_subtotal_or_tax(self) -> None:
        total = receipts.read_total([{"page": 1, "text": CVS}])
        assert total is not None
        assert (total.cents, total.page, total.because) == (
            1080,
            1,
            "TOTAL              10.80",
        )

    def test_an_order_total_with_a_label_and_a_dollar_sign(self) -> None:
        text = "Items: $52.00\nEstimated tax: $2.12\nOrder Total: $54.12\n"
        total = receipts.read_total([{"page": 1, "text": text}])
        assert total is not None and total.cents == 5412

    def test_no_total_is_none(self) -> None:
        assert (
            receipts.read_total([{"page": 1, "text": "Thank you for shopping"}]) is None
        )


class TestItWaits:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # one sweep before the charge, one after
    async def test_the_charge_arriving_later_gets_the_card(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        document_id = await _receipt(db)

        assert await receipts.match_waiting(db, owner_user_id=None) == 0
        assert await _cards(db) == []

        # The charge posts two days later, with the next sync.
        charge = await _charge(db, -1080, BOUGHT + timedelta(days=2))
        assert await receipts.match_waiting(db, owner_user_id=None) == 1

        (card,) = await _cards(db)
        assert card.payload["document_id"] == document_id
        assert card.payload["transaction_id"] == charge
        assert card.batch_id is not None  # one sweep, one decision

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # a sweep, an approval, a sweep
    async def test_asked_once_and_filed_on_approval(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        document_id = await _receipt(db)
        charge = await _charge(db, -1080, BOUGHT + timedelta(days=1))

        await receipts.match_waiting(db, owner_user_id=None)
        assert await receipts.match_waiting(db, owner_user_id=None) == 0  # pending

        (card,) = await _cards(db)
        await approve(db, int(card.id or 0))
        filed = await tagged_with(db, [transaction_tag(charge)])
        assert filed[transaction_tag(charge)] == [document_id]
        assert await receipts.match_waiting(db, owner_user_id=None) == 0

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)
    async def test_a_rejected_match_is_not_asked_again(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        await _receipt(db)
        await _charge(db, -1080, BOUGHT)
        await receipts.match_waiting(db, owner_user_id=None)
        (card,) = await _cards(db)
        await reject(db, int(card.id or 0))

        assert await receipts.match_waiting(db, owner_user_id=None) == 0

    @pytest.mark.asyncio
    async def test_two_charges_that_could_be_it_is_no_answer(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        await _receipt(db)
        await _charge(db, -1080, BOUGHT)
        await _charge(db, -1080, BOUGHT + timedelta(days=3), name="CVS 0921")

        assert await receipts.match_waiting(db, owner_user_id=None) == 0

    @pytest.mark.asyncio
    async def test_a_charge_long_before_the_receipt_is_not_it(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        await _receipt(db)
        await _charge(db, -1080, BOUGHT - timedelta(days=40))

        assert await receipts.match_waiting(db, owner_user_id=None) == 0


class TestItStopsWaiting:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # the sweep, three times
    async def test_after_the_wait_it_needs_attention_once(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        document_id = await _receipt(db)
        today = current_date() + timedelta(days=receipts.WAIT_DAYS + 1)

        await receipts.match_waiting(db, owner_user_id=None, today=today)
        await receipts.match_waiting(db, owner_user_id=None, today=today)

        found = (
            await db.exec(
                sql_select(FinanceInsight).where(
                    FinanceInsight.insight_type == receipts.UNMATCHED
                )
            )
        ).all()
        assert len(found) == 1
        assert found[0].dedup_key == f"{receipts.UNMATCHED}:{document_id}"
        assert "CVS receipt" in found[0].title
        assert "$10.80" in (found[0].body or "")

        # Done waiting: a charge arriving now is no longer asked about.
        # (A receipt can still be attached from its charge's menu.)
        await _charge(db, -1080, BOUGHT)
        assert await receipts.match_waiting(db, owner_user_id=None, today=today) == 0

    @pytest.mark.asyncio
    async def test_a_receipt_kept_from_before_is_not_waiting(
        self, async_db_session: AsyncSession
    ) -> None:
        """A receipt on the shelf long before receipts waited is not an
        Attention item the day this ships."""
        db = async_db_session
        await _receipt(db)
        today = current_date() + timedelta(days=receipts.WAIT_DAYS + 30)

        await receipts.match_waiting(db, owner_user_id=None, today=today)

        found = (
            await db.exec(
                sql_select(FinanceInsight).where(
                    FinanceInsight.insight_type == receipts.UNMATCHED
                )
            )
        ).all()
        assert found == []


class TestWhenItLooksAgain:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # looked for on reading, then on approval
    async def test_paper_called_a_receipt_on_approval_is_matched(
        self, async_db_session: AsyncSession
    ) -> None:
        """A receipt uploaded as plain paper: the reader names it a receipt
        by its heading, and approving that is when it starts waiting."""
        from app.services.documents.domains.reading import propose_reading
        from tests._session import opens

        db = async_db_session
        document = await DocumentService(db).ingest(
            b"%PDF-1.4 order", title="order.pdf", media_type="application/pdf"
        )
        document_id = int(document.id or 0)
        db.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                status="read",
                method="text_layer",
                text="Order Receipt\nOrder placed September 3, 2026\nOrder Total: $54.12\n",
            )
        )
        await _charge(db, -5412, BOUGHT + timedelta(days=1), name="AMAZON MKTPL")
        await propose_reading(opens(db), document_id)
        (metadata,) = [
            c
            for c in await list_changes(db, status="pending")
            if c.change_type == "document.metadata"
        ]
        assert metadata.payload["kind"]["value"] == "receipt"

        await approve(db, int(metadata.id or 0))
        (card,) = await _cards(db)
        assert card.payload["document_id"] == document_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["sync", "sync_all", "import", "nightly"])
    async def test_every_arrival_looks_again(
        self, path: str, monkeypatch: pytest.MonkeyPatch, async_db_session: AsyncSession
    ) -> None:
        from app.components.worker.tasks import finance_tasks
        from app.core import db as core_db
        from app.services.documents.domains.reading import filing
        from app.services.finance import jobs
        from app.services.finance.adapters.providers import connections
        from app.services.finance.domains import imports_job
        from app.services.finance.service import FinanceService
        from tests._session import opens

        looked: list[int | None] = []

        async def _look(db: AsyncSession, *, owner_user_id: int | None) -> int:
            looked.append(owner_user_id)
            return 0

        async def _nothing(*_a: Any, **_k: Any) -> Any:
            return []

        monkeypatch.setattr(receipts, "match_waiting", _look)
        for module in (core_db, filing, jobs, imports_job):
            monkeypatch.setattr(module, "get_async_session", opens(async_db_session))
        monkeypatch.setattr(connections, "sync_one_connection", _nothing)
        monkeypatch.setattr(connections, "sync_owner_connections", _nothing)
        monkeypatch.setattr(FinanceService, "import_file", _nothing)

        if path == "sync":
            await finance_tasks.finance_sync_connection_task({}, 1, None)
        elif path == "sync_all":
            monkeypatch.setattr(
                "app.services.finance.adapters.providers.queries.connected_owner_ids",
                lambda *_a, **_k: _owners(),
            )
            await jobs.finance_sync_connections_job()
        elif path == "import":
            monkeypatch.setattr(
                imports_job, "import_result_payload", lambda r: {}, raising=False
            )
            await _import(monkeypatch)
        else:
            await filing.reread_unfiled_job()

        assert looked == [None]


async def _owners() -> list[int | None]:
    return [None]


async def _import(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import storage
    from app.services.finance import schemas
    from app.services.finance.domains import imports_job

    class _Store:
        async def get(self, _key: str) -> bytes:
            return b"Date,Amount\n"

    monkeypatch.setattr(storage, "get_storage", lambda: _Store())
    monkeypatch.setattr(schemas, "import_result_payload", lambda _r: {})
    await imports_job.run_import(
        "key", file_name="x.csv", account_id=None, owner_user_id=None
    )
