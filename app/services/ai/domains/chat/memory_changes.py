"""Memory writes as proposals: ``memory.save``, ``memory.replace``,
``memory.update`` and ``memory.forget`` (#484).

Untrusted text reaches the assistant - a transaction's description, an
email body, a document, a fetched page - and a fact saved on its say-so
is a lasting instruction in every later prompt, as a fact forgotten on
its say-so is a standing instruction gone. So a memory write is a card
on the change queue like every other write, and nothing changes until
the owner approves it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.chat import user_memory
from app.services.finance.schemas import ChangeDisplayRow


class SavePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    fact: str
    category: str = "general"

    @field_validator("category")
    @classmethod
    def _known(cls, category: str) -> str:
        # The card shows the category it will be filed under.
        return user_memory._normalize_category(category)


class ReplacePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    memory_text: str


class UpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    old_fact: str
    new_fact: str


class ForgetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    fact: str


async def save_execute(
    db: AsyncSession, payload: SavePayload, owner_user_id: int | None
) -> dict[str, Any]:
    said = await user_memory.save_user_fact(
        payload.user_id, payload.fact, payload.category, session=db
    )
    return {"memory": said}


async def save_describe(
    db: AsyncSession, payload: SavePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    return [
        ChangeDisplayRow(label="Remember", value=payload.fact.strip()),
        ChangeDisplayRow(label="Category", value=payload.category),
    ]


async def replace_execute(
    db: AsyncSession, payload: ReplacePayload, owner_user_id: int | None
) -> dict[str, Any]:
    said = await user_memory.replace_user_memory(
        payload.user_id, payload.memory_text, session=db
    )
    return {"memory": said}


async def replace_describe(
    db: AsyncSession, payload: ReplacePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    saved = await user_memory.list_user_facts(payload.user_id, session=db)
    return [
        ChangeDisplayRow(label="Forget", value=f"all {len(saved)} saved fact(s)"),
        ChangeDisplayRow(label="Remember instead", value=payload.memory_text.strip()),
    ]


# Update and forget name their fact by its words (``user_memory._locate``).
async def update_execute(
    db: AsyncSession, payload: UpdatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    entry = await user_memory.update_user_fact(
        payload.user_id, payload.old_fact, fact=payload.new_fact, session=db
    )
    return {"memory": f"Updated ({entry['category']}): {entry['fact']}"}


async def update_describe(
    db: AsyncSession, payload: UpdatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    entry = await user_memory.saved_fact(payload.user_id, payload.old_fact, session=db)
    return [
        ChangeDisplayRow(label="Was", value=entry["fact"]),
        ChangeDisplayRow(label="Now", value=payload.new_fact.strip()),
    ]


async def forget_execute(
    db: AsyncSession, payload: ForgetPayload, owner_user_id: int | None
) -> dict[str, Any]:
    gone = await user_memory.delete_user_fact(payload.user_id, payload.fact, session=db)
    return {"memory": f"Forgot: {gone['fact']}"}


async def forget_describe(
    db: AsyncSession, payload: ForgetPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    entry = await user_memory.saved_fact(payload.user_id, payload.fact, session=db)
    return [ChangeDisplayRow(label="Forget", value=entry["fact"])]
