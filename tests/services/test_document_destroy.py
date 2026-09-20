"""Hard delete: the paper, its pages, its labels, and every pointer at
it, in one transaction - then the bytes, if nothing else holds them.

``soft_delete`` retires a row and keeps everything. That is the audit
answer; this is the other one, for the two logos a mail import filed
before it knew a logo from a statement (2026-09-21).
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.storage import FilesystemStorage, set_storage
from app.services.documents import DocumentService
from app.services.documents.domains.shelf.destroy import destroy
from app.services.documents.models import DocumentPage, DocumentTag
from app.services.mail.models import MailAttachment, MailBatch, MailMessage
from app.services.matters.models.core import DocumentParty, Fact


@pytest.fixture
def svc(async_db_session: AsyncSession, tmp_path):
    set_storage(FilesystemStorage(tmp_path))
    yield DocumentService(async_db_session)
    set_storage(None)


class TestDestroy:
    @pytest.mark.asyncio
    async def test_the_row_its_pages_and_labels_go_and_the_bytes_follow(
        self, svc: DocumentService, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        doc = await svc.ingest(b"logo", title="logo.png", owner_user_id=1)
        db.add(
            DocumentPage(
                document_id=doc.id, page_number=1, image_key="sha256/aa/bb/" + "a" * 64
            )
        )
        await svc.tag(doc.id, "junk")
        await db.flush()

        orphaned = await destroy(db, doc.id, owner_user_id=1)
        await db.commit()

        assert await svc.get(doc.id, owner_user_id=1) is None
        assert (
            await db.exec(
                select(DocumentPage).where(DocumentPage.document_id == doc.id)
            )
        ).all() == []
        assert (
            await db.exec(select(DocumentTag).where(DocumentTag.document_id == doc.id))
        ).all() == []
        assert set(orphaned) == {doc.storage_key, "sha256/aa/bb/" + "a" * 64}

    @pytest.mark.asyncio
    async def test_bytes_another_document_still_holds_are_kept(
        self, svc: DocumentService, async_db_session: AsyncSession
    ) -> None:
        """The store dedupes by content: two people's copies of one form
        are two rows over one blob. Destroying one must not blank the other."""
        mine = await svc.ingest(b"form", title="mine", owner_user_id=1)
        theirs = await svc.ingest(b"form", title="theirs", owner_user_id=2)

        orphaned = await destroy(async_db_session, mine.id, owner_user_id=1)

        assert orphaned == []
        assert await svc.get(theirs.id, owner_user_id=2) is not None

    @pytest.mark.asyncio
    async def test_everything_that_pointed_at_it_lets_go(
        self, svc: DocumentService, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        letter = await svc.ingest(b"letter", title="letter", owner_user_id=1)
        logo = await svc.ingest(b"logo", title="logo", owner_user_id=1)
        batch = MailBatch(
            file_name="x.eml", file_sha256="f" * 64, messages_total=1, status="done"
        )
        db.add(batch)
        await db.flush()
        message = MailMessage(
            batch_id=batch.id,
            message_id="<m>",
            from_address="a@b.c",
            document_id=letter.id,
        )
        db.add(message)
        await db.flush()
        db.add(
            MailAttachment(
                message_id=message.id, document_id=logo.id, filename="logo.png"
            )
        )
        db.add(DocumentParty(document_id=letter.id, party_id=7, role="sender"))
        db.add(
            Fact(
                subject_party_id=7,
                attribute="premium",
                value_cents=100,
                document_id=letter.id,
            )
        )
        await db.flush()

        await destroy(db, letter.id, owner_user_id=1)
        await destroy(db, logo.id, owner_user_id=1)
        await db.commit()

        # The letter IS the message: its row goes too, and the batch once
        # it is empty, so a re-upload of the file is a fresh import - not
        # "identical file, already have it" about paper that is gone.
        assert (await db.exec(select(MailMessage))).all() == []
        assert (await db.exec(select(MailBatch))).all() == []
        assert (await db.exec(select(MailAttachment))).all() == []
        assert (await db.exec(select(DocumentParty))).all() == []
        [fact] = (await db.exec(select(Fact))).all()
        assert fact.document_id is None

    @pytest.mark.asyncio
    async def test_a_protected_document_needs_its_title_and_a_stranger_gets_nothing(
        self, svc: DocumentService, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import ProtectedDocumentError

        doc = await svc.ingest(b"deed", title="Deed", owner_user_id=1)
        await svc.update(doc.id, {"protected": True}, owner_user_id=1)

        with pytest.raises(ProtectedDocumentError):
            await destroy(async_db_session, doc.id, owner_user_id=1)
        assert (
            await destroy(async_db_session, doc.id, owner_user_id=2, confirm="Deed")
            is None
        )
        assert await destroy(
            async_db_session, doc.id, owner_user_id=1, confirm="Deed"
        ) == [doc.storage_key]
        assert await svc.get(doc.id, owner_user_id=1) is None
