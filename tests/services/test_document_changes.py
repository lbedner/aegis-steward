"""``document.metadata``: what a document says it is, as a card.

An LLM or a regex silently mis-reading the execution date on a legal
document is precisely the failure the approval queue exists to prevent,
so a document's OWN metadata goes through the queue like everything
else. Every field on the card names the page and the line it was read
from; a field that cannot cite one is not proposed.
"""

from datetime import date
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.service import DocumentService
from tests._session import opens


async def _filed(db: AsyncSession, title: str = "Mortgage Interest Statement.pdf"):
    return await DocumentService(db).ingest(
        b"%PDF-1.4 " + title.encode(), title=title, media_type="application/pdf"
    )


class TestTheCardShowsItsWorking:
    @pytest.mark.asyncio
    async def test_each_field_names_the_page_and_the_line(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_describe,
        )

        document = await _filed(async_db_session)
        payload = MetadataPayload(
            document_id=int(document.id),
            kind=ReadValue(
                value="statement", page=1, because="Mortgage Interest Statement"
            ),
            document_date=ReadValue(
                value="2026-03-03", page=1, because="Statement Date: March 3, 2026"
            ),
        )

        said = {
            r.label: r.value
            for r in await metadata_describe(async_db_session, payload, None)
        }
        assert said["Document"] == "Mortgage Interest Statement.pdf"
        assert said["Kind"].startswith("other → statement")
        assert said["Dated"].startswith("- → 2026-03-03")
        assert (
            "page 1" in said["Kind"] and "Mortgage Interest Statement" in said["Kind"]
        )

    @pytest.mark.asyncio
    async def test_approving_it_files_what_it_read(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        document = await _filed(async_db_session)
        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=int(document.id),
                kind=ReadValue(value="statement", page=1, because="Statement"),
                document_date=ReadValue(value="2026-03-03", page=1, because="As of"),
            ),
            None,
        )
        await async_db_session.commit()

        filed = await DocumentService(async_db_session).get(int(document.id))
        assert (filed.kind, filed.document_date) == ("statement", date(2026, 3, 3))
        # The bytes and the title are untouched: a document is what arrived.
        assert filed.title == "Mortgage Interest Statement.pdf"


