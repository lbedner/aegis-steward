"""Talking to Illiana: the microphone fills the composer, and her answer
can be heard.

A spoken turn is the typed turn (#246): the transcript lands in the
composer, editable, and goes out through the same stream. These routes are
the two ends around it - audio in, audio out.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.components.backend.api.ai.router import ai_service
from app.components.web_frontend.routes.chat import SPEECH
from app.components.web_frontend.routes.chat_speech import (
    NO_SPEECH,
    SAY,
    SPEECH_FAILED,
    TRANSCRIPTS,
)
from app.core.config import settings
from app.services.ai.domains.voice.models import (
    AudioFormat,
    STTProvider,
    TranscriptionResult,
)
from app.services.finance.domains.detection.analyst.shared import (
    FINANCE_VOICE_AGENT_SLUG,
    STANDALONE_USER_ID,
)
from tests.web.dom import one

SECRET = "secret internal detail"


class FakeSpeech:
    def __init__(self, heard: str = "What is due this week?") -> None:
        self.transcribe = AsyncMock(
            return_value=TranscriptionResult(
                text=heard, provider=STTProvider.OPENAI_WHISPER
            )
        )
        self.spoken: list[tuple[str, str | None]] = []
        self.fail: Exception | None = None

    async def synthesize_stream(self, request: Any, user_id: str | None = None) -> Any:
        """Streams, so playback starts with the first chunk."""
        self.spoken.append((request.text, user_id))
        if self.fail:
            raise self.fail
        for chunk in (b"ID3", b"mp3"):
            yield chunk


@pytest.fixture
def speech(monkeypatch: pytest.MonkeyPatch) -> FakeSpeech:
    fake = FakeSpeech()
    monkeypatch.setattr(ai_service, "_stt_service", fake)
    monkeypatch.setattr(ai_service, "_tts_service", fake)
    return fake


def _post(
    client: TestClient, audio: bytes = b"webm-bytes", name: str = "speech.webm"
) -> Any:
    return client.post(TRANSCRIPTS, files={"audio": (name, audio, "audio/webm")})


class TestHearing:
    def test_the_recording_comes_back_as_text(
        self, client: TestClient, speech: FakeSpeech
    ) -> None:
        response = _post(client)

        assert response.status_code == 200
        # The turn it starts is answered by her voice agent (#260).
        assert response.json() == {
            "text": "What is due this week?",
            "agent_slug": FINANCE_VOICE_AGENT_SLUG,
        }
        audio = speech.transcribe.call_args.args[0]
        assert audio.format == AudioFormat.WEBM
        assert audio.content == b"webm-bytes"
        assert "Illiana" in audio.prompt  # she was "Ilyana" without it
        assert speech.transcribe.call_args.kwargs["user_id"] == str(STANDALONE_USER_ID)

    @pytest.mark.parametrize(
        "name,expected",
        [("speech.mp4", AudioFormat.MP4), ("speech.ogg", AudioFormat.OGG)],
    )
    def test_the_browsers_other_formats(
        self, client: TestClient, speech: FakeSpeech, name: str, expected: AudioFormat
    ) -> None:
        """Safari records mp4, Firefox ogg; Chrome webm."""
        assert _post(client, name=name).status_code == 200
        assert speech.transcribe.call_args.args[0].format == expected

    def test_an_empty_recording_is_a_422(
        self, client: TestClient, speech: FakeSpeech
    ) -> None:
        response = _post(client, audio=b"")
        assert response.status_code == 422
        assert response.json() == {"error": NO_SPEECH}
        speech.transcribe.assert_not_awaited()

    def test_silence_is_a_422(self, client: TestClient, speech: FakeSpeech) -> None:
        speech.transcribe.return_value = TranscriptionResult(
            text="   ", provider=STTProvider.OPENAI_WHISPER
        )
        response = _post(client)
        assert response.status_code == 422
        assert response.json() == {"error": NO_SPEECH}

    def test_a_failed_transcription_keeps_its_reason_in_the_log(
        self, client: TestClient, speech: FakeSpeech
    ) -> None:
        speech.transcribe.side_effect = RuntimeError(SECRET)
        response = _post(client)
        assert response.status_code == 503
        assert response.json() == {"error": SPEECH_FAILED}
        assert SECRET not in response.text


class TestSpeaking:
    def test_an_answer_is_said_without_its_markup(
        self, client: TestClient, speech: FakeSpeech, stored: tuple[str, str]
    ) -> None:
        conversation_id, message_id = stored
        response = client.get(f"{SPEECH}/{conversation_id}/{message_id}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"ID3mp3"  # every streamed chunk, in order
        said, user_id = speech.spoken[0]
        assert said.startswith("Two bills this week:")
        assert "**" not in said and "- Water" not in said
        assert user_id == str(STANDALONE_USER_ID)

    def test_streamed_speech_is_never_cached(
        self, client: TestClient, speech: FakeSpeech, stored: tuple[str, str]
    ) -> None:
        """A streamed answer cut short (a replay stopped, a tab closed) was
        kept by Chrome as a truncated cache entry; the next play asked for
        the missing range, this route answered from the start, and the audio
        element stalled on its spinner (2026-09-25). A replay costs one
        more synthesis instead."""
        conversation_id, message_id = stored
        response = client.get(f"{SPEECH}/{conversation_id}/{message_id}")
        assert response.headers["cache-control"] == "no-store"

    def test_an_unknown_message_is_a_404(
        self, client: TestClient, speech: FakeSpeech, stored: tuple[str, str]
    ) -> None:
        conversation_id, _ = stored
        assert client.get(f"{SPEECH}/{conversation_id}/nope").status_code == 404
        assert client.get(f"{SPEECH}/nope/nope").status_code == 404

    def test_a_failed_synthesis_keeps_its_reason_in_the_log(
        self, client: TestClient, speech: FakeSpeech, stored: tuple[str, str]
    ) -> None:
        speech.fail = RuntimeError(SECRET)
        conversation_id, message_id = stored
        response = client.get(f"{SPEECH}/{conversation_id}/{message_id}")
        assert response.status_code == 503
        assert SECRET not in response.text


class TestSpeakingAsSheWrites:
    """``live`` replies (#261): each sentence she streams is spoken as soon
    as it is complete, through the same stripping and streaming as a
    stored answer."""

    def test_the_defaults(self) -> None:
        from app.core.voice_settings import VoiceSettings

        voice = VoiceSettings()
        assert voice.VOICE_REPLY == "live"
        assert voice.VOICE_WORKING_SOUND == "typing"  # not narration

    @pytest.mark.parametrize("speed", [0.2, 4.1])
    def test_the_speed_stays_in_the_models_range(self, speed: float) -> None:
        from pydantic import ValidationError

        from app.core.voice_settings import VoiceSettings

        with pytest.raises(ValidationError):
            VoiceSettings(TTS_SPEED=speed)

    def test_a_sentence_is_said_without_its_markup(
        self, client: TestClient, speech: FakeSpeech
    ) -> None:
        response = client.get(
            SAY, params={"text": "- **$60** left in *Vanessa's* envelope."}
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"ID3mp3"
        assert response.headers["cache-control"] == "no-store"
        said, user_id = speech.spoken[0]
        assert said == "$60 left in Vanessa's envelope."
        assert user_id == str(STANDALONE_USER_ID)

    @pytest.mark.parametrize("text", ["", "   ", "**", "x" * 2001])
    def test_nothing_sayable_or_too_long_is_a_422(
        self, client: TestClient, speech: FakeSpeech, text: str
    ) -> None:
        assert client.get(SAY, params={"text": text}).status_code == 422
        assert speech.spoken == []

    def test_a_failed_sentence_keeps_its_reason_in_the_log(
        self, client: TestClient, speech: FakeSpeech
    ) -> None:
        speech.fail = RuntimeError(SECRET)
        response = client.get(SAY, params={"text": "Hello."})
        assert response.status_code == 503
        assert SECRET not in response.text


class TestTheControls:
    """The mic sits in the composer and every answer can be heard; both
    render paths carry them, and the recorder's states are text the page
    already holds (the script only picks one)."""

    @pytest.mark.parametrize("path_client", ["client", "hx"])
    def test_the_composer_has_a_microphone(
        self, request: pytest.FixtureRequest, path_client: str
    ) -> None:
        page = request.getfixturevalue(path_client).get("/chat").text
        mic = one(page, "#chat-composer button#chat-mic")
        assert mic.get("aria-label")
        # One route each, spelled in Python and in the template: pinned.
        assert mic.get("data-transcripts") == TRANSCRIPTS
        assert mic.get("data-say") == SAY
        # How she is heard rides on the mic, so Listen gets it too (#261).
        assert json.loads(mic.get("data-voice")) == {
            "reply": settings.VOICE_REPLY,
            "sound": settings.VOICE_WORKING_SOUND,
            "idle": settings.VOICE_LIVE_IDLE_SECONDS,
        }
        # What the mic is doing shows on the mic itself: the page draws each
        # state, voice.js only sets data-state.
        assert mic.get("data-state") == "idle"
        drawn = {e.get("data-mic-visual") for e in mic.cssselect("[data-mic-visual]")}
        assert drawn == {"recording", "thinking", "speaking"}
        states = one(page, "template#chat-mic-states")
        assert {"thinking", "speaking"} <= {
            e.get("data-state") for e in states.cssselect("[data-state]")
        }
        status = one(page, "#chat-mic-status")
        assert status.get("aria-live") == "polite"
        one(page, "template#chat-mic-states")

    def test_a_settled_answer_can_be_heard(
        self, client: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, message_id = stored
        html = client.get(f"/chat/messages/{conversation_id}/{message_id}").text
        button = one(html, "[data-role=assistant] button[data-speak]")
        assert button.get("data-speak") == f"{SPEECH}/{conversation_id}/{message_id}"
