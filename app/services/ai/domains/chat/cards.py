"""Cards Illiana draws in a reply (#266): the kinds, and the turn's stage.

Ported from Pulse, where she draws charts under her answers. Three rules
the shape enforces:

**The model authors data, never layout.** A ``kind`` selects a template
the app wrote; no classes, colours or column order travel in a payload.

**A kind owns a schema, checked when she draws.** Rows come out of a
code-mode script, which can produce any shape; a payload that does not
fit is a tool error she can fix on her next step, never a stored row that
fails in a template later.

**The tool runs inside the sandbox.** ``draw_card`` is NOT ``native_write``:
that would take it out of ``run_code`` and make her retype every row she
just computed. The cards a script draws are staged for the turn (like
``record_reading``) and ride that script's trace entry as markers
(``attach_cards``), which is what the settled message draws from.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError, model_validator

from app.core.db import get_async_session
from app.services.ai.domains.chat.tools import register_tool
from app.services.ai.domains.chat.user_memory import current_conversation_id
from app.services.ai.models.chat_card import ChatCard

MARKER = "chat_card"
# Money is integer cents, like every finance tool; a count is a count.
Unit = Literal["money", "count"]


@dataclass(frozen=True, slots=True)
class CardKind:
    """One drawable kind. ``rows`` is its own ceiling (0: nothing to cap):
    a ranking and a two-figure comparison want very different limits."""

    name: str
    schema: type[BaseModel]
    template: str
    rows: int


_KINDS: dict[str, CardKind] = {}


def register_kind(name: str, schema: type[BaseModel], *, rows: int) -> CardKind:
    kind = CardKind(name, schema, f"partials/chat/cards/{name}.html", rows)
    _KINDS[name] = kind
    return kind


def kind_names() -> tuple[str, ...]:
    return tuple(sorted(_KINDS))


def lookup(name: str) -> CardKind | None:
    return _KINDS.get(name)


# --- the kinds ---------------------------------------------------------------
# Ten rows is a top-10, which is what a card in a conversation is for; the
# long tail belongs in the prose, or on the page that already lists it.
ROW_CAP = 10


class TableRow(BaseModel):
    label: str
    values: list[str | int | float]


class TablePayload(BaseModel):
    """``columns`` names EVERY column, the label one first."""

    title: str
    columns: list[str]
    rows: list[TableRow]

    @model_validator(mode="after")
    def _columns_match_rows(self) -> TablePayload:
        if len(self.columns) < 2:
            raise ValueError("columns: the label column, then one per value")
        wanted = len(self.columns) - 1
        for row in self.rows:
            if len(row.values) != wanted:
                raise ValueError(
                    f"row {row.label!r} has {len(row.values)} values but "
                    f"{len(self.columns)} columns were named (the first names "
                    f"the label column, so {wanted} values)"
                )
        return self


class BarRow(BaseModel):
    label: str
    value: float


class BarPayload(BaseModel):
    """A ranking, drawn in the order given. ``measure`` is drawn, not implied."""

    title: str
    measure: str
    unit: Unit = "money"
    rows: list[BarRow]


class PiePayload(BaseModel):
    """Parts of a whole: each row a slice, drawn like the Overview's
    spending doughnut. Fold the tail into an "Other" row yourself."""

    title: str
    measure: str
    unit: Unit = "money"
    rows: list[BarRow]


# The chart palette has eight colours; a ninth slice would repeat one.
SLICE_CAP = 8


class ComparePayload(BaseModel):
    """A before and an after. No change field: the card works it out, so
    she is never asked for arithmetic nobody checks."""

    title: str
    measure: str
    unit: Unit = "money"
    before_label: str
    before: float
    after_label: str
    after: float


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    value: float


class TrendPayload(BaseModel):
    """One measure over time, drawn as a line."""

    title: str
    measure: str
    unit: Unit = "money"
    rows: list[TrendPoint]


register_kind("table", TablePayload, rows=ROW_CAP)
register_kind("bar", BarPayload, rows=ROW_CAP)
register_kind("pie", PiePayload, rows=SLICE_CAP)
register_kind("compare", ComparePayload, rows=0)
register_kind("trend", TrendPayload, rows=400)


def shape_of(schema: type[BaseModel], depth: int = 0) -> str:
    """A schema as the object literal that satisfies it."""
    fields = [
        f"{name}: {_type_name(info.annotation, depth)}"
        for name, info in schema.model_fields.items()
    ]
    return "{" + ", ".join(fields) + "}"


def _type_name(annotation: Any, depth: int) -> str:
    origin = get_origin(annotation)
    if origin is list:
        (inner,) = get_args(annotation)
        return f"[{_type_name(inner, depth + 1)}, ...]"
    if origin in (Union, UnionType):
        return "|".join(_type_name(arg, depth) for arg in get_args(annotation))
    if origin is Literal:
        return "|".join(str(arg) for arg in get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return shape_of(annotation, depth + 1) if depth < 2 else annotation.__name__
    if annotation is type(None):
        return "null"
    return getattr(annotation, "__name__", str(annotation))


def kinds_help() -> str:
    """Every kind and its payload, derived from the schemas: a hand-kept
    list is one a new kind silently falls out of."""
    lines = []
    for name in kind_names():
        kind = _KINDS[name]
        cap = f" (max {kind.rows} rows)" if kind.rows else ""
        lines.append(f"- {name}{cap}: {shape_of(kind.schema)}")
    return "\n".join(lines)


async def stored_card(card_id: str) -> ChatCard | None:
    """A drawn card by id, read in a session of its own that is closed on
    return: SQLite sessions here BEGIN IMMEDIATE, so a caller holding one
    open while it checks the conversation locked against itself."""
    async with get_async_session() as session:
        return await session.get(ChatCard, card_id)


# --- the turn ----------------------------------------------------------------

_staged: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "drawn_cards", default=None
)


@contextmanager
def card_stage() -> Iterator[list[dict[str, Any]]]:
    """The turn wrapper: ``draw_card`` stages a marker per card here, and
    the stream moves them onto the script that drew them."""
    staged: list[dict[str, Any]] = []
    token = _staged.set(staged)
    try:
        yield staged
    finally:
        _staged.reset(token)


def attach_cards(trace: list[dict[str, Any]], drawn: list[dict[str, Any]]) -> None:
    """Move the cards drawn so far onto the trace entry that just finished
    (the ``run_code`` that drew them), emptying ``drawn``."""
    if not drawn or not trace:
        return
    entry = trace[-1]
    held = entry.get("component")
    markers = held if isinstance(held, list) else [held] if held else []
    entry["component"] = [*markers, *drawn]
    drawn.clear()


async def draw_card(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Draw a card under your answer. Compute the data first, then call this
    from your script with the kind and the data - it displays what you give
    it and computes nothing itself.

    Pass data only: layout and colours belong to the kind. Money is integer
    cents (unit "money", the default); use unit "count" for anything else.
    Keep a card to what it shows and say the rest in prose - a card is a
    top-10, not a dump - and say what it shows in your answer too, so the
    reply still reads to someone who cannot see it.

    Returns {'kind', 'id'}, or {'error'} saying what to fix.

    Kinds, and the payload each takes:
    """
    staged = _staged.get()
    conversation_id = current_conversation_id.get()
    if staged is None or not conversation_id:
        return {"error": "No conversation turn is active; nothing was drawn."}
    declared = lookup((kind or "").strip())
    if declared is None:
        offered = ", ".join(kind_names())
        return {"error": f"Unknown card kind {kind!r}. Available kinds: {offered}."}
    try:
        data = declared.schema.model_validate(payload)
    except ValidationError as exc:
        # Every fault at once, and the shape that fits: one at a time is
        # how a wrong guess becomes four round trips.
        faults = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ())) or 'payload'} "
            f"{err.get('msg', 'is invalid')}"
            for err in exc.errors()[:4]
        )
        return {
            "error": f"{declared.name} card: {faults}. "
            f"It takes {shape_of(declared.schema)}."
        }
    held = len(getattr(data, "rows", None) or [])
    if declared.rows and held > declared.rows:
        return {
            "error": f"{declared.name} card takes at most {declared.rows} rows, "
            f"got {held}. Narrow it and say the rest in prose."
        }
    card = ChatCard(
        conversation_id=conversation_id,
        kind=declared.name,
        payload=data.model_dump(mode="json"),
    )
    async with get_async_session() as session:
        session.add(card)
    staged.append({"kind": MARKER, "id": card.id})
    return {"kind": declared.name, "id": card.id}


# The model reads the docstring as the tool's description; appended at
# import, before any wrapping copies it.
draw_card.__doc__ = (draw_card.__doc__ or "") + kinds_help()

register_tool(
    "draw_card",
    draw_card,
    description="Draw a chart, ranking, comparison or table under the answer",
    replace=True,
)
