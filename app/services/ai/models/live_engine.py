"""What a live call can run on, as rows the app changes (#273).

An engine is how she uses a catalog model on a call, the way an agent is
how she uses one in chat: the model itself (its name, its maker, its
price) is the catalog's row (``llm_id``), and only what is ours lives
here - how it connects, its instructions and reply cap, and whether it
is offered. The defaults are seeded at startup when missing and never
overwrite an edit.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, Relationship, SQLModel

from app.core.clock import utcnow
from app.services.ai.models.llm import LargeLanguageModel


class LiveEngine(SQLModel, table=True):
    __tablename__ = "live_engine"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True, max_length=48)
    llm_id: int = Field(foreign_key="large_language_model.id", index=True)
    # "gpt_live" (our GPT-Live path) or "realtime" (a Pydantic AI model).
    transport: str = Field(max_length=16)
    # A line on what it is like; and what does not work yet, if anything.
    note: str = ""
    warning: str | None = None
    # Its own instructions: GPT-Live's persona, or a realtime model's
    # live-call section ahead of her voice agent's prompt.
    instructions: str | None = None
    # A hard cap on one spoken reply, where the engine takes one.
    max_output_tokens: int | None = Field(default=None, ge=1)
    is_enabled: bool = True
    sort_order: int = 0
    updated_at: datetime = Field(default_factory=utcnow)

    llm: LargeLanguageModel = Relationship()
