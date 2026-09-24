"""Agent-tool link table."""

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class AgentTool(SQLModel, table=True):
    """
    Attaches a registered tool to an agent.

    Link rows are agent-owned: deleting an agent (or a tool) removes its
    links via ON DELETE CASCADE.
    """

    __tablename__ = "agent_tool"
    # One link per (agent, tool); the generated schema always had this index.
    __table_args__ = (
        UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool_pair"),
    )

    id: int | None = Field(default=None, primary_key=True)
    agent_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
        )
    )
    tool_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tool.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
