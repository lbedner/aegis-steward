"""Agent registry model."""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel

from app.core.time import utcnow

from .agent_tool import AgentTool
from .tool import Tool


class Agent(SQLModel, table=True):
    """
    A database-driven agent definition.

    Agents are the AI service's default architecture: every chat request
    resolves an agent (the seeded ``assistant`` by default) and hydrates
    the runtime from this row. ``model_id`` is a plain catalog reference
    (NOT a foreign key, matching ``llm_usage``: the catalog is ETL-synced
    and rows churn); ``None`` means "use the service's active model".
    """

    __tablename__ = "agent"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str | None = None
    category: str | None = None
    model_id: str | None = Field(default=None, index=True)
    system_prompt: str
    # The fingerprint of the prompt the APP last wrote here. A row whose
    # prompt no longer matches it was edited by hand (the dashboard), and
    # a resync refuses to overwrite that unless told to. None on rows
    # older than the column: nothing is known about them, and a resync
    # treats them as it always did.
    prompt_fingerprint: str | None = Field(default=None, max_length=64)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=1000)
    memory_modules: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    knowledge_base_ids: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    is_active: bool = Field(default=True)
    # Grants this agent sandboxed code execution (code mode): the model
    # writes Python that calls the agent's granted tools as functions and
    # runs it in the Monty interpreter. Off by default; flipping it is a
    # capability grant, deliberately DB-driven like tool attachments.
    code_mode: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    tools: list[Tool] = Relationship(link_model=AgentTool)
