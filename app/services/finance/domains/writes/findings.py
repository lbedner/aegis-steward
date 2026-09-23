"""What an anomaly turned out to be, proposed rather than decided (FW-09).

The assistant's own list keeps "marking anomalies resolved without your
confirmation" behind explicit confirmation, so this is a card, never a
direct write: the assistant proposes the reading and the person's words,
the person approves it. Approving lands in ``resolve_insight``, the same
place the Attention tab's own dialog does.

Resolving never edits the transaction the anomaly is about. A
mis-categorized reading is this plus a separate category proposal.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow


async def awaiting_resolution(db: AsyncSession) -> set[int]:
    """Insights a pending card is deciding. A rule must not withdraw one
    out from under the person mid-decision: found live, when a rule fix
    retracted insight 1058 and approving its card answered "not found"."""
    from app.services.finance.domains.writes.queue import list_changes

    return {
        int(change.payload["insight_id"])
        for change in await list_changes(db, status="pending")
        if change.change_type == "insight.resolve"
    }


def _labels() -> dict[str, str]:
    from app.services.finance.models.planning import INSIGHT_RESOLUTIONS

    return dict(INSIGHT_RESOLUTIONS)


class ResolveInsightPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: int
    state: str
    note: str | None = None

    @field_validator("state")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in _labels():
            raise ValueError(f"state must be one of {', '.join(_labels())}")
        return value


async def resolve_execute(
    db: AsyncSession, payload: ResolveInsightPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.finance.domains.planning.insights import resolve_insight

    insight = await resolve_insight(
        db,
        payload.insight_id,
        state=payload.state,
        note=payload.note,
        owner_user_id=owner_user_id,
    )
    if insight is None:
        raise ValueError(f"Insight {payload.insight_id} not found.")
    return {"insight_id": insight.id, "resolution": insight.resolution}


async def resolve_describe(
    db: AsyncSession, payload: ResolveInsightPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.planning import queries

    insight = await queries.insight_by_id(
        db, payload.insight_id, owner_user_id=owner_user_id
    )
    rows = [
        ChangeDisplayRow(
            label="Anomaly",
            value=insight.title if insight else f"insight {payload.insight_id}",
        ),
        ChangeDisplayRow(label="Reading", value=_labels()[payload.state]),
    ]
    if payload.note and payload.note.strip():
        rows.append(ChangeDisplayRow(label="Note", value=payload.note.strip()))
    return rows