class TestTaxPaperOnTheCard:
    @pytest.mark.asyncio
    async def test_approving_files_the_form_and_the_year(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_describe,
            metadata_execute,
        )

        document = await _filed(async_db_session, "1098.pdf")
        payload = MetadataPayload(
            document_id=int(document.id),
            kind=ReadValue(value="tax", page=1, because="MORTGAGE INTEREST"),
            form_type=ReadValue(value="1098", page=1, because="MORTGAGE INTEREST"),
            tax_year=ReadValue(value="2025", page=1, because="For calendar year 2025"),
        )
        said = {
            r.label: r.value
            for r in await metadata_describe(async_db_session, payload, None)
        }
        assert said["Form"].startswith("- → 1098")
        assert said["Tax year"].startswith("- → 2025")

        await metadata_execute(async_db_session, payload, None)
        filed = await DocumentService(async_db_session).get(int(document.id))
        assert (filed.kind, filed.form_type, filed.tax_year) == ("tax", "1098", 2025)

    def test_a_form_the_shelf_does_not_know(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload, ReadValue

        with pytest.raises(ValidationError):
            MetadataPayload(
                document_id=1,
                form_type=ReadValue(value="1040-ish", page=1, because="x"),
            )


class TestWhatItRefuses:
    def test_a_kind_the_shelf_does_not_allow(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload, ReadValue

        with pytest.raises(ValidationError):
            MetadataPayload(
                document_id=1,
                kind=ReadValue(value="invoice", page=1, because="Invoice"),
            )

    def test_a_date_that_is_not_a_date(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload, ReadValue

        with pytest.raises(ValidationError):
            MetadataPayload(
                document_id=1,
                document_date=ReadValue(value="the third", page=1, because="x"),
            )

    def test_a_card_that_would_change_nothing(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload

        with pytest.raises(ValidationError):
            MetadataPayload(document_id=1)

    @pytest.mark.asyncio
    async def test_a_document_nobody_filed(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        with pytest.raises(ValueError, match="document"):
            await metadata_execute(
                async_db_session,
                MetadataPayload(
                    document_id=999_999,
                    kind=ReadValue(value="letter", page=1, because="Dear"),
                ),
                None,
            )


class TestItIsInTheOneQueue:
    def test_the_change_type_is_registered(self) -> None:
        from app.services.finance.domains.writes.registry import executor_for

        assert executor_for("document.metadata").title


class TestReadingADocumentProducesACard:
    """The whole point of ST-08: dropping paper in produces a card,
    without anybody opening the chat and asking for one."""

    async def _read(self, db: AsyncSession, title: str, *texts: str) -> int:
        from app.services.documents.models import DocumentPage

        document = await _filed(db, title)
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

    async def _cards(self, db: AsyncSession, document_id: int) -> list[Any]:
        from app.services.finance.domains.writes.queue import list_changes

        return [
            c
            for c in await list_changes(db, status="pending")
            if c.change_type == "document.metadata"
            and c.payload["document_id"] == document_id
        ]

    @pytest.mark.asyncio
    async def test_what_it_read_lands_as_one_pending_card(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session,
            "Aug-2026.pdf",
            "CITIZENS BANK\nMortgage Interest Statement\nStatement Date: March 3, 2026\n",
        )

        assert await propose_reading(opens(async_db_session), document_id) is not None
        await async_db_session.commit()

        (card,) = await self._cards(async_db_session, document_id)
        assert card.payload["kind"]["value"] == "statement"
        assert card.payload["kind"]["page"] == 1
        assert card.payload["document_date"]["value"] == "2026-03-03"
        # Nothing has moved yet: the document is untouched until approval.
        filed = await DocumentService(async_db_session).get(document_id)
        assert filed.kind == "other" and filed.document_date is None

    @pytest.mark.asyncio
    async def test_reading_it_again_does_not_stack_up_cards(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Twice.pdf", "Statement\nAs of 09/16/2026\n"
        )
        await propose_reading(opens(async_db_session), document_id)
        await async_db_session.commit()
        assert await propose_reading(opens(async_db_session), document_id) is None
        await async_db_session.commit()

        assert len(await self._cards(async_db_session, document_id)) == 1

    @pytest.mark.asyncio
    async def test_it_proposes_nothing_it_already_says(
        self, async_db_session: AsyncSession
    ) -> None:
        """A card that changes nothing wastes a decision."""
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Known.pdf", "Statement\nAs of 09/16/2026\n"
        )
        await DocumentService(async_db_session).update(
            document_id, {"kind": "statement", "document_date": date(2026, 9, 16)}
        )
        await async_db_session.flush()

        assert await propose_reading(opens(async_db_session), document_id) is None
        assert await self._cards(async_db_session, document_id) == []

    @pytest.mark.asyncio
    async def test_a_page_that_says_nothing_certain_proposes_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Quiet.pdf", "Page 1 of 1\nThank you for your business.\n"
        )

        assert await propose_reading(opens(async_db_session), document_id) is None
        assert await self._cards(async_db_session, document_id) == []


class TestWhoSentIt:
    """A document nobody attributed is a letterhead nobody can read.

    Metadata shipped title, kind and date; the sender was the fourth
    field the ticket named and the one that makes the shelf searchable
    by WHO. Without it, four parties ended up tagged to the same
    statement and the letter carrying Dutchess County DSS's own address
    was attached to nobody (found 2026-09-18).
    """

    @pytest.mark.asyncio
    async def test_approving_a_sender_tags_the_document(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )
        from app.services.documents.queries import document_ids_by_tag_prefix
        from app.services.matters.models import PARTY_TAG_PREFIX, party_tag
        from app.services.matters.service import PartyService

        sender = await PartyService(async_db_session).create(
            name="Dutchess Testcase", kind="organization"
        )
        document = await _a_document(async_db_session, "Request.pdf")

        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=document.id,
                sender=ReadValue(
                    value=str(sender.id),
                    page=1,
                    because="DUTCHESS COUNTY DEPARTMENT OF COMMUNITY AND FAMILY SERVICES",
                ),
            ),
            None,
        )
        await async_db_session.commit()

        filed = await document_ids_by_tag_prefix(async_db_session, PARTY_TAG_PREFIX)
        assert document.id in filed.get(party_tag(sender.id), [])

    @pytest.mark.asyncio
    async def test_the_card_names_the_sender_rather_than_its_id(
        self, async_db_session: AsyncSession
    ) -> None:
        """An id on a card is a number somebody has to go and look up."""
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            ReadValue,
            metadata_describe,
        )
        from app.services.matters.service import PartyService

        sender = await PartyService(async_db_session).create(
            name="Delta Testcase", kind="organization"
        )
        document = await _a_document(async_db_session, "Invoice.pdf")

        said = {
            row.label: row.value
            for row in await metadata_describe(
                async_db_session,
                MetadataPayload(
                    document_id=document.id,
                    sender=ReadValue(
                        value=str(sender.id), page=1, because="letterhead"
                    ),
                ),
                None,
            )
        }
        assert "Delta Testcase" in said["From"]
        assert "page 1" in said["From"]

    @pytest.mark.asyncio
    async def test_a_sender_who_does_not_exist_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        document = await _a_document(async_db_session, "Orphan.pdf")
        with pytest.raises(ValueError, match="999999"):
            await metadata_execute(
                async_db_session,
                MetadataPayload(
                    document_id=document.id,
                    sender=ReadValue(value="999999", page=1, because="letterhead"),
                ),
                None,
            )

    @pytest.mark.asyncio
    async def test_tagging_the_same_sender_twice_is_not_two_tags(
        self, async_db_session: AsyncSession
    ) -> None:
        """Re-reading a document must not file it twice."""
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )
        from app.services.documents.queries import document_ids_by_tag_prefix
        from app.services.matters.models import PARTY_TAG_PREFIX, party_tag
        from app.services.matters.service import PartyService

        sender = await PartyService(async_db_session).create(
            name="Twice Testcase", kind="organization"
        )
        document = await _a_document(async_db_session, "Repeat.pdf")
        read = MetadataPayload(
            document_id=document.id,
            sender=ReadValue(value=str(sender.id), page=1, because="letterhead"),
        )
        await metadata_execute(async_db_session, read, None)
        await metadata_execute(async_db_session, read, None)
        await async_db_session.commit()

        filed = await document_ids_by_tag_prefix(async_db_session, PARTY_TAG_PREFIX)
        assert filed.get(party_tag(sender.id), []).count(document.id) == 1


