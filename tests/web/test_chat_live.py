"""Talking to Illiana live (#252): GPT-Live is her ears and voice, her own
agent does the thinking.

The browser's WebRTC offer goes through steward (the key never leaves the
server) to open a gpt-live-1 session with CLIENT delegation. When GPT-Live
needs real work it says so; the browser sends what was said to steward,
which runs her actual agent turn - her prompt, tools and approval cards, in
the conversation - and hands back what to say.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.components.backend.api.ai.router import ai_service
from app.components.web_frontend.routes import chat_live
from app.components.web_frontend.routes.chat_live import (
    DELEGATIONS,
    LIVE_SORRY,
    SESSIONS,
)
from app.services.ai.models import StreamingMessage
from app.services.finance.domains.detection.analyst.prompts import (
    FINANCE_LIVE_INSTRUCTIONS,
)
from app.services.finance.domains.detection.analyst.shared import (
    FINANCE_VOICE_AGENT_SLUG,
    STANDALONE_USER_ID,
)

SECRET = "secret internal detail"


class _Live:
    """Stands in for ``AsyncOpenAI().live``."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def create(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return SimpleNamespace(
            session=SimpleNamespace(id="live_123"),
            transport=SimpleNamespace(type="webrtc", sdp="v=0 answer"),
        )


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> _Live:
    fake = _Live()
    monkeypatch.setattr(chat_live, "_live_api", lambda: fake)
    return fake


