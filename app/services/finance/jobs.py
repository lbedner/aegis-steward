"""Scheduler jobs for the finance service.

Inline async jobs (mirrors ``app/services/insights/jobs.py``), NOT a worker
queue: APScheduler awaits the coroutine directly, so this is backend-agnostic.
Each job opens its own session and commits (services never commit themselves).
"""

import logging
from typing import Any

from app.core.db import get_async_session
from app.services.finance.domains.detection import (
    detect_recurring,
    generate_insights,
    promote_curated_streams,
)
from app.services.finance.domains.ledger.accounts import redraw_line
from app.services.finance.domains.ledger.queries import account_owner_ids
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

logger = logging.getLogger(__name__)


async def finance_recompute_snapshots_job() -> None:
    """Nightly: materialize per-account balance + per-user net-worth snapshots.

    Recomputes per owner so net worth stays isolated across users (a single
    ``None`` owner in standalone/no-auth mode). The whole line, every
    night: a rule that changes which accounts count (#314) reaches every
    day it was drawn for, not only the last month.
    """
    try:
        async with get_async_session() as session:
            owners = await account_owner_ids(session)
            days_written = 0
            for owner_user_id in owners:
                await detect_recurring(session, owner_user_id=owner_user_id)
                await promote_curated_streams(session, owner_user_id=owner_user_id)
                await generate_insights(session, owner_user_id=owner_user_id)
                days_written += await redraw_line(session, owner_user_id)
            await session.commit()
        logger.info(
            "Finance: recomputed net-worth snapshots for %d owner(s), %d day(s)",
            len(owners),
            days_written,
        )
    except Exception:
        logger.exception("Finance snapshot recompute failed")


async def finance_goal_auto_contribute_job() -> None:
    """Monthly (the 1st): book each toggled-on virtual goal's declared
    amount as a goal_auto valuation - the plan saves unless actively
    paused (per-goal opt-in). Idempotent per month, so a scheduler
    catch-up run later in the month books nothing twice."""
    try:
        async with get_async_session() as session:
            service = FinanceService(session)
            owners = await account_owner_ids(session)
            booked = 0
            for owner_user_id in owners:
                booked += await service.auto_contribute_goals(
                    owner_user_id=owner_user_id, today=current_date()
                )
            await session.commit()
        logger.info("Finance: auto-contributed to %d goal(s)", booked)
    except Exception:
        logger.exception("Finance goal auto-contribute failed")


async def finance_budget_close_job() -> None:
    """Monthly (the 5th): record last month as it ended, so it reads back
    the same however its bills change later (#319). The 5th, not the 1st:
    the charges of its last days post a few days late. Idempotent: a
    month is recorded once."""
    from app.services.finance.domains.planning import budgets
    from app.services.finance.utils import current_period_month, shift_period

    try:
        async with get_async_session() as session:
            owners = await account_owner_ids(session)
            ended = shift_period(current_period_month(), -1)
            for owner_user_id in owners:
                await budgets.close_month(
                    session, owner_user_id=owner_user_id, period_month=ended
                )
            await session.commit()
        logger.info(
            "Finance: recorded budget month %d for %d owner(s)", ended, len(owners)
        )
    except Exception:
        logger.exception("Finance budget month close failed")


async def finance_file_blank_rows_job() -> None:
    """Daily: every synced row nobody filed, filed where its payee now
    settles (#541). A sync names and files what it brings; a row that
    arrived before its payee was named, merged or given a default stayed
    blank, and the sync only reads the last few days."""
    from app.services.finance.domains.ledger import payee_aliases

    try:
        async with get_async_session() as session:
            filed = 0
            owners = await account_owner_ids(session)
            # A newer payee key re-keys the rows first, and the memory
            # follows, so tonight's filing reads the new keys (#542).
            service = FinanceService(session)
            if await service.rekey_payees():
                for owner_user_id in owners:
                    await service.recompute_payee_aliases(owner_user_id=owner_user_id)
            for owner_user_id in owners:
                filed += await payee_aliases.file_blank_rows(
                    session, owner_user_id=owner_user_id
                )
            await session.commit()
        logger.info("Finance: filed %d blank row(s)", filed)
    except Exception:
        logger.exception("Finance blank-row filing failed")


async def finance_envelope_credit_job() -> None:
    """Monthly (the 1st): book each auto-credit-on envelope's monthly
    credit - the allowance arrives whether anyone logs in or not.
    Idempotent per month, catch-up safe."""
    try:
        async with get_async_session() as session:
            service = FinanceService(session)
            owners = await account_owner_ids(session)
            booked = 0
            for owner_user_id in owners:
                booked += await service.auto_credit_envelopes(
                    owner_user_id=owner_user_id, today=current_date()
                )
            await session.commit()
        logger.info("Finance: auto-credited %d envelope(s)", booked)
    except Exception:
        logger.exception("Finance envelope auto-credit failed")


