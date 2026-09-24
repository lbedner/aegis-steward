"""The next turn sees what the last turn did, not only what it said.

History replayed as final text alone, and the code-mode sandbox resets
every turn. So on "try again" the assistant searched for two transaction
ids she had proposed against one turn earlier (#242, 2026-09-23). The
steps are already saved on the message as ``tool_trace``; this replays
them, compactly, inside the history budget.
"""

from app.services.ai.models import AIProvider, Conversation, MessageRole
from app.services.ai.service.prompt import PromptMixin
from app.services.ai.service.trace import steps_line

# The real turn, as tool_trace stored it.
DARKSIDE_TRACE = [
    {"tool": "pending", "args": '{"about": "With Vanessa", "draw": false}'},
    {
        "tool": "run_code",
        "code": 'ts = await transactions(payee="DARKSIDE RECORDS", limit=50)\nprint(ts)',
        "result": "[{'id': 54820, 'date': '2026-08-04', 'amount_cents': -2160}, "
        "{'id': 54939, 'date': '2026-08-21', 'amount_cents': -1371}]",
        "nested": [
            {
                "tool": "transactions",
                "args": '{"payee": "DARKSIDE RECORDS", "limit": 50}',
            }
        ],
    },
    {
        "tool": "propose_many",
        "args": '{"change_type": "transaction.untag", "payloads": '
        '[{"transaction_id": 54820, "tag": "With Vanessa"}, '
        '{"transaction_id": 54939, "tag": "With Vanessa"}]}',
        "result": '{"batch_id": "b-1", "count": 2}',
        "component": {"kind": "pending_change_batch", "batch_id": "b-1"},
    },
]


def _build(conversation: Conversation, budget: int = 24_000) -> str:
    return PromptMixin._build_conversation_context(  # type: ignore[arg-type]
        None, conversation, history_budget=budget
    )


def _thread(trace: list[dict]) -> Conversation:
    conversation = Conversation(id="c1", provider=AIProvider.OLLAMA, model="test-model")
    conversation.add_message(MessageRole.USER, "swap With Vanessa for Vanessa")
    conversation.add_message(
        MessageRole.ASSISTANT,
        "I proposed replacing it on both Dark Side Records transactions.",
        metadata={"tool_trace": trace},
    )
    conversation.add_message(MessageRole.USER, "try again")
    return conversation


class TestTheStepsLine:
    def test_it_carries_the_ids_and_the_payloads(self) -> None:
        line = steps_line(DARKSIDE_TRACE)
        assert "54820" in line and "54939" in line
        assert "propose_many" in line and "transaction.untag" in line

    def test_a_script_is_named_by_what_it_called(self) -> None:
        line = steps_line(DARKSIDE_TRACE)
        assert "transactions" in line
        # The script's source is not replayed whole.
        assert "print(ts)" not in line

    def test_it_is_capped(self) -> None:
        huge = [{"tool": "run_code", "code": "x = 1", "result": "9" * 50_000}] * 20
        assert len(steps_line(huge)) <= 1_200

    def test_long_scripts_never_squeeze_out_the_proposal(self) -> None:
        """Live: three lookups ahead of the propose_many ran the line to
        its cap and cut the payload at "replaces": "With Vanes" - the one
        step worth replaying (2026-09-23)."""
        wordy = {
            "tool": "run_code",
            "code": "a = await transactions()",
            "result": "{'id': 1} " * 400,
            "nested": [{"tool": "transactions", "args": "{" + '"x": 1, ' * 60 + "}"}] * 3,
        }
        propose = {
            "tool": "propose_many",
            "args": '{"change_type": "transaction.tag", "payloads": [{"transaction_id": 54820, '
            '"tag": "Vanessa", "replaces": "With Vanessa"}]}',
        }
        line = steps_line([wordy, wordy, wordy, propose])
        assert '"replaces": "With Vanessa"}]}' in line

    def test_no_trace_no_line(self) -> None:
        assert steps_line([]) == ""


class TestTheHistoryCarriesIt:
    def test_the_last_turns_steps_ride_ahead_of_what_she_said(self) -> None:
        context = _build(_thread(DARKSIDE_TRACE))
        steps = context.index("Assistant steps:")
        said = context.index("Assistant: I proposed replacing it")
        assert steps < said
        assert "54820" in context

    def test_a_tight_budget_keeps_the_words_and_drops_the_steps(self) -> None:
        """What she SAID outranks what she did: the steps are the first
        thing to go, never the conversation."""
        conversation = _thread(DARKSIDE_TRACE)
        words = len("User: swap With Vanessa for Vanessa") + len(
            "Assistant: I proposed replacing it on both Dark Side Records transactions."
        )
        context = _build(conversation, budget=words + 10)
        assert "Assistant: I proposed replacing it" in context
        assert "Assistant steps:" not in context
