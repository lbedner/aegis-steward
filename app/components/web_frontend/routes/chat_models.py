"""Her models, in one place (#273): the chat model and what a live call
runs on, picked from one catalog with one search.

Each role is a kind of catalog model (``LargeLanguageModel.mode``) and
what a pick of it sets: the chat model is the install-wide active model;
a live model becomes the active voice profile's engine, made on the spot
when it has none. A new role (image, speech) is a new entry in ``ROLES``
and a branch in ``pick_model``.

Switching is an install-wide override (the AI routes re-read it per
request), not a per-conversation setting. Honest caps: a section shows
this many rows and a flat view this many, with a "more - search to
narrow" line rather than silent truncation. The catalog is small enough
to fetch per open.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.ai.router import ai_service
from app.components.backend.api.llm.routes import (
    SetModelRequest,
    get_current,
    get_models,
    get_vendors,
    set_current,
)
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, templates, with_toast
from app.core.config import settings
from app.core.db import get_async_session
from app.services.ai.domains.llm import queries as llm_queries
from app.services.ai.domains.llm.picker import (
    display_title,
    filter_models,
    format_context_window,
    format_price,
    group_models,
    is_local_model,
    lab_for_model,
    model_label,
    newest_first,
)
from app.services.ai.domains.voice import live_engines, profiles
from app.services.finance.domains.detection.analyst.live_engines import ENGINE_SEEDS

router = APIRouter()

MODELS = section("chat").path + "/models"
# The roles: the catalog kind each picks from, and the modality the filter
# knows it by. One list holds every role's models; a row's kind says which
# role a pick of it sets.
ROLES = (("chat", "text"), ("realtime", "voice"))
FILTERS = (("all", "All"), ("text", "Text"), ("voice", "Voice"))
# How many recently used models lead the list; read from a few more, as
# some may not be in the list on show (another kind, or not callable).
RECENT = 3
RECENT_LOOKBACK = 12
SECTION_ROW_CAP = 30
FLAT_ROW_CAP = 60
CATALOG_LIMIT = 200


# Every read and write here is a short session of its own, closed before
# the next: SQLite sessions here BEGIN IMMEDIATE, and the catalog handlers
# open their own - a request session held across them locked the dialog
# against itself (2026-09-28).


async def _running() -> tuple[Any, Any]:
    """What each role runs on now: the chat model's config, and the live
    engine. Read once a request; the dialog and the chip share it."""
    async with get_async_session() as db:
        engine = await live_engines.resolve(db, settings.VOICE_LIVE_ENGINE)
    return await get_current(), engine


def _chip(current: Any, engine: Any) -> dict[str, Any]:
    """What the composer's chip says: the chat model's id, clipped, and
    what a live call runs on."""
    label = model_label(current.model_dump())
    if engine is not None:
        label = f"{label} · Live: {engine.llm.title}"
    return {"label": label, "model": current.model}


def _row(
    model: dict[str, Any], *, under_vendor: str | None, icons: dict[str, str]
) -> dict[str, Any]:
    voice = model["kind"] != "chat"
    """One picker row. An icon only where it adds information: the lab
    behind a hosted model when it differs from the section's vendor, and
    the vendor in flat views that have no section context."""
    lab = lab_for_model(model)
    vendor = str(model.get("vendor") or "")
    if under_vendor:
        icon_for = lab if lab and lab.casefold() != under_vendor.casefold() else None
    else:
        icon_for = lab or vendor
    facts = " · ".join(
        part
        for part in (
            # A voice model's window is not what picks it; its price is.
            None if voice else format_context_window(model.get("context_window")),
            format_price(
                model.get("input_price"),
                model.get("output_price"),
                local=is_local_model(model),
                per_minute=model.get("per_minute"),
            ),
        )
        if part
    )
    return {
        "model_id": model["model_id"],
        "title": display_title(model, under_vendor=under_vendor),
        "facts": facts,
        "icon_name": icon_for,
        "icon_b64": icons.get(icon_for or ""),
        "color": model.get("color") or "",
        "kind": model["kind"],
        "voice": voice,
    }


def _flat(
    models: list[dict[str, Any]], query: str, icons: dict[str, str]
) -> list[dict[str, Any]]:
    rows = newest_first(filter_models(models, query) if query else models)
    shown = rows[:FLAT_ROW_CAP]
    return [
        {
            "name": None,
            "rows": [_row(m, under_vendor=None, icons=icons) for m in shown],
            "hidden": len(rows) - len(shown),
        }
    ]


def _grouped(
    models: list[dict[str, Any]], icons: dict[str, str]
) -> list[dict[str, Any]]:
    """A group per vendor, each starting closed: the models in use lead
    the list among the recently used, so an opened group only pushes them
    down."""
    sections = []
    for vendor, rows in group_models(models, by="vendor"):
        shown = rows[:SECTION_ROW_CAP]
        sections.append(
            {
                "name": vendor,
                "vendor": vendor,
                "icon_b64": icons.get(vendor),
                "color": next((m.get("color") for m in rows if m.get("color")), ""),
                "count": len(rows),
                "rows": [_row(m, under_vendor=vendor, icons=icons) for m in shown],
                "hidden": len(rows) - len(shown),
            }
        )
    return sections


async def _recent_ids() -> list[str]:
    async with get_async_session() as db:
        return await llm_queries.recent_model_ids(db, RECENT_LOOKBACK)


def _recent(
    models: list[dict[str, Any]], recent_ids: list[str], icons: dict[str, str]
) -> list[dict[str, Any]]:
    """The models in use, then the last few used, leading the list with no
    heading - the ones you switch between, one click away. Each once, and
    only what the list holds (another modality is filtered out)."""
    by_id = {model["model_id"]: model for model in models}
    recent = [by_id[i] for i in dict.fromkeys(recent_ids) if i in by_id][:RECENT]
    if not recent:
        return []
    return [
        {
            "name": None,
            "recent": True,
            "rows": [_row(m, under_vendor=None, icons=icons) for m in recent],
            "hidden": 0,
        }
    ]


def _callable(model: Any, kind: str) -> bool:
    """A live model must be one a call can reach: its vendor has a
    transport (``CALL_TRANSPORTS``), and its id is the vendor's own - the
    catalog's routed copies ("gemini/...", "vertex_ai/...") are not."""
    if kind != "realtime":
        return True
    return model.vendor in live_engines.CALL_TRANSPORTS and "/" not in model.model_id


