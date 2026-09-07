"""
Slash command system for Illiana interactive chat.

Provides in-session commands for managing the chat experience without
leaving the interactive loop. Commands start with / and provide quick
access to provider switching, conversation management, and configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.cli import theme
from app.i18n import t

if TYPE_CHECKING:
    from app.cli.status_line import ChatSessionState
    from app.services.ai.service import AIService


class SlashCommandName(StrEnum):
    """Slash command identifiers."""

    HELP = "help"
    CLEAR = "clear"
    NEW = "new"
    MODEL = "model"
    STATUS = "status"

    EXIT = "exit"


@dataclass
class CommandResult:
    """Result of executing a slash command."""

    success: bool
    message: str | None = None
    should_exit: bool = False
    new_conversation_id: str | None = None
    # For commands that need to update session state
    update_provider: str | None = None
    update_model: str | None = None
    update_rag: bool | None = None
    update_rag_collection: str | None = None
    update_show_sources: bool | None = None


@dataclass
class SlashCommand:
    """Definition of a slash command."""

    name: str
    description: str
    usage: str
    aliases: list[str] = field(default_factory=list)


class SlashCommandHandler:
    """
    Handler for slash commands in interactive chat.

    Provides command parsing, execution, and help display.
    Reuses existing service methods rather than duplicating logic.
    """

    def __init__(
        self,
        ai_service: AIService,
        session_state: ChatSessionState,
        console: Console,
        current_conversation_id: str | None = None,
    ) -> None:
        """Initialize slash command handler."""
        self.ai_service = ai_service
        self.session_state = session_state
        self.console = console
        self.current_conversation_id = current_conversation_id

        # Define available commands (minimal set, no aliases)
        self.commands: dict[SlashCommandName, SlashCommand] = {
            SlashCommandName.HELP: SlashCommand(
                name=SlashCommandName.HELP,
                description=t("slash.help_desc"),
                usage="/help",
            ),
            SlashCommandName.CLEAR: SlashCommand(
                name=SlashCommandName.CLEAR,
                description=t("slash.clear_desc"),
                usage="/clear",
            ),
            SlashCommandName.NEW: SlashCommand(
                name=SlashCommandName.NEW,
                description=t("slash.new_desc"),
                usage="/new",
            ),
            SlashCommandName.MODEL: SlashCommand(
                name=SlashCommandName.MODEL,
                description=t("slash.model_desc"),
                usage="/model [name]",
            ),
            SlashCommandName.STATUS: SlashCommand(
                name=SlashCommandName.STATUS,
                description=t("slash.status_desc"),
                usage="/status",
            ),
            SlashCommandName.EXIT: SlashCommand(
                name=SlashCommandName.EXIT,
                description=t("slash.exit_desc"),
                usage="/exit",
            ),
        }

        # Build alias lookup
        self._alias_map: dict[str, str] = {}
        for cmd_name, cmd in self.commands.items():
            for alias in cmd.aliases:
                self._alias_map[alias] = cmd_name

        # Cache for model completions (populated at startup)
        self._model_cache: list[str] = []
        # Cache for collection completions (populated at startup)
        self._collection_cache: list[str] = []

    def get_command_names(self) -> list[str]:
        """Get all command names and aliases for autocomplete."""
        names = list(self.commands.keys())
        names.extend(self._alias_map.keys())
        return sorted(names)

    def get_model_completions(self) -> list[str]:
        """Get cached model names for tab completion."""
        return self._model_cache

    def get_collection_completions(self) -> list[str]:
        """Get cached collection names for tab completion."""
        return self._collection_cache

    async def load_model_cache(self) -> None:
        """Pre-populate model cache for tab completion."""
        from app.core.log import logger

        all_model_ids: list[str] = []

        # Check for Ollama models (local, no API key needed)
        try:
            from app.services.ai.domains.llm.ollama import OllamaClient

            ollama_client = OllamaClient()
            if await ollama_client.is_available():
                ollama_models = await ollama_client.fetch_models()
                all_model_ids.extend([m.model_id for m in ollama_models])
                logger.debug(f"Loaded {len(ollama_models)} Ollama models")
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")

        # Check for cloud providers with API keys (requires LLM catalog)
        from app.services.ai.domains.llm.llm_service import list_models
        from app.services.ai.domains.llm.provider_management import (
            check_provider_dependency_installed,
            get_existing_api_key,
        )
        from app.services.ai.models import AIProvider

        provider_to_vendor = {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "google": "Google",
            "groq": "Groq",
            "mistral": "Mistral",
            "cohere": "Cohere",
        }

        configured_vendors = []
        for provider in AIProvider:
            if provider.value in ("public", "ollama"):
                continue
            if not check_provider_dependency_installed(provider.value):
                continue
            if not get_existing_api_key(provider.value):
                continue
            vendor_name = provider_to_vendor.get(provider.value)
            if vendor_name:
                configured_vendors.append(vendor_name)

        # Fetch cloud provider models from database
        if configured_vendors:
            try:
                for vendor in configured_vendors:
                    results = await list_models(pattern=None, vendor=vendor, limit=100)
                    all_model_ids.extend([m.model_id for m in results])
                logger.debug(f"Loaded models from vendors: {configured_vendors}")
            except Exception as e:
                logger.warning(f"Failed to load cloud model cache: {e}")

        if not all_model_ids:
            logger.debug("No models available for model cache")
            self.console.print(
                "[dim]No models available for /model tab completion. "
                "Start Ollama or set provider API keys.[/dim]"
            )
            return

        self._model_cache = all_model_ids
        logger.debug(f"Model cache loaded: {len(self._model_cache)} models")

    def is_slash_command(self, text: str) -> bool:
        """Check if input is a slash command."""
        return text.strip().startswith("/")

    def parse_input(self, text: str) -> tuple[str | None, list[str]]:
        """Parse input to extract command and arguments."""
        text = text.strip()
        if not text.startswith("/"):
            return None, []

        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        args = parts[1].split() if len(parts) > 1 else []
        return command, args

    async def execute(self, text: str) -> CommandResult | None:
        """Execute a slash command if input starts with /."""
        command_name, args = self.parse_input(text)
        if command_name is None:
            return None  # Not a slash command

        # Resolve alias to command name
        if command_name in self._alias_map:
            command_name = self._alias_map[command_name]

        if command_name not in self.commands:
            msg = t("slash.unknown_command", name=command_name)
            return CommandResult(success=False, message=msg)

        # Dispatch to handler method
        handler_method = getattr(self, f"_cmd_{command_name}", None)
        if handler_method is None:
            return CommandResult(
                success=False,
                message=f"Command /{command_name} is not implemented.",
            )

        return await handler_method(args)

    async def _cmd_help(self, args: list[str]) -> CommandResult:
        """Show available commands."""
        table = Table(
            show_header=True,
            header_style="dim",
            box=None,
            padding=(0, 2),
        )
        table.add_column("Command", style=theme.ACCENT)
        table.add_column("Description")

        for name, cmd in sorted(self.commands.items()):
            table.add_row(f"/{name}", cmd.description)

        self.console.print(
            Panel(table, title=t("slash.commands_panel"), border_style="dim")
        )
        return CommandResult(success=True)

    async def _cmd_clear(self, args: list[str]) -> CommandResult:
        """Clear the screen."""
        # Use appropriate clear command for the platform
        os.system("cls" if os.name == "nt" else "clear")
        return CommandResult(success=True)

    async def _cmd_new(self, args: list[str]) -> CommandResult:
        """Start a new conversation."""
        self.current_conversation_id = None
        return CommandResult(
            success=True,
            message=f"[{theme.ACCENT}]{t('slash.new_conversation')}[/{theme.ACCENT}]",
            new_conversation_id="new",  # Signal to reset
        )

    async def _cmd_model(self, args: list[str]) -> CommandResult:
        """Switch AI model (auto-detects provider)."""
        # Show current if no args
        if not args:
            current = self.ai_service.config
            content = Text()
            content.append(t("ai.provider_label") + " ", style="dim")
            content.append(f"{current.provider.value}\n")
            content.append(t("ai.model_label") + " ", style="dim")
            content.append(f"{current.model}\n")
            content.append(t("slash.use_model_hint"), style="dim")
            self.console.print(
                Panel(content, title=t("slash.current_panel"), border_style="dim")
            )
            return CommandResult(success=True)

        model = args[0]

        # Use set_active_model which auto-detects provider from model
        # (requires LLM catalog)
        from app.services.ai.domains.llm.llm_service import set_active_model

        result = await set_active_model(model)

        if not result.success:
            # Model not in catalog - try with force
            result = await set_active_model(model, force=True)

        from dotenv import load_dotenv

        load_dotenv(override=True)
        self.ai_service.refresh_config()

        # Build message
        if result.provider_updated:
            switched = t(
                "slash.switched_to",
                provider=result.vendor,
                model=model,
            )
            msg = f"[{theme.ACCENT}]{switched}[/{theme.ACCENT}]"
            return CommandResult(
                success=True,
                message=msg,
                update_provider=result.vendor.lower() if result.vendor else None,
                update_model=model,
            )
        else:
            return CommandResult(
                success=True,
                message=f"[{theme.ACCENT}]{t('slash.switched_model', model=model)}[/{theme.ACCENT}]",
                update_model=model,
            )

    async def _cmd_status(self, args: list[str]) -> CommandResult:
        """Show current configuration."""
        current = self.ai_service.config

        content = Text()
        content.append(t("ai.provider_label") + " ", style="dim")
        content.append(f"{current.provider.value}\n")
        content.append(t("ai.model_label") + " ", style="dim")
        content.append(f"{current.model}\n")
        content.append(t("ai.temperature_label") + " ", style="dim")
        content.append(f"{current.temperature}\n")
        content.append(t("ai.max_tokens_label") + " ", style="dim")
        content.append(f"{current.max_tokens}")

        self.console.print(
            Panel(content, title=t("slash.status_panel"), border_style="dim")
        )
        return CommandResult(success=True)

    async def _cmd_exit(self, args: list[str]) -> CommandResult:
        """Exit the chat session."""
        return CommandResult(
            success=True,
            message=f"[dim]{t('ai.goodbye')}[/dim]",
            should_exit=True,
        )
