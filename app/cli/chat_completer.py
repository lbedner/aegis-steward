"""
Autocompletion for Illiana interactive chat.

Provides completion for slash commands and message history using
prompt_toolkit's completion system.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from app.i18n import t

if TYPE_CHECKING:
    from app.cli.slash_commands import SlashCommandHandler


class ChatCompleter(Completer):
    """
    Completer for slash commands in interactive chat.

    Shows autocomplete suggestions when typing / followed by command names.
    Provides descriptions for each command in the completion popup.
    """

    def __init__(
        self,
        command_handler: SlashCommandHandler,
    ) -> None:
        """
        Initialize chat completer.

        Args:
            command_handler: The slash command handler to get command names from.
        """
        self.command_handler = command_handler

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        """
        Get completions for the current input.

        Args:
            document: The current document being edited.
            complete_event: The completion event (unused but required by interface).

        Yields:
            Completion objects for matching commands.
        """
        text = document.text_before_cursor

        # Only complete slash commands
        if not text.startswith("/"):
            return

        # Complete model names after "/model "
        if text.startswith("/model "):
            model_part = text[7:]  # After "/model "
            model_part_lower = model_part.lower()

            for model_id in self.command_handler.get_model_completions():
                if model_id.lower().startswith(model_part_lower):
                    yield Completion(
                        model_id,
                        start_position=-len(model_part),
                        display=model_id,
                    )
            return

        # Complete collection names after "/rag "
        if text.startswith("/rag "):
            collection_part = text[5:]  # After "/rag "
            collection_part_lower = collection_part.lower()

            # Also offer "off" as an option
            if "off".startswith(collection_part_lower):
                yield Completion(
                    "off",
                    start_position=-len(collection_part),
                    display="off",
                    display_meta=t("chat.disable_rag"),
                )

            for collection in self.command_handler.get_collection_completions():
                if collection.lower().startswith(collection_part_lower):
                    yield Completion(
                        collection,
                        start_position=-len(collection_part),
                        display=collection,
                    )
            return

        # Complete options after "/sources "
        if text.startswith("/sources "):
            arg_part = text[9:]  # After "/sources "
            options = [
                ("enable", t("chat.show_source_references")),
                ("disable", t("chat.hide_source_references")),
            ]
            for opt, desc in options:
                if opt.startswith(arg_part.lower()):
                    yield Completion(
                        opt,
                        start_position=-len(arg_part),
                        display=opt,
                        display_meta=desc,
                    )
            return

        # Get the command part being typed (after the /)
        command_part = text[1:].lower()

        # Get all command names (including aliases)
        for cmd_name in self.command_handler.get_command_names():
            if cmd_name.startswith(command_part):
                # Get description for display
                description = self._get_command_description(cmd_name)

                yield Completion(
                    cmd_name,
                    start_position=-len(command_part),
                    display=f"/{cmd_name}",
                    display_meta=description,
                )

    def _get_command_description(self, name: str) -> str:
        """
        Get short description for a command.

        Args:
            name: Command name or alias.

        Returns:
            Description string for the command.
        """
        # Check if it's an alias
        if name in self.command_handler._alias_map:
            name = self.command_handler._alias_map[name]

        cmd = self.command_handler.commands.get(name)
        return cmd.description if cmd else ""
