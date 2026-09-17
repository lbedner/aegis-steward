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
    """Which document belongs where: an account, a contact or a matter,
    exactly one of them.

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

    @model_validator(mode="after")
    def _one_place(self) -> FileDocumentPayload:
        given = [
            v for v in (self.account_id, self.party_id, self.matter_id) if v is not None
        ]
        if len(given) != 1:
            raise ValueError("Exactly one of account_id, party_id, matter_id.")
        return self


async def _place(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> tuple[str, str]:
    """The tag that files it and the name of the place, or a ValueError
    naming what was not found."""
    from app.services.finance.constants import account_tag
    from app.services.finance.domains.ledger.accounts import get_account
    from app.services.matters.matters import MatterService
    from app.services.matters.models import matter_tag, party_tag
    from app.services.matters.service import PartyService

    if payload.account_id is not None:
        account = await get_account(db, payload.account_id, owner_user_id=owner_user_id)
        if account is None:
            raise ValueError(f"Account {payload.account_id} not found.")
        return account_tag(payload.account_id), account.name
    if payload.party_id is not None:
        party = await PartyService(db).get(payload.party_id)
        if party is None:
            raise ValueError(f"Contact {payload.party_id} not found.")
        return party_tag(payload.party_id), party.name
    matter = await MatterService(db).get(payload.matter_id or 0)
    if matter is None:
        raise ValueError(f"Matter {payload.matter_id} not found.")
    return matter_tag(matter.id), matter.title


async def _filed(
    db: AsyncSession, paste_id: str, owner_user_id: int | None
) -> tuple[int, str] | None:
    """The document a paste id names, as (id, title), or None."""
    from app.services.ai.domains.chat.user_memory import load_user_pastes
    from app.services.finance.domains.detection.analyst.shared import user_id_for

    # The same mapping the agent's own deps use, not a second guess at
    # what a finance owner is called on the chat side.
    for paste in await load_user_pastes(user_id_for(owner_user_id), db):
        if paste.get("id") == paste_id and paste.get("document_id"):
            return int(paste["document_id"]), str(paste.get("title") or "document")
    return None


async def file_document_execute(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.documents.service import DocumentService

    tag, _name = await _place(db, payload, owner_user_id)
    found = await _filed(db, payload.paste_id, owner_user_id)
    if found is None:
        raise ValueError(
            f"{payload.paste_id!r} is not an attached document. Only a file "
            "that was attached and read can be filed; pasted text cannot."
        )
    document_id, _ = found
    await DocumentService(db).tag(document_id, tag)
    await db.flush()
    return {"document_id": document_id, "filed_under": tag}


async def file_document_describe(
    db: AsyncSession, payload: FileDocumentPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    try:
        _tag, name = await _place(db, payload, owner_user_id)
    except ValueError as exc:
        name = str(exc)
    found = await _filed(db, payload.paste_id, owner_user_id)
    return [
        ChangeDisplayRow(
            label="Document",
            value=found[1] if found else f"paste {payload.paste_id}",
        ),
        ChangeDisplayRow(label="File under", value=name),
    ]
