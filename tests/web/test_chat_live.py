"""Talking to Illiana live (#252): GPT-Live is her ears and voice, her own
agent does the thinking.

The browser's WebRTC offer goes through steward (the key never leaves the
server) to open a gpt-live-1 session with CLIENT delegation. When GPT-Live
needs real work it says so; the browser sends what was said to steward,
which runs her actual agent turn - her prompt, tools and approval cards, in
the conversation - and hands back what to say.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.components.backend.api.ai.router import ai_service, sync_active_model
from app.components.web_frontend.routes import chat_live
from app.components.web_frontend.routes.chat_live import (
    DELEGATIONS,
    LIVE_SORRY,
    SESSIONS,
)
from app.services.ai.domains.voice.realtime_calls import END_CALL
from app.services.ai.models import StreamingMessage
from app.services.finance.domains.detection.analyst.live_engines import (
    REALTIME_REPLY_CAP,
)
from app.services.finance.domains.detection.analyst.prompts import (
    FINANCE_LIVE_INSTRUCTIONS,
)
from app.services.finance.domains.detection.analyst.shared import (
    FINANCE_VOICE_AGENT_SLUG,
    STANDALONE_USER_ID,
)
from tests._voice_catalog import VOICE_MODELS

pytestmark = pytest.mark.usefixtures("live_engine_rows")
# An engine is named by its catalog model.
TITLES = {m["model_id"]: m["title"] for m in VOICE_MODELS}

SECRET = "secret internal detail"


class _Live:
    """Stands in for ``AsyncOpenAI().live``."""

    def __init__(self, fail: Exception | None = None) -> None:
        import uuid

        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        # Its own id per test: the ledger rows outlive a test (they go
        # through the app's session), and a shared id found another
        # test's row in CI's order.
        self.session_id = f"live_{uuid.uuid4().hex[:12]}"

    async def create(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return SimpleNamespace(
            session=SimpleNamespace(id=self.session_id),
            transport=SimpleNamespace(type="webrtc", sdp="v=0 answer"),
        )


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> _Live:
    fake = _Live()
    monkeypatch.setattr(chat_live, "_live_api", lambda: fake)
    return fake


@pytest.fixture
def synced(client: TestClient) -> Iterator[list[bool]]:
    """One entry each time a live route adopts the active model."""
    calls: list[bool] = []

    async def _sync() -> None:
        calls.append(True)

    client.app.dependency_overrides[sync_active_model] = _sync  # type: ignore[attr-defined]
    yield calls
    client.app.dependency_overrides.pop(sync_active_model)  # type: ignore[attr-defined]


class TestOpeningASession:
    def test_the_offer_is_answered_by_a_client_delegated_gpt_live(
        self, client: TestClient, live: _Live
    ) -> None:
        response = client.post(SESSIONS, json={"sdp": "v=0 offer"})

        assert response.status_code == 200
        assert response.json() == {
            "sdp": "v=0 answer",
            "session_id": live.session_id,
            "transport": "gpt_live",
            "engine": {
                "label": TITLES["gpt-live-1"],
                "per_second": pytest.approx(0.05 / 60),
            },
            # how she opens: a first call is greeted (#292)
            "greeting": chat_live.LIVE_GREETING,
        }
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
        client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        )

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
        drawn = {
            e.get("data-mic-visual") for e in button.cssselect("[data-mic-visual]")
        }
        assert drawn == {"recording", "thinking", "speaking", "working"}

    def test_the_status_line_says_so(self, client: TestClient) -> None:
        from tests.web.dom import one

        one(client.get("/chat").text, "template#chat-mic-states [data-state=working]")


class TestHerModel:
    """A delegation runs on the stored active model, not the .env default:
    a reload mid-call left the process on llama3.2:3b, which Ollama did not
    have, and every answer failed (2026-09-25)."""

    def test_every_live_route_adopts_the_active_model_first(
        self,
        client: TestClient,
        live: _Live,
        monkeypatch: pytest.MonkeyPatch,
        synced: list[bool],
    ) -> None:
        TestDoingTheWork._answers(monkeypatch, "Fine.")
        client.post(SESSIONS, json={"sdp": "v=0 offer"})
        client.post(DELEGATIONS, json={"text": "How am I doing?"})
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
        # a realtime call ends on her tool; the page knows its name
        assert button.get("data-end-call") == END_CALL
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


class TestTheMeter:
    """Every live minute is in the ledger (#270): the call's row opens with
    the call, and the page reports the billed seconds as OpenAI counts them."""

    @staticmethod
    async def _row(session_id: str) -> Any:
        from sqlmodel import select

        from app.core.db import get_async_session
        from app.services.ai.models.llm import LLMUsage

        async with get_async_session() as session:
            return (
                await session.exec(
                    select(LLMUsage).where(LLMUsage.session_id == session_id)
                )
            ).first()

    async def test_opening_a_call_opens_its_row(
        self, client: TestClient, live: _Live, stored: tuple[str, str]
    ) -> None:
        conversation_id, _ = stored
        client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        )
        row = await self._row(live.session_id)
        assert row is not None
        assert (row.model_id, row.action, row.conversation_id) == (
            "gpt-live-1",
            "live",
            conversation_id,
        )

    async def test_the_page_reports_the_seconds(
        self, client: TestClient, live: _Live
    ) -> None:
        client.post(SESSIONS, json={"sdp": "v=0 offer"})
        response = client.post(
            chat_live.USAGE,
            json={
                "session_id": live.session_id,
                "seconds": 90,
                "reason": "close_requested",
            },
        )
        assert response.status_code == 204
        row = await self._row(live.session_id)
        assert row.audio_seconds == 90
        assert row.total_cost == pytest.approx(0.075)

    def test_an_unknown_call_is_a_404(self, client: TestClient) -> None:
        response = client.post(
            chat_live.USAGE, json={"session_id": "nope", "seconds": 5}
        )
        assert response.status_code == 404

    def test_the_live_button_knows_where_to_report(self, client: TestClient) -> None:
        from tests.web.dom import one

        button = one(client.get("/chat").text, "button#chat-live")
        assert button.get("data-usage") == chat_live.USAGE


class TestTheEngine:
    """The phone dials whichever engine her voice profile names (#273)."""

    @pytest.fixture
    def realtime(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        from app.core.config import settings
        from app.services.ai.domains.voice import realtime_calls

        calls: list[dict[str, Any]] = []

        async def _built(**kwargs: Any) -> dict[str, Any]:
            return kwargs  # stands in for the realtime agent it describes

        async def _open(sdp: str, **kwargs: Any) -> str:
            calls.append({"sdp": sdp, **kwargs})
            return "v=0 realtime answer"

        monkeypatch.setattr(settings, "VOICE_LIVE_ENGINE", "gpt-realtime-2.1")
        monkeypatch.setattr(realtime_calls, "realtime_for", _built)
        monkeypatch.setattr(realtime_calls, "open_call", _open)
        return calls

    def test_a_realtime_engine_runs_her_own_agent(
        self,
        client: TestClient,
        realtime: list[dict[str, Any]],
        stored: tuple[str, str],
    ) -> None:
        conversation_id, _ = stored
        response = client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        )

        assert response.status_code == 200
        assert response.json() == {
            "sdp": "v=0 realtime answer",
            "transport": "realtime",
            "conversation_id": conversation_id,
            "engine": {"label": TITLES["gpt-realtime-2.1"], "per_second": None},
            # the page opens the call with this: a conversation under way is
            # carried on, not met again (#292); a dropped one picks up
            "greeting": chat_live.LIVE_CONTINUE,
        }
        (call,) = realtime
        assert call["model"] == "openai:gpt-realtime-2.1"
        assert call["agent_slug"] == FINANCE_VOICE_AGENT_SLUG
        assert call["conversation_id"] == conversation_id
        # Her engine's call manners lead her prompt; a reply is capped.
        built = call["realtime"]
        assert "LIVE" in built["instructions"]
        # The names she will hear, spelled (#293): the transcript of a call
        # takes no hint, so she is told them instead.
        assert "Illiana" in built["instructions"].rsplit("\n\n", 1)[-1]
        assert built["max_output_tokens"] == REALTIME_REPLY_CAP
        assert built["conversation"].id == conversation_id

    def test_without_a_conversation_it_starts_one(
        self, client: TestClient, realtime: list[dict[str, Any]]
    ) -> None:
        body = client.post(SESSIONS, json={"sdp": "v=0 offer"}).json()
        assert body["conversation_id"]
        assert realtime[0]["conversation_id"] == body["conversation_id"]

    def test_a_refused_realtime_call_is_a_502(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        stored: tuple[str, str],
    ) -> None:
        from app.core.config import settings
        from app.services.ai.domains.voice import realtime_calls

        async def _refused(sdp: str, **kwargs: Any) -> str:
            raise RuntimeError(SECRET)

        monkeypatch.setattr(settings, "VOICE_LIVE_ENGINE", "gpt-realtime-2.1")
        monkeypatch.setattr(realtime_calls, "open_call", _refused)
        conversation_id, _ = stored
        response = client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        )
        assert response.status_code == 502
        assert SECRET not in response.text


class TestTheCallBar:
    """In a call, the composer row becomes a call bar (#273): what she is
    doing, the engine, a timer and the running cost, mute on the same mic,
    and hang up."""

    def test_the_bar_waits_hidden_in_the_page(self, client: TestClient) -> None:
        from tests.web.dom import one

        page = client.get("/chat").text
        bar = one(page, "#chat-call")
        assert bar.get("hidden") is not None
        mute = one(page, "#chat-call button#chat-mute")
        assert mute.get("aria-pressed") == "false"
        one(page, "#chat-call button#chat-hang-up")
        one(page, "#chat-call [data-call-timer]")
        one(page, "#chat-call [data-call-cost]")
        one(page, "#chat-call [data-call-engine]")
        one(page, "template#chat-mic-states [data-state=muted]")

    def test_gpt_live_says_its_rate(self, client: TestClient, live: _Live) -> None:
        body = client.post(SESSIONS, json={"sdp": "v=0 offer"}).json()
        assert body["engine"] == {
            "label": TITLES["gpt-live-1"],
            "per_second": pytest.approx(0.05 / 60),
        }

    def test_a_realtime_engine_is_priced_after_the_call(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        stored: tuple[str, str],
    ) -> None:
        from app.core.config import settings
        from app.services.ai.domains.voice import realtime_calls

        async def _open(sdp: str, **kwargs: Any) -> str:
            return "v=0 realtime answer"

        monkeypatch.setattr(settings, "VOICE_LIVE_ENGINE", "gpt-realtime-2.1")
        monkeypatch.setattr(realtime_calls, "open_call", _open)
        conversation_id, _ = stored
        body = client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        ).json()
        assert body["engine"] == {
            "label": TITLES["gpt-realtime-2.1"],
            "per_second": None,
        }


