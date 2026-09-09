"""The Chat section: a page, a turn's bubbles, and the settled message.

The stream itself is the API's (``/api/v1/ai/chat/stream``) and is not
exercised here; these cover everything the page renders around it.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.components.backend.api.ai.router import ai_service
from app.components.web_frontend.filters import markdown
from app.services.ai.domains.chat.transcript import (
    balance_fences,
    footer_line,
    trace_label,
)
from app.services.ai.models import AIProvider, MessageRole
from app.services.finance.domains.detection.analyst.shared import STANDALONE_USER_ID
from tests.web.conftest import Review
from tests.web.dom import none, one, select, text, triggers


class TestPage:
    def test_renders_both_ways_with_thread_and_composer(
        self, client: TestClient, hx: TestClient
    ) -> None:
        page = client.get("/chat").text
        one(page, "aside#sidebar a[aria-current=page][href='/chat']")
        one(page, "#chat-thread")
        form = one(page, "form#chat-composer")
        assert (
            form.get("hx-post") == "/chat/turns"
            and form.get("hx-target") == "#chat-thread"
        )
        assert (
            one(form, "textarea[name=message]").get("placeholder") == "Message Illiana"
        )
        one(page, "#chat-stop[hidden]")
        fragment = hx.get("/chat").text
        none(fragment, "aside#sidebar")
        one(fragment, "#chat-thread")

    def test_the_stream_config_rides_on_the_page(self, client: TestClient) -> None:
        import json

        config = json.loads(one(client.get("/chat").text, "#chat").get("data-chat"))
        assert config["stream"] == "/api/v1/ai/chat/stream"
        assert config["defaults"] == {
            "user_id": STANDALONE_USER_ID,
            "agent_slug": "finance-assistant",
            "surface": "finance",
        }


class TestTurn:
    def test_post_returns_the_user_bubble_and_a_streaming_bubble(
        self, hx: TestClient
    ) -> None:
        html = hx.post("/chat/turns", data={"message": "  How much is due?  "}).text
        user = one(html, "li[data-role=user]")
        assert text(one(user, "[data-text]")) == "How much is due?"
        one(user, "button[data-replay]")
        bubble = one(html, "li[data-role=assistant][data-stream]")
        assert bubble.get("data-text") == "How much is due?"
        assert bubble.get("data-conversation-id") is None
        one(bubble, "[data-body]")
        one(bubble, "[data-busy]")

    def test_a_continuing_turn_carries_its_conversation(self, hx: TestClient) -> None:
        html = hx.post(
            "/chat/turns", data={"message": "and next month?", "conversation_id": "c-1"}
        ).text
        assert one(html, "[data-stream]").get("data-conversation-id") == "c-1"

    def test_empty_message_is_a_422(self, hx: TestClient) -> None:
        assert hx.post("/chat/turns", data={"message": "   "}).status_code == 422


@pytest.fixture(autouse=True)
def _own_conversations() -> None:
    """The app-owned test database lives for the whole session and commits,
    so conversations from other tests would otherwise be "the latest" here
    and pad the history; each test starts with none on this surface."""
    manager = ai_service.conversation_manager
    for conversation in manager.list_conversations(
        STANDALONE_USER_ID, surface="finance"
    ):
        manager.delete_conversation(conversation.id)


@pytest.fixture
def stored() -> tuple[str, str]:
    """A conversation with one answered turn, the way a stream leaves it."""
    conversation = ai_service.conversation_manager.create_conversation(
        provider=AIProvider.OLLAMA,
        model="gpt-5.6-luna",
        user_id=STANDALONE_USER_ID,
        surface="finance",
    )
    conversation.add_message(MessageRole.USER, "What is due?")
    reply = conversation.add_message(
        MessageRole.ASSISTANT,
        "**Two bills** this week:\n\n- Water: $45\n- Rent: $1,500\n\n<script>alert(1)</script>",
        metadata={
            "model": "gpt-5.6-luna",
            "gen_tps": 58.5,
            "cost": 0.0063,
            "tool_trace": [
                {"tool": "bills", "args": '{"days": 7}', "result": '{"count": 2}'},
                {
                    "tool": "run_code",
                    "code": "# plan\nledger(months=3)\n",
                    "result": "Runtime error: boom",
                },
            ],
        },
    )
    ai_service.conversation_manager.save_conversation(conversation)
    return conversation.id, reply.id


class TestSettledMessage:
    def test_renders_markdown_trail_and_footer(
        self, hx: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, message_id = stored
        html = hx.get(f"/chat/messages/{conversation_id}/{message_id}").text
        bubble = one(html, "li[data-role=assistant]")
        assert bubble.get("data-message-id") == message_id
        body = one(bubble, "[data-body]")
        assert text(one(body, "strong")) == "Two bills"
        assert [text(li) for li in select(body, "li")] == ["Water: $45", "Rent: $1,500"]
        none(body, "script")  # raw HTML from the model is escaped, never rendered
        rows = select(bubble, "[data-trail] button[hx-get]")
        assert [text(r) for r in rows] == [
            "bills(days=7)",
            "run_code: ledger(months=3)",
        ]
        assert "text-error" in (rows[1].get("class") or "")
        assert rows[1].get("hx-target") == "#dialog-body"
        assert (
            rows[1].get("hx-get")
            == f"/chat/messages/{conversation_id}/{message_id}/runs/1"
        )
        assert text(one(bubble, "[data-footer]")) == "gpt-5.6-luna · 58.5 tps · $0.0063"
        one(bubble, "button[data-copy]")

    def test_a_run_opens_in_the_dialog_with_its_script_and_output(
        self, hx: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, message_id = stored
        base = f"/chat/messages/{conversation_id}/{message_id}/runs"
        script = hx.get(f"{base}/1").text
        assert text(one(script, "h2")) == "run_code"
        assert "text-error" in (one(script, "h2").get("class") or "")
        code = one(script, "pre.hl")
        assert "ledger" in text(code) and select(code, "span")  # highlighted spans
        assert text(one(script, "pre:not(.hl)")) == "Runtime error: boom"
        plain = hx.get(f"{base}/0").text
        assert text(one(plain, "h2")) == "bills"
        args = select(plain, "pre.hl")[0]
        assert text(args) == '{ "days": 7 }' and select(
            args, "span"
        )  # indented, coloured
        assert hx.get(f"{base}/9").status_code == 404

    def test_unknown_message_or_conversation_is_a_404(
        self, hx: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, _ = stored
        assert hx.get(f"/chat/messages/{conversation_id}/nope").status_code == 404
        assert hx.get("/chat/messages/nope/nope").status_code == 404


class TestWords:
    def test_the_assistant_is_named_once(self) -> None:
        """The queue stamps proposals with an agent slug; every surface
        shows the assistant's name for it, and an unknown slug shows as
        itself."""
        from app.components.web_frontend.filters import assistant

        assert assistant("finance-assistant") == "Illiana"
        assert assistant("steward") == "steward"
        assert assistant(None) == ""

    def test_markdown_filter_escapes_raw_html(self) -> None:
        html = str(markdown("hi <b>there</b>\n\n<div>x</div>"))
        assert "<b>" not in html and "&lt;b&gt;" in html and "&lt;div&gt;" in html

    def test_transcript_helpers_moved_with_their_behavior(self) -> None:
        assert balance_fences("```py\nx") == "```py\nx\n```"
        assert (
            trace_label({"tool": "ledger", "args": '{"months": 3}'})
            == "ledger(months=3)"
        )
        assert (
            footer_line({"model": "m", "gen_tps": 10, "cost": 0.5})
            == "m  ·  10 tps  ·  $0.5000"
        )


@pytest.fixture
def proposed(review: Review) -> tuple[str, str]:
    """A turn whose trace proposed the review fixture's change and batch,
    plus a marker of a kind nobody knows."""
    conversation = ai_service.conversation_manager.create_conversation(
        provider=AIProvider.OLLAMA,
        model="m",
        user_id=STANDALONE_USER_ID,
        surface="finance",
    )
    conversation.add_message(MessageRole.USER, "File these")
    reply = conversation.add_message(
        MessageRole.ASSISTANT,
        "Queued.",
        metadata={
            "tool_trace": [
                {
                    "tool": "propose",
                    "args": "{}",
                    "result": "{...clipped",
                    "component": {
                        "kind": "pending_change",
                        "pending_change_id": review.change,
                    },
                },
                {
                    "tool": "propose_many",
                    "args": "{}",
                    "result": "{...clipped",
                    "component": {
                        "kind": "pending_change_batch",
                        "batch_id": review.batch,
                        "count": 2,
                    },
                },
                {
                    "tool": "propose",
                    "args": "{}",
                    "result": "{}",
                    "component": {"kind": "hologram", "id": 7},
                },
            ]
        },
    )
    ai_service.conversation_manager.save_conversation(conversation)
    return conversation.id, reply.id


class TestComponents:
    def test_settled_message_places_one_loader_per_known_kind(
        self, hx: TestClient, proposed: tuple[str, str], review: Review
    ) -> None:
        """The model never authors layout: a marker becomes a placeholder
        that loads the card from the queue, and an unknown kind is absent."""
        conversation_id, message_id = proposed
        html = hx.get(f"/chat/messages/{conversation_id}/{message_id}").text
        loaders = select(html, "[data-components] [data-component-load]")
        assert [
            (el.get("data-component-load"), el.get("hx-get")) for el in loaders
        ] == [
            ("pending_change", f"/chat/components/change/{review.change}"),
            ("pending_change_batch", f"/chat/components/batch/{review.batch}"),
        ]
        assert all(el.get("hx-trigger") == "load" for el in loaders)

    def test_change_card_approves_in_place_and_leaves_the_queue(
        self, client: TestClient, hx: TestClient, review: Review
    ) -> None:
        card = one(
            hx.get(f"/chat/components/change/{review.change}").text,
            "[data-component=pending_change]",
        )
        assert card.get("id") == f"chat-change-{review.change}"
        assert text(one(card, "[data-tone=warn]")) == "Awaiting your approval"
        approve = next(
            b for b in select(card, "button[hx-post]") if text(b) == "Approve"
        )
        assert (
            approve.get("hx-post") == f"/chat/components/change/{review.change}/approve"
        )
        assert approve.get("hx-target") == f"#chat-change-{review.change}"

        after = one(
            hx.post(f"/chat/components/change/{review.change}/approve").text,
            "[data-component=pending_change]",
        )
        assert text(one(after, "[data-tone=ok]")) == "Approved"
        none(after, "button[hx-post]")
        none(client.get("/review").text, f"#change-{review.change}")

    def test_batch_card_vetoes_then_reads_as_an_outcome(
        self, hx: TestClient, review: Review
    ) -> None:
        card = one(
            hx.get(f"/chat/components/batch/{review.batch}").text,
            "[data-component=pending_change_batch]",
        )
        boxes = select(card, "input[name=exclude_ids]")
        assert len(boxes) == 2
        approve = next(
            b for b in select(card, "button[hx-post]") if text(b) == "Approve all"
        )
        assert approve.get("hx-include") == f"#chat-batch-{review.batch}"

        after = hx.post(
            f"/chat/components/batch/{review.batch}/approve",
            data={"exclude_ids": [boxes[1].get("value")]},
        ).text
        resolved = one(after, "[data-component=pending_change_batch]")
        assert text(one(resolved, "[data-outcome]")) == "1 approved, 1 rejected"
        none(resolved, "input[name=exclude_ids]")

    def test_unknown_card_is_a_404(self, hx: TestClient, review: Review) -> None:
        assert hx.get("/chat/components/change/999999").status_code == 404
        assert hx.get("/chat/components/batch/nope").status_code == 404


class TestHistory:
    def test_page_resumes_the_latest_conversation(
        self, client: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, message_id = stored
        page = client.get("/chat").text
        none(page, "#chat-empty")
        assert one(page, "#chat-conversation").get("value") == conversation_id
        roles = [li.get("data-role") for li in select(page, "#chat-thread > li")]
        assert roles == ["user", "assistant"]
        assert one(page, f"[data-message-id='{message_id}'] [data-footer]") is not None

    def test_empty_history_opens_blank(self, client: TestClient) -> None:
        page = client.get("/chat").text
        one(page, "#chat-empty")
        assert one(page, "#chat-conversation").get("value") == ""

    def test_history_dialog_lists_and_loads(
        self, hx: TestClient, stored: tuple[str, str]
    ) -> None:
        conversation_id, _ = stored
        dialog = hx.get("/chat/conversations").text
        row = one(dialog, "[data-history] button[hx-get]")
        assert row.get("hx-get") == f"/chat/conversations/{conversation_id}"
        assert row.get("hx-target") == "#chat-thread"
        assert text(row).startswith("What is due?")

        response = hx.get(f"/chat/conversations/{conversation_id}")
        assert "dialog:close" in triggers(response)
        thread = response.text
        assert [li.get("data-role") for li in select(thread, "li[data-role]")] == [
            "user",
            "assistant",
        ]
        oob = one(thread, "input#chat-conversation[hx-swap-oob]")
        assert oob.get("value") == conversation_id

    def test_new_conversation_empties_the_thread(
        self, hx: TestClient, stored: tuple[str, str]
    ) -> None:
        thread = hx.get("/chat/conversations/new").text
        one(thread, "#chat-empty")
        none(thread, "li[data-role]")
        assert one(thread, "input#chat-conversation[hx-swap-oob]").get("value") == ""

    def test_replayed_user_message_drops_its_attachment_marker(
        self, hx: TestClient
    ) -> None:
        conversation = ai_service.conversation_manager.create_conversation(
            provider=AIProvider.OLLAMA,
            model="m",
            user_id=STANDALONE_USER_ID,
            surface="finance",
        )
        conversation.add_message(
            MessageRole.USER, "What is this?\n\n[attached 1 image: receipt.png]"
        )
        ai_service.conversation_manager.save_conversation(conversation)
        thread = hx.get(f"/chat/conversations/{conversation.id}").text
        assert text(one(thread, "[data-role=user] [data-text]")) == "What is this?"

    def test_unknown_conversation_is_a_404(self, hx: TestClient) -> None:
        assert hx.get("/chat/conversations/nope").status_code == 404


class TestAttachments:
    def test_turn_notes_the_attached_images(self, hx: TestClient) -> None:
        """Only the names come to the turn route; the bytes ride the
        stream request. No text with images sends the house line."""
        html = hx.post(
            "/chat/turns",
            data={"message": "", "attachment_names": ["receipt.png", "menu.jpg"]},
        ).text
        user = one(html, "li[data-role=user]")
        assert text(one(user, "[data-text]")) == "See the attached images."
        assert text(one(user, "[data-attached]")) == "Attached: receipt.png, menu.jpg"
        assert one(html, "[data-stream]").get("data-text") == "See the attached images."

    async def test_replay_shows_stored_images_and_serves_them(
        self, hx: TestClient
    ) -> None:
        from app.core.storage import get_storage

        png = b"\x89PNG-not-really-a-png"
        key = await get_storage().put(png, content_type="image/png")
        conversation = ai_service.conversation_manager.create_conversation(
            provider=AIProvider.OLLAMA,
            model="m",
            user_id=STANDALONE_USER_ID,
            surface="finance",
        )
        conversation.add_message(
            MessageRole.USER,
            "What did I buy?\n\n[attached 1 image: receipt.png]",
            metadata={
                "attachments": [
                    {"key": key, "media_type": "image/png", "name": "receipt.png"}
                ]
            },
        )
        ai_service.conversation_manager.save_conversation(conversation)

        thread = hx.get(f"/chat/conversations/{conversation.id}").text
        img = one(thread, "[data-role=user] [data-attachments] img")
        assert img.get("alt") == "receipt.png"
        assert img.get("src") == f"/chat/attachments/{key}?type=image%2Fpng"
        # Opens in the one modal, never a new tab.
        none(thread, "[data-attachments] a[target=_blank]")
        assert one(thread, "[data-attachments] button[data-view-image]").get(
            "data-view-image"
        ) == img.get("src")

        served = hx.get(img.get("src"))
        assert served.status_code == 200
        assert served.content == png and served.headers["content-type"] == "image/png"
        assert "immutable" in served.headers["cache-control"]

    def test_attachment_route_refuses_bad_keys_and_types(self, hx: TestClient) -> None:
        assert hx.get("/chat/attachments/../etc/passwd").status_code == 404
        assert hx.get("/chat/attachments/" + "a" * 64).status_code == 404
        assert (
            hx.get(
                "/chat/attachments/" + "a" * 64, params={"type": "text/html"}
            ).status_code
            == 404
        )

    def test_composer_has_the_attach_control(self, client: TestClient) -> None:
        page = client.get("/chat").text
        attach = one(page, "input#chat-attach[type=file]")
        assert attach.get("accept") == "image/png,image/jpeg,image/webp,image/gif"
        assert attach.get("multiple") is not None
        one(page, "#chat-attachments[hidden]")


class TestModelPicker:
    """The picker reads the catalog through the LLM handlers; the suite's
    catalog is empty, so these stub the two reads and exercise the
    shaping, the markup and the pick."""

    @pytest.fixture
    def catalog(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        import importlib

        llm = importlib.import_module("app.components.backend.api.llm.router")
        from app.components.web_frontend.routes import chat as routes

        state: dict[str, Any] = {"active": "qwen2.5:7b", "picks": []}

        async def current() -> Any:
            return llm.CurrentConfigResponse(
                provider="ollama",
                model=state["active"],
                temperature=0.7,
                max_tokens=1024,
            )

        async def models(**_: Any) -> list[Any]:
            return [
                llm.ModelResponse(
                    model_id="qwen2.5:7b",
                    title="Qwen 2.5 7B",
                    vendor="ollama",
                    family="qwen",
                    context_window=32000,
                    input_price=None,
                    output_price=None,
                    released_on="2025-01-01",
                ),
                llm.ModelResponse(
                    model_id="gpt-oss:20b",
                    title="GPT OSS 20B",
                    vendor="ollama",
                    family="gpt-oss",
                    context_window=128000,
                    input_price=None,
                    output_price=None,
                    released_on="2025-08-01",
                ),
                llm.ModelResponse(
                    model_id="gpt-4o",
                    title="OpenAI: GPT-4o",
                    vendor="openai",
                    family="gpt-4",
                    context_window=128000,
                    input_price=2.5,
                    output_price=10.0,
                    released_on="2024-05-13",
                ),
            ]

        async def vendors(**_: Any) -> list[Any]:
            return [
                llm.VendorResponse(name="ollama", model_count=2),
                llm.VendorResponse(name="openai", model_count=1, icon_b64="AAAA"),
            ]

        async def set_current(body: Any) -> Any:
            if body.model_id == "nope":
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=404, detail="Model 'nope' is not in the catalog."
                )
            state["active"] = body.model_id
            state["picks"].append(body.model_id)
            return llm.SetModelResponse(
                success=True, model_id=body.model_id, message="ok"
            )

        monkeypatch.setattr(routes, "get_current", current)
        monkeypatch.setattr(routes, "get_models", models)
        monkeypatch.setattr(routes, "get_vendors", vendors)
        monkeypatch.setattr(routes, "set_current", set_current)
        return state

    def test_chip_names_the_active_model_and_opens_the_picker(
        self, client: TestClient, catalog: dict[str, Any]
    ) -> None:
        chip = one(client.get("/chat").text, "#chat-model")
        assert text(chip) == "qwen2.5:7b"
        assert (
            chip.get("hx-get") == "/chat/models"
            and chip.get("hx-target") == "#dialog-body"
        )

    def test_vendor_sections_open_on_the_active_model_and_mark_it(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.get("/chat/models").text
        sections = select(html, "details")
        assert [text(one(s, "summary .micro-label")) for s in sections] == [
            "ollama",
            "openai",
        ]
        assert sections[0].get("open") is not None and sections[1].get("open") is None
        active = one(html, "button[aria-current=true]")
        assert active.get("data-model-id") == "qwen2.5:7b"
        # Newest first within a section; the vendor prefix comes off under its own section.
        ollama_rows = [
            b.get("data-model-id") for b in select(sections[0], "button[data-model-id]")
        ]
        assert ollama_rows == ["gpt-oss:20b", "qwen2.5:7b"]
        gpt4o = one(sections[1], "button[data-model-id='gpt-4o']")
        assert text(one(gpt4o, "span span:first-child")) == "GPT-4o"
        assert "128k · $2.50 / $10" in text(gpt4o)
        one(sections[1], "summary img")  # the vendor icon rides the section

    def test_family_sections_wear_the_vendor_and_their_rows_go_bare(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.get("/chat/models", params={"mode": "family"}).text
        sections = select(html, "details")
        names = [text(one(s, "summary .micro-label")) for s in sections]
        assert names == ["Gpt 4", "Gpt Oss", "Qwen"]
        gpt4 = sections[0]
        assert one(gpt4, "summary img").get("width") == "28"  # the vendor, once
        none(gpt4, "button[data-model-id] img")  # the rows do not repeat it

    def test_search_flattens_and_keeps_the_context(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.get("/chat/models", params={"q": "gpt", "mode": "vendor"}).text
        none(html, "details")
        rows = select(html, "button[data-model-id]")
        assert [r.get("data-model-id") for r in rows] == ["gpt-oss:20b", "gpt-4o"]
        assert text(one(rows[1], "span span:first-child")) == "OpenAI: GPT-4o"
        assert "No models match" in hx.get("/chat/models", params={"q": "zzz"}).text

    def test_a_pick_switches_in_place_and_updates_the_chip(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.post(
            "/chat/models", data={"model_id": "gpt-4o", "q": "", "mode": "vendor"}
        ).text
        assert catalog["picks"] == ["gpt-4o"]
        assert one(html, "button[aria-current=true]").get("data-model-id") == "gpt-4o"
        chip = one(html, "#chat-model[hx-swap-oob]")
        assert text(chip) == "gpt-4o"

    def test_a_refused_pick_changes_nothing_and_says_why(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        response = hx.post("/chat/models", data={"model_id": "nope"})
        assert catalog["picks"] == []
        assert (
            one(response.text, "button[aria-current=true]").get("data-model-id")
            == "qwen2.5:7b"
        )
        none(response.text, "#chat-model")
        assert "not in the catalog" in triggers(response)["toast"]["text"]
