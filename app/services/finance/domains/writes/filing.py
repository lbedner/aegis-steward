"""Filing a document where it belongs: ``document.file``.

An attached PDF is read once and stands in the conversation as a paste
marker; this is the card that puts the document it names on an account,
a contact or a matter, so the evidence is findable from the page that
asks for it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow


class FileDocumentPayload(BaseModel):
    """Which document belongs where: an account, a contact, a matter or
    a transaction (its receipt, #331), exactly one of them.

    Takes the PASTE id, because that is what the conversation is holding:
    an attached PDF is read once and stands in the message as
    [pasted text #abc12345], and asking the agent for a document id it
    was never shown is asking it to invent one.
    """

    model_config = ConfigDict(extra="forbid")

    paste_id: str
    account_id: int | None = None
    party_id: int | None = None
    matter_id: int | None = None
    transaction_id: int | None = None
    # What it IS, where it was read (#430): "Citizens Bank 1099-INT, 2025"
    # rather than IMG_6611.jpeg. Applied on approval; none of it required.
    title: str | None = None
    kind: str | None = None
    form_type: str | None = None
    tax_year: int | None = None
    # What it SAYS, as printed: "box 1 interest income" -> "$127.78" (#442).
    figures: dict[str, str] = {}

    @model_validator(mode="after")
    def _named_as_a_document_can_be(self) -> FileDocumentPayload:
        from app.services.documents.service import check_fields, check_figures

        if self.form_type and not self.kind:
            self.kind = "tax"  # a form type is tax paper
        check_fields(self.naming())
        check_figures(self.figures)
        return self

    def naming(self) -> dict[str, Any]:
        """The fields that name the document, as given."""
        return {
            k: v
            for k, v in (
                ("title", self.title),
                ("kind", self.kind),
                ("form_type", self.form_type),
                ("tax_year", self.tax_year),
            )
            if v is not None
        }

    @model_validator(mode="after")
    def _one_place(self) -> FileDocumentPayload:
        given = [
            v
            for v in (
                self.account_id,
                self.party_id,
                self.matter_id,
                self.transaction_id,
            )
            if v is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "Exactly one of account_id, party_id, matter_id, transaction_id."
            )
        return self


async def place(
    db: AsyncSession,
    owner_user_id: int | None,
    *,
    account_id: int | None = None,
    party_id: int | None = None,
    matter_id: int | None = None,
    transaction_id: int | None = None,
) -> tuple[str, str]:
    """The label that files something under an account, a contact or a
    matter, and the place's name; a ValueError naming what was not
    found. Documents and transactions are filed by the same labels."""
    from app.services.finance.constants import account_tag
    from app.services.finance.domains.ledger.accounts import get_account
    from app.services.matters.matters import MatterService
    from app.services.matters.models import matter_tag, party_tag
    from app.services.matters.service import PartyService

    if account_id is not None:
        account = await get_account(db, account_id, owner_user_id=owner_user_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found.")
        return account_tag(account_id), account.name
    if transaction_id is not None:
        from app.services.finance.constants import transaction_tag
        from app.services.finance.domains.writes.display import txn_subject

        txn, subject = await txn_subject(db, transaction_id, owner_user_id)
        if txn is None:
            raise ValueError(f"Transaction {transaction_id} not found.")
        return transaction_tag(transaction_id), subject
    if party_id is not None:
        party = await PartyService(db).get(party_id)
        if party is None:
            raise ValueError(f"Contact {party_id} not found.")
        return party_tag(party_id), party.name
    matter = await MatterService(db).get(matter_id or 0)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")
    return matter_tag(matter.id), matter.title


async def chat_entry(
    db: AsyncSession, paste_id: str, owner_user_id: int | None
) -> dict[str, Any] | None:
    """The user's index entry a paste id names: a read document, or a
    photo kept from a chat turn (#285); None for anything else."""
    from app.services.ai.domains.chat.user_memory import load_user_pastes
    from app.services.finance.domains.detection.analyst.shared import user_id_for

    # The same mapping the agent's own deps use, not a second guess at
    # what a finance owner is called on the chat side.
    for paste in await load_user_pastes(user_id_for(owner_user_id), db):
        if paste.get("id") == paste_id and (
            paste.get("document_id")
            or str(paste.get("media_type", "")).startswith("image/")
        ):
            return paste
    return None


async def filed_document(
    db: AsyncSession, paste_id: str, owner_user_id: int | None
) -> tuple[int, str] | None:
    """The read document a paste id names, as (id, title), or None."""
    entry = await chat_entry(db, paste_id, owner_user_id)
    if entry is None or not entry.get("document_id"):
        return None
    return int(entry["document_id"]), str(entry.get("title") or "document")


async def _as_document(
    db: AsyncSession, photo: dict[str, Any], owner_user_id: int | None
) -> int:
    """The document a kept photo becomes: its bytes on the shelf, the
    index entry pointing at it so the next filing finds it."""
    from app.core.storage import get_storage
    from app.services.ai.domains.chat.pastes import store_document
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.detection.analyst.shared import user_id_for

    data = await get_storage().get(str(photo["key"]))
    if data is None:
        raise ValueError("The photo is no longer in storage; attach it again.")
    document = await DocumentService(db).ingest(
        data,
        title=str(photo.get("title") or "photo"),
        media_type=str(photo["media_type"]),
        source="chat",
    )
    await db.flush()
    document_id = int(document.id or 0)
    title = str(photo.get("title") or "photo")
    await store_document(user_id_for(owner_user_id), document_id, title, 0, db)
    return document_id


async def file_document_execute(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.documents.service import DocumentService

    tag, _name = await place(
        db,
        owner_user_id,
        account_id=payload.account_id,
        party_id=payload.party_id,
        matter_id=payload.matter_id,
        transaction_id=payload.transaction_id,
    )
    entry = await chat_entry(db, payload.paste_id, owner_user_id)
    if entry is None:
        raise ValueError(
            f"{payload.paste_id!r} is not an attached document or photo. Only a "
            "file or photo that was attached can be filed; pasted text cannot."
        )
    made = not entry.get("document_id")
    document_id = (
        int(entry["document_id"])
        if not made
        else await _as_document(db, entry, owner_user_id)
    )
    await DocumentService(db).tag(document_id, tag)
    if payload.naming():
        await DocumentService(db).update(document_id, payload.naming())
    await DocumentService(db).add_figures(document_id, payload.figures)
    await db.flush()
    # A photo just made a document is read once the approval commits.
    return {"document_id": document_id, "filed_under": tag, "read": made}


async def file_document_read(result: dict[str, Any]) -> None:
    """After the commit: read the document a filed photo became, like any
    upload. Before it, the worker could look for a row not there yet."""
    from app.services.documents.domains.extraction import dispatch

    if result.get("read") and result.get("document_id"):
        await dispatch.start_extraction(
            int(result["document_id"]), owner_user_id=None, force=False
        )


async def file_document_describe(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    try:
        _tag, name = await place(
            db,
            owner_user_id,
            account_id=payload.account_id,
            party_id=payload.party_id,
            matter_id=payload.matter_id,
            transaction_id=payload.transaction_id,
        )
    except ValueError as exc:
        name = str(exc)
    found = await chat_entry(db, payload.paste_id, owner_user_id)
    said = (
        str(found.get("title") or "document") if found else f"paste {payload.paste_id}"
    )
    rows = [
        ChangeDisplayRow(
            label="Document",
            value=f"{said} → {payload.title}" if payload.title else said,
            scan=photo_key(found) is not None,
        ),
        ChangeDisplayRow(label="File under", value=name),
    ]
    if what := [str(v) for k, v in payload.naming().items() if k != "title"]:
        rows.append(ChangeDisplayRow(label="Is", value=" · ".join(what)))
    return rows + figure_rows(payload.figures)


def figure_rows(figures: dict[str, str]) -> list[ChangeDisplayRow]:
    """A form's figures on its card, each as printed, to check against
    the photo before they are kept (#442)."""
    return [
        ChangeDisplayRow(label=label, value=value) for label, value in figures.items()
    ]


def photo_key(entry: dict[str, Any] | None) -> str | None:
    """A photo kept from chat, not yet a document: its bytes in the store."""
    if (
        entry
        and not entry.get("document_id")
        and str(entry.get("media_type", "")).startswith("image/")
    ):
        return str(entry["key"])
    return None


async def file_document_scan(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> list[str]:
    """The photo the card files, so the card shows it (#430)."""
    key = photo_key(await chat_entry(db, payload.paste_id, owner_user_id))
    return [key] if key else []
