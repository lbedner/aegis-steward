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

from app.services.ai.domains.chat.cards import MARKER

CARD_TOOLS = ("propose", "propose_many", "pending")


def components(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``{kind, id}`` for every card the trace carries: its compact
    markers (one per card), else the parsed result of a pre-marker
    trace, else the identity salvaged from a clipped one."""
    found: list[dict[str, Any]] = []
    for entry in trace:
        held = entry.get("component")
        for marker in held if isinstance(held, list) else []:
            if isinstance(marker, dict) and marker.get("kind") == MARKER:
                found.append({"kind": MARKER, "id": marker.get("id")})
        if entry.get("tool") not in CARD_TOOLS:
            continue
        for data in _card_data(entry):
            if data.get("batch_id"):
                found.append({"kind": "pending_change_batch", "id": data["batch_id"]})
            elif data.get("pending_change_id"):
                found.append(
                    {"kind": "pending_change", "id": data["pending_change_id"]}
                )
    return found


def _card_data(entry: dict[str, Any]) -> list[dict[str, Any]]:
    marker = entry.get("component")
    markers = marker if isinstance(marker, list) else [marker]
    found = [
        m
        for m in markers
        if isinstance(m, dict) and m.get("kind") not in (None, MARKER)
    ]
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
    for key in ("batch_id", "pending_change_id"):
        m = re.search(rf'"{key}":\s*"?([^",}}]*)"?', clipped)
        if m:
            fields[key] = m.group(1)
    return fields or None