class TestOpeningASession:
    def test_the_offer_is_answered_by_a_client_delegated_gpt_live(
        self, client: TestClient, live: _Live
    ) -> None:
        response = client.post(SESSIONS, json={"sdp": "v=0 offer"})

        assert response.status_code == 200
        assert response.json() == {"sdp": "v=0 answer", "session_id": "live_123"}
        call = live.calls[0]
        assert call["transport"] == {"type": "webrtc", "sdp": "v=0 offer"}
        session = call["session"]
        assert session["model"] == "gpt-live-1"
        assert session["delegation"] == {"type": "client"}
        assert session["instructions"] == FINANCE_LIVE_INSTRUCTIONS

    def test_it_starts_with_the_conversation_so_far(
        self, client: TestClient, live: _Live, stored: tuple[str, str]
    ) -> None:
        conversation_id, _ = stored
        client.post(SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id})

        history = live.calls[0]["session"]["input"]
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[0]["content"] == [{"type": "input_text", "text": "What is due?"}]
        said = history[1]["content"][0]
        assert said["type"] == "output_text"
        assert said["text"].startswith("Two bills this week")  # spoken form

    def test_no_offer_is_a_422(self, client: TestClient, live: _Live) -> None:
        assert client.post(SESSIONS, json={}).status_code == 422
        assert live.calls == []

    def test_a_refused_session_keeps_its_reason_in_the_log(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(chat_live, "_live_api", lambda: _Live(RuntimeError(SECRET)))
        response = client.post(SESSIONS, json={"sdp": "v=0 offer"})
        assert response.status_code == 502
        assert SECRET not in response.text


class TestDoingTheWork:
    """A delegated turn is a typed turn: the same stream, so what it keeps
    (tool trace, model, cost) is what the thread draws its trail, cards
    and footer from. ``chat()`` kept none of it (2026-09-25)."""

    @staticmethod
    def _answers(
        monkeypatch: pytest.MonkeyPatch, content: str, fail: bool = False
    ) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []

        async def _stream(**kwargs: Any) -> Any:
            seen.append(kwargs)
            if fail:
                raise RuntimeError(SECRET)
            yield StreamingMessage(content="working", is_delta=True)
            yield StreamingMessage(
                content=content,
                is_final=True,
                conversation_id="c-9",
                metadata={"tool_trace": [{"tool": "run_code"}]},
            )

        monkeypatch.setattr(ai_service, "stream_chat", _stream)
        return seen

    def test_her_own_agent_answers_through_the_typed_turns_path(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        turns = self._answers(monkeypatch, "## Vanessa\n\n- **$60** left this week.")
        response = client.post(
            DELEGATIONS,
            json={"text": "How much is left for Vanessa?", "conversation_id": "c-9"},
        )

        assert response.status_code == 200
        assert turns[0]["message"] == "How much is left for Vanessa?"
        assert turns[0]["agent_slug"] == FINANCE_VOICE_AGENT_SLUG
        assert turns[0]["surface"] == "finance"
        assert turns[0]["user_id"] == STANDALONE_USER_ID
        assert turns[0]["conversation_id"] == "c-9"
        # What GPT-Live is handed to say: plain speech, within its limit.
        assert response.json() == {
            "speak": "Vanessa. $60 left this week.",
            "conversation_id": "c-9",
        }

    def test_a_long_answer_is_cut_to_what_gpt_live_takes(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._answers(monkeypatch, "A sentence that goes on. " * 400)
        spoken = client.post(DELEGATIONS, json={"text": "Tell me everything."}).json()
        assert len(spoken["speak"]) <= chat_live.LIVE_ANSWER_CHARS

    def test_nothing_said_is_a_422(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        turns = self._answers(monkeypatch, "unused")
        assert client.post(DELEGATIONS, json={"text": "  "}).status_code == 422
        assert turns == []

    def test_a_failed_turn_gives_her_something_to_say(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._answers(monkeypatch, "unused", fail=True)
        response = client.post(DELEGATIONS, json={"text": "How am I doing?"})
        assert response.status_code == 200
        assert response.json() == {"speak": LIVE_SORRY, "conversation_id": None}
        assert SECRET not in response.text


class TestTheControl:
    @pytest.mark.parametrize("path_client", ["client", "hx"])
    def test_the_composer_has_a_live_button(
        self, request: pytest.FixtureRequest, path_client: str
    ) -> None:
        from tests.web.dom import one

        page = request.getfixturevalue(path_client).get("/chat").text
        button = one(page, "#chat-composer button#chat-live")
        assert button.get("data-sessions") == SESSIONS
        assert button.get("data-delegations") == DELEGATIONS
        assert button.get("data-state") == "idle"


class TestWhileSheWorks:
    """Her agent pulling data is its own state, not the connecting spinner:
    it holds for the whole delegation, through GPT-Live's "let me check"."""

    def test_the_live_button_draws_working(self, client: TestClient) -> None:
        from tests.web.dom import one

        button = one(client.get("/chat").text, "button#chat-live")
        drawn = {e.get("data-mic-visual") for e in button.cssselect("[data-mic-visual]")}
        assert drawn == {"recording", "thinking", "speaking", "working"}

    def test_the_status_line_says_so(self, client: TestClient) -> None:
        from tests.web.dom import one

        one(client.get("/chat").text, "template#chat-mic-states [data-state=working]")


class TestHerModel:
    """A delegation runs on the stored active model, not the .env default:
    a reload mid-call left the process on llama3.2:3b, which Ollama did not
    have, and every answer failed (2026-09-25)."""

    def test_every_live_route_adopts_the_active_model_first(
        self, client: TestClient, live: _Live, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.components.backend.api.ai.router import sync_active_model

        synced: list[bool] = []

        async def _sync() -> None:
            synced.append(True)

        TestDoingTheWork._answers(monkeypatch, "Fine.")
        client.app.dependency_overrides[sync_active_model] = _sync  # type: ignore[attr-defined]
        try:
            client.post(SESSIONS, json={"sdp": "v=0 offer"})
            client.post(DELEGATIONS, json={"text": "How am I doing?"})
        finally:
            client.app.dependency_overrides.pop(sync_active_model)  # type: ignore[attr-defined]
        assert synced == [True, True]


class TestHangingUp:
    """Minutes cost money: the call ends on its own when the conversation
    is over (her sign-off) or after the profile's seconds of dead air."""

    def test_her_instructions_end_on_the_sign_off_the_page_listens_for(
        self, client: TestClient
    ) -> None:
        from tests.web.dom import one

        assert chat_live.LIVE_SIGN_OFF in FINANCE_LIVE_INSTRUCTIONS
        button = one(client.get("/chat").text, "button#chat-live")
        assert button.get("data-sign-off") == chat_live.LIVE_SIGN_OFF
        # What she says when the page has no answer: the server's words.
        assert button.get("data-sorry") == LIVE_SORRY
        assert button.get("data-not-heard") == chat_live.LIVE_NOT_HEARD
        # She opens the call: a cue, not a script, so the words vary.
        assert button.get("data-greeting") == chat_live.LIVE_GREETING

    def test_the_dead_air_limit_rides_with_how_she_is_heard(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        from app.core.config import settings
        from tests.web.dom import one

        monkeypatch.setattr(settings, "VOICE_LIVE_IDLE_SECONDS", 45)
        mic = one(client.get("/chat").text, "button#chat-mic")
        assert json.loads(mic.get("data-voice") or "{}")["idle"] == 45
