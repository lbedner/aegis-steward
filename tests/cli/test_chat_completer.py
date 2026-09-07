"""Tests for chat autocomplete."""

from io import StringIO
from unittest.mock import MagicMock

from prompt_toolkit.document import Document
import pytest
from rich.console import Console

from app.cli.chat_completer import ChatCompleter
from app.cli.slash_commands import SlashCommandHandler
from app.cli.status_line import ChatSessionState


@pytest.fixture
def mock_command_handler() -> SlashCommandHandler:
    """SlashCommandHandler with mocks and pre-populated cache."""
    mock_ai_service = MagicMock()
    mock_ai_service.config.provider.value = "openai"
    mock_ai_service.config.model = "gpt-4o"
    mock_ai_service.config.temperature = 0.7
    mock_ai_service.config.max_tokens = 4096

    output = StringIO()
    mock_console = Console(file=output, force_terminal=False, width=80)

    session_state = ChatSessionState(provider="openai", model="gpt-4o")
    # See note in ``test_slash_commands.py``: RAG params moved out of
    # ``SlashCommandHandler.__init__`` — tests now mutate attributes
    # directly for RAG-state scenarios.
    handler = SlashCommandHandler(
        ai_service=mock_ai_service,
        session_state=session_state,
        console=mock_console,
    )
    handler._collection_cache = ["my-code", "docs", "tests"]
    handler._model_cache = ["gpt-4o", "gpt-4-turbo", "claude-sonnet-4"]
    return handler


@pytest.fixture
def completer(mock_command_handler: SlashCommandHandler) -> ChatCompleter:
    """ChatCompleter with mocked command handler."""
    return ChatCompleter(mock_command_handler)


def get_completions_list(completer: ChatCompleter, text: str) -> list[str]:
    """Helper to get completion texts from a document."""
    doc = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, None)]


class TestChatCompleterBasics:
    """Test basic completion behavior."""

    def test_no_completions_for_non_slash(self, completer: ChatCompleter) -> None:
        """Non-slash text yields no completions."""
        completions = get_completions_list(completer, "hello")
        assert completions == []

    def test_no_completions_for_empty(self, completer: ChatCompleter) -> None:
        """Empty text yields no completions."""
        completions = get_completions_list(completer, "")
        assert completions == []

    def test_completions_for_slash_prefix(self, completer: ChatCompleter) -> None:
        """'/' yields command completions.

        ``/rag`` was removed when RAG state moved off the slash-command
        handler; tab completion no longer offers it.
        """
        completions = get_completions_list(completer, "/")
        assert len(completions) > 0
        assert "help" in completions
        assert "model" in completions

    def test_completions_filtered_by_typed_chars(
        self, completer: ChatCompleter
    ) -> None:
        """'/he' only yields commands starting with 'he'."""
        completions = get_completions_list(completer, "/he")
        assert "help" in completions
        assert "model" not in completions
        assert "rag" not in completions


class TestModelCompletions:
    """Test /model tab completion."""

    def test_model_space_triggers_model_completions(
        self, completer: ChatCompleter
    ) -> None:
        """'/model ' yields model names from cache."""
        completions = get_completions_list(completer, "/model ")
        assert "gpt-4o" in completions
        assert "gpt-4-turbo" in completions
        assert "claude-sonnet-4" in completions

    def test_model_partial_filters(self, completer: ChatCompleter) -> None:
        """'/model gpt' filters to models starting with 'gpt'."""
        completions = get_completions_list(completer, "/model gpt")
        assert "gpt-4o" in completions
        assert "gpt-4-turbo" in completions
        assert "claude-sonnet-4" not in completions

    def test_model_case_insensitive(self, completer: ChatCompleter) -> None:
        """'/model GPT' matches 'gpt-4o'."""
        completions = get_completions_list(completer, "/model GPT")
        assert "gpt-4o" in completions
        assert "gpt-4-turbo" in completions


class TestRagCompletions:
    """Test /rag tab completion."""

    def test_rag_space_includes_off_option(self, completer: ChatCompleter) -> None:
        """'/rag ' yields 'off' as an option."""
        completions = get_completions_list(completer, "/rag ")
        assert "off" in completions

    def test_rag_space_includes_collections(self, completer: ChatCompleter) -> None:
        """'/rag ' yields collection names from cache."""
        completions = get_completions_list(completer, "/rag ")
        assert "my-code" in completions
        assert "docs" in completions
        assert "tests" in completions

    def test_rag_partial_filters_collections(self, completer: ChatCompleter) -> None:
        """'/rag my' filters to collections starting with 'my'."""
        completions = get_completions_list(completer, "/rag my")
        assert "my-code" in completions
        assert "docs" not in completions
        assert "tests" not in completions

    def test_rag_off_partial(self, completer: ChatCompleter) -> None:
        """'/rag o' matches 'off'."""
        completions = get_completions_list(completer, "/rag o")
        assert "off" in completions

    def test_off_option_has_description(self, completer: ChatCompleter) -> None:
        """'off' completion has display_meta='Disable RAG'."""
        doc = Document("/rag ", cursor_position=5)
        completions = list(completer.get_completions(doc, None))

        off_completion = next((c for c in completions if c.text == "off"), None)
        assert off_completion is not None
        # display_meta may be a FormattedText object or string
        meta_str = str(off_completion.display_meta)
        assert "Disable RAG" in meta_str


class TestCommandDescriptions:
    """Test command description retrieval."""

    def test_get_command_description_for_known_command(
        self, completer: ChatCompleter
    ) -> None:
        """Known commands return their description."""
        description = completer._get_command_description("help")
        assert "command" in description.lower()

    def test_get_command_description_for_unknown(
        self, completer: ChatCompleter
    ) -> None:
        """Unknown commands return empty string."""
        description = completer._get_command_description("nonexistent")
        assert description == ""

    def test_completions_include_descriptions(self, completer: ChatCompleter) -> None:
        """Command completions include descriptions in display_meta."""
        doc = Document("/", cursor_position=1)
        completions = list(completer.get_completions(doc, None))

        help_completion = next((c for c in completions if c.text == "help"), None)
        assert help_completion is not None
        assert help_completion.display_meta is not None
        assert len(help_completion.display_meta) > 0
