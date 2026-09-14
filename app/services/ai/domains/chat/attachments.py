"""Chat image attachments: generic multimodal message parts.

One module owns the whole shape so every surface stays in step: the
wire model (``ChatAttachment``, what the API accepts), the model-call
content (``build_user_content``, what the agent actually sees) and the
history marker (``annotate_attachments``, what later turns know). Any
agent on any chat surface gets attachments by threading this module -
nothing here belongs to a particular service or screen.

The bytes ride the current turn only: history replays as text, so the
marker records that a screenshot was there without re-sending it every
turn. Whether the model can SEE the image is the model's own capability
(a vision-capable pick like a Qwen-VL class model); a text-only model
still receives the marker and can say so.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from pydantic import BaseModel

from app.core.log import logger
from app.core.storage import get_storage


class ChatAttachment(BaseModel):
    """One image part of a chat turn, base64 over the JSON body."""

    media_type: str
    data_b64: str
    name: str | None = None

    def decoded(self) -> bytes:
        try:
            return base64.b64decode(self.data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(
                f"attachment {self.name or self.media_type!r} is not valid base64"
            ) from None


def build_user_content(
    context: str, attachments: list[ChatAttachment] | None
) -> str | list[Any]:
    """The user prompt for the model call: the conversation context,
    followed by each image as binary content. With no attachments the
    plain string goes through untouched - the zero-cost common case."""
    if not attachments:
        return context
    # Lazy: pydantic_ai is only installed when it is the chat framework.
    from pydantic_ai.messages import BinaryContent

    parts: list[Any] = [context]
    for attachment in attachments:
        parts.append(
            BinaryContent(data=attachment.decoded(), media_type=attachment.media_type)
        )
    return parts


def annotate_attachments(message: str, attachments: list[ChatAttachment] | None) -> str:
    """Stamp the stored user message with what was attached.

    History replays as text only, so this marker is how a later turn
    (or a text-only model) knows images rode this one."""
    if not attachments:
        return message
    names = ", ".join(a.name or a.media_type for a in attachments)
    noun = "image" if len(attachments) == 1 else "images"
    return f"{message}\n\n[attached {len(attachments)} {noun}: {names}]"


async def persist_attachments(
    attachments: list[ChatAttachment] | None,
) -> list[dict[str, Any]]:
    """Keep the images, and describe where they went.

    The bytes ride one model call by design, but the picture itself is
    worth keeping: a reopened conversation should show the screenshot it
    is talking about, and a replay should not depend on session memory
    still holding megabytes. Each attachment is stored once - content
    addressing makes re-attaching the same image free - and the returned
    descriptors are what the message carries.

    Storage failing is not the user losing their turn: the question still
    goes to the model, the picture is simply not kept.
    """
    if not attachments:
        return []
    storage = get_storage()
    stored: list[dict[str, Any]] = []
    for attachment in attachments:
        # Decoded OUTSIDE the storage guard: a malformed payload is the
        # caller sending nonsense, not the store failing, and swallowing
        # it here would drop the image silently while logging a reason
        # that never happened.
        data = attachment.decoded()
        try:
            key = await storage.put(data, content_type=attachment.media_type)
        except OSError as exc:
            logger.warning(f"Could not store chat attachment: {exc}")
            continue
        stored.append(
            {
                "key": key,
                "media_type": attachment.media_type,
                "name": attachment.name,
            }
        )
    return stored


def attachment_metadata(stored: list[dict[str, Any]]) -> dict[str, Any]:
    """Message metadata for stored attachments, or nothing at all.

    A message that carried no image should read as one, so this returns
    an empty dict rather than a key holding an empty list.
    """
    return {"attachments": stored} if stored else {}


async def prepare_turn(
    message: str, attachments: list[ChatAttachment] | None, user_id: str = ""
) -> tuple[str, dict[str, Any]]:
    """The stored message text and metadata for one turn.

    Both chat paths - streaming and not - need the same steps in the
    same order, and doing them by hand in each is how one of them ends
    up missing a step. One call, both callers.

    A wall of PASTED text is lifted out of the message here (see
    ``pastes``): the bytes go to the store, a one-line marker stands
    where the wall was, and the agent reads the text back on demand.
    Doing it at this door means every surface gets it - the browser, the
    API, a CLI - rather than whichever one remembered to.
    """
    # A PDF is READ, not shipped: the documents service takes the bytes,
    # extracts them page by page, and the text joins the message as a
    # marker like a pasted page. Only the images ride on as bytes.
    images = [a for a in attachments or [] if a.media_type.startswith("image/")]
    documents = [a for a in attachments or [] if not a.media_type.startswith("image/")]
    text, read = await read_documents(message, documents, user_id)
    text, lifted = await lift_pastes(text, user_id)
    stored = await persist_attachments(images)
    # The markers ``read_documents`` just wrote are markers, so
    # ``lift_pastes`` reports them too: the lists overlap by
    # construction and the message must not draw two chips for one file.
    pastes = _by_id((read.get("pastes") or []) + (lifted.get("pastes") or []))
    return annotate_attachments(text, images), {
        **({"pastes": pastes} if pastes else {}),
        **attachment_metadata(stored),
    }


def _by_id(pastes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pastes, first mention kept, in the order they were named."""
    seen: dict[str, dict[str, Any]] = {}
    for paste in pastes:
        seen.setdefault(str(paste.get("id")), paste)
    return list(seen.values())


