"""A check download is one card: skip some, approve the rest, fix a payee (#420).

The reader filed fourteen separate check cards from one download, and a
misread payee ("Holly Cow" for Holy Cow) could be fixed by nobody: the
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


def _card(transaction_id: int, payee: str | None = "Holly Cow") -> dict[str, Any]:
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
            "payee": {"value": "Dr. Clark", "page": 5, "because": "ORDER OF Dr. Clark"},
        }
        old.pop("payee_because")
        card = check_card.CheckPayload(**old)
        assert (card.payee, card.payee_because) == ("Dr. Clark", "ORDER OF Dr. Clark")

    @pytest.mark.asyncio
    async def test_only_the_payee_can_be_edited(
        self, async_db_session: AsyncSession
    ) -> None:
        change = await propose(
            async_db_session, checks.CHECK, _card(1), owner_user_id=None
        )
        assert change.id is not None

        revised = await revise_fields(
            async_db_session, change.id, {"payee": "Holy Cow"}, owner_user_id=None
        )

        assert revised.payload["payee"] == "Holy Cow"
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
        """ "It's Holy Cow" fixes the reader's cards that said Holly Cow,
        and leaves the one that read Dr. Clark alone."""
        from app.services.ai.domains.chat.user_memory import memory_user
        from app.services.finance import ai_write_tools

        monkeypatch.setattr(
            ai_write_tools, "get_async_session", opens(async_db_session)
        )
        for n, payee in ((1, "Holly Cow"), (2, "Holly Cow"), (3, "Dr. Clark")):
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
                {"payee": "Holy Cow"}, about="Holly Cow"
            )

        assert len(result["revised"]) == 2
        payees = sorted(
            c.payload["payee"]
            for c in await list_changes(async_db_session, status="pending")
            if c.change_type == checks.CHECK
        )
        assert payees == ["Dr. Clark", "Holy Cow", "Holy Cow"]
