"""
Shared rendering utilities for AI chat responses.

Provides consistent, beautiful output formatting across streaming
and non-streaming modes using marko markdown parser with terminal rendering.
"""

from marko import Markdown
from marko.ext.gfm import GFM
from rich.console import Console

from app.cli import theme
from app.cli.marko_terminal_renderer import TerminalRenderer
from app.i18n import t


class StreamingMarkdownRenderer:
    """
    Line-based streaming markdown renderer using marko.

    Processes markdown content as it streams in, using marko to parse complete
    blocks and render them with beautiful ANSI styling for terminal output.

    Uses unified line-buffering for all content, making it compatible with both
    PydanticAI (word-aligned tokens) and LangChain (subword/character-level tokens).
    Content is accumulated until newlines, then rendered as complete lines.
    """

    def __init__(self, console: Console):
        """
        Initialize streaming renderer.

        Args:
            console: Rich console instance for output management
        """
        self.console = console
        self.buffer = ""
        self.in_code_block = False
        self.code_buffer: list[str] = []
        self.code_lang = ""
        self.markdown = Markdown(extensions=[GFM], renderer=TerminalRenderer)

    def add_delta(self, delta: str) -> None:
        """
        Process streaming delta using unified line-buffering.

        Accumulates content until newlines, then renders complete lines.
        This simple approach works correctly with any token boundary pattern,
        whether word-aligned (PydanticAI) or character-level (LangChain).

        Args:
            delta: New text content to process
        """
        self.buffer += delta
        self._process_complete_lines()

    def _process_complete_lines(self) -> None:
        """Process any complete lines in the buffer."""
        lines = self.buffer.split("\n")

        # Keep the last (potentially incomplete) line in buffer
        if len(lines) > 1:
            complete_lines = lines[:-1]
            self.buffer = lines[-1]

            # Process each complete line
            for line in complete_lines:
                self._render_line(line)

    def _render_line(self, line: str) -> None:
        """
        Render a complete line with markdown formatting using marko.

        Args:
            line: Complete line to render
        """
        # Handle code blocks (accumulate until closing)
        if self.in_code_block:
            if line.strip() == "```":
                # End of code block - render complete block
                self._render_code_block()
                self.in_code_block = False
                self.code_buffer = []
                self.code_lang = ""
            else:
                # Inside code block, accumulate
                self.code_buffer.append(line)
            return

        if line.strip().startswith("```"):
            # Start of code block
            self.code_lang = line.strip()[3:].strip() or "text"
            self.in_code_block = True
            self.code_buffer = []
            return

        # For other content, parse line as markdown and render
        if line.strip():
            # Parse and render single line with marko
            rendered = self.markdown(line)
            # Write to console's file to support both terminal and testing
            self.console.file.write(rendered)
            self.console.file.flush()
        else:
            # Empty line
            self.console.print()

    def _render_code_block(self) -> None:
        """Render accumulated code block using marko."""
        code_content = "\n".join(self.code_buffer)

        # Create markdown code block
        markdown_code = f"```{self.code_lang}\n{code_content}\n```"

        # Parse and render with marko
        rendered = self.markdown(markdown_code)
        # Write to console's file to support both terminal and testing
        self.console.file.write(rendered)
        self.console.file.flush()

    def finalize(self) -> None:
        """Finalize any remaining content in buffer."""
        if self.buffer.strip():
            # Process any remaining incomplete line
            rendered = self.markdown(self.buffer.strip())
            # Write to console's file to support both terminal and testing
            self.console.file.write(rendered)
            self.console.file.flush()

        # Handle unclosed code block
        if self.in_code_block and self.code_buffer:
            self._render_code_block()


def render_ai_header(console: Console, inline: bool = True) -> None:
    """
    Render the Illiana AI response header.

    Args:
        console: Rich console instance
        inline: If True, use inline style, else use separate line style

    Examples:
        >>> render_ai_header(console, inline=True)  # Outputs: "Illiana: "
        >>> render_ai_header(console, inline=False) # Outputs: "Illiana:"
    """
    if inline:
        console.print(t("ai.header_inline"), style=theme.ACCENT, end="")
    else:
        console.print(t("ai.header"), style=f"{theme.ACCENT} bold")


