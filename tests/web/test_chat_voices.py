"""Her voice, switched and tuned in the app (#262).

The composer carries a voice chip beside the model chip; it opens the one
dialog, which lists the voice profiles with a preview each, switches the
active one, makes a new one from the active, and edits one. What changes
applies to the next thing she says, with no restart.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.routes import chat_voices
from app.components.web_frontend.routes.chat_voices import VOICES
from app.core.config import settings
from app.services.ai.domains.voice import profiles
from app.services.ai.models.voice_profile import VoiceProfile
from tests.web.dom import none, one, oob, select, text


@pytest.fixture(autouse=True)
def _restore_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching a profile writes the running settings; each test puts
    them back."""
    for key in profiles.SETTINGS.values():
        monkeypatch.setattr(settings, key, getattr(settings, key))


@pytest.fixture
async def voices(async_db_session: AsyncSession) -> dict[str, VoiceProfile]:
    for row in await profiles.list_profiles(async_db_session):
        await async_db_session.delete(row)
    await async_db_session.commit()
    marin = VoiceProfile(
        name="Illiana",
        is_active=True,
        stt_model="gpt-transcribe",
        tts_model="gpt-4o-mini-tts-2025-03-20",
        tts_voice="marin",
        tts_speed=1.2,
        tts_instructions="Voice Affect: Warm.",
    )
    nova = VoiceProfile(
        name="Nova, plain", tts_model="gpt-4o-mini-tts", tts_voice="nova"
    )
    async_db_session.add_all([marin, nova])
    await async_db_session.commit()
    await async_db_session.refresh(marin)
    await async_db_session.refresh(nova)
    return {"marin": marin, "nova": nova}


