"""What a message's stored trace draws, as ``{kind, id}`` markers.

A tool result carries DATA; presentation is system code selected by kind
from the registry (the ``component`` macro); the model never authors
layout, and an unknown kind degrades to absence. A card carries identity
only and loads its rows from the queue, so it always shows the queue's
truth: the result blob in the trace is clipped, and a resolution made on
the Review page must reach a card already in the transcript.

A card she DRAWS (``draw_card``, #266) rides whatever entry drew it,
usually a ``run_code``, as a ``chat_card`` marker.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.ai.domains.chat.cards import MARKER, markers_of
from app.services.ai.service.trace import CARD_TOOLS, IDENTITY_KEYS, card_identity


def components(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``{kind, id}`` for every card the trace carries: its compact
    markers (one per card), else the parsed result of a pre-marker
    trace, else the identity salvaged from a clipped one.

    A drawn card is one per answer: a redraw (she fixed the first) replaces
    the card before it."""
    found: list[dict[str, Any]] = []
    for entry in trace:
        for marker in markers_of(entry):
            if marker.get("kind") == MARKER:
                found = [f for f in found if f["kind"] != MARKER]
                found.append({"kind": MARKER, "id": marker.get("id")})
        if entry.get("tool") not in CARD_TOOLS:
            continue
        for data in _card_data(entry):
            if named := card_identity(data):
                found.append({"kind": named[0], "id": named[2]})
    return found


def _card_data(entry: dict[str, Any]) -> list[dict[str, Any]]:
    found = [m for m in markers_of(entry) if m.get("kind") not in (None, MARKER)]
    if found or entry.get("tool") == "pending":
        return found
    result = entry.get("result")
    if not isinstance(result, str):
        return []
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        parsed = _salvage_identity(result)
    return [parsed] if isinstance(parsed, dict) else []


def _salvage_identity(clipped: str) -> dict[str, Any] | None:
    """Identity fields lead a proposal's result, so they survive the
    display clip that truncates the rest to invalid JSON."""
    fields: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        m = re.search(rf'"{key}":\s*"?([^",}}]*)"?', clipped)
        if m:
            fields[key] = m.group(1)
    return fields or None
