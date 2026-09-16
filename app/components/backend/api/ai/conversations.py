"""Conversation history over the API: the list, and one conversation in
full."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ConversationSummary(BaseModel):
    """Summary model for conversation listing."""

    id: str
    title: str | None
    message_count: int
    last_activity: str
    provider: str
    model: str


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: str = "api-user", surface: str | None = None, limit: int = 50
) -> list[ConversationSummary]:
    """
    List conversations for a user, optionally scoped to one chat surface.

    Args:
        user_id: User identifier
        surface: Originating surface to filter by (e.g. "finance")
        limit: Maximum number of conversations to return

    Returns:
        List of conversation summaries
    """
    from app.components.backend.api.ai.router import ai_service

    try:
        conversations = (await ai_service.list_conversations(user_id, surface=surface))[
            :limit
        ]

        return [
            ConversationSummary(
                id=conv.id,
                title=conv.title,
                message_count=conv.get_message_count(),
                last_activity=conv.updated_at.isoformat(),
                provider=conv.provider.value,
                model=conv.model,
            )
            for conv in conversations
        ]

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list conversations: {e}"
        )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, user_id: str = "api-user"
) -> dict[str, Any]:
    """
    Get a specific conversation with full message history.

    Args:
        conversation_id: The conversation identifier
        user_id: User identifier for access control

    Returns:
        Full conversation details with messages

    Raises:
        HTTPException: If conversation not found or access denied
    """
    from app.components.backend.api.ai.router import ai_service

    try:
        conversation = await ai_service.get_conversation(conversation_id)

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Check access (basic user matching)
        if conversation.metadata.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        return {
            "id": conversation.id,
            "title": conversation.title,
            "provider": conversation.provider.value,
            "model": conversation.model,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "message_count": conversation.get_message_count(),
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role.value,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat(),
                    "metadata": msg.metadata,
                }
                for msg in conversation.messages
            ],
            "metadata": conversation.metadata,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get conversation: {e}")