def render_markdown_response(console: Console, content: str) -> None:
    """
    Render markdown content with beautiful terminal styling using marko.

    Parses complete markdown content and renders with ANSI-styled output
    for beautiful terminal display.

    Args:
        console: Rich console instance
        content: Markdown content to render

    Examples:
        >>> render_markdown_response(console, "# Hello World")
        >>> render_markdown_response(console, "```python\\nprint('hi')\\n```")
    """
    if not content or not content.strip():
        console.print(t("ai.no_response_content"), style="dim italic")
        return

    # Clean up excessive whitespace from AI responses
    cleaned_content = _clean_ai_content(content)

    # Parse and render with marko (GFM for table support)
    markdown = Markdown(extensions=[GFM], renderer=TerminalRenderer)
    rendered = markdown(cleaned_content)

    # Write to console's file to support both terminal and testing
    console.file.write(rendered)
    console.file.flush()


def _clean_ai_content(content: str) -> str:
    """
    Clean up excessive whitespace and formatting issues from AI responses.

    Some AI providers send responses with excessive blank lines or spacing
    that hurts readability. This function normalizes the content.

    Args:
        content: Raw AI response content

    Returns:
        Cleaned content with normalized spacing
    """

    # Split into lines for processing
    lines = content.split("\n")
    cleaned_lines = []
    consecutive_empty = 0

    for line in lines:
        is_empty = not line.strip()

        if is_empty:
            consecutive_empty += 1
            # Allow maximum 1 consecutive empty line
            if consecutive_empty <= 1:
                cleaned_lines.append("")
        else:
            consecutive_empty = 0
            # Clean up the line (remove trailing whitespace)
            cleaned_lines.append(line.rstrip())

    # Remove leading and trailing empty lines
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    # Join back together
    return "\n".join(cleaned_lines)


def render_conversation_metadata(
    console: Console,
    conversation_id: str,
    message_count: int | None = None,
    response_time: float | None = None,
) -> None:
    """
    Render conversation metadata consistently.

    Args:
        console: Rich console instance
        conversation_id: The conversation identifier
        message_count: Number of messages in conversation
        response_time: Response time in milliseconds

    Examples:
        >>> render_conversation_metadata(console, "conv-123")
        >>> render_conversation_metadata(
        ...     console, "conv-123", message_count=5, response_time=150.5
        ... )
    """
    console.print()  # Blank line for spacing
    console.print(f"{t('ai.conversation_label')} {conversation_id}", style="dim")
    if message_count:
        console.print(f"{t('ai.messages_label')} {message_count}", style="dim")
    if response_time:
        console.print(
            f"{t('ai.response_time_label')} {response_time:.1f}ms", style="dim"
        )


def render_error_message(
    console: Console, error: str, suggestion: str | None = None
) -> None:
    """
    Render an error message consistently.

    Args:
        console: Rich console instance
        error: The error message to display
        suggestion: Optional suggestion for fixing the error

    Examples:
        >>> render_error_message(console, "Connection failed")
        >>> render_error_message(console, "API key invalid", "Check your .env file")
    """
    console.print(f"{t('shared.error')} {error}", style=theme.ERROR)
    if suggestion:
        console.print(f"{t('ai.tip_label')} {suggestion}", style="dim")


def render_thinking_spinner(console: Console) -> tuple:
    """
    Create a thinking spinner for AI processing.

    Returns:
        Tuple of (Spinner, Live) objects to control the spinner

    Examples:
        >>> spinner, live = render_thinking_spinner(console)
        >>> live.start()
        >>> # ... do work ...
        >>> live.stop()
    """
    from rich.live import Live
    from rich.spinner import Spinner

    spinner = Spinner("dots", text=t("ai.thinking"), style="dim")
    live = Live(spinner, console=console, refresh_per_second=12)
    return spinner, live