class TestTheChip:
    def test_the_composer_loads_it(self, client: TestClient) -> None:
        loader = one(client.get("/chat").text, "#chat-composer #chat-voice")
        assert loader.get("hx-get") == f"{VOICES}/chip"
        # It sits inside the composer form, whose hx-target is the thread:
        # htmx inherits that, and the chip replaced the whole conversation
        # (2026-09-25). A loader names its own target.
        assert loader.get("hx-target") == "this"

    def test_it_names_the_active_voice_and_opens_the_dialog(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        chip = one(hx.get(f"{VOICES}/chip").text, "button#chat-voice")
        assert text(chip) == "Illiana"
        assert chip.get("hx-get") == VOICES


class TestTheDialog:
    def test_lists_every_voice_and_marks_the_active_one(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        html = hx.get(VOICES).text
        none(html, "html")
        rows = select(html, "[data-voice-profile]")
        assert [text(r.cssselect("[data-name]")[0]) for r in rows] == [
            "Illiana",
            "Nova, plain",
        ]
        active = one(html, "[data-voice-profile][aria-current=true]")
        assert active.get("data-voice-profile") == str(voices["marin"].id)

    def test_a_preview_shows_what_it_is_doing(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        """The same state visuals as the mic (one macro): loading, then an
        equalizer while it plays. A speaker never records."""
        for html in (
            hx.get(VOICES).text,
            hx.get(f"{VOICES}/{voices['marin'].id}/edit").text,
        ):
            for button in select(html, "button[data-speak]"):
                assert button.get("data-state") == "idle"
                drawn = {
                    e.get("data-mic-visual")
                    for e in button.cssselect("[data-mic-visual]")
                }
                assert drawn == {"thinking", "speaking"}

    def test_every_voice_can_be_previewed(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        html = hx.get(VOICES).text
        previews = [b.get("data-speak") for b in select(html, "button[data-speak]")]
        assert previews == [
            f"{VOICES}/{v.id}/preview" for v in (voices["marin"], voices["nova"])
        ]


class TestSwitching:
    async def test_the_next_thing_she_says_uses_it(
        self,
        hx: TestClient,
        voices: dict[str, VoiceProfile],
        async_db_session: AsyncSession,
    ) -> None:
        response = hx.post(f"{VOICES}/{voices['nova'].id}/active")

        assert response.status_code == 200
        assert settings.TTS_VOICE == "nova"  # applied to the running app
        assert settings.TTS_MODEL == "gpt-4o-mini-tts"
        active = await profiles.active_profile(async_db_session)
        assert active is not None and active.name == "Nova, plain"
        primary, siblings = oob(response.text)
        chip = [e for e in siblings if e.get("id") == "chat-voice"]
        assert [text(e) for e in chip] == ["Nova, plain"]
        current = [
            e
            for root in primary
            for e in root.cssselect("[data-voice-profile][aria-current=true]")
        ]
        assert [e.get("data-voice-profile") for e in current] == [
            str(voices["nova"].id)
        ]

    def test_a_missing_voice_is_a_404(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        assert hx.post(f"{VOICES}/999999/active").status_code == 404


class TestMaking:
    async def test_a_new_voice_starts_from_the_active_one(
        self,
        hx: TestClient,
        voices: dict[str, VoiceProfile],
        async_db_session: AsyncSession,
    ) -> None:
        response = hx.post(VOICES, data={"name": "Marin, faster"})

        assert response.status_code == 200
        made = next(
            p
            for p in await profiles.list_profiles(async_db_session)
            if p.name == "Marin, faster"
        )
        assert (made.tts_voice, made.tts_speed, made.is_active) == ("marin", 1.2, False)
        # It opens straight into its own edit form.
        assert (
            one(response.text, "form[data-voice-form]").get("hx-post")
            == f"{VOICES}/{made.id}"
        )

    def test_a_taken_name_is_a_422_with_the_reason(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        response = hx.post(VOICES, data={"name": "Illiana"})
        assert response.status_code == 422
        one(response.text, "[role=alert]")


class TestEditing:
    def test_the_form_offers_the_documented_choices(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        form = one(
            hx.get(f"{VOICES}/{voices['marin'].id}/edit").text, "form[data-voice-form]"
        )
        voice_options = [
            o.get("value") for o in form.cssselect("select[name=tts_voice] option")
        ]
        assert voice_options == list(profiles.TTS_VOICES)
        chosen = form.cssselect("select[name=tts_voice] option[selected]")
        assert [o.get("value") for o in chosen] == ["marin"]
        instructions = form.cssselect("textarea[name=tts_instructions]")
        assert [text(t) for t in instructions] == ["Voice Affect: Warm."]

    async def test_saving_the_active_voice_applies_it(
        self,
        hx: TestClient,
        voices: dict[str, VoiceProfile],
        async_db_session: AsyncSession,
    ) -> None:
        response = hx.post(
            f"{VOICES}/{voices['marin'].id}",
            data=_form(voices["marin"], tts_speed="1.35"),
        )

        assert response.status_code == 200
        assert settings.TTS_SPEED == 1.35
        await async_db_session.refresh(voices["marin"])
        assert voices["marin"].tts_speed == 1.35

    async def test_saving_another_voice_leaves_hers_alone(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        before = settings.TTS_VOICE
        hx.post(
            f"{VOICES}/{voices['nova'].id}",
            data=_form(voices["nova"], tts_voice="coral"),
        )
        assert settings.TTS_VOICE == before

    @pytest.mark.parametrize(
        "change",
        [
            {"tts_speed": "9"},
            {"tts_speed": "fast"},
            {"tts_voice": "ghost"},
            {"name": ""},
        ],
    )
    def test_nonsense_is_a_422_that_keeps_the_form(
        self, hx: TestClient, voices: dict[str, VoiceProfile], change: dict[str, str]
    ) -> None:
        response = hx.post(
            f"{VOICES}/{voices['marin'].id}", data=_form(voices["marin"], **change)
        )
        assert response.status_code == 422
        one(response.text, "form[data-voice-form] [role=alert]")


class TestPreviewing:
    def test_a_preview_is_that_voice_not_hers(
        self,
        client: TestClient,
        voices: dict[str, VoiceProfile],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        heard: list[Any] = []

        class _TTS:
            def __init__(self, voice_settings: Any) -> None:
                heard.append(voice_settings)

            async def synthesize_stream(
                self, request: Any, user_id: str | None = None
            ) -> Any:
                heard.append(request)
                yield b"ID3"

        monkeypatch.setattr(chat_voices, "TTSService", _TTS)
        hers = settings.TTS_VOICE
        response = client.get(f"{VOICES}/{voices['nova'].id}/preview")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        preview_settings, request = heard
        assert (preview_settings.TTS_VOICE, preview_settings.TTS_MODEL) == (
            "nova",
            "gpt-4o-mini-tts",
        )
        assert request.text == chat_voices.PREVIEW_TEXT
        assert settings.TTS_VOICE == hers  # previewing does not switch her


class TestPreviewingUnsavedChanges:
    """The form's preview speaks what is in the form, not what was saved:
    changing the voice and pressing preview kept playing the old one."""

    def test_the_form_preview_reads_the_form(
        self, hx: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        form = one(
            hx.get(f"{VOICES}/{voices['marin'].id}/edit").text, "form[data-voice-form]"
        )
        button = form.cssselect("button[data-speak]")[0]
        assert button.get("data-speak") == f"{VOICES}/{voices['marin'].id}/preview"
        assert button.get("data-speak-form") is not None

    def test_values_in_the_query_are_spoken_unsaved(
        self,
        client: TestClient,
        voices: dict[str, VoiceProfile],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        heard: list[Any] = []

        class _TTS:
            def __init__(self, voice_settings: Any) -> None:
                heard.append(voice_settings)

            async def synthesize_stream(
                self, request: Any, user_id: str | None = None
            ) -> Any:
                yield b"ID3"

        monkeypatch.setattr(chat_voices, "TTSService", _TTS)
        response = client.get(
            f"{VOICES}/{voices['marin'].id}/preview",
            params=_form(voices["marin"], tts_voice="coral", tts_speed="1.5"),
        )

        assert response.status_code == 200
        assert (heard[0].TTS_VOICE, heard[0].TTS_SPEED) == ("coral", 1.5)
        assert voices["marin"].tts_voice == "marin"  # nothing saved

    def test_nonsense_in_the_query_is_a_422(
        self, client: TestClient, voices: dict[str, VoiceProfile]
    ) -> None:
        response = client.get(
            f"{VOICES}/{voices['marin'].id}/preview",
            params=_form(voices["marin"], tts_voice="ghost"),
        )
        assert response.status_code == 422


def _form(profile: VoiceProfile, **changes: str) -> dict[str, str]:
    values = {
        "name": profile.name,
        "stt_model": profile.stt_model or "",
        "tts_model": profile.tts_model or "",
        "tts_voice": profile.tts_voice or "",
        "tts_speed": str(profile.tts_speed),
        "tts_instructions": profile.tts_instructions or "",
        "reply": profile.reply,
        "working_sound": profile.working_sound,
    }
    values.update(changes)
    return values