async def _a_document(db: AsyncSession, title: str) -> Any:
    from app.services.documents.models import Document

    document = Document(
        title=title, kind="other", storage_key=title, content_hash=title
    )
    db.add(document)
    await db.flush()
    return document


class TestProposingALink:
    """ST-08's other half. ST-07 says the link is a HUMAN action there
    and proposing one is this ticket's job - so extraction offers, the
    queue approves, and nothing is linked because a model was confident.
    """

    @pytest.mark.asyncio
    async def test_approving_it_answers_the_ask(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            EvidencePayload,
            evidence_execute,
        )
        from app.services.matters.evidence import satisfied_by
        from app.services.matters.requests import RequestService

        item, document = await _an_ask_and_a_document(async_db_session)
        await evidence_execute(
            async_db_session,
            EvidencePayload(
                document_id=document.id,
                request_item_id=item.id,
                page=2,
                because="Net Benefit: $1004.93",
            ),
            None,
        )
        await async_db_session.commit()

        found = await satisfied_by(async_db_session, item.id)
        assert [(one.document_id, one.page) for one in found] == [(document.id, 2)]
        assert (await RequestService(async_db_session).item(item.id)).status == (
            "satisfied"
        )

    @pytest.mark.asyncio
    async def test_the_card_quotes_the_line_it_read(
        self, async_db_session: AsyncSession
    ) -> None:
        """A link nobody can check is a link nobody should approve."""
        from app.services.documents.domains.reading.changes import (
            EvidencePayload,
            evidence_describe,
        )

        item, document = await _an_ask_and_a_document(async_db_session)
        said = {
            row.label: row.value
            for row in await evidence_describe(
                async_db_session,
                EvidencePayload(
                    document_id=document.id,
                    request_item_id=item.id,
                    page=2,
                    because="Net Benefit: $1004.93",
                ),
                None,
            )
        }
        assert "Proof of gross income" in said["Answers"]
        assert "page 2" in said["Because"]
        assert "Net Benefit" in said["Because"]

    @pytest.mark.asyncio
    async def test_an_ask_that_does_not_exist_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            EvidencePayload,
            evidence_execute,
        )

        _item, document = await _an_ask_and_a_document(async_db_session)
        with pytest.raises(ValueError, match="999999"):
            await evidence_execute(
                async_db_session,
                EvidencePayload(
                    document_id=document.id,
                    request_item_id=999999,
                    page=1,
                    because="x",
                ),
                None,
            )

    def test_a_link_with_no_reason_is_refused(self) -> None:
        """The citation is not optional here either: "because" is what
        somebody reads to decide whether this paper answers this ask."""
        from pydantic import ValidationError

        from app.services.documents.domains.reading.changes import EvidencePayload

        with pytest.raises(ValidationError):
            EvidencePayload(document_id=1, request_item_id=1, page=1, because="  ")


