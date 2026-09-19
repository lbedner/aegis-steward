"""An open request nags BEFORE it is late, not after.

The deadline already sits on the request, and the sidebar already shows
a dot once it has passed. That is the wrong moment: a county renewal
missed by two days is a benefit stopped, and "it went red this morning"
is not a warning, it is a post-mortem.

So the deadline surfaces where everything else needing attention already
lives - the insight list, read by the Attention queue, the overview
banner and the analyst's own briefing - rather than growing a fourth
place that has to be looked at.

Retraction is half the feature. An alert nobody can clear is an alert
everybody learns to scroll past, so a deadline that has been answered,
waived, moved or deleted takes its alert with it (ST-09).
"""

from __future__ import annotations

from datetime import date, timedelta

# How long before a deadline is worth saying something. Two weeks: long
# enough to find a statement a bank posts monthly, short enough that the
# alert is still true when it arrives. An alert that turns up in July for
# a December renewal is one somebody dismisses while it is still true.
WITHIN_DAYS = 14

INSIGHT_TYPE = "matter_due"


def _key(request_id: int, due_on: date, late: bool) -> str:
    """One alert per request and deadline, and a DIFFERENT one once the
    day passes: "due in 9 days" and "9 days late" are not the same fact,
    and the reader must not be left looking at the first."""
    return f"{INSIGHT_TYPE}:{request_id}:{due_on.isoformat()}:{'late' if late else 'soon'}"


def _said(
    matter_title: str, due_on: date, today: date, late: bool
) -> tuple[str, str, str]:
    """The severity, the title and the body, in the words a person would
    use about their own case.

    Told whether it is late rather than working it out again: whether a
    request is past its deadline is ``requests.overdue``'s question, and
    a second reading of the same comparison is how two screens come to
    disagree about one date.
    """
    days = (due_on - today).days
    if late:
        late = -days
        return (
            "critical",
            f"{matter_title}: {late} day{'s' if late != 1 else ''} past the deadline",
            f"The agency asked for this by {due_on.isoformat()} and the "
            "request is still open. Late is the one state nobody can fix "
            "by working faster.",
        )
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    return (
        "warning",
        f"{matter_title}: due {when}",
        f"An open request on this matter is due {due_on.isoformat()}. "
        "Anything still outstanding needs finding before then, not after.",
    )


async def due_soon(
    db, *, today: date | None = None, within_days: int = WITHIN_DAYS
) -> list:
    """Every open request due inside the window, late ones first.

    One query behind both doors: the alert this module raises and the
    count the overview banner draws. Two readings of "what is coming up"
    is how a banner says three and a list shows two.
    """
    from sqlmodel import col, select

    from app.services.finance.utils import current_date
    from app.services.matters.models import Request

    today = today or current_date()
    return list(
        (
            await db.exec(
                select(Request)
                .where(col(Request.status) == "open")
                .where(col(Request.deleted_at).is_(None))
                .where(col(Request.due_on).is_not(None))
                .where(col(Request.due_on) <= today + timedelta(days=within_days))
                .order_by(col(Request.due_on))
            )
        ).all()
    )


async def nag(
    db,
    *,
    owner_user_id: int | None,
    today: date | None = None,
    within_days: int = WITHIN_DAYS,
) -> int:
    """Raise an alert for every open request due soon or already late,
    and retract the ones whose work is done. Returns what it created.

    Idempotent: running it twice raises one alert, because the dedup key
    is the request, its deadline and which side of it today falls on.
    """
    from sqlmodel import col, select

    from app.services.finance.domains.detection.insights.rules import (
        create_insight_if_new,
    )
    from app.services.finance.models import FinanceInsight
    from app.services.finance.utils import current_date
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import overdue as is_overdue

    today = today or current_date()
    store_owner = 0 if owner_user_id is None else owner_user_id
    due = await due_soon(db, today=today, within_days=within_days)

    # The matters those requests belong to, in one query rather than one
    # per request: a shelf of twenty letters is twenty deadlines.
    # The matters behind those deadlines, in one query: a shelf of
    # letters is a list of deadlines, and asking per letter is the walk
    # the answer sheet was just cured of.
    wanted = {request.matter_id for request in due}
    titles = {
        matter.id: matter
        for matter in await MatterService(db).find()
        if matter.id in wanted
    }

    standing: set[str] = set()
    created = 0
    for request in due:
        matter = titles.get(request.matter_id)
        if matter is None:
            continue
        # The one predicate for "past its deadline and still wanting
        # something", not a second reading of the same comparison: the
        # sidebar's dot and this alert must never disagree about what is
        # late.
        late = is_overdue(request, today)
        key = _key(request.id, request.due_on, late)
        standing.add(key)
        severity, title, body = _said(matter.title, request.due_on, today, late)
        if await create_insight_if_new(
            db,
            owner_user_id=store_owner,
            insight_type=INSIGHT_TYPE,
            dedup_key=key,
            severity=severity,
            title=title,
            body=body,
        ):
            created += 1

    # Everything this rule has ever said that it would not say now: the
    # request was answered, waived, deleted, or its deadline moved.
    stale = [
        alert
        for alert in (
            await db.exec(
                select(FinanceInsight)
                .where(col(FinanceInsight.owner_user_id) == store_owner)
                .where(col(FinanceInsight.insight_type) == INSIGHT_TYPE)
            )
        ).all()
        if alert.dedup_key not in standing
    ]
    for alert in stale:
        await db.delete(alert)
    if stale:
        await db.flush()
    return created
