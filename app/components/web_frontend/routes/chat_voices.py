"""Her voice, switched and tuned in the app (#262).

A voice chip sits in the composer beside the model chip. It opens the one
dialog, which lists the voice profiles (each with a preview), switches the
active one, makes a new one from the active, and edits one. The active
profile is applied onto the running settings (profiles.apply), so the next
thing she says uses it - no .env edit, no restart.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.backend.api.ai.router import ai_service
from app.components.web_frontend.rendering import dialog, render, with_toast
from app.components.web_frontend.routes.chat import SECTION
from app.components.web_frontend.routes.chat_speech import spoken
from app.core.config import settings
from app.core.db import get_async_db
from app.services.ai.domains.voice import profiles
from app.services.ai.domains.voice.tts import TTSService
from app.services.ai.models.voice_profile import VoiceProfile

router = APIRouter()

VOICES = SECTION.path + "/voices"
TEMPLATE = "partials/chat/voices.html"

# One passage for every preview, so voices are compared on the same words:
# a figure, some bad news, and a question - what her voice has to carry.
PREVIEW_TEXT = (
    "Okay, I looked at your spending since April. The biggest place to save "
    "is eating out and delivery, about three hundred dollars a month. Heads "
    "up, though: the pet food budget is already ninety-seven dollars over. "
    "Want me to set up an envelope for eating out?"
)


def _choices() -> dict[str, list[dict[str, str]]]:
    """The edit form's options, from the one list each (profiles)."""
    as_options = lambda values: [{"id": v, "name": v} for v in values]  # noqa: E731
    return {
        "tts_voice": as_options(profiles.TTS_VOICES),
        "tts_model": as_options(profiles.TTS_MODELS),
        "stt_model": as_options(profiles.STT_MODELS),
        "reply": as_options(profiles.REPLIES),
        "working_sound": as_options(profiles.WORKING_SOUNDS),
    }


async def _listing(
    request: Request,
    db: AsyncSession,
    *,
    chip: bool = False,
    errors: list[str] | None = None,
    new_name: str = "",
    rows: list[VoiceProfile] | None = None,
) -> Response:
    if rows is None:
        rows = await profiles.list_profiles(db)
    return dialog(
        request,
        TEMPLATE,
        status_code=422 if errors else 200,
        view="list",
        voices=rows,
        path=VOICES,
        active=next((p for p in rows if p.is_active), None),
        chip_oob=chip,
        errors=errors or [],
        new_name=new_name,
    )


def _form(
    request: Request,
    profile: VoiceProfile,
    *,
    values: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    return dialog(
        request,
        TEMPLATE,
        status_code=status_code,
        view="form",
        voice=profile,
        values=values or profile.model_dump(),
        errors=errors or [],
        choices=_choices(),
        path=VOICES,
    )


async def _owned(db: AsyncSession, profile_id: int) -> VoiceProfile:
    profile = await db.get(VoiceProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404)
    return profile


@router.get(VOICES + "/chip", include_in_schema=False)
async def chip(request: Request, db: AsyncSession = Depends(get_async_db)) -> Response:
    """The composer's voice chip: loaded by the chip's placeholder, so the
    chat surface needs nothing new in its own context."""
    return render(
        request,
        TEMPLATE,
        {"view": "chip", "active": await profiles.active_profile(db), "path": VOICES},
    )


@router.get(VOICES, include_in_schema=False)
async def listing(
    request: Request, db: AsyncSession = Depends(get_async_db)
) -> Response:
    return await _listing(request, db)


@router.post(VOICES + "/{profile_id}/active", include_in_schema=False)
async def use(
    request: Request, profile_id: int, db: AsyncSession = Depends(get_async_db)
) -> Response:
    try:
        rows = await profiles.activate(db, profile_id)
    except profiles.VoiceProfileError:
        raise HTTPException(status_code=404) from None
    chosen = next(p for p in rows if p.is_active)
    profiles.apply(chosen, settings, ai_service)
    return with_toast(
        await _listing(request, db, chip=True, rows=rows),
        f"She sounds like {chosen.name} now.",
    )


@router.post(VOICES, include_in_schema=False)
async def make(request: Request, db: AsyncSession = Depends(get_async_db)) -> Response:
    """A new voice from the active one, opened straight into its form."""
    name = str((await request.form()).get("name") or "")
    try:
        made = await profiles.create(db, name=name)
    except profiles.VoiceProfileError as error:
        return await _listing(request, db, errors=[str(error)], new_name=name)
    return _form(request, made)


@router.get(VOICES + "/{profile_id}/edit", include_in_schema=False)
async def edit(
    request: Request, profile_id: int, db: AsyncSession = Depends(get_async_db)
) -> Response:
    return _form(request, await _owned(db, profile_id))


@router.post(VOICES + "/{profile_id}", include_in_schema=False)
async def save(
    request: Request, profile_id: int, db: AsyncSession = Depends(get_async_db)
) -> Response:
    profile = await _owned(db, profile_id)
    values, errors = profiles.parse_form(await request.form())
    if not errors:
        try:
            profile = await profiles.update(db, profile_id, **values)
        except profiles.VoiceProfileError as error:
            errors = [str(error)]
    if errors:
        return _form(request, profile, values=values, errors=errors, status_code=422)
    if profile.is_active:
        profiles.apply(profile, settings, ai_service)
    return with_toast(
        await _listing(request, db, chip=profile.is_active), f"Saved {profile.name}."
    )


@router.get(VOICES + "/{profile_id}/preview", include_in_schema=False)
async def preview(
    request: Request, profile_id: int, db: AsyncSession = Depends(get_async_db)
) -> Response:
    """The preview passage in THIS profile's voice, whichever one is active -
    or, from the edit form, in the values the form holds, unsaved (the
    query carries them, checked as a save would be)."""
    profile = await _owned(db, profile_id)
    if "tts_voice" in request.query_params:
        values, errors = profiles.parse_form(request.query_params)
        if errors:
            return Response(status_code=422)
        profile = VoiceProfile(**values)
    return await spoken(
        PREVIEW_TEXT, tts=TTSService(profiles.settings_for(profile, settings))
    )
