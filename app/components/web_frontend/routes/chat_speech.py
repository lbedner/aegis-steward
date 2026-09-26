"""Talking to Illiana: the two ends around a spoken turn.

A spoken turn is the typed turn (#246). The microphone's recording is
transcribed into the composer, where it can be read and fixed before it
goes out through the same stream as anything typed; her stored answer can
then be heard. Audio is never kept - the conversation holds the words.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, File, UploadFile
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.components.backend.api.ai.router import ai_service
from app.components.web_frontend.routes.chat import (
    ASSISTANT_NAME,
    SPEECH,
    stored_message,
)
from app.core.chat_transcript import readable
from app.core.log import logger
from app.services.ai.domains.voice.models import (
    AudioFormat,
    AudioInput,
    SpeechRequest,
)
from app.services.ai.domains.voice.spoken import to_spoken
from app.services.finance.domains.detection.analyst.shared import (
    FINANCE_VOICE_AGENT_SLUG,
    STANDALONE_USER_ID,
)

router = APIRouter()

TRANSCRIPTS = SPEECH + "/transcripts"

NO_SPEECH = "Didn't catch anything - try again, a little closer."
SPEECH_FAILED = "Couldn't make that out right now. You can type it instead."

# Names the transcriber cannot guess: "Illiana" came back "Ilyana".
TRANSCRIPTION_HINT = f"{ASSISTANT_NAME}."


def _format(filename: str | None) -> AudioFormat:
    """Chrome records webm, Safari mp4, Firefox ogg; the script names the
    file for what the browser gave it."""
    extension = (filename or "").rsplit(".", 1)[-1].lower()
    try:
        return AudioFormat(extension)
    except ValueError:
        return AudioFormat.WEBM


@router.post(TRANSCRIPTS, include_in_schema=False)
async def transcribe(audio: UploadFile = File(...)) -> Response:
    """The recording as text, for the composer. JSON, like the paste
    route: the script puts it in the textarea, where it can be edited."""
    content = await audio.read()
    if not content:
        return JSONResponse({"error": NO_SPEECH}, status_code=422)
    try:
        heard = await ai_service.stt.transcribe(
            AudioInput(
                content=content,
                format=_format(audio.filename),
                prompt=TRANSCRIPTION_HINT,
            ),
            user_id=str(STANDALONE_USER_ID),
        )
    except Exception:
        logger.exception("Transcribing the microphone failed")
        return JSONResponse({"error": SPEECH_FAILED}, status_code=503)
    text = heard.text.strip()
    if not text:
        return JSONResponse({"error": NO_SPEECH}, status_code=422)
    # The turn this starts is answered by her voice agent (#260): the
    # script sends it with the transcript, so the choice stays here.
    return JSONResponse({"text": text, "agent_slug": FINANCE_VOICE_AGENT_SLUG})


# A stored answer never changes, so the browser may keep its audio: the
# autoplay and a Listen press were each paying for a full synthesis. A
# sentence is keyed by its own text, so the same holds for it.
SPOKEN_CACHE = "private, max-age=86400"
SAY = SPEECH + "/say"
SAY_LIMIT = 2_000  # a sentence or a short paragraph, never a document


async def _spoken(text: str) -> Response:
    """``text`` said aloud: markup stripped, streamed as it is synthesized so
    playback starts with the first chunk (whole, an 843-character answer
    took 31s before anything played, 2026-09-25). The first chunk is
    awaited here so a synthesis that fails at once is a 503, not a broken
    stream."""
    chunks = ai_service.tts.synthesize_stream(
        SpeechRequest(text=to_spoken(text)), user_id=str(STANDALONE_USER_ID)
    )
    try:
        first = await anext(chunks)
    except Exception:
        logger.exception("Speaking failed")
        return Response(status_code=503)

    async def audio() -> AsyncIterator[bytes]:
        yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(
        audio(), media_type="audio/mpeg", headers={"Cache-Control": SPOKEN_CACHE}
    )


@router.get(SAY, include_in_schema=False)
async def say(text: str = "") -> Response:
    """One piece of what she is writing, said as soon as it is complete
    (``VOICE_REPLY=live``): voice.js cuts her stream into sentences."""
    if len(text) > SAY_LIMIT or not to_spoken(text):
        return Response(status_code=422)
    return await _spoken(text)


@router.get(SPEECH + "/{conversation_id}/{message_id}", include_in_schema=False)
async def speak(conversation_id: str, message_id: str) -> Response:
    """A stored answer, said aloud: her words without the markup."""
    message = await stored_message(conversation_id, message_id)
    return await _spoken(readable(message.content))
