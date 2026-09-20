"""A file of mail becomes rows, and its paper lands on the shelf.

Three short holds on the database, never one long one: the batch and
the senders on file; then each message in its own transaction; then the
counts. The parse happens before the first and the extraction dispatch
after the last, holding nothing - a file of ten thousand messages is
slow, and SQLite's single writer must not wait on it (#210).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.core.db import OpenSession
from app.core.log import logger
from app.services.documents.domains.extraction.dispatch import read_quietly
from app.services.documents.domains.reading.identity import parties_by_email
from app.services.documents.models import DocumentPage
from app.services.documents.service import DocumentService
from app.services.finance.domains.writes.queue import propose
from app.services.finance.models.changes import FinancePendingChange
from app.services.mail import parse
from app.services.mail.models import MailAttachment, MailBatch, MailMessage
from app.services.matters.models.core import party_tag
from app.services.system.jobs import SetLabel, unwatched

# A heartbeat per this many messages. Messages are heavier than ledger
# rows - each may carry paper the shelf has to hash and store - so the
# cadence is shorter than the import's 2000.
PROGRESS_EVERY = 200
# Messages per transaction. A commit is an fsync; one per message on a
# 3,000-message export is 3,000 fsyncs for no isolation anybody wanted -
# a message is idempotent on its Message-ID, so a re-run after a crash
# mid-chunk files the rest and skips what landed. The lock is still held
# for one chunk's milliseconds, never across the file (#210).
COMMIT_EVERY = 100


@dataclass
class MailImportResult:
    batch_id: int
    messages_total: int = 0
    messages_new: int = 0
    messages_duplicate: int = 0
    letters_filed: int = 0
    attachments_filed: int = 0
    attachments_duplicate: int = 0
    # Paper that was NEW to the shelf, and so gets read.
    document_ids: list[int] = field(default_factory=list)

    def as_payload(self) -> dict[str, int]:
        return {
            "batch_id": self.batch_id,
            "messages_total": self.messages_total,
            "messages_new": self.messages_new,
            "messages_duplicate": self.messages_duplicate,
            "letters_filed": self.letters_filed,
            "attachments_filed": self.attachments_filed,
            "attachments_duplicate": self.attachments_duplicate,
        }


async def ingest_mail(
    open_session: OpenSession,
    *,
    data: bytes,
    file_name: str,
    owner_user_id: int | None = None,
    on_label: SetLabel | None = None,
) -> MailImportResult:
    """Read a mailbox export into the shelf. Returns what it did.

    Idempotent twice over: the same bytes are answered with the batch
    that already read them, and a message already filed - by Message-ID,
    whatever file it came in - is counted and left alone.
    """
    say = on_label or unwatched
    # Refuses a file that is not mail before anything is written.
    messages = parse.messages(data, file_name)
    sha = hashlib.sha256(data).hexdigest()

    async with open_session() as db:
        already = (
            await db.exec(select(MailBatch).where(MailBatch.file_sha256 == sha))
        ).first()
        if already is not None:
            logger.info(
                "mail.import.identical_reupload",
                file_name=file_name,
                batch_id=already.id,
            )
            return _result_of(already)
        batch = MailBatch(
            owner_user_id=owner_user_id,
            file_name=file_name,
            file_sha256=sha,
            messages_total=len(messages),
        )
        db.add(batch)
        await db.flush()
        batch_id = int(batch.id)
        senders = await parties_by_email(db)
        offered = await _strangers_already_offered(db)

    await say(f"Reading {len(messages):,} messages...")
    result = MailImportResult(batch_id=batch_id, messages_total=len(messages))

    for start in range(0, len(messages), COMMIT_EVERY):
        async with open_session() as db:
            for message in messages[start : start + COMMIT_EVERY]:
                await _file_one(
                    db,
                    message,
                    result,
                    senders=senders,
                    offered=offered,
                    owner_user_id=owner_user_id,
                )
        done = min(start + COMMIT_EVERY, len(messages))
        if done % PROGRESS_EVERY == 0 or done == len(messages):
            await say(
                f"Read {done:,} of {len(messages):,} - "
                f"{result.attachments_filed:,} attachments filed"
            )

    async with open_session() as db:
        batch = await db.get(MailBatch, batch_id)
        assert batch is not None
        batch.messages_new = result.messages_new
        batch.messages_duplicate = result.messages_duplicate
        batch.letters_filed = result.letters_filed
        batch.attachments_filed = result.attachments_filed
        batch.attachments_duplicate = result.attachments_duplicate
        batch.status = "done"
        batch.finished_at = utcnow()
        db.add(batch)
        await db.flush()

    # Holding nothing: each read is a job of its own on the worker.
    for document_id in result.document_ids:
        await read_quietly(document_id, owner_user_id=owner_user_id)

    logger.info("mail.import.finished", file_name=file_name, **result.as_payload())
    return result


async def _file_one(
    db: AsyncSession,
    message: parse.ParsedMessage,
    result: MailImportResult,
    *,
    senders: dict[str, int],
    offered: set[str],
    owner_user_id: int | None,
) -> None:
    """One message: the row, its paper onto the shelf, and - when nobody
    on file wrote it - a card offering to add them."""
    seen = (
        await db.exec(
            select(MailMessage.id).where(MailMessage.message_id == message.message_id)
        )
    ).first()
    if seen is not None:
        result.messages_duplicate += 1
        return

    row = MailMessage(
        owner_user_id=owner_user_id,
        batch_id=result.batch_id,
        message_id=message.message_id,
        from_address=message.from_address,
        from_name=message.from_name or None,
        subject=message.subject or None,
        sent_at=message.sent_at,
        party_id=senders.get(message.from_address),
    )
    db.add(row)
    await db.flush()
    result.messages_new += 1

    if row.party_id is None and message.from_address:
        await _offer_contact(db, message, offered=offered, owner_user_id=owner_user_id)

    documents = DocumentService(db)
    # The message as a letter. Half of what an agency sends has no
    # attachment; the body is the letter, and it is read the way a
    # scanned one is - by writing its page as already read, so extraction
    # skips straight to proposing what it says about itself (MI-05).
    letter = letter_text(message)
    if letter:
        paper, created = await documents.store(
            letter.encode("utf-8"),
            title=message.subject or "(no subject)",
            kind="letter",
            media_type="text/plain",
            owner_user_id=owner_user_id,
            document_date=message.sent_at.date() if message.sent_at else None,
            source="mail",
            channel="email",
            page_count=1,
        )
        if created:
            db.add(
                DocumentPage(
                    document_id=int(paper.id),
                    page_number=1,
                    status="read",
                    method="mail",
                    text=letter,
                )
            )
            result.letters_filed += 1
            result.document_ids.append(int(paper.id))
        row.document_id = int(paper.id)
        db.add(row)
        await _file_under(documents, int(paper.id), row.party_id)

    linked: set[int] = set()
    for attachment in message.attachments:
        if not attachment.data:
            continue
        paper, created = await documents.store(
            attachment.data,
            title=attachment.filename,
            media_type=attachment.media_type,
            owner_user_id=owner_user_id,
            # The letter's own date, not the day it was imported.
            document_date=message.sent_at.date() if message.sent_at else None,
            source="mail",
            channel="email",
        )
        if created:
            result.attachments_filed += 1
            result.document_ids.append(int(paper.id))
        else:
            result.attachments_duplicate += 1
        # The same file attached twice to one message is one link.
        if paper.id in linked:
            continue
        linked.add(int(paper.id))
        db.add(
            MailAttachment(
                message_id=int(row.id),
                document_id=int(paper.id),
                filename=attachment.filename,
            )
        )
        await _file_under(documents, int(paper.id), row.party_id)
    await db.flush()


async def _file_under(
    documents: DocumentService, document_id: int, party_id: int | None
) -> None:
    """Paper from a known sender sits with them: the same party tag an
    approved letterhead filing writes, so Contacts shows it and nothing
    learns a second way of filing. A stranger's paper waits for the card."""
    if party_id is not None:
        await documents.tag(document_id, party_tag(party_id))


