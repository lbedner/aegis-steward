"""A check download is one card: skip some, approve the rest, fix a payee (#420).

The reader filed fourteen separate check cards from one download, and a
misread payee ("Sweat Scoop" for Sweet Scoop) could be fixed by nobody: the
card was not editable and Illiana had no way to touch a pending card.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import check_card, checks
from app.services.finance.domains.writes.queue import (
    list_changes,
    propose,
    revise_fields,
)
from tests._session import opens


def _card(transaction_id: int, payee: str | None = "Sweat Scoop") -> dict[str, Any]:
    return {
        "document_id": 22,
        "page": 5,
        "slot": transaction_id,
        "transaction_id": transaction_id,
        "number": str(1800 + transaction_id),
        "front_key": f"scans/front-{transaction_id}",
        "back_key": None,
        "payee": payee,
        "payee_because": f"ORDER OF {payee}" if payee else None,
    }


class TestTheCard:
    def test_a_card_filed_before_the_payee_was_text_still_reads(self) -> None:
        """Fourteen cards are pending in the live queue with the payee as a
        cited reading; they must stay approvable."""
        old = {
            **_card(1),
            "payee": {"value": "Dr. Molar", "page": 5, "because": "ORDER OF Dr. Molar"},
        }
        old.pop("payee_because")
        card = check_card.CheckPayload(**old)
        assert (card.payee, card.payee_because) == ("Dr. Molar", "ORDER OF Dr. Molar")

    @pytest.mark.asyncio
    async def test_only_the_payee_can_be_edited(
        self, async_db_session: AsyncSession
    ) -> None:
        change = await propose(
            async_db_session, checks.CHECK, _card(1), owner_user_id=None
        )
        assert change.id is not None

        revised = await revise_fields(
            async_db_session, change.id, {"payee": "Sweet Scoop"}, owner_user_id=None
        )

        assert revised.payload["payee"] == "Sweet Scoop"
        # Everything else is the card's own and stays.
        assert revised.payload["front_key"] == "scans/front-1"
        assert revised.payload["transaction_id"] == 1
        with pytest.raises(ValueError, match="payee"):
            await revise_fields(
                async_db_session, change.id, {"number": "9999"}, owner_user_id=None
            )


class TestOneDownloadOneBatch:
    @pytest.mark.asyncio
    async def test_a_downloads_checks_are_proposed_together(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.documents.service import DocumentService
        from tests.services.test_check_images import (
            OCR_1524,
            VISION_1802,
            _download,
            _rows,
        )

        await _rows(async_db_session)
        pdf = _download([("white", "gray"), ("ivory", "silver")])
        document = await DocumentService(async_db_session).ingest(
            pdf, title="checkdownload.pdf", media_type="application/pdf"
        )
        assert document.id is not None
        fronts = [c.front for c in checks.split_checks(pdf)]
        monkeypatch.setattr(
            checks, "ocr", lambda image: OCR_1524 if image == fronts[0] else ""
        )

        async def _vision(_image: bytes, _type: str) -> tuple[str, str]:
            return VISION_1802, "vision"

        await checks.propose_checks(
            opens(async_db_session), document.id, owner_user_id=None, read=_vision
        )

        batches = {
            c.batch_id
            for c in await list_changes(async_db_session, status="pending")
            if c.change_type == checks.CHECK
        }
        assert len(batches) == 1 and None not in batches


class TestIllianaFixesAPayee:
    @pytest.mark.asyncio
    async def test_a_phrase_revises_every_card_that_misread_it(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "It's Sweet Scoop" fixes the reader's cards that said Sweat Scoop,
        and leaves the one that read Dr. Molar alone."""
        from app.services.ai.domains.chat.user_memory import memory_user
        from app.services.finance import ai_write_tools

        monkeypatch.setattr(
            ai_write_tools, "get_async_session", opens(async_db_session)
        )
        for n, payee in ((1, "Sweat Scoop"), (2, "Sweat Scoop"), (3, "Dr. Molar")):
            await propose(
                async_db_session,
                checks.CHECK,
                _card(n, payee),
                owner_user_id=None,
                proposed_by_agent="reading",
            )
        await async_db_session.commit()

        with memory_user("1", agent_slug="finance-assistant", conversation_id="c-1"):
            result = await ai_write_tools.revise(
                {"payee": "Sweet Scoop"}, about="Sweat Scoop"
            )

        assert len(result["revised"]) == 2
        payees = sorted(
            c.payload["payee"]
            for c in await list_changes(async_db_session, status="pending")
            if c.change_type == checks.CHECK
        )
        assert payees == ["Dr. Molar", "Sweet Scoop", "Sweet Scoop"]


class TestACheckIsOneDocument:
    """A check's front and back were filed as two documents, "Check 1522"
    and "Check 1522 (back)", and read as two things on the shelf (#582).
    One document now, a page a side, labelled."""

    @pytest.mark.asyncio
    async def test_its_front_and_back_are_the_pages_of_one(
        self, async_db_session: AsyncSession
    ) -> None:
        import io

        from PIL import Image

        from app.core.storage import get_storage
        from app.services.documents.queries import pages_for, tags_for_many
        from app.services.documents.service import DocumentService

        def jpeg(color: str) -> bytes:
            out = io.BytesIO()
            Image.new("RGB", (600, 270), color).save(out, "JPEG")
            return out.getvalue()

        store = get_storage()
        front = await store.put(jpeg("white"), content_type="image/jpeg")
        back = await store.put(jpeg("gray"), content_type="image/jpeg")

        result = await check_card.check_execute(
            async_db_session,
            check_card.CheckPayload(
                document_id=1,
                page=1,
                slot=0,
                transaction_id=4242,
                number="1522",
                front_key=front,
                back_key=back,
            ),
            None,
        )

        (made_id,) = result["documents"]
        made = await DocumentService(async_db_session).get(made_id)
        assert made is not None
        assert (made.title, made.kind, made.page_count) == ("Check 1522", "receipt", 2)
        assert (made.meta_data or {})["page_labels"] == ["Front", "Back"]
        pages = await pages_for(async_db_session, made_id)
        assert [p.image_key for p in pages] == [front, back]
        assert (
            "transaction:4242"
            in (await tags_for_many(async_db_session, [made_id]))[made_id]
        )