async def _catalog(kind: str) -> list[dict[str, Any]]:
    # Every argument spelled out: called in-process, the handler's Query
    # defaults are not values.
    return [
        m.model_dump()
        for m in await get_models(
            pattern=None,
            vendor=None,
            modality=None,
            limit=CATALOG_LIMIT,
            include_disabled=False,
            usable=True,
            mode=kind,
        )
        if _callable(m, kind)
    ]


async def picker(query: str, show: str, running: tuple[Any, Any]) -> dict[str, Any]:
    """The dialog's contents: one list of every role's models, narrowed to
    a modality by ``show``, grouped by vendor - or flat when searching -
    with the recently used leading and each role's model marked."""
    current, engine = running
    active = {current.model, engine.llm.model_id if engine else ""}
    icons = {v.name: v.icon_b64 for v in await get_vendors(usable=True) if v.icon_b64}
    query = query.strip()
    show = show if show in dict(FILTERS) else "all"
    models = [
        {**model, "kind": kind}
        for kind, modality in ROLES
        if show in ("all", modality)
        for model in await _catalog(kind)
    ]
    sections = _flat(models, query, icons) if query else _grouped(models, icons)
    if not query:
        # A pick leads at once: it has no usage until she runs on it.
        picks = [current.model, engine.llm.model_id if engine else ""]
        sections = _recent(models, picks + await _recent_ids(), icons) + sections
    return {
        "query": query,
        "show": show,
        "filters": FILTERS,
        "active": active,
        "sections": sections,
        "empty": not any(section["rows"] for section in sections),
        "path": MODELS,
    }


TEMPLATE = "partials/chat/models.html"


def _dialog(request: Request, body: dict[str, Any]) -> Response:
    return dialog(request, TEMPLATE, picker=body)


@router.get(MODELS + "/chip", include_in_schema=False)
async def model_chip(request: Request) -> Response:
    """The composer's chip; it loads itself, and a pick sends it back out
    of band."""
    return templates.TemplateResponse(
        request=request,
        name=TEMPLATE,
        context={"chip": _chip(*await _running()), "chip_only": True, "path": MODELS},
    )


@router.get(MODELS, include_in_schema=False)
async def models_dialog(
    request: Request,
    q: str = "",
    show: str = "all",
) -> Response:
    """The picker, in the one modal; the same route re-renders its body
    as the search or the filter changes."""
    return _dialog(request, await picker(q, show, await _running()))


async def _use_for_calls(model_id: str) -> str | None:
    """Make ``model_id`` what a live call runs on; what went wrong, if
    anything."""
    async with get_async_session() as db:
        engine = await live_engines.choose(db, model_id, ENGINE_SEEDS)
        active = await profiles.active_profile(db)
        if engine is None:
            return f"{model_id} is not in the catalog."
        if active is None or active.id is None:
            return "Make a voice first: a live call runs on the active voice."
        active = await profiles.update(db, active.id, live_engine=engine.key)
    profiles.apply(active, settings, ai_service)
    return None


@router.post(MODELS, include_in_schema=False)
async def pick_model(
    request: Request,
    model_id: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "chat",
    q: Annotated[str, Form()] = "",
    show: Annotated[str, Form()] = "all",
) -> Response:
    """A pick sets its role and updates the dialog in place (compare and
    switch twice without reopening) and the composer's chip out of band;
    a refused pick says why and changes nothing."""
    refused: str | None = None
    if kind == "realtime":
        refused = await _use_for_calls(model_id)
    else:
        try:
            await set_current(SetModelRequest(model_id=model_id))
        except HTTPException as exc:
            refused = str(exc.detail) or "Model switch failed."
    running = await _running()
    if refused:
        return with_toast(
            _dialog(request, await picker(q, show, running)),
            refused,
            tone="error",
        )
    return templates.TemplateResponse(
        request=request,
        name=TEMPLATE,
        context={
            "picker": await picker(q, show, running),
            "chip": _chip(*running),
            "chip_oob": True,
        },
    )