async def papers_of(db: AsyncSession, row: MailMessage) -> list[int]:
    """The letter and every attachment a message put on the shelf."""
    links = (
        await db.exec(
            select(MailAttachment.document_id).where(
                MailAttachment.message_id == row.id
            )
        )
    ).all()
    return ([row.document_id] if row.document_id else []) + [int(d) for d in links]


async def adopt_sender(db: AsyncSession, party_id: int, address: str | None) -> int:
    """A stranger's card was approved: everything they already sent is
    theirs now - the message rows name them, and their letters and
    attachments are filed under them the way a known sender's are on
    ingest. Returns how many messages were adopted."""
    if not address:
        return 0
    documents = DocumentService(db)
    rows = (
        await db.exec(
            select(MailMessage).where(
                MailMessage.from_address == address.lower(),
                MailMessage.party_id.is_(None),
            )
        )
    ).all()
    for row in rows:
        row.party_id = party_id
        db.add(row)
        for document_id in await papers_of(db, row):
            await _file_under(documents, document_id, party_id)
    await db.flush()
    return len(rows)


def _result_of(batch: MailBatch) -> MailImportResult:
    return MailImportResult(
        batch_id=int(batch.id),
        messages_total=batch.messages_total,
        messages_new=0,
        messages_duplicate=batch.messages_total,
        attachments_filed=0,
        attachments_duplicate=batch.attachments_filed + batch.attachments_duplicate,
    )