async def finance_analyst_note_job() -> None:
    """Nightly, after the rules: one plain-language note per owner.

    Runs behind the recompute job on purpose - the note is written from the
    findings that pass produced, so it has to see tonight's, not last night's.
    Each owner is isolated: one owner's model failure must not cost the others
    their note. ``run_analyst_note`` already absorbs its own errors, so this
    only has to guard the session.
    """
    from app.services.finance.domains.detection.analyst import run_analyst_note

    try:
        # Two short transactions per owner, not one long one for all of
        # them. The owners query is its own; ``run_analyst_note`` opens
        # what it needs and lets go around the model call, which is the
        # whole point - this job used to hold the write lock across every
        # model call it made, and SQLite has one writer (2026-09-20).
        async with get_async_session() as session:
            owners = await account_owner_ids(session)

        written = 0
        for owner_user_id in owners:
            note = await run_analyst_note(
                get_async_session, owner_user_id=owner_user_id
            )
            written += 1 if note is not None else 0
        logger.info(
            "Finance: analyst notes ready for %d of %d owner(s)",
            written,
            len(owners),
        )
    except Exception:
        logger.exception("Finance analyst note run failed")


async def finance_bill_due_email_job() -> None:
    """Daily: one mail and one notification listing the bills due in the
    next few days.

    Comms' first finance use. It mails nobody until
    ``FINANCE_BILL_EMAIL_TO`` is set, so a fresh project never mails
    anyone by surprise; a device that turned notifications on asked for
    them, so it is told whatever the mail setting. Nothing goes out on a
    quiet day. The window and the shaping are the ones Settings previews,
    so what the page shows and what lands in the inbox cannot drift apart.

    One address means one mail: every owner's bills are gathered into it
    rather than sent as separate, indistinguishable copies to the same
    inbox. A single owner that fails costs only its own lines.
    """
    from app.core.config import settings
    from app.services.comms import push
    from app.services.comms.service import send_email_simple
    from app.services.finance.domains.planning.recurring.forecast import (
        bill_reminder,
        upcoming_outflows,
    )
    from app.services.finance.service import FinanceService

    recipient = settings.FINANCE_BILL_EMAIL_TO
    days = settings.FINANCE_BILL_EMAIL_DAYS
    bills: list[dict[str, Any]] = []
    try:
        async with get_async_session() as session:
            owners = await account_owner_ids(session)
            service = FinanceService(session)
            for owner_user_id in owners:
                try:
                    projection = await service.project_balances(
                        owner_user_id=owner_user_id, days=days
                    )
                except Exception:
                    logger.exception(
                        "Finance bill reminder: owner %s could not be projected",
                        owner_user_id,
                    )
                    continue
                bills.extend(upcoming_outflows(projection))
    except Exception:
        logger.exception("Finance bill reminder could not read the ledger")
        return

    if not bills:
        return
    reminder = bill_reminder(bills, days)
    lines = "\n".join(
        f"- {b['name']}: ${b['amount'] / 100:,.2f} on {b['due_date'] or b['date']}"
        for b in reminder["bills"]
    )
    subject, total = reminder["subject"], reminder["total"]
    try:
        async with get_async_session() as session:
            await push.send_push(session, subject, lines)
    except Exception:
        logger.exception("Finance bill reminder could not be pushed")
    if not recipient:
        return
    try:
        await send_email_simple(
            to=recipient,
            subject=subject,
            text=f"{lines}\n\n${total / 100:,.2f} in total.",
        )
    except Exception:
        logger.exception("Finance bill reminder could not be sent")
        return
    logger.info("Finance: bill reminder sent for %d bill(s)", len(bills))


async def finance_sync_connections_job() -> None:
    """Periodic: refresh every provider connection (accounts, balances, new
    transactions, holdings) per owner. ``sync_owner_connections`` dispatches on
    each connection's provider and also recomputes net worth, so the register
    and Overview trend stay current without a reconnect. SnapTrade activities
    are internally throttled to one pull per account per day (their polling
    budget), so a more frequent schedule stays within it.
    """
    from app.services.documents.service import receipts
    from app.services.finance.adapters.providers import connections
    from app.services.finance.adapters.providers.queries import connected_owner_ids

    try:
        async with get_async_session() as session:
            owners = await connected_owner_ids(
                session, tuple(connections.registry.ADAPTERS)
            )
            added = 0
            for owner_user_id in owners:
                results = await connections.sync_owner_connections(
                    session, owner_user_id=owner_user_id
                )
                added += sum(r.added for r in results)
                # A charge a waiting receipt is for may have posted (#330).
                await receipts.match_waiting(session, owner_user_id=owner_user_id)
            await session.commit()
        logger.info(
            "Finance: synced connections for %d owner(s), %d new txn(s)",
            len(owners),
            added,
        )
    except Exception:
        logger.exception("Finance connection sync failed")
