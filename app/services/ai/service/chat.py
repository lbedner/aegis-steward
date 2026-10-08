"""A conversation's setup and teardown around a turn.

The turn itself is ``stream_chat()`` in ``streaming.py``; ``chat()``
there runs it to its end (#455).
"""

from datetime import UTC, datetime
from typing import Any

from app.services.ai.config import AIServiceConfig
from app.services.ai.domains.chat.self_context import current_turn
from app.services.ai.domains.chat.summary import enqueue_if_due
from app.services.ai.domains.chat.titles import conversation_title
from app.services.ai.models import Conversation, MessageRole
from app.services.ai.service.base import (
    ConversationError,
)
from app.services.ai.service.prompt import PromptMixin

# Importing registers the finance host tools (ledger, accounts, quote)
# so DB-granted names resolve in every process that serves chat - the
# API, the CLI, and code-mode agents alike.
import app.services.finance.ai_tools  # noqa: F401

# And the matter tools (parties, matters, requests, facts): the case
# surface is read the same way, by name, in every process that chats.
import app.services.insurance.ai_tools  # noqa: F401
import app.services.mail.ai_tools  # noqa: F401
import app.services.matters.ai_tools  # noqa: F401


class ChatMixin(PromptMixin):
    """Conversation setup and teardown, shared by the one turn."""

    async def _setup_conversation(
        self,
        message: str,
        conversation_id: str | None,
        user_id: str,
        config: AIServiceConfig | None = None,
        surface: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """
        Get or create conversation and add user message.

        Args:
            message: The user's message
            conversation_id: Optional conversation ID (creates new if None)
            user_id: User identifier for conversation ownership
            config: Optional fresh config (uses self.config if not provided)
            surface: Originating chat surface, recorded on new conversations
                so each embedded chat lists only its own history

        Returns:
            Conversation: The conversation with user message added

        Raises:
            ConversationError: If conversation_id provided but not found
        """
        # Use provided config or fall back to cached
        cfg = config or self.config

        # Get or create conversation
        if conversation_id:
            conversation = await self.conversation_manager.get_conversation(
                conversation_id
            )
            if not conversation:
                raise ConversationError(f"Conversation {conversation_id} not found")
        else:
            conversation = await self.conversation_manager.create_conversation(
                provider=cfg.provider,
                model=cfg.model,
                user_id=user_id,
                surface=surface,
            )
            # First message names the conversation; history lists need a
            # human hook, not a UUID.
            conversation.title = conversation_title(message)

        # Add user message to conversation
        conversation.add_message(MessageRole.USER, message, metadata=metadata)

        return conversation

    async def _finalize_conversation(
        self,
        conversation: Conversation,
        response_time_ms: float,
    ) -> None:
        """
        Update conversation metadata and save.

        Args:
            conversation: The conversation to finalize
            response_time_ms: Response time in milliseconds
        """
        # Update conversation metadata
        metadata_update = {
            "last_response_time_ms": response_time_ms,
            "total_messages": conversation.get_message_count(),
            "last_activity": datetime.now(UTC).isoformat(),
        }

        conversation.metadata.update(metadata_update)

        # Save conversation
        await self.conversation_manager.save_conversation(conversation)
        # Turns that left this one's window fold into the running summary
        # on the worker, once enough have (#295).
        if (stamp := current_turn()) is not None and stamp.messages_dropped:
            await enqueue_if_due(conversation, stamp.first_kept)
