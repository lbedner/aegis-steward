"""LLM Modality model for input/output capabilities."""

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .large_language_model import LargeLanguageModel


class Modality(str, Enum):
    """Types of input/output modalities supported by LLMs."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"


class Direction(str, Enum):
    """Direction of modality support."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"


class LLMModality(SQLModel, table=True):
    """
    Tracks input/output capabilities of an LLM model.

    A model can have multiple modalities (e.g., text input, image output).
    Uses check constraints instead of native enums for SQLite compatibility.
    """

    __tablename__ = "llm_modality"
    __table_args__ = (
        CheckConstraint(
            f"modality IN ("
            f"'{Modality.TEXT.value}', "
            f"'{Modality.AUDIO.value}', "
            f"'{Modality.IMAGE.value}', "
            f"'{Modality.VIDEO.value}'"
            f")",
            name="modality_check",
        ),
        CheckConstraint(
            f"direction IN ("
            f"'{Direction.INPUT.value}', "
            f"'{Direction.OUTPUT.value}', "
            f"'{Direction.BIDIRECTIONAL.value}'"
            f")",
            name="direction_check",
        ),
        UniqueConstraint(
            "llm_id", "modality", "direction", name="unique_llm_modality_direction"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    llm_id: int = Field(foreign_key="large_language_model.id", index=True)
    modality: Modality = Field(sa_column=Column("modality", String, nullable=False))
    direction: Direction = Field(
        default=Direction.INPUT,
        sa_column=Column("direction", String, nullable=False),
    )

    # Relationship
    llm: LargeLanguageModel = Relationship(back_populates="modalities")
