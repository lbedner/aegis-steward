"""
AI conversation management.


SQLite-based conversation storage and management for AI chat sessions.
Provides persistent conversation history across application restarts.

"""

from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy import delete, func, text
from sqlmodel import select

from app.core.db import db_session, init_database
from app.core.log import logger
from app.models.conversation import Conversation as ConversationModel
from app.models.conversation import ConversationMessage as MessageModel
from app.services.ai.models import (
    AIProvider,
    Conversation,
    ConversationMessage,
    MessageRole,
)


class ConversationManager:
    """

    Manages AI conversations with SQLite persistence.

    Stores conversations in the project database using SQLModel tables.
    Provides persistence across application restarts.

    """

    def __init__(self) -> None:
        """Initialize conversation manager."""

        # Ensure database is initialized (creates tables if missing)
        # This allows CLI commands (like 'ai chat') to work without starting the server
        init_database()

    def create_conversation(
        self,
        provider: AIProvider,
        model: str,
        user_id: str = "default",
        conversation_id: str | None = None,
        surface: str | None = None,
    ) -> Conversation:
        """
        Create a new conversation.

        Args:
            provider: AI provider being used
            model: Model name
            user_id: User identifier
            conversation_id: Optional custom conversation ID
            surface: Originating chat surface (e.g. "finance"); scopes
                history listings so each embedded chat shows its own past

        Returns:
            Conversation: The created conversation
        """
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        metadata: dict = {"user_id": user_id, "created_by": "ai_service"}
        if surface:
            metadata["surface"] = surface
        conversation = Conversation(
            id=conversation_id,
            provider=provider,
            model=model,
            metadata=metadata,
        )

        # Save to database
        self._save_to_db(conversation)

        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """
        Get a conversation by ID.

        Args:
            conversation_id: The conversation identifier

        Returns:
            Conversation | None: The conversation if found, None otherwise
        """

        with db_session() as session:
            conv_db = session.get(ConversationModel, conversation_id)
            if not conv_db:
                return None

            # Load messages
            stmt = (
                select(MessageModel)
                .where(MessageModel.conversation_id == conversation_id)
                .order_by(MessageModel.timestamp)
            )
            messages_db = session.exec(stmt).all()

            # Convert to Pydantic models
            messages = [
                ConversationMessage(
                    id=msg.id,
                    role=MessageRole(msg.role),
                    content=msg.content,
                    timestamp=msg.timestamp,
                    metadata=msg.meta_data or {},
                )
                for msg in messages_db
            ]

            # Get provider/model from meta_data
            meta_data = conv_db.meta_data or {}

            return Conversation(
                id=conv_db.id,
                title=conv_db.title,
                provider=AIProvider(meta_data.get("provider", "openai")),
                model=meta_data.get("model", "unknown"),
                created_at=conv_db.created_at,
                updated_at=conv_db.updated_at,
                messages=messages,
                metadata=meta_data,
            )

    def save_conversation(self, conversation: Conversation) -> None:
        """
        Save a conversation (update in storage).

        Args:
            conversation: The conversation to save
        """
        conversation.updated_at = datetime.now(UTC)

        self._save_to_db(conversation)

    def list_conversations(
        self, user_id: str | None = None, surface: str | None = None
    ) -> list[Conversation]:
        """
        List conversations, optionally filtered by user and surface.

        Args:
            user_id: Optional user ID to filter by
            surface: Optional originating surface to filter by

        Returns:
            list[Conversation]: List of conversations
        """

        from sqlalchemy.orm import selectinload

        with db_session() as session:
            # Use selectinload to eagerly load messages (2 queries instead of N+1)
            stmt = (
                select(ConversationModel)
                .options(selectinload(ConversationModel.messages))  # type: ignore[arg-type]
                .order_by(ConversationModel.updated_at.desc())
            )
            all_convs = session.exec(stmt).all()

            conversations = []
            for conv_db in all_convs:
                meta_data = conv_db.meta_data or {}

                # Filter by user_id / surface if specified
                if user_id and meta_data.get("user_id") != user_id:
                    continue
                if surface and meta_data.get("surface") != surface:
                    continue

                # Messages already loaded via selectinload
                messages = [
                    ConversationMessage(
                        id=msg.id,
                        role=MessageRole(msg.role),
                        content=msg.content,
                        timestamp=msg.timestamp,
                        metadata=msg.meta_data or {},
                    )
                    for msg in sorted(conv_db.messages, key=lambda m: m.timestamp)
                ]

                conversations.append(
                    Conversation(
                        id=conv_db.id,
                        title=conv_db.title,
                        provider=AIProvider(meta_data.get("provider", "openai")),
                        model=meta_data.get("model", "unknown"),
                        created_at=conv_db.created_at,
                        updated_at=conv_db.updated_at,
                        messages=messages,
                        metadata=meta_data,
                    )
                )

            return conversations

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete a conversation.

        Args:
            conversation_id: The conversation identifier

        Returns:
            bool: True if conversation was deleted, False if not found
        """

        with db_session() as session:
            conv_db = session.get(ConversationModel, conversation_id)
            if not conv_db:
                return False

            # Delete messages first
            stmt = select(MessageModel).where(
                MessageModel.conversation_id == conversation_id
            )
            messages = session.exec(stmt).all()
            for msg in messages:
                session.delete(msg)

            # Delete conversation
            session.delete(conv_db)
            session.commit()

            return True

    def get_conversation_count(self, user_id: str | None = None) -> int:
        """
        Get count of conversations.

        Args:
            user_id: Optional user ID to filter by

        Returns:
            int: Number of conversations
        """

        if user_id:
            # Must filter by JSON metadata field
            return len(self.list_conversations(user_id))

        with db_session() as session:
            return session.exec(
                select(func.count()).select_from(ConversationModel)
            ).one()

    def get_recent_conversations(
        self, limit: int = 10, user_id: str | None = None
    ) -> list[Conversation]:
        """
        Get recent conversations.

        Args:
            limit: Maximum number of conversations to return
            user_id: Optional user ID to filter by

        Returns:
            list[Conversation]: Recent conversations
        """
        conversations = self.list_conversations(user_id)
        return conversations[:limit]

    def cleanup_old_conversations(self, max_age_hours: int = 24) -> int:
        """
        Clean up old conversations.

        Args:
            max_age_hours: Maximum age in hours before cleanup

        Returns:
            int: Number of conversations cleaned up
        """

        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)

        with db_session() as session:
            # Find IDs of old conversations
            old_conv_ids = session.exec(
                select(ConversationModel.id).where(
                    ConversationModel.updated_at < cutoff
                )
            ).all()

            if not old_conv_ids:
                return 0

            # Bulk delete messages first (FK constraint), then conversations
            session.execute(
                delete(MessageModel).where(
                    MessageModel.conversation_id.in_(old_conv_ids)  # type: ignore[union-attr]
                )
            )
            session.execute(
                delete(ConversationModel).where(
                    ConversationModel.id.in_(old_conv_ids)  # type: ignore[union-attr]
                )
            )
            session.commit()

            deleted_count = len(old_conv_ids)

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old conversations")

        return deleted_count

    def get_stats(self) -> dict[str, Any]:
        """
        Get conversation manager statistics.

        Returns:
            dict: Statistics about conversations
        """

        with db_session() as session:
            # Use SQL COUNT queries instead of loading all rows into memory
            total_conversations = session.exec(
                select(func.count()).select_from(ConversationModel)
            ).one()

            total_messages = session.exec(
                select(func.count()).select_from(MessageModel)
            ).one()

            unique_users = (
                session.execute(
                    text(
                        "SELECT COUNT(DISTINCT json_extract(meta_data, '$.user_id')) "
                        "FROM conversation "
                        "WHERE json_extract(meta_data, '$.user_id') IS NOT NULL"
                    )
                ).scalar()
                or 0
            )

            return {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "unique_users": unique_users,
                "average_messages_per_conversation": (
                    total_messages / total_conversations
                    if total_conversations > 0
                    else 0
                ),
            }

    def _save_to_db(self, conversation: Conversation) -> None:
        """Save conversation and its messages to database."""
        with db_session() as session:
            # Build meta_data with provider/model included
            meta_data = {
                **conversation.metadata,
                "provider": conversation.provider.value,
                "model": conversation.model,
            }

            # Check if conversation exists
            conv_db = session.get(ConversationModel, conversation.id)
            if conv_db:
                # Update existing
                conv_db.title = conversation.title
                conv_db.updated_at = conversation.updated_at
                conv_db.meta_data = meta_data
            else:
                # Create new
                conv_db = ConversationModel(
                    id=conversation.id,
                    title=conversation.title,
                    user_id=conversation.metadata.get("user_id", "default"),
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                    meta_data=meta_data,
                )
                session.add(conv_db)

            # Save messages
            for message in conversation.messages:
                msg_db = session.get(MessageModel, message.id)
                if not msg_db:
                    msg_db = MessageModel(
                        id=message.id,
                        conversation_id=conversation.id,
                        role=message.role.value,
                        content=message.content,
                        timestamp=message.timestamp,
                        meta_data=message.metadata,
                    )
                    session.add(msg_db)

            session.commit()
