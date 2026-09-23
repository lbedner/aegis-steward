"""Matters: the cases letters are episodes of.

Its own sidebar section, unlike People. A party is maintained; a matter
is navigated TO - it is the thing you open when a letter arrives with a
deadline on it, and it is where the documents, the requests and the
answers hang.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.web_frontend.documents import PAPER_COLUMNS
from app.components.web_frontend.filters import parse_date
from app.components.web_frontend.nav import matter_tabs, section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    render,
    templates,
    where_from,
)
from app.components.web_frontend.routes.facts import facts_for
from app.components.web_frontend.routes.matter_papers import matter_papers
from app.core.db import get_async_session
from app.services.finance.deps import get_owner_user_id
from app.services.matters.answers import answer_sheet
from app.services.matters.drawing import drawn as drawn_request
from app.services.matters.matters import MatterService, summarised
from app.services.matters.models import PARTICIPANT_ROLES
from app.services.matters.requests import RequestService
from app.services.matters.service import PartyService, party_or_new

SECTION = section("matters")
router = APIRouter(prefix=SECTION.path)

MATTER_COLUMNS = (
    # A page, not a modal: a matter is worked for weeks, read beside a
    # letter and come back to, and none of that survives in a dialog.
    {"key": "title", "label": "Matter", "kind": "page"},
    {"key": "kind", "label": "Kind"},
    {"key": "reference", "label": "Their reference"},
    {"key": "who", "label": "With", "kind": "contact"},
    {"key": "state", "label": "", "kind": "status"},
)


def _row(matter: Any, names: dict[int, str], late: set[int]) -> dict[str, Any]:
    return {
        "id": matter.id,
        "title": {
            "label": matter.title,
            "url": f"{SECTION.path}/{matter.id}",
        },
        "kind": (matter.kind or "").title(),
        "reference": matter.reference or "",
        "who": (
            {
                "id": matter.counterpart_party_id,
                "label": names.get(matter.counterpart_party_id, ""),
            }
            if matter.counterpart_party_id
            else ""
        ),
        # Overdue outranks open: a case with a missed deadline is the
        # row the reader is looking for.
        "state": (
            {"label": "overdue", "tone": "error"}
            if matter.id in late
            else {
                "label": matter.status,
                "tone": "ok" if matter.status == "open" else "muted",
            }
        ),
    }


@router.get("", include_in_schema=False)
async def page(
    request: Request, owner_user_id: int | None = Depends(get_owner_user_id)
) -> Response:
    """Every case, open ones first."""
    async with get_async_session() as db:
        matters = await MatterService(db).find(owner_user_id=owner_user_id)
        names = await PartyService(db).names()
        late = {r.matter_id for r in await RequestService(db).overdue()}
        return render(
            request,
            "pages/matters.html",
            {
                "section": SECTION,
                "rows": [_row(m, names, late) for m in matters],
                "columns": list(MATTER_COLUMNS),
                "path": SECTION.path,
            },
        )


@router.get("/attention", include_in_schema=False)
async def attention(request: Request) -> Response:
    """The sidebar's mark: a red dot while any request is overdue, nothing
    otherwise. Fetched by the nav on load, so a page that never touches
    matters still shows the deadline that passed."""
    async with get_async_session() as db:
        count = len(await RequestService(db).overdue())
    return templates.TemplateResponse(
        request=request,
        name="partials/attention.html",
        context={"count": count, "tone": "error", "label": "overdue"},
    )


@router.get("/new", include_in_schema=False)
async def new_matter(request: Request) -> Response:
    async with get_async_session() as db:
        parties = await PartyService(db).options()
    return dialog(
        request,
        "partials/matters/matter.html",
        matter=None,
        parties=parties,
        roles=PARTICIPANT_ROLES,
        errors=[],
    )


@router.post("/new", include_in_schema=False)
async def create_matter(
    request: Request,
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    reference: Annotated[str, Form()] = "",
    subject_party_id: Annotated[str, Form()] = "",
    new_subject: Annotated[str, Form()] = "",
    counterpart_party_id: Annotated[str, Form()] = "",
    new_counterpart: Annotated[str, Form()] = "",
    opened_on: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Open a case.

    A reference that already names a matter does NOT make a second one:
    the agency's own number is how two letters agree they are about the
    same thing, and a duplicate case is how the second letter gets lost.
    """
    async with get_async_session() as db:
        matters = MatterService(db)
        existing = await matters.by_reference(reference)
        if existing is not None:
            return dialog_done(
                f"{SECTION.path}/{existing.id}",
                f"{existing.title} already has that reference",
            )
        try:
            matter = await matters.open(
                title=title,
                kind=kind,
                reference=reference,
                subject_party_id=await party_or_new(
                    db, subject_party_id, new_subject, owner_user_id=owner_user_id
                ),
                counterpart_party_id=await party_or_new(
                    db,
                    counterpart_party_id,
                    new_counterpart,
                    kind="organization",
                    owner_user_id=owner_user_id,
                ),
                owner_user_id=owner_user_id,
                opened_on=parse_date(opened_on),
            )
        except ValueError as exc:
            return dialog(
                request,
                "partials/matters/matter.html",
                422,
                matter=None,
                parties=await PartyService(db).options(),
                roles=PARTICIPANT_ROLES,
                errors=[str(exc)],
                title=title,
                kind=kind,
                reference=reference,
            )
        # The subject and the counterpart are participants too: the
        # columns say who the case is ABOUT and who it is WITH, and the
        # participant list is what a page reads, so both have to be true.
        for party_id, role in (
            (matter.subject_party_id, "subject"),
            (matter.counterpart_party_id, "agency"),
        ):
            if party_id:
                await matters.add_participant(matter.id, party_id, role)
        await db.commit()
        opened = matter.id
    return dialog_done(f"{SECTION.path}/{opened}", "Opened")


