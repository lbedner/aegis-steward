"""The Overseer's chat: steward's one chat surface, shown as the AI page's
Chat section and in the Illiana drawer beside every other Overseer page.
Everything the surface does (turns, settling, history) is steward's chat
routes and is covered in test_chat.py; this covers the Overseer mounting it.

It is still Illiana. What differs is how she behaves there: the default
agent, which carries the system's own context (health, usage, the model
catalog), and a history of its own.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.components.web_frontend.routes import chat
from app.services.ai.domains.chat.agent_loader import DEFAULT_AGENT_SLUG
from app.services.ai.models import (
    AIProvider,
    Conversation,
    ConversationMessage,
    MessageRole,
)
from app.services.finance.domains.detection.analyst.shared import STANDALONE_USER_ID
from app.services.system.models import ComponentStatus
from tests.web.dom import none, one, select, text
from tests.web.overseer import sign_in, status_with

PAGE = "/overseer/services/ai"


def _conversation() -> Conversation:
    return Conversation(
        id="c1",
        title="Hello",
        provider=AIProvider.OLLAMA,
        model="llama3",
        metadata={"user_id": STANDALONE_USER_ID},
        messages=[
            ConversationMessage(id="m1", role=MessageRole.USER, content="What is due?"),
            ConversationMessage(
                id="m2",
                role=MessageRole.ASSISTANT,
                content="A **bill**.",
                metadata={"model": "llama3", "provider": "ollama"},
            ),
        ],
    )


@pytest.fixture
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def conversations(surface: str = chat.SURFACE) -> list[Conversation]:
        return [_conversation()] if surface == "overseer" else []

    async def model_icons(messages: list) -> dict[str, str]:
        return {}

    monkeypatch.setattr(chat, "_conversations", conversations)
    monkeypatch.setattr(chat, "model_icons", model_icons)
    ai = ComponentStatus(name="ai", message="AI", metadata={"engine": "pydantic-ai"})
    sign_in(app, monkeypatch, status_with(services=[ai]))
    return TestClient(app)


def _chat_page(client: TestClient) -> str:
    response = client.get(f"{PAGE}/chat")
    assert response.status_code == 200, response.text
    return response.text


def test_the_section_opens_on_the_latest_conversation(client: TestClient) -> None:
    html = _chat_page(client)
    bubbles = select(html, "#chat-thread [data-role]")
    assert [b.get("data-role") for b in bubbles] == ["user", "assistant"]
    assert one(bubbles[1], "[data-body] strong") is not None  # markdown, server-side
    assert one(html, "#chat-conversation").get("value") == "c1"


def test_it_is_illiana_with_the_system_s_context(client: TestClient) -> None:
    """One chat and one name; in the Overseer she runs as the default
    agent, the one the stream hands health, usage and the catalog to, and
    her threads here are the Overseer's own."""
    html = _chat_page(client)
    config = json.loads(one(html, "#chat").get("data-chat"))
    assert config["defaults"] == {
        "user_id": STANDALONE_USER_ID,
        "agent_slug": DEFAULT_AGENT_SLUG,
        "surface": "overseer",
    }
    assert config["urls"] == chat.CHAT_URLS
    assert one(html, "form#chat-composer").get("hx-post") == chat.TURNS
    placeholder = one(html, "#chat-composer textarea").get("placeholder")
    assert placeholder == f"Message {chat.ASSISTANT_NAME}"


def test_the_header_carries_the_chat_verbs(client: TestClient) -> None:
    header = one(_chat_page(client), "#app-content header")
    assert f"Ask {chat.ASSISTANT_NAME} anything" in text(header)
    assert select(header, f"[hx-get='{chat.CONVERSATIONS}/new']")
    assert select(header, f"[hx-get='{chat.CONVERSATIONS}?surface=overseer']")


def test_one_surface_per_document(client: TestClient) -> None:
    """The page is the surface; the drawer beside it stays empty, so the
    ids the scripts look up answer for one element only."""
    html = _chat_page(client)
    assert len(select(html, "#chat")) == 1
    assert len(one(html, "#illiana-body")) == 0


def test_other_pages_carry_steward_s_drawer(client: TestClient) -> None:
    html = client.get(PAGE).text
    none(html, "#chat")
    none(html, "#chat-fab")  # no second drawer
    drawer = f"{chat.SECTION.path}/drawer?surface=overseer"
    shell = one(html, f"[x-data=\"shell('{chat.SECTION.path}', false, '{drawer}')\"]")
    one(shell, "#illiana-fab")


def test_the_overseer_drawer_and_history_are_the_overseer_s(
    client: TestClient,
) -> None:
    drawer = client.get(f"{chat.SECTION.path}/drawer?surface=overseer").text
    assert one(drawer, "#chat-conversation").get("value") == "c1"
    config = json.loads(one(drawer, "#chat").get("data-chat"))
    assert config["defaults"]["surface"] == "overseer"
    history = client.get(f"{chat.CONVERSATIONS}?surface=overseer").text
    assert "Hello" in history
    assert "Hello" not in client.get(chat.CONVERSATIONS).text


def test_an_unknown_surface_is_not_found(client: TestClient) -> None:
    assert client.get(f"{chat.CONVERSATIONS}?surface=nope").status_code == 404
    assert client.get(f"{chat.SECTION.path}/drawer?surface=nope").status_code == 404
