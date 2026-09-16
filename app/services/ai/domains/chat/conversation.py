"""
AI conversation management.


SQLite-based conversation storage and management for AI chat sessions.
Provides persistent conversation history across application restarts.

Async, like every other store in the app: a chat turn saves its
conversation on the event loop, and a sync engine there waits its
SQLite turn with the whole server frozen behind it.
"""

from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy import delete, func, text
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_async_session, init_database, retry_on_locked
from app.core.log import logger
from app.models.conversation import Conversation as ConversationModel
from app.models.conversation import ConversationMessage as MessageModel
from app.services.ai.models import (
    AIProvider,
    Conversation,
    ConversationMessage,
    MessageRole,
)


def _to_conversation(conv_db: ConversationModel, messages_db: Any) -> Conversation:
    """The stored row and its messages as the service's model."""
    meta_data = conv_db.meta_data or {}
    return Conversation(
        id=conv_db.id,
        title=conv_db.title,
        provider=AIProvider(meta_data.get("provider", "openai")),
        model=meta_data.get("model", "unknown"),
        created_at=conv_db.created_at,
        updated_at=conv_db.updated_at,
        messages=[
            ConversationMessage(
                id=msg.id,
                role=MessageRole(msg.role),
                content=msg.content,
                timestamp=msg.timestamp,
                metadata=msg.meta_data or {},
            )
            for msg in sorted(messages_db, key=lambda m: m.timestamp)
        ],
        metadata=meta_data,
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

    async def create_conversation(
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

        await self._save_to_db(conversation)

        return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """
        Get a conversation by ID.

        Args:
            conversation_id: The conversation identifier

        Returns:
            Conversation | None: The conversation if found, None otherwise
        """

        async with get_async_session() as session:
            conv_db = await session.get(ConversationModel, conversation_id)
            if not conv_db:
                return None

            stmt = (
                select(MessageModel)
                .where(MessageModel.conversation_id == conversation_id)
                .order_by(MessageModel.timestamp)
            )
            messages_db = (await session.exec(stmt)).all()
            return _to_conversation(conv_db, messages_db)

    async def save_conversation(self, conversation: Conversation) -> None:
        """
        Save a conversation (update in storage).

        Args:
            conversation: The conversation to save
        """
        conversation.updated_at = datetime.now(UTC)

        await self._save_to_db(conversation)

    async def list_conversations(
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

        async with get_async_session() as session:
            # Use selectinload to eagerly load messages (2 queries instead of N+1)
            stmt = (
                select(ConversationModel)
                .options(selectinload(ConversationModel.messages))  # type: ignore[arg-type]
                .order_by(ConversationModel.updated_at.desc())
            )
            all_convs = (await session.exec(stmt)).all()

            conversations = []
            for conv_db in all_convs:
                meta_data = conv_db.meta_data or {}

                # Filter by user_id / surface if specified
                if user_id and meta_data.get("user_id") != user_id:
                    continue
                if surface and meta_data.get("surface") != surface:
                    continue

                # Messages already loaded via selectinload
                conversations.append(_to_conversation(conv_db, conv_db.messages))

            return conversations

    async def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete a conversation.

        Args:
            conversation_id: The conversation identifier

        Returns:
            bool: True if conversation was deleted, False if not found
        """

        async with get_async_session() as session:
            conv_db = await session.get(ConversationModel, conversation_id)
            if not conv_db:
                return False

            # Delete messages first
            stmt = select(MessageModel).where(
                MessageModel.conversation_id == conversation_id
            )
            for msg in (await session.exec(stmt)).all():
                await session.delete(msg)

            await session.delete(conv_db)
            await session.commit()

            return True

    async def get_conversation_count(self, user_id: str | None = None) -> int:
        """
        Get count of conversations.

        Args:
            user_id: Optional user ID to filter by

        Returns:
            int: Number of conversations
        """

        if user_id:
            # Must filter by JSON metadata field
            return len(await self.list_conversations(user_id))

        async with get_async_session() as session:
            return (
                await session.exec(select(func.count()).select_from(ConversationModel))
            ).one()

    async def get_recent_conversations(
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
        conversations = await self.list_conversations(user_id)
        return conversations[:limit]

    async def cleanup_old_conversations(self, max_age_hours: int = 24) -> int:
        """
        Clean up old conversations.

        Args:
            max_age_hours: Maximum age in hours before cleanup

        Returns:
            int: Number of conversations cleaned up
        """

        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)

        async with get_async_session() as session:
            # Find IDs of old conversations
            old_conv_ids = (
                await session.exec(
                    select(ConversationModel.id).where(
                        ConversationModel.updated_at < cutoff
                    )
                )
            ).all()

            if not old_conv_ids:
                return 0

            # Bulk delete messages first (FK constraint), then conversations
            await session.execute(
                delete(MessageModel).where(
                    MessageModel.conversation_id.in_(old_conv_ids)  # type: ignore[union-attr]
                )
            )
            await session.execute(
                delete(ConversationModel).where(
                    ConversationModel.id.in_(old_conv_ids)  # type: ignore[union-attr]
                )
            )
            await session.commit()

            deleted_count = len(old_conv_ids)

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old conversations")

        return deleted_count

    async def get_stats(self) -> dict[str, Any]:
        """
        Get conversation manager statistics.

        Returns:
            dict: Statistics about conversations
        """

        async with get_async_session() as session:
            # Use SQL COUNT queries instead of loading all rows into memory
            total_conversations = (
                await session.exec(select(func.count()).select_from(ConversationModel))
            ).one()

            total_messages = (
                await session.exec(select(func.count()).select_from(MessageModel))
            ).one()

            unique_users = (
                await session.execute(
                    text(
                        "SELECT COUNT(DISTINCT json_extract(meta_data, '$.user_id')) "
                        "FROM conversation "
                        "WHERE json_extract(meta_data, '$.user_id') IS NOT NULL"
                    )
                )
            ).scalar() or 0

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

    async def _save_to_db(self, conversation: Conversation) -> None:
        """Save conversation and its messages to database.

        Its own short session, read then write: the upgrade can fail at
        once under another writer, so it is retried where it happens.
        """

        async def write() -> None:
            async with get_async_session() as session:
                await self._write(session, conversation)

        await retry_on_locked(write)

    @staticmethod
    async def _write(session: AsyncSession, conversation: Conversation) -> None:
        # Build meta_data with provider/model included
        meta_data = {
            **conversation.metadata,
            "provider": conversation.provider.value,
            "model": conversation.model,
        }

        conv_db = await session.get(ConversationModel, conversation.id)
        if conv_db:
            conv_db.title = conversation.title
            conv_db.updated_at = conversation.updated_at
            conv_db.meta_data = meta_data
        else:
            conv_db = ConversationModel(
                id=conversation.id,
                title=conversation.title,
                user_id=conversation.metadata.get("user_id", "default"),
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                meta_data=meta_data,
            )
            session.add(conv_db)

        for message in conversation.messages:
            msg_db = await session.get(MessageModel, message.id)
            if not msg_db:
                session.add(
                    MessageModel(
                        id=message.id,
                        conversation_id=conversation.id,
                        role=message.role.value,
                        content=message.content,
                        timestamp=message.timestamp,
                        meta_data=message.metadata,
                    )
                )

        await session.commit()