@router.get("/{matter_id:int}", include_in_schema=False)
async def matter(request: Request, matter_id: int) -> Response:
    """One case: who is in it, and under what."""
    async with get_async_session() as db:
        found = await MatterService(db).get(matter_id)
        or_404(found)
        drawn = await summarised(db, found)
        asked = [
            await drawn_request(db, one)
            for one in await RequestService(db).for_matter(matter_id)
        ]
        known = await facts_for(db, matter_id)
        papers = await matter_papers(db, matter_id)
        outstanding = (await answer_sheet(db, matter_id))["outstanding"]
    return render(
        request,
        "pages/matter.html",
        {
            "section": SECTION,
            **matter_tabs(matter_id, "case", outstanding),
            "matter": drawn,
            "requests": asked,
            "facts": known,
            "papers": papers,
            "paper_columns": list(PAPER_COLUMNS),
            "path": SECTION.path,
        },
    )


def _cadence_dialog(
    request: Request,
    matter_id: int,
    cadence: str | None,
    next_expected_on: object,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    from app.services.matters.models import MATTER_CADENCES

    return dialog(
        request,
        "partials/matters/cadence.html",
        status_code,
        post=f"{SECTION.path}/{matter_id}/cadence",
        cadences=MATTER_CADENCES,
        cadence=cadence,
        next_expected_on=next_expected_on,
        errors=errors or [],
    )


@router.get("/{matter_id:int}/cadence", include_in_schema=False)
async def cadence_dialog(
    request: Request,
    matter_id: int,
    cadence: str | None = None,
    next_expected_on: str = "",
) -> Response:
    """When the next letter is due, where the matter is read (ST-11).

    Picking a cadence asks for this again with the choice, and the date
    comes back filled in from when the last letter arrived - unless one
    was already typed. The rule lives here, not in the browser.
    """
    from app.services.matters.matters import suggested_next
    from app.services.matters.models import MATTER_CADENCES

    async with get_async_session() as db:
        found = or_404(await MatterService(db).get(matter_id))
        if cadence is None:
            chosen, when = found.cadence, found.next_expected_on
        else:
            chosen, when = cadence or None, parse_date(next_expected_on)
        if chosen in MATTER_CADENCES and when is None:
            when = await suggested_next(db, matter_id, chosen)
    return _cadence_dialog(request, matter_id, chosen, when)


@router.post("/{matter_id:int}/cadence", include_in_schema=False)
async def save_cadence(
    request: Request,
    matter_id: int,
    cadence: Annotated[str, Form()] = "",
    next_expected_on: Annotated[str, Form()] = "",
) -> Response:
    async with get_async_session() as db:
        or_404(await MatterService(db).get(matter_id))
        try:
            await MatterService(db).set_cadence(
                matter_id,
                cadence=cadence or None,
                next_expected_on=parse_date(next_expected_on),
            )
        except ValueError as exc:
            return _cadence_dialog(
                request, matter_id, cadence, next_expected_on, 422, [str(exc)]
            )
        await db.commit()
    return dialog_done(where_from(request, f"{SECTION.path}/{matter_id}"), "Saved")


@router.get("/{matter_id:int}/answers", include_in_schema=False)
async def answers(request: Request, matter_id: int) -> Response:
    """What to write on the county's form, and where each figure came
    from. Its own page because it is read beside a paper form with the
    app's chrome dropped out of print."""
    async with get_async_session() as db:
        or_404(await MatterService(db).get(matter_id))
        sheet = await answer_sheet(db, matter_id)
        drawn = await summarised(db, sheet["matter"])
    return render(
        request,
        "pages/matter_answers.html",
        {
            "section": SECTION,
            "path": SECTION.path,
            **matter_tabs(matter_id, "answers", sheet["outstanding"]),
            "matter": drawn,
            "reference": sheet["reference"],
            "due_on": sheet["due_on"],
            "answers": sheet["answers"],
            "outstanding": sheet["outstanding"],
            # Whose case it is, by name. On paper this is what tells
            # somebody which person the sheet is about.
            "subject": next(
                (
                    one["party"]
                    for one in (drawn.get("participants") or [])
                    if one.get("role") == "subject"
                ),
                None,
            ),
        },
    )


@router.get("/{matter_id:int}/participants/new", include_in_schema=False)
async def new_participant(request: Request, matter_id: int) -> Response:
    """Who else is in this case.

    The button for this shipped with ST-03 and the route did not: it
    404'd from the day the page existed, which is what a control nobody
    clicked in a test looks like.
    """
    async with get_async_session() as db:
        or_404(await MatterService(db).get(matter_id))
        parties = await PartyService(db).options()
    return dialog(
        request,
        "partials/matters/participant.html",
        post=f"{SECTION.path}/{matter_id}/participants",
        parties=parties,
        roles=PARTICIPANT_ROLES,
        errors=[],
    )


@router.post("/{matter_id:int}/participants", include_in_schema=False)
async def add_participant(
    request: Request,
    matter_id: int,
    party_id: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "other",
    note: Annotated[str, Form()] = "",
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    async with get_async_session() as db:
        matters = MatterService(db)
        if await matters.get(matter_id) is None:
            raise HTTPException(status_code=404)
        # Named here rather than in Settings: an agency turns up in a
        # letter, and sending somebody to another page mid-sentence
        # loses the four fields they had already typed.
        whom = await party_or_new(
            db,
            party_id,
            new_name,
            # An organization, because the ones a letter adds are: the
            # county office, the facility, the firm copied on it. A
            # person added this way is renamed in People, not misfiled.
            kind="organization" if new_name.strip() else "person",
            owner_user_id=owner_user_id,
        )
        if whom:
            await matters.add_participant(
                matter_id, whom, role, note=note.strip() or None
            )
            await db.commit()
    return dialog_done(where_from(request, f"{SECTION.path}/{matter_id}"), "Added")