def letter_text(message: parse.ParsedMessage) -> str:
    """The message as a page of text, or "" when the body said nothing.

    The header is the letterhead. A printed letter carries the sender's
    address at the top and the identity rules read it there; this puts
    the From, Subject and Date on the first lines for the same reason,
    so the address names the sender through the same rule a phone or a
    website does, with no second path for "it came from a header."
    """
    body = (message.body_text or "").strip()
    if not body:
        return ""
    who = (
        f"{message.from_name} <{message.from_address}>"
        if message.from_name
        else message.from_address
    )
    head = [f"From: {who}", f"Subject: {message.subject}"]
    if message.sent_at:
        head.append(f"Date: {message.sent_at.date().isoformat()}")
    return "\n".join([*head, "", body])


async def _strangers_already_offered(db: AsyncSession) -> set[str]:
    """Addresses with a contact.create card waiting or refused. A second
    export from the same stranger does not offer them twice, and a
    rejected card was an answer: no means no."""
    rows = (
        await db.exec(
            select(FinancePendingChange).where(
                FinancePendingChange.change_type == "contact.create",
                FinancePendingChange.status.in_(("pending", "rejected")),
            )
        )
    ).all()
    return {
        str(row.payload["email"]).lower() for row in rows if row.payload.get("email")
    }


def _domain_name(address: str) -> str:
    """``of-service@of.optum.com`` -> ``Optum``: the label before the TLD,
    when the header gave no name. ponytail: a two-part public suffix
    (``bank.co.uk`` -> ``Co``) is wrong; the card is where it is fixed."""
    labels = address.rpartition("@")[2].split(".")
    return (labels[-2] if len(labels) >= 2 else labels[0]).capitalize() or address


async def _offer_contact(
    db: AsyncSession,
    message: parse.ParsedMessage,
    *,
    offered: set[str],
    owner_user_id: int | None,
) -> None:
    """A stranger wrote. Offer to add them - never add them.

    The letterhead rule refuses to guess a sender off a printed line,
    because that is how a second address book starts. A From header is
    not a guess: the sender asserted a name and an address. Still a card
    (ST-08: extraction proposes, never writes), once per address.

    ``kind`` is the one guess. A header cannot tell a county caseworker
    from a claims robot, so it says organization and the card is where
    that gets corrected - not a name-shape rule, which is the kind of
    rule this codebase keeps having to take back.
    """
    address = message.from_address
    if address in offered:
        return
    offered.add(address)
    when = message.sent_at.date().isoformat() if message.sent_at else "an unknown date"
    await propose(
        db,
        "contact.create",
        {
            "name": message.from_name or _domain_name(address),
            "kind": "organization",
            "email": address,
            "note": f"Wrote on {when}: {message.subject or '(no subject)'}",
        },
        owner_user_id=owner_user_id,
        proposed_by_agent="mail",
    )
