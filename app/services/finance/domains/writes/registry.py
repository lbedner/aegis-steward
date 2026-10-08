"""The change-type registry: what the queue knows how to execute.

One entry per mutation kind. FW-06..09 grow by REGISTERING here - a
payload contract, an executor, a describer - never by adding queue
machinery. The payload model validates at propose time (a card the user
cannot safely approve should never exist) and again at approve time
(the world may have moved between the two).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow


@dataclass(frozen=True)
class ChangeExecutor:
    """One kind of proposable mutation.

    ``execute`` runs the REAL service mutation and returns an audit
    summary; ``describe`` resolves the payload's ids into the typed
    card rows the confirmation surface renders - system truth from the
    database, never model-authored copy. Rows are ``ChangeDisplayRow``
    end to end; they become plain dicts only at the freeze into the
    audit column and at the tool-result boundary.
    """

    change_type: str
    title: str
    payload_model: type[BaseModel]
    execute: Callable[[AsyncSession, Any, int | None], Awaitable[dict[str, Any]]]
    describe: Callable[
        [AsyncSession, Any, int | None], Awaitable[list[ChangeDisplayRow]]
    ]
    # Whether the person answering the card may put their own words on
    # it first (``queue.revise``). A card off a From header says "Optum"
    # because that is the domain; the reader knows it is Optum Financial.
    # Off by default: a categorize is a yes/no, not a form.
    editable: bool = False
    # Work that needs the change COMMITTED first, run with its result
    # once the approval lands (a filed photo's reading goes to the
    # worker, which must find the new document row). Never fails an
    # approval that already landed.
    after_commit: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    # For an editable type, the fields that are a choice rather than
    # free text: ``{"kind": PARTY_KINDS}`` draws a select.
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # For an editable type, the only fields that may be edited, where not
    # every text field is the reader's to change: a check card's scans
    # and row are the card's own, its payee is not (#420). Empty: every
    # text field.
    edits: tuple[str, ...] = ()
    # Where the pictures a card shows live (storage keys, in the order its
    # display marks rows ``scan``): a check's front, a photo, a combined
    # document's pages (#439). None for a type that pictures nothing.
    scan: Callable[[AsyncSession, Any, int | None], Awaitable[list[str]]] | None = None


_EXECUTORS: dict[str, ChangeExecutor] = {}


def register(executor: ChangeExecutor) -> ChangeExecutor:
    """Add one change type. Double registration is a wiring bug, not a
    merge - two executors silently fighting over one type is exactly
    the ambiguity a confirmation queue cannot carry."""
    if executor.change_type in _EXECUTORS:
        raise ValueError(f"change type {executor.change_type!r} already registered")
    _EXECUTORS[executor.change_type] = executor
    return executor


def executor_for(change_type: str) -> ChangeExecutor:
    try:
        return _EXECUTORS[change_type]
    except KeyError:
        raise ValueError(f"unknown change type: {change_type!r}") from None


def current_payload(
    executor: ChangeExecutor, payload: dict[str, Any]
) -> dict[str, Any]:
    """A stored card read through its contract, so one filed in an older
    shape (a check's payee as a cited reading) edits as today's does."""
    return executor.payload_model(**payload).model_dump(mode="json")


def editable_fields(executor: ChangeExecutor) -> list[str]:
    """The fields a person - or Illiana - may change on a pending card of
    this type: ``edits`` where it names them, else every text field."""
    if not executor.editable:
        return []
    texts = [
        name
        for name, info in executor.payload_model.model_fields.items()
        if info.annotation in (str, str | None)
    ]
    return (
        [name for name in texts if name in executor.edits] if executor.edits else texts
    )


def registered_change_types() -> tuple[str, ...]:
    return tuple(sorted(_EXECUTORS))
