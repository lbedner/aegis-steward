"""An agent can be asked what it can still see.

History is budgeted by SIZE and the oldest turns drop silently, which
makes two very different states sound identical: an agent that read the
page you pasted and found no match, and one that no longer has the page
at all. On 2026-09-12 that cost an evening - four Amazon pages of 9k to
16k characters each filled a 60k-char budget, and the agent went on
answering "no matching charge found" for orders it could no longer see,
until the user pasted its own earlier answer back at it.
"""

import pytest

from app.services.ai.domains.chat.self_context import (
    Block,
    begin_turn_context,
    context,
    record_turn_context,
)
from app.services.ai.models import (
    AIProvider,
    Conversation,
    MessageRole,
)
from app.services.ai.service.prompt import PromptMixin


def _conversation(*sizes: int) -> Conversation:
    """A thread whose messages are the given lengths, oldest first."""
    conversation = Conversation(id="c1", provider=AIProvider.OLLAMA, model="test-model")
    for index, size in enumerate(sizes):
        role = MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT
        conversation.add_message(role, "x" * size)
    conversation.add_message(MessageRole.USER, "and now?")
    return conversation


def _build(conversation: Conversation, budget: int) -> str:
    """The history builder, which reads nothing off ``self``."""
    return PromptMixin._build_conversation_context(  # type: ignore[arg-type]
        None, conversation, history_budget=budget
    )


class TestWhatFellOutIsCounted:
    def test_messages_over_the_budget_are_counted_not_just_stopped_at(self) -> None:
        """The loop used to ``break`` at the first message that did not
        fit, so the number the agent most needed - how much it had lost -
        was never known to anything."""
        begin_turn_context()

        # Oldest first: the 5k message is the one that cannot fit.
        _build(_conversation(5_000, 100, 100, 100), budget=400)

        stamp = begin_turn_context.__globals__["_turn"].get()
        assert stamp.messages_kept == 3
        assert stamp.messages_dropped == 1

    def test_a_thread_inside_the_budget_drops_nothing(self) -> None:
        begin_turn_context()

        _build(_conversation(100, 100), budget=10_000)

        stamp = begin_turn_context.__globals__["_turn"].get()
        assert stamp.messages_kept == 2
        assert stamp.messages_dropped == 0

    def test_the_kept_messages_are_the_newest(self) -> None:
        """Oldest drop first: the budget keeps what was said most
        recently, which is the half a follow-up question is about."""
        conversation = _conversation(50, 50)
        conversation.messages[0].content = "OLDEST"
        conversation.messages[1].content = "NEWEST"

        built = _build(conversation, budget=20)

        assert "NEWEST" in built
        assert "OLDEST" not in built


class TestTheReport:
    @pytest.mark.asyncio
    async def test_it_says_the_dropped_count_out_loud(self) -> None:
        begin_turn_context()
        record_turn_context(
            model="gpt-5.6-luna",
            provider="openai",
            context_window=1_050_000,
            blocks=[Block("persona", 13_305)],
            history_chars=59_400,
            history_budget=60_000,
            messages_kept=14,
            messages_dropped=37,
        )

        report = await context()

        assert "1,050,000-token window" in report
        assert "37 older message(s) did NOT fit" in report
        assert "persona" in report

    @pytest.mark.asyncio
    async def test_a_whole_conversation_says_so(self) -> None:
        begin_turn_context()
        record_turn_context(messages_kept=4, messages_dropped=0)

        assert "Nothing was dropped" in await context()

    @pytest.mark.asyncio
    async def test_it_reports_sizes_never_the_text(self) -> None:
        """A context report that quoted the transcript would double the
        thing it is reporting on."""
        begin_turn_context()
        record_turn_context(blocks=[Block("persona", 13_305)])

        assert "x" * 50 not in await context()


