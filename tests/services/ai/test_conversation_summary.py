"""A long conversation keeps what fell out of view, condensed (#295).

The history replayed each turn is budgeted by size and the oldest turns
drop. At 519 messages Illiana said 256 had fallen out of her view and
that anything said only there "may need to be repeated". Turns leaving
the window are now folded, off the request path, into a running summary
kept on the conversation, and the summary rides ahead of the history.
"""

from typing import Any

import pytest

from app.services.ai.domains.chat import summary
from app.services.ai.domains.chat.self_context import begin_turn_context
from app.services.ai.models import AIProvider, Conversation, MessageRole
from app.services.ai.service.prompt import PromptMixin


def _conversation(turns: int, size: int = 100) -> Conversation:
    """``turns`` messages of ``size`` characters, the first naming the dog,
    then the question being asked now."""
    conversation = Conversation(id="c1", provider=AIProvider.OLLAMA, model="test-model")
    for index in range(turns):
        role = MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT
        text = "Our dog is called Biscuit." if index == 0 else "x" * size
        conversation.add_message(role, text.ljust(size, "."))
    conversation.add_message(MessageRole.USER, "What is the dog called?")
    return conversation


def _build(conversation: Conversation, budget: int) -> str:
    return PromptMixin._build_conversation_context(  # type: ignore[arg-type]
        None, conversation, history_budget=budget
    )


class TestTheSummaryRides:
    def test_an_early_turn_answers_from_the_summary(self) -> None:
        """The dog's name fell out of the window; the summary still has it."""
        conversation = _conversation(40)
        conversation.metadata[summary.SUMMARY_KEY] = {
            "text": "They have a dog called Biscuit.",
            "through": 30,
        }

        built = _build(conversation, budget=1_000)

        assert "Our dog is called Biscuit" not in built  # out of view
        assert "Biscuit" in built  # but summarized
        assert built.index("Biscuit") < built.index("What is the dog called?")

    def test_a_thread_inside_the_window_carries_no_summary(self) -> None:
        conversation = _conversation(4)
        conversation.metadata[summary.SUMMARY_KEY] = {"text": "STALE", "through": 2}

        assert "STALE" not in _build(conversation, budget=10_000)

    def test_the_turn_records_where_the_window_starts(self) -> None:
        begin_turn_context()
        conversation = _conversation(40)

        _build(conversation, budget=1_000)

        stamp = begin_turn_context.__globals__["_turn"].get()
        assert stamp.first_kept == stamp.messages_dropped  # all user/assistant


class TestTheFold:
    @pytest.mark.asyncio
    async def test_fallen_turns_fold_into_the_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conversation = _conversation(40)
        conversation.metadata[summary.SUMMARY_KEY] = {
            "text": "Earlier: hello.",
            "through": 0,
        }
        asked: list[str] = []

        async def condense(prompt: str, **_: Any) -> str:
            asked.append(prompt)
            return "They have a dog called Biscuit."

        monkeypatch.setattr(summary, "_condense", condense)

        folded = await summary.fold(conversation, upto=30)

        assert folded is True
        kept = conversation.metadata[summary.SUMMARY_KEY]
        assert kept == {"text": "They have a dog called Biscuit.", "through": 30}
        assert "Earlier: hello." in asked[0]  # the summary so far is folded in
        assert "Our dog is called Biscuit" in asked[0]  # with the turns that fell

    @pytest.mark.asyncio
    async def test_a_fold_already_done_is_not_paid_for_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conversation = _conversation(40)
        conversation.metadata[summary.SUMMARY_KEY] = {"text": "Done.", "through": 30}

        async def condense(prompt: str, **_: Any) -> str:
            raise AssertionError("no model call for work already done")

        monkeypatch.setattr(summary, "_condense", condense)

        assert await summary.fold(conversation, upto=30) is False


class TestWhenToFold:
    def test_only_once_enough_has_fallen_out(self) -> None:
        conversation = _conversation(4)
        conversation.metadata[summary.SUMMARY_KEY] = {"text": "x", "through": 10}

        assert summary.due(conversation, first_kept=10 + summary.FOLD_AFTER - 1) is None
        assert summary.due(conversation, first_kept=10 + summary.FOLD_AFTER) == (
            10 + summary.FOLD_AFTER
        )