class TestTheRelay:
    """Gemini Live has no WebRTC hand-off: the call's audio comes through
    our server's WebSocket, both ways (#273)."""

    @pytest.fixture
    def gemini(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            PartEndEvent,
            RealtimeTurnCompleteEvent,
            SpeechPart,
            ToolCallPart,
        )

        from app.core.config import settings
        from app.services.ai.domains.voice import realtime_calls
        from tests._realtime import FakeRealtime

        fake = FakeRealtime(
            [
                PartEndEvent(
                    index=0, part=SpeechPart(speaker="user", transcript="Hi there")
                ),
                FunctionToolCallEvent(
                    part=ToolCallPart(
                        tool_name="run_code",
                        args={"code": "result = await budget()"},
                        tool_call_id="t1",
                    )
                ),
                PartEndEvent(
                    index=1,
                    part=SpeechPart(
                        speaker="assistant", transcript="Hello! Talk soon."
                    ),
                ),
                FunctionToolCallEvent(
                    part=ToolCallPart(
                        tool_name=realtime_calls.END_CALL, args={}, tool_call_id="e1"
                    )
                ),
                RealtimeTurnCompleteEvent(),
            ],
            after_audio=True,
        )

        async def _built(**kwargs: Any) -> Any:
            return fake

        monkeypatch.setattr(settings, "VOICE_LIVE_ENGINE", "gemini-live")
        monkeypatch.setattr(realtime_calls, "realtime_for", _built)
        return fake

    def test_the_phone_asks_how_to_dial(self, client: TestClient, gemini: Any) -> None:
        body = client.get(chat_live.ENGINE).json()
        assert body == {
            "transport": "relay",
            "engine": {"label": TITLES["gemini-3.8-live"], "per_second": None},
        }

    def test_a_webrtc_engine_dials_as_before(self, client: TestClient) -> None:
        assert client.get(chat_live.ENGINE).json()["transport"] == "gpt_live"

    def test_the_call_carries_audio_both_ways(
        self, client: TestClient, gemini: Any
    ) -> None:
        import json

        from tests._realtime import VOICE

        heard: list[bytes] = []
        told: list[dict[str, Any]] = []
        with client.websocket_connect(chat_live.RELAY) as ws:
            ready = ws.receive_json()
            ws.send_bytes(b"\x10\x20")
            for _ in range(20):  # a bound, never a hang
                message = ws.receive()
                if message.get("bytes"):
                    heard.append(message["bytes"])
                elif message.get("text"):
                    told.append(json.loads(message["text"]))
                if heard and any(t["type"] == "saved" for t in told):
                    break

        assert ready["type"] == "ready"
        assert (ready["input_rate"], ready["output_rate"]) == (16_000, 24_000)
        assert ready["conversation_id"]  # a call without one starts one
        assert heard == [VOICE]  # her voice, as the model spoke it
        assert gemini.live.audio == [b"\x10\x20"]  # yours, as the page sent it
        assert gemini.live.sent == [chat_live.LIVE_GREETING]  # she speaks first
        said = [t["text"] for t in told if t["type"] == "said"]
        assert said == ["Hello! Talk soon."]
        # each step as it runs, labelled as the thread's trail labels it
        steps = [t.get("label") for t in told if t["type"] == "working"]
        assert steps == ["run_code: result = await budget()"]
        # she ended the call: the page hangs up once her audio has played
        assert {"type": "hang_up"} in told
        saved = told[-1]
        assert saved["type"] == "saved"  # the thread reloads
        assert saved["cost"] > 0  # and the call bar shows the running cost

    def test_a_call_into_a_conversation_does_not_meet_them_again(
        self, client: TestClient, live: _Live, stored: tuple[str, str]
    ) -> None:
        """#292: every call opened "Lovely to meet you" with hundreds of turns
        behind it. A call into a conversation picks it up instead."""
        conversation_id, _ = stored
        response = client.post(
            SESSIONS, json={"sdp": "v=0 offer", "conversation_id": conversation_id}
        )

        assert response.json()["greeting"] == chat_live.LIVE_CONTINUE

    def test_a_dropped_call_picks_up_where_it_stopped(
        self, client: TestClient, gemini: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.ai.domains.voice import realtime_calls

        monkeypatch.setattr(
            realtime_calls, "resumed", lambda conversation: "How much is left?"
        )
        with client.websocket_connect(chat_live.RELAY) as ws:
            ws.receive_json()
            ws.send_bytes(b"\x10\x20")  # the fake plays once it hears you
        (opening,) = gemini.live.sent
        assert opening != chat_live.LIVE_GREETING
        assert "How much is left?" in opening

    def test_the_relay_adopts_the_active_model_first(
        self, client: TestClient, gemini: Any, synced: list[bool]
    ) -> None:
        """Her prompt is built for the service's model: a call that skipped
        adopting the active one built it for the container's default
        (Ollama, in compact mode) instead."""
        with client.websocket_connect(chat_live.RELAY) as ws:
            ws.receive_json()
            ws.send_bytes(b"\x10\x20")
        assert synced == [True]

    def test_a_webrtc_engine_is_refused_on_the_relay(self, client: TestClient) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(chat_live.RELAY) as ws,
        ):
            ws.receive_json()
        assert refused.value.code == 1008

    def test_the_phone_knows_the_relay(self, client: TestClient) -> None:
        from tests.web.dom import one

        button = one(client.get("/chat").text, "button#chat-live")
        assert button.get("data-engine") == chat_live.ENGINE
        assert button.get("data-relay") == chat_live.RELAY
        assert "mic-worklet" in (button.get("data-worklet") or "")