class TestTheAgentCanActuallyCallIt:
    def test_the_chat_agent_is_granted_it(self) -> None:
        from app.services.finance.domains.detection.analyst.seeds import (
            FINANCE_CHAT_TOOL_NAMES,
        )

        assert "context" in FINANCE_CHAT_TOOL_NAMES

    def test_the_name_resolves_to_a_callable(self) -> None:
        """A granted name with no registered callable is skipped with a
        warning, so the grant alone proves nothing."""
        from app.services.ai.domains.chat.tools import resolve_tools

        assert len(resolve_tools(["context"])) == 1


class TestReadingsBelongToTheUser:
    """Stored per conversation, twelve pages of extracted Amazon orders
    were invisible the moment a new thread was opened, and recovering
    them meant reading them back out of the database by hand."""

    @pytest.mark.asyncio
    async def test_a_reading_recorded_in_one_turn_is_there_for_the_next(
        self,
    ) -> None:
        from app.services.ai.domains.chat.readings import (
            merge_staged_readings,
            user_readings,
        )

        reading = {"kind": "order", "title": "Amazon July 6", "items": [{"label": "x"}]}
        await merge_staged_readings("reader-1", [reading])

        assert await user_readings("reader-1") == [reading]

    @pytest.mark.asyncio
    async def test_one_user_never_sees_another_user_reading(self) -> None:
        from app.services.ai.domains.chat.readings import (
            merge_staged_readings,
            user_readings,
        )

        await merge_staged_readings(
            "reader-2",
            [{"kind": "order", "title": "theirs", "items": [{"label": "x"}]}],
        )

        assert await user_readings("reader-3") == []

    @pytest.mark.asyncio
    async def test_the_oldest_drop_once_the_cap_is_reached(self) -> None:
        """Readings are re-injected every turn, so the list is bounded."""
        from app.services.ai.domains.chat.readings import (
            _MAX_READINGS_PER_USER,
            merge_staged_readings,
            user_readings,
        )

        pages = [
            {"kind": "order", "title": f"page {i}", "items": [{"label": "x"}]}
            for i in range(_MAX_READINGS_PER_USER + 3)
        ]
        await merge_staged_readings("reader-4", pages)

        kept = await user_readings("reader-4")
        assert len(kept) == _MAX_READINGS_PER_USER
        assert kept[0]["title"] == "page 3"

    @pytest.mark.asyncio
    async def test_an_in_flight_thread_keeps_what_it_already_read(self) -> None:
        """Readings recorded before the store moved ride the first write
        for a user who has none, so the cutover costs nobody their pages."""
        from app.services.ai.domains.chat.readings import (
            merge_staged_readings,
            user_readings,
        )

        old = {"kind": "order", "title": "recorded earlier", "items": [{"label": "x"}]}
        new = {"kind": "order", "title": "recorded now", "items": [{"label": "y"}]}

        await merge_staged_readings("reader-5", [new], legacy=[old])

        assert [r["title"] for r in await user_readings("reader-5")] == [
            "recorded earlier",
            "recorded now",
        ]

    @pytest.mark.asyncio
    async def test_legacy_is_ignored_once_the_user_has_a_store(self) -> None:
        """One home: the fallback exists for the cutover, not forever."""
        from app.services.ai.domains.chat.readings import (
            merge_staged_readings,
            user_readings,
        )

        first = {"kind": "order", "title": "first", "items": [{"label": "x"}]}
        await merge_staged_readings("reader-6", [first])

        await merge_staged_readings(
            "reader-6",
            [{"kind": "order", "title": "second", "items": [{"label": "y"}]}],
            legacy=[{"kind": "order", "title": "stale", "items": [{"label": "z"}]}],
        )

        assert [r["title"] for r in await user_readings("reader-6")] == [
            "first",
            "second",
        ]


class TestItSurvivesWithoutATurn:
    @pytest.mark.asyncio
    async def test_recording_outside_a_turn_is_a_no_op(self) -> None:
        """Prompt assembly is callable from tests and one-off scripts."""
        import app.services.ai.domains.chat.self_context as module

        token = module._turn.set(None)
        try:
            record_turn_context(model="whatever")
            assert await context() == "No turn context recorded."
        finally:
            module._turn.reset(token)
