"""Mail host tool: what came in, as the intake page sees it (MI-06).

One tool, one call, the same rows the intake page reads - the page and
the assistant cannot disagree about what arrived or what it became.
There is no write tool here; a message that wants filing raises a card.
"""

from __future__ import annotations

from typing import Any

from app.core.db import get_async_session
from app.services.ai.domains.chat.tools import register_tool
from app.services.mail.arrivals import arrivals as _arrivals


async def arrivals(days: int = 1) -> dict[str, Any]:
    """What came in by mail, grouped by the day it arrived, newest first.

    Args:
        days: How many of the most recent days WITH mail to report. The
            default 1 is "today, or the most recent day anything came in":
            when nothing arrived today the latest day is returned instead,
            so the answer is never an empty "nothing" while recent mail
            exists. Say which date you are reporting.

    Returns a dict with key 'days': a list of {'date': 'YYYY-MM-DD',
    'messages': [...]}. Each message carries 'id', 'received_at',
    'sent_at', 'sender', 'address', 'subject', 'party' ({'id','name'}
    when filed under someone - the id is what `parties` reports),
    'letter_id' and 'attachment_ids' (documents, each readable with
    `paper`), 'card' (the status of the card offering to add the sender
    as a contact, or null) and 'became' - the one sentence saying what
    the message turned into, or why nothing. Repeat 'became' to the
    user as written; do not reword it.
    """
    async with get_async_session() as db:
        found = await _arrivals(db, owner_user_id=None)
    return {
        "days": [
            {
                "date": day.date.isoformat(),
                "messages": [row.as_dict() for row in day.messages],
            }
            for day in found[: max(days, 1)]
        ]
    }


register_tool(
    "arrivals",
    arrivals,
    description="What came in by mail and what each message became, by day",
    replace=True,
)