# Why a document produced no text, in the words the reader needs. The
# two cases are not the same advice and must not share a message: a scan
# is answered with a screenshot, and a reader that could not RUN is
# answered by nobody - the text is simply not there yet, and telling
# someone to screenshot a perfectly good PDF sends them to do work that
# will not help. Live: the extraction library was missing from a running
# image, and every statement came back reported as a scan.
UNREAD = {
    "unreadable": (
        "no text could be read from it - most likely a scan with no text "
        "layer. A screenshot of the part that matters will work"
    ),
    "broken": (
        "it could not be processed, which is not the same as it being "
        "unreadable - the text may be perfectly good. Say so rather than "
        "guessing at its contents"
    ),
}


async def read_documents(
    message: str, documents: list[ChatAttachment], user_id: str
) -> tuple[str, dict[str, Any]]:
    """Ingest and extract each attached document; append its marker.

    A document that cannot be read says so IN the message rather than
    vanishing: an attachment the user watched upload and then never
    hears about again reads as the app losing it.
    """
    from app.services.ai.domains.chat import pastes

    if not documents or not user_id:
        return message, {}
    kept: list[dict[str, Any]] = []
    lines: list[str] = []
    for attachment in documents:
        name = attachment.name or "document"
        reason = "unreadable"
        try:
            entry = await ingest_and_read(attachment, name, user_id)
        except Exception as exc:  # noqa: BLE001 - see below
            # Deliberately broad: this is a third-party parser over bytes
            # somebody uploaded, and a turn must not be lost to a
            # malformed file. But WHY it failed is not the same answer -
            # see below.
            logger.warning(f"Could not read attached document: {exc}")
            entry, reason = None, "broken"
        if entry is None:
            lines.append(f"[{name}: {UNREAD[reason]}]")
            continue
        kept.append(entry)
        lines.append(pastes.marker(entry))
    text = "\n\n".join([message, *lines]) if lines else message
    return text.strip(), {"pastes": kept} if kept else {}


async def ingest_and_read(
    attachment: ChatAttachment, name: str, user_id: str
) -> dict[str, Any] | None:
    """Store one document, read it, and index it for the agent.

    Extraction runs INLINE rather than on the worker: the person is
    waiting on the answer that needs it, and a queued job would have the
    turn reply about a document it cannot see yet.
    """
    from app.core.db import get_async_session
    from app.services.ai.domains.chat import pastes
    from app.services.documents.domains.extraction.jobs import run_extraction
    from app.services.documents.service import DocumentService

    async with get_async_session() as session:
        document = await DocumentService(session).ingest(
            attachment.decoded(),
            title=name,
            media_type=attachment.media_type,
            source="chat",
        )
        await session.commit()
        document_id = int(document.id or 0)
    if not document_id:
        return None
    await run_extraction(
        document_id, owner_user_id=None, force=False, report=lambda _: None
    )
    text = await pastes.document_text(document_id)
    if not text:
        return None
    return await pastes.store_document(user_id, document_id, name, len(text))


async def lift_pastes(message: str, user_id: str) -> tuple[str, dict[str, Any]]:
    """Replace each wall of text in ``message`` with its marker.

    Storing is best-effort by design: a store that refuses must not cost
    the user their turn, so the paste stays in the message text, which
    is exactly what used to happen to every paste.
    """
    from app.services.ai.domains.chat import pastes

    if not user_id:
        return message, {}
    # Already lifted by the composer, which knew it was a paste: the
    # markers are the record of what this message carries.
    already = await pastes.named_pastes(user_id, message)
    text, blocks = pastes.lift(message)
    if not blocks:
        return message, {"pastes": already} if already else {}
    kept: list[dict[str, Any]] = []
    for block in blocks:
        try:
            paste = await pastes.store_paste(
                user_id, block, title=pastes.title_of(block)
            )
        except OSError as exc:
            logger.warning(f"Could not store pasted text: {exc}")
            text = text.replace(pastes.PLACEHOLDER, block, 1)
            continue
        kept.append(paste)
        text = text.replace(pastes.PLACEHOLDER, pastes.marker(paste), 1)
    found = already + kept
    return text, {"pastes": found} if found else {}
