"""A pasted wall of text, stored once and read on demand.

People paste pages. An order listing, a log, a spreadsheet dump: 9,000
to 17,000 characters at a time, and every one of them went into the
message text. That is wrong twice over.

It is wrong on screen, where the transcript becomes the paste and the
conversation disappears into it. And it is wrong in the model call,
because history is REPLAYED: the same page rides every later turn until
the budget pushes it out. Measured on one real session - twelve pasted
pages, 144,196 characters - the pages alone accounted for 1,422,367
characters of replayed history across 102 turns, about ten times over,
and they still fell out of the window by the end.

So a big paste stops being message text. The bytes go to the object
store (content-addressed, so pasting the same page twice is free), a
one-line MARKER goes in the message, and the agent reads the text back
with ``pasted`` when it actually needs it. Twelve pages then cost twelve
lines of history instead of 144k characters, and nothing evicts them.

The index lives beside the saved facts and readings in
``agent_user_memory``: one row per user, so a paste is still reachable
from the next conversation - the same reason readings moved there.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from app.core.log import logger
from app.core.storage import get_storage
from app.services.ai.domains.chat.tools import register_tool
from app.services.ai.domains.chat.user_memory import (
    current_user_id,
    load_user_pastes,
    store_user_pastes,
)

# What counts as "a wall of text" rather than a long question. Below it
# the paste is just what someone typed and belongs in the message; above
# it, it is a document they brought.
PASTE_THRESHOLD = 2_000

# The index is a line each; the blobs live in storage. Bounded anyway,
# so a year of pasting cannot grow the row without limit.
MAX_PASTES_PER_USER = 50

# Short enough for a model to quote back without fumbling it, long
# enough that two pastes will not collide.
ID_LENGTH = 8

# Stands in the message where a wall of text was, until the paste is
# stored and knows its own id.
PLACEHOLDER = "\x00paste\x00"

# The id inside a marker this module wrote.
_MARKER_ID = re.compile(r"\[pasted text #([0-9a-f]+)")


def paste_id(key: str) -> str:
    """The handle the agent uses, from the storage key.

    The key is ``sha256/ab/cd/<64 hex>`` - 78 characters, and asking a
    model to reproduce one exactly is asking for a hallucinated id. The
    digest's first bytes are as unique as they need to be here.
    """
    return key.rsplit("/", 1)[-1][:ID_LENGTH]


def marker(paste: dict[str, Any]) -> str:
    """The line that stands in for the paste in the message text.

    It says what the paste IS and how to read it, because a marker the
    agent cannot act on is just a hole in the conversation.
    """
    return (
        f"[pasted text #{paste['id']}"
        f"{': ' + paste['title'] if paste.get('title') else ''}"
        f" - {paste['chars']:,} characters."
        f' Read it with pasted("{paste["id"]}")]'
    )


def ids_in(text: str) -> list[str]:
    """The paste ids a message already names.

    The composer stores a paste the moment it is pasted and puts the
    marker in the box, so by the time a message arrives its walls are
    already lifted. The markers are then the record of WHICH pastes the
    message carries - the chips under it are drawn from these.
    """
    return _MARKER_ID.findall(text)


async def named_pastes(user_id: str, text: str) -> list[dict[str, Any]]:
    """The descriptors for every paste a message's markers name."""
    wanted = ids_in(text)
    if not wanted:
        return []
    index = {p["id"]: p for p in await load_user_pastes(user_id)}
    return [index[i] for i in wanted if i in index]


def title_of(block: str) -> str:
    """A name for a wall of text: its first line that says anything.

    The reader has to recognise the paste in a chip, and the agent has
    to recognise it in a marker, so the name comes from the text rather
    than a counter. Pages open with navigation furniture - "All", "Cart",
    "Home" - so a line of one short word is skipped, but the threshold
    stays low enough that a real heading ("Your Orders") still wins.
    """
    for line in block.splitlines():
        line = line.strip()
        if len(line) > 8:
            return line[:60]
    return "pasted text"


def split_blocks(text: str) -> list[str]:
    """The message, cut where a paste is likely to start and end.

    A page almost never arrives alone: it comes with the sentence that
    introduces it ("here is another page"), and lifting the whole
    message would take the question with it. Blank lines are where a
    paste joins what someone actually wrote.
    """
    return text.split("\n\n")


def lift(text: str) -> tuple[str, list[str]]:
    """Pull the walls of text out of a message, in place.

    Returns the message with a placeholder where each wall was, and the
    walls themselves. A message that is only a long question comes back
    untouched with nothing lifted - the threshold is about size, but the
    UNIT is a block, so a single paragraph someone really typed is left
    alone unless it is itself enormous.
    """
    if len(text) <= PASTE_THRESHOLD:
        return text, []
    kept: list[str] = []
    lifted: list[str] = []
    for block in split_blocks(text):
        if len(block) > PASTE_THRESHOLD:
            kept.append(PLACEHOLDER)
            lifted.append(block)
        else:
            kept.append(block)
    return "\n\n".join(kept), lifted


async def store_paste(
    user_id: str, text: str, title: str | None = None
) -> dict[str, Any]:
    """Keep the text, index it for this user, and describe it.

    Returns the descriptor the message carries. Storage failing is not
    the user losing their paste: the caller falls back to sending it as
    message text, which is what used to happen to every paste.
    """
    data = text.encode("utf-8")
    storage = get_storage()
    key = await storage.put(data, content_type="text/plain")
    paste = {
        "id": paste_id(key),
        "key": key,
        "title": title or "pasted text",
        "chars": len(text),
        "at": datetime.now(UTC).isoformat(),
    }
    index = await load_user_pastes(user_id)
    # Re-pasting the same page is the same bytes and so the same id;
    # keep one entry rather than a row per paste of it.
    index = [p for p in index if p.get("id") != paste["id"]]
    index.append(paste)
    await store_user_pastes(user_id, index[-MAX_PASTES_PER_USER:])
    return paste


async def read_paste(
    user_id: str, paste_id: str
) -> tuple[dict[str, Any], str] | None:
    """The descriptor and the text, or None when there is no such paste.

    The one lookup. ``pasted`` is the agent's door onto it and the
    viewer route is the reader's; neither re-implements the search.
    """
    index = await load_user_pastes(user_id)
    match = next((p for p in index if p.get("id") == paste_id), None)
    if match is None:
        return None
    data = await get_storage().get(str(match["key"]))
    if data is None:
        logger.warning("paste index points at missing bytes", paste_id=paste_id)
        return None
    return match, data.decode("utf-8", errors="replace")


async def pasted(paste_id: str) -> str:
    """Read back a block of text the user pasted earlier.

    A pasted page - an order listing, a log, a statement - is stored
    once and stands in the conversation as a one-line marker naming its
    id, because replaying the whole page into every later turn is what
    pushes the rest of the conversation out of your context. Call this
    with the id from the marker the moment you need the CONTENT, and
    call it again in a later turn rather than trying to remember it: the
    text does not change, and the stored copy outlives the conversation
    it was pasted into.
    """
    user_id = current_user_id.get()
    if not user_id:
        return "No user context available; cannot read that paste."
    found = await read_paste(user_id, paste_id)
    if found is None:
        known = ", ".join(p["id"] for p in await load_user_pastes(user_id)) or "none"
        return f"No paste with id {paste_id!r}. Pastes on file: {known}."
    return found[1]


# Built-in registration: importing this module makes the tool grantable
# via the agent registry. replace=True keeps re-imports idempotent.
register_tool(
    "pasted",
    pasted,
    description="Read back a block of text the user pasted earlier",
    replace=True,
)
