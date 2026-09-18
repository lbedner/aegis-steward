"""Reading a letter's demands, with a model, and believing almost none
of what it says.

Bullet points buried in agency prose are the one thing a pattern cannot
pull apart honestly, so this is where a model earns its place. What it
does not get is trust. Every item it returns has to name the page it
read and quote the line it read there, and ``checked`` throws away
anything the document cannot support before a card is ever proposed.

The division that makes this safe: the model READS, the rules DECIDE,
and a person approves. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from pydantic import BaseModel, ConfigDict, Field

from app.services.documents.domains.reading.findings import Page
from app.services.matters.changes import ITEM_KIND_KEYS

SURFACE = "documents:read-letter"

# The kind an unrecognised label falls back to. The SENTENCE is the
# fact; the kind is a label a person corrects in one click, and dropping
# a real demand over its label would lose more than it protects.
FALLBACK_KIND = "document"

INSTRUCTIONS = (
    "You read one letter and report only what it DEMANDS of the recipient. "
    "Every item must carry a quote copied EXACTLY from the page it names - "
    "character for character, not a tidied version. Each quote is checked "
    "against that page and the item is thrown away when it does not match, "
    "so copy first and write the demand afterwards. Report the date the "
    "letter was written and the date by which it must be answered, and only "
    "when the letter states them - never infer a deadline from a number of "
    "days. A letter that demands nothing has no items. Do not summarize, "
    "explain, advise, or add anything the letter does not say."
)


class ReadItem(BaseModel):
    """One demand, and where it was read.

    Every field says what it is for, because the schema is the
    instruction the model actually reads.
    """

    model_config = ConfigDict(extra="forbid")

    asked: str = Field(
        description="The demand, as one plain sentence a person can act on."
    )
    kind: str = Field(
        description="The kind of work it takes. Exactly one of: "
        + ", ".join(ITEM_KIND_KEYS)
        + ". Use 'document' when unsure."
    )
    page: int = Field(description="The page number the demand appears on.")
    quote: str = Field(
        description="Text copied EXACTLY from that page, character for "
        "character, showing the demand. It is checked against the page and "
        "the item is discarded when it does not match, so never tidy, "
        "shorten or paraphrase it."
    )


class LetterReading(BaseModel):
    """What a letter asks for, as the model reports it."""

    model_config = ConfigDict(extra="forbid")

    due_on: date | None = Field(
        default=None,
        description="The date the letter says it must be answered by, or "
        "null when it states none.",
    )
    received_on: date | None = Field(
        default=None,
        description="The date the letter was written or issued, or null "
        "when it states none.",
    )
    items: list[ReadItem] = Field(
        default=[], description="Every demand the letter makes, in its order."
    )


def _flat(text: str) -> str:
    """Text with its whitespace collapsed and its case dropped, so a
    quote is compared by what it SAYS rather than how it wrapped."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(frozen=True)
class Checked:
    """What survived checking, and how much did not.

    Separate from ``LetterReading`` because what the model SAID and what
    the document SUPPORTS are different things, and conflating them is
    how the second gets mistaken for the first.

    ``dropped`` is carried because a model copies a quote imperfectly
    about one demand in six. Five asks where the letter made six is a
    card somebody trusts as complete, so the card says it is not.
    """

    due_on: date | None
    received_on: date | None
    items: list[ReadItem]
    dropped: int


def checked(reading: LetterReading, pages: list[Page]) -> Checked | None:
    """The part of a reading the document itself supports.

    ``None`` when nothing survives: a letter that demands nothing, and a
    reading whose every item failed, are the same answer - propose
    nothing and let somebody read the page.
    """
    said = {page["page"]: _flat(page["text"] or "") for page in pages}
    kept: list[ReadItem] = []
    seen: set[str] = set()
    dropped = 0
    for item in reading.items:
        asked = " ".join(item.asked.split())
        quote = _flat(item.quote)
        if _flat(asked) in seen:
            continue
        if not asked or not quote:
            dropped += 1
            continue
        # The citation is the promise; a page the letter does not have,
        # or a line that is not on the page named, is a sentence the
        # model wrote rather than read.
        if quote not in said.get(item.page, ""):
            dropped += 1
            continue
        seen.add(_flat(asked))
        kept.append(
            item.model_copy(
                update={
                    "asked": asked,
                    "kind": item.kind if item.kind in ITEM_KIND_KEYS else FALLBACK_KIND,
                }
            )
        )
    if not kept:
        return None
    due = reading.due_on
    # A deadline before the letter was written is a misread, not a
    # deadline that has already passed.
    if due and reading.received_on and due < reading.received_on:
        due = None
    return Checked(
        due_on=due,
        received_on=reading.received_on,
        items=kept,
        dropped=dropped,
    )


async def read_letter(pages: list[Page]) -> LetterReading:
    """Hand the letter's text to the model and take back its reading."""
    from pydantic_ai import Agent as PydanticAgent

    from app.core.config import settings
    from app.services.ai.domains.llm import active_model
    from app.services.ai.usage_recording import extract_usage, record_usage

    model, model_name = await active_model.model_for_active(settings)
    agent: PydanticAgent[None, LetterReading] = PydanticAgent(
        model, instructions=INSTRUCTIONS, output_type=LetterReading
    )
    written = "\n\n".join(
        f"--- page {page['page']} ---\n{page['text']}" for page in pages if page["text"]
    )
    result = await agent.run(f"Read this letter.\n\n{written}")
    await record_usage(
        action=SURFACE, model_name=model_name, usage=extract_usage(result), user_id=None
    )
    return result.output


async def letter_reader():  # noqa: ANN201
    """The reader, or None when no model is reachable. A stack without
    the AI service reads paper and proposes its metadata as before;
    only the demands go unread."""
    from app.core.config import settings
    from app.services.ai.domains.llm import active_model
    from app.services.ai.domains.llm.providers import ProviderError

    try:
        await active_model.model_for_active(settings)
    except ProviderError:
        return None
    return read_letter