class TestOffTheRequestPath:
    @pytest.mark.asyncio
    async def test_a_due_fold_is_enqueued_not_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enqueued: list[tuple[Any, ...]] = []

        class Pool:
            async def enqueue_job(self, name: str, *args: Any, **_: Any) -> None:
                enqueued.append((name, *args))

        async def pool(_queue: str) -> tuple[Pool, str]:
            return Pool(), "system"

        monkeypatch.setattr(summary, "get_queue_pool", pool)
        conversation = _conversation(4)

        await summary.enqueue_if_due(conversation, first_kept=summary.FOLD_AFTER - 1)
        await summary.enqueue_if_due(conversation, first_kept=summary.FOLD_AFTER)

        assert enqueued == [("fold_conversation_task", "c1", summary.FOLD_AFTER)]

    @pytest.mark.asyncio
    async def test_a_fold_already_queued_is_not_queued_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The summary does not move until a fold finishes, so every turn
        while one ran queued another: 10 in 15 minutes on 2026-10-07, and
        the 750-message catch-up would have been paid for per turn (#437).
        One job id per conversation; arq refuses a second while it holds
        the first, as this pool does."""
        held: dict[str, tuple[Any, ...]] = {}

        class Pool:
            async def enqueue_job(
                self, name: str, *args: Any, _job_id: str | None = None, **_: Any
            ) -> object | None:
                if _job_id is not None and _job_id in held:
                    return None
                held[_job_id or str(len(held))] = (name, *args)
                return object()

        async def pool(_queue: str) -> tuple[Pool, str]:
            return Pool(), "system"

        monkeypatch.setattr(summary, "get_queue_pool", pool)
        conversation = _conversation(4)

        for more in range(3):  # three turns while the first fold runs
            await summary.enqueue_if_due(
                conversation, first_kept=summary.FOLD_AFTER + more
            )

        assert held == {"fold:c1": ("fold_conversation_task", "c1", summary.FOLD_AFTER)}

    @pytest.mark.asyncio
    async def test_the_job_writes_only_the_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A turn may land while the fold runs: the job keeps the metadata
        as it is now and adds the summary, rather than saving the whole
        conversation it read earlier."""
        from app.services.ai.deps import ai_service

        conversation = await ai_service.conversation_manager.create_conversation(
            provider=AIProvider.OLLAMA, model="test-model", user_id="u1"
        )
        for index in range(30):
            role = MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT
            conversation.add_message(role, f"turn {index}")
        await ai_service.conversation_manager.save_conversation(conversation)

        async def condense(prompt: str, **_: Any) -> str:
            # a turn lands meanwhile, changing other metadata
            later = await ai_service.get_conversation(conversation.id)
            assert later is not None
            later.metadata["last_activity"] = "meanwhile"
            await ai_service.conversation_manager.save_conversation(later)
            return "Thirty turns."

        monkeypatch.setattr(summary, "_condense", condense)

        assert await summary.fold_conversation(conversation.id, upto=20) is True

        stored = await ai_service.get_conversation(conversation.id)
        assert stored is not None
        assert stored.metadata[summary.SUMMARY_KEY] == {
            "text": "Thirty turns.",
            "through": 20,
        }
        assert stored.metadata["last_activity"] == "meanwhile"


class TestTheCondenseCall:
    @pytest.mark.asyncio
    async def test_it_bills_the_conversations_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real call, with only the model faked: a conversation keeps
        its user in its metadata, and every fold failed reading a
        ``user_id`` it did not have before ``Conversation.user_id`` read it
        there. A real pydantic-ai agent and result, because a hand-made
        result kept ``usage()`` callable after 2.51 made it a property, and
        every fold failed in the worker for ten days while this passed
        (#429)."""
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        from app.services.ai import usage_recording
        from app.services.ai.domains.llm import providers

        agent = Agent(TestModel(custom_output_text=" Biscuit. "))
        billed: list[Any] = []

        async def record(*args: Any, **kwargs: Any) -> None:
            billed.append((args, kwargs))

        async def get_agent(*_a: Any, **_k: Any) -> Agent:
            return agent

        monkeypatch.setattr(providers, "get_agent", get_agent)
        monkeypatch.setattr(usage_recording, "record_usage", record)
        conversation = _conversation(4)
        conversation.metadata["user_id"] = "u7"

        assert await summary._condense("p", conversation=conversation) == "Biscuit."
        ((args, kwargs),) = billed
        assert args[0] == "chat:summary" and args[3] == "u7"
        assert args[2]["input_tokens"] > 0  # what the run reported
        assert kwargs["conversation_id"] == "c1"


class TestATurnDoesNotUndoTheFold:
    @pytest.mark.asyncio
    async def test_a_stale_turn_keeps_the_newer_summary(self) -> None:
        """A turn read the conversation before the fold landed; saving it
        must not put back the older summary."""
        from app.services.ai.deps import ai_service

        manager = ai_service.conversation_manager
        conversation = await manager.create_conversation(
            provider=AIProvider.OLLAMA, model="test-model", user_id="u1"
        )
        conversation.metadata[summary.SUMMARY_KEY] = {"text": "old", "through": 10}
        await manager.save_conversation(conversation)
        stale = await ai_service.get_conversation(conversation.id)
        newer = await ai_service.get_conversation(conversation.id)
        assert stale is not None and newer is not None

        newer.metadata[summary.SUMMARY_KEY] = {"text": "new", "through": 30}
        await manager.save_conversation(newer)
        stale.add_message(MessageRole.USER, "and another thing")
        await manager.save_conversation(stale)

        stored = await ai_service.get_conversation(conversation.id)
        assert stored is not None
        assert stored.metadata[summary.SUMMARY_KEY] == {"text": "new", "through": 30}


def test_a_blank_saved_fact_is_a_duplicate_of_nothing() -> None:
    """A blank is a substring of every fact: one blank entry made every
    later fact in its category "already known"."""
    from app.services.ai.domains.chat.user_memory import is_duplicate

    assert not is_duplicate("", "Their dog is called Biscuit.")
    assert is_duplicate("dog is called biscuit", "Their dog is called Biscuit.")
