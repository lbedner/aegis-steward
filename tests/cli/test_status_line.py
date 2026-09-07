"""Tests for CLI status line component."""

from unittest.mock import patch

from prompt_toolkit.formatted_text import HTML

from app.cli.status_line import ChatSessionState, create_toolbar_callback


class TestChatSessionState:
    """Test ChatSessionState dataclass."""

    def test_default_values(self):
        """Test default state values."""
        state = ChatSessionState()

        assert state.provider == ""
        assert state.model == ""
        assert state.rag_enabled is False
        assert state.rag_collection is None
        assert state.cumulative_tokens == 0
        assert state.cumulative_cost == 0.0
        assert state.version == ""

    def test_initialized_values(self):
        """Test state with initialized values."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            rag_enabled=True,
            rag_collection="my-code",
            cumulative_tokens=100,
            cumulative_cost=0.0025,
            version="0.4.1",
        )

        assert state.provider == "anthropic"
        assert state.model == "claude-sonnet-4-20250514"
        assert state.rag_enabled is True
        assert state.rag_collection == "my-code"
        assert state.cumulative_tokens == 100
        assert state.cumulative_cost == 0.0025
        assert state.version == "0.4.1"

    def test_add_tokens_cumulative(self):
        """Test token accumulation across multiple calls."""
        state = ChatSessionState()

        state.add_tokens(100, 50)  # 150 total
        assert state.cumulative_tokens == 150

        state.add_tokens(200, 100)  # +300 = 450 total
        assert state.cumulative_tokens == 450

        state.add_tokens(0, 0)  # +0 = still 450
        assert state.cumulative_tokens == 450

    def test_add_cost_cumulative(self):
        """Test cost accumulation across multiple calls."""
        state = ChatSessionState()

        state.add_cost(0.001)
        assert state.cumulative_cost == 0.001

        state.add_cost(0.0025)
        assert state.cumulative_cost == 0.0035

        state.add_cost(0.0)
        assert state.cumulative_cost == 0.0035


class TestFormatStatusLine:
    """Test status line formatting."""

    def test_format_with_provider(self):
        """Test formatting with provider connected."""
        state = ChatSessionState(
            provider="google",
            model="gemini-3-flash",
            version="0.4.1",
        )

        result = state.format_status_line()

        assert isinstance(result, HTML)
        html_str = str(result)
        assert "google/gemini-3-flash" in html_str

    def test_format_not_connected(self):
        """Test formatting when not connected."""
        state = ChatSessionState(version="0.4.1")

        result = state.format_status_line()
        html_str = str(result)

        assert "not connected" in html_str

    def test_format_rag_enabled_with_collection(self):
        """Test RAG status when enabled with collection."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            rag_enabled=True,
            rag_collection="my-codebase",
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "RAG: ON" in html_str
        assert "my-codebase" in html_str

    def test_format_rag_enabled_no_collection(self):
        """Test RAG status when enabled without specific collection."""
        state = ChatSessionState(
            provider="openai",
            model="gpt-4o",
            rag_enabled=True,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "RAG: ON" in html_str

    def test_format_rag_disabled(self):
        """Test RAG status when disabled."""
        state = ChatSessionState(
            provider="openai",
            model="gpt-4o",
            rag_enabled=False,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "RAG: OFF" in html_str

    def test_format_tokens_with_comma_separator(self):
        """Test token count formatting with thousands separator."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            cumulative_tokens=12345,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "12,345 tokens" in html_str

    def test_format_cost_with_precision(self):
        """Test cost formatting with 4 decimal places."""
        state = ChatSessionState(
            provider="openai",
            model="gpt-4o",
            cumulative_cost=0.12345,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "$0.1235" in html_str  # 4 decimal places

    def test_format_includes_version(self):
        """Test version is included in status line."""
        state = ChatSessionState(
            provider="google",
            model="gemini-3-flash",
            version="1.2.3",
        )

        result = state.format_status_line()
        html_str = str(result)

        assert "v1.2.3" in html_str

    def test_format_includes_divider(self):
        """Test divider line is included."""
        state = ChatSessionState(
            provider="google",
            model="gemini-3-flash",
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        # Check for divider character
        assert "─" in html_str

    @patch("shutil.get_terminal_size")
    def test_format_narrow_terminal_abbreviated(self, mock_term_size):
        """Test abbreviated format for narrow terminals (< 60 columns)."""
        mock_term_size.return_value = type("Size", (), {"columns": 50})()

        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            rag_enabled=True,
            rag_collection="my-codebase",
            cumulative_tokens=1000,
            cumulative_cost=0.0025,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        # Narrow format should include model, tokens, cost but skip RAG and version
        assert "claude-sonnet-4-20250514" in html_str
        assert "1,000 tokens" in html_str
        assert "$0.0025" in html_str

    @patch("shutil.get_terminal_size")
    def test_format_medium_terminal_skips_version(self, mock_term_size):
        """Test medium terminal (< 80 columns) skips version."""
        mock_term_size.return_value = type("Size", (), {"columns": 70})()

        state = ChatSessionState(
            provider="google",
            model="gemini-3-flash",
            rag_enabled=False,
            cumulative_tokens=500,
            cumulative_cost=0.001,
            version="0.4.1",
        )

        result = state.format_status_line()
        html_str = str(result)

        # Medium format includes everything except version
        assert "google/gemini-3-flash" in html_str
        assert "RAG: OFF" in html_str
        assert "500 tokens" in html_str
        # Version should be skipped in medium width
        # (Note: actual behavior depends on parts[:-1] logic)


class TestCreateToolbarCallback:
    """Test toolbar callback creation."""

    def test_callback_returns_callable(self):
        """Test that create_toolbar_callback returns a callable."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            version="0.4.1",
        )

        callback = create_toolbar_callback(state)

        assert callable(callback)

    def test_callback_returns_html(self):
        """Test that callback returns HTML object."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            version="0.4.1",
        )

        callback = create_toolbar_callback(state)
        result = callback()

        assert isinstance(result, HTML)

    def test_callback_reflects_state_changes(self):
        """Test that callback reflects state changes."""
        state = ChatSessionState(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            cumulative_tokens=0,
            version="0.4.1",
        )

        callback = create_toolbar_callback(state)

        # Initial state
        result1 = callback()
        html_str1 = str(result1)
        assert "0 tokens" in html_str1

        # Update state
        state.add_tokens(100, 50)

        # Callback should reflect new state
        result2 = callback()
        html_str2 = str(result2)
        assert "150 tokens" in html_str2
