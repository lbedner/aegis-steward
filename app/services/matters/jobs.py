"""Scheduler jobs for matters.

Its own module beside ``finance/jobs.py`` and the same shape: an inline
async job that opens its own session and commits, because services never
commit themselves.
"""

import logging

from app.core.db import get_async_session

logger = logging.getLogger(__name__)


async def matters_deadline_nag_job() -> None:
    """Daily: raise an alert for every open request due soon or late, and
    retract the ones whose work is done.

    Early, before the day's work: a deadline you learn about in the
    morning is one you can still do something about (ST-09).
    """
    try:
        async with get_async_session() as session:
            from app.services.matters.deadlines import nag

            raised = await nag(session, owner_user_id=None)
            await session.commit()
        logger.info("Matters: raised %d deadline alert(s)", raised)
    except Exception:
        logger.exception("Matters deadline nag failed")