async def _an_ask_and_a_document(db: AsyncSession) -> tuple[Any, Any]:
    from app.services.documents.models import Document
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    matter = await MatterService(db).open(title="Renewal", reference="R-9")
    request = await RequestService(db).record(
        matter_id=matter.id,
        items=[{"asked": "Proof of gross income", "kind": "figure"}],
    )
    item = (await RequestService(db).items(request.id))[0]
    document = Document(
        title="NYSLRS.pdf", kind="statement", storage_key="n1", content_hash="n1"
    )
    db.add(document)
    await db.flush()
    return item, document


class TestACardDecidedLaterThanItWasMade:
    """A card sits in the queue for as long as it takes somebody to get
    to it, and the document is not frozen meanwhile."""

    @pytest.mark.asyncio
    async def test_a_name_somebody_gave_it_survives_approval(
        self, async_db_session: AsyncSession
    ) -> None:
        """The title proposal exists BECAUSE the document was still named
        after a file. Name it yourself and the premise is gone - applying
        it anyway overwrites a person's word with a machine's, silently,
        which is what it did before anybody asked (2026-09-19)."""
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            metadata_describe,
            metadata_execute,
        )
        from app.services.documents.models import Document

        document = Document(
            title="statements-0007.pdf",
            storage_key="testcase/stale.pdf",
            media_type="application/pdf",
            content_hash="testcase-stale-1",
            size_bytes=11,
        )
        async_db_session.add(document)
        await async_db_session.flush()
        payload = MetadataPayload(
            document_id=document.id,
            title={
                "value": "Chase statement, August 2026",
                "page": 1,
                "because": "JPMorgan Chase Bank, N.A.",
            },
            kind={"value": "statement", "page": 1, "because": "Account Summary"},
        )

        # Somebody gets there first.
        document.title = "Dad's August bank statement"
        async_db_session.add(document)
        await async_db_session.flush()

        # The card stops offering to rename it, and still offers the rest.
        said = {
            row.label: row.value
            for row in await metadata_describe(async_db_session, payload, None)
        }
        assert not any("Chase statement" in value for value in said.values())

        await metadata_execute(async_db_session, payload, None)
        await async_db_session.flush()
        assert document.title == "Dad's August bank statement"
        assert document.kind == "statement"

    @pytest.mark.asyncio
    async def test_a_card_with_nothing_left_to_do_says_so(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            metadata_describe,
        )
        from app.services.documents.models import Document

        document = Document(
            title="Dad's August bank statement",
            kind="statement",
            storage_key="testcase/stale2.pdf",
            media_type="application/pdf",
            content_hash="testcase-stale-2",
            size_bytes=11,
        )
        async_db_session.add(document)
        await async_db_session.flush()

        with pytest.raises(ValueError, match="nothing"):
            await metadata_describe(
                async_db_session,
                MetadataPayload(
                    document_id=document.id,
                    title={
                        "value": "Chase statement",
                        "page": 1,
                        "because": "JPMorgan Chase Bank, N.A.",
                    },
                    kind={
                        "value": "statement",
                        "page": 1,
                        "because": "Account Summary",
                    },
                ),
                None,
            )
