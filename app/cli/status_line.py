"""Status line UI component for Illiana CLI chat.

Provides a persistent status bar at the bottom of the terminal during
interactive chat sessions, showing token count, model info, RAG status,
and version.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import shutil

from prompt_toolkit.formatted_text import HTML

from app.cli import theme
from app.i18n import t


@dataclass
class ChatSessionState:
    """Mutable state for the chat session status line."""

    provider: str = ""
    model: str = ""
    rag_enabled: bool = False
    rag_collection: str | None = None
    show_sources: bool = False
    cumulative_tokens: int = 0
    cumulative_cost: float = 0.0
    version: str = ""

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Add tokens from a chat exchange."""
        self.cumulative_tokens += input_tokens + output_tokens

    def add_cost(self, cost: float) -> None:
        """Add cost from a chat exchange."""
        self.cumulative_cost += cost

    def update_provider(self, provider: str, model: str) -> None:
        """Update provider and model after a /model command."""
        self.provider = provider
        self.model = model

    def toggle_rag(self, enabled: bool, collection: str | None = None) -> None:
        """Toggle RAG mode on/off and optionally set collection."""
        self.rag_enabled = enabled
        if collection is not None:
            self.rag_collection = collection

    def toggle_sources(self, enabled: bool) -> None:
        """Toggle source reference display on/off."""
        self.show_sources = enabled

    def format_status_line(self) -> HTML:
        """Format the status line with HTML markup for prompt_toolkit."""
        term_width = shutil.get_terminal_size().columns

        # Build components
        if self.provider:
            model_str = f"{self.provider}/{self.model}"
        else:
            model_str = t("status.not_connected")

        if self.rag_enabled and self.rag_collection:
            rag_str = f"{t('status.rag_on')} ({self.rag_collection})"
        elif self.rag_enabled:
            rag_str = t("status.rag_on")
        else:
            rag_str = t("status.rag_off")

        sources_str = t("status.src_on") if self.show_sources else t("status.src_off")

        tokens_str = f"{self.cumulative_tokens:,} {t('status.tokens')}"
        cost_str = f"${self.cumulative_cost:.4f}"
        version_str = f"v{self.version}"

        # Separator (middle dot)
        sep = " <style bg='default' fg='ansibrightblack'>\u00b7</style> "

        # Build parts: values neutral, enabled states teal, off/chrome dim.
        parts = [
            model_str,
            f"<style fg='{theme.ACCENT}'>{rag_str}</style>"
            if self.rag_enabled
            else f"<style fg='ansibrightblack'>{rag_str}</style>",
            f"<style fg='{theme.ACCENT}'>{sources_str}</style>"
            if self.show_sources
            else f"<style fg='ansibrightblack'>{sources_str}</style>",
            tokens_str,
            cost_str,
            f"<style fg='ansibrightblack'>{version_str}</style>",
        ]

        # For narrow terminals, abbreviate
        if term_width < 60:
            # Abbreviated format: model | tokens | cost
            parts = [
                self.model or t("status.na"),
                tokens_str,
                cost_str,
            ]
        elif term_width < 80:
            # Skip version
            parts = parts[:-1]

        status_content = sep.join(parts)

        # Add divider line above status
        divider = "─" * term_width
        full_line = f"<style fg='ansibrightblack'>{divider}</style>\n{status_content}\n"
        return HTML(full_line)


def create_toolbar_callback(state: ChatSessionState) -> Callable[[], HTML]:
    """Create a bottom_toolbar callback that reads from state.

    Args:
        state: The ChatSessionState instance to read from.

    Returns:
        A callable that returns the formatted status line.
    """

    def get_toolbar() -> HTML:
        return state.format_status_line()

    return get_toolbar
