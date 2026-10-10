"""The words a chat transcript shows around the model's text.

Pure functions, shared by every surface that renders a conversation (the
web chat page, the Flet panel): the fence balancer that keeps partial
markdown from swallowing what follows an open code block, the trail
labels for tool calls, the attachment gate and marker, and the footer
line under a finished answer.
"""

from __future__ import annotations

import ast
import json
import pprint
import re
from typing import Any

from app.core.formatting import FREE, format_duration_ms

# Rendered while text is still arriving; dropped from the final render.
STREAM_CURSOR = "▌"

# The image types a chat attachment may carry, by upload extension.
_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
# Every image a turn may carry (what a model sees, what the documents
# service reads off a page), and how large one may be.
IMAGE_TYPES = frozenset(_IMAGE_MEDIA_TYPES.values())
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def image_media_type(filename: str) -> str | None:
    """Mime type for an image attachment by extension, None otherwise.

    The gate for the attach picker: anything returning None is refused
    client-side instead of failing in the model call."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _IMAGE_MEDIA_TYPES.get(extension)


# The service's stored-history marker for image parts - the format
# written by ``services/ai/domains/chat/attachments.annotate_attachments``.
_ATTACHMENT_MARKER = re.compile(r"\n\n\[attached \d+ images?: [^\]\n]*\]$")


# The service's stored marker for a lifted paste - the format written by
# ``services/ai/domains/chat/pastes.marker``.
_PASTE_MARKER = re.compile(r"\[pasted text #[0-9a-f]+[^\]]*\]")


def strip_paste_markers(text: str) -> str:
    """What the reader sees instead of a marker: nothing, because the
    paste is drawn beside the message as a chip they can open. The
    MODEL still gets the marker - it is how the text is asked for."""
    return _PASTE_MARKER.sub("", text).strip()


def strip_attachment_marker(text: str) -> str:
    """A replayed message must not carry its old attachment marker: the
    bytes are gone (they ride one turn only), so re-sending the marker
    would claim images the model cannot see."""
    return _ATTACHMENT_MARKER.sub("", text)


def readable(content: str) -> str:
    """A stored message as the reader gets it, on screen or aloud."""
    return strip_paste_markers(strip_attachment_marker(content))


def balance_fences(text: str) -> str:
    """Close an unterminated code fence so partial markdown renders sanely.

    A streaming response frequently pauses inside a ``` block; rendering
    that verbatim makes the markdown control swallow everything after the
    opening fence. An odd fence count gets a synthetic closer appended.
    """
    if text.count("```") % 2 == 1:
        return f"{text}\n```"
    return text


def tool_label(name: str, args: str = "") -> str:
    """One trail line for a tool call: ``ledger(months=3)``.

    A code-mode script call arrives as ``{"code": "<first line>"}`` and
    reads as ``run_code: <first line>`` - what the script was FOR, not
    a parenthesized argument dump.
    """
    if not name:
        return "working..."
    inner = ""
    if args:
        try:
            parsed = json.loads(args)
        except (ValueError, TypeError):
            inner = args
        else:
            if isinstance(parsed, dict) and set(parsed) == {"code"}:
                return f"{name}: {parsed['code']}"
            if isinstance(parsed, dict):
                inner = ", ".join(f"{key}={value}" for key, value in parsed.items())
            elif parsed is not None:
                inner = str(parsed)
    return f"{name}({inner})"


def script_line(code: str) -> str:
    """What a trail shows of a script: its first line that is not a
    comment, clipped; empty when it is all comments."""
    for line in code.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:80]
    return ""


def trace_label(entry: dict[str, Any]) -> str:
    """A persisted trace entry as its trail line (same look as live)."""
    name = str(entry.get("tool", ""))
    code = entry.get("code")
    if isinstance(code, str):
        line = script_line(code)
        return f"{name}: {line}" if line else f"{name}:"
    return tool_label(name, str(entry.get("args", "") or ""))


def _pretty(text: str) -> str:
    """Printed output made readable: each line that parses as a Python
    or JSON literal is re-rendered through pprint (wrapped to the trace
    dialog's width); everything else passes through untouched."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("{", "[", "(")):
            lines.append(line)
            continue
        try:
            value = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            try:
                value = json.loads(stripped)
            except ValueError:
                lines.append(line)
                continue
        lines.append(pprint.pformat(value, width=72, sort_dicts=False))
    return "\n".join(lines)


def trace_output(entry: dict[str, Any]) -> str:
    """The human part of a trace entry's result: the printed output when
    the result parses as a run_code return, the raw text otherwise."""
    result = entry.get("result")
    if not isinstance(result, str) or not result:
        return ""
    try:
        parsed = json.loads(result)
    except ValueError:
        return result
    if isinstance(parsed, dict):
        # run_code results arrive as {"return_value": {"output": ...}}
        # or the flat {"output": ...}; either way show the printed text.
        value = parsed.get("return_value", parsed)
        if isinstance(value, dict) and isinstance(value.get("output"), str):
            return _pretty(value["output"])
        if isinstance(value, dict) or isinstance(value, list):
            return pprint.pformat(value, width=72, sort_dicts=False)
        if value is not None:
            return str(value)
    return _pretty(result)


_FAILURE_MARKS = ("Type error in code", "Runtime error", "Fix the errors")


def trace_failed(entry: dict[str, Any]) -> bool:
    """Whether a trace entry's result is a sandbox failure report - the
    trail marks these so a spiral of failing scripts is visible instead
    of reading like quiet success."""
    result = entry.get("result")
    return isinstance(result, str) and any(m in result for m in _FAILURE_MARKS)


def narration_note(text: str, limit: int = 100) -> str:
    """Pre-tool narration folded into the trail instead of vanishing:
    one clipped line ("" when there was nothing to keep)."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def footer_line(meta: dict[str, Any], *, local: bool = False) -> str:
    """One quiet attribution line for a finished assistant message.

    ``local`` is passed in rather than read off the metadata here: which
    providers run on this machine is an AI-service fact, and core does
    not reach upward for it. Same shape as ``format_price``.
    """
    parts: list[str] = []
    if meta.get("model"):
        parts.append(str(meta["model"]))
    if meta.get("gen_tps"):
        parts.append(f"{meta['gen_tps']} tps")
    # Beside the rate, because they answer different questions: how fast
    # it wrote, and how long you waited for it.
    elapsed = format_duration_ms(meta.get("response_time_ms"))
    if elapsed:
        parts.append(elapsed)
    # A local model is free and says so; a cloud one with no cost
    # recorded says nothing, because a blank admits we do not know what
    # the turn cost and a figure would be invented. Zero renders with
    # its cents for the same reason it does in the picker: trailing
    # zeros are what make it read as a price rather than a value that
    # failed to load.
    cost = meta.get("cost")
    if cost:
        parts.append(f"${cost:.4f}")
    elif local:
        parts.append(FREE)
    return "  ·  ".join(parts)


# What an approval card's status (``card_outcome``) reads as, and its tone,
# in every chat UI (the Flet chat and the htmx one); each UI maps a tone to
# its colours.
CARD_STATUS: dict[str, tuple[str, str]] = {
    "pending": ("Awaiting your approval", "warn"),
    "approved": ("Approved", "ok"),
    "rejected": ("Rejected", "error"),
    "withdrawn": ("Withdrawn", "muted"),
    "expired": ("Expired", "muted"),
}


# How a withdrawal's note starts (``change_queue.withdraw`` writes it).
WITHDRAWN = "Withdrawn"


def proposer_line(proposed_by_agent: str | None) -> str | None:
    """Who filed a card, as every card UI shows it: a card from an
    outside assistant (``mcp:<client>``) must never read as the app's own
    assistant's."""
    return f"Proposed by {proposed_by_agent}" if proposed_by_agent else None


def card_outcome(status: str, note: Any) -> str:
    """A card's status as a person reads it: a rejection the proposing
    agent filed against itself is "withdrawn", not the user saying no."""
    if status == "rejected" and str(note or "").startswith(WITHDRAWN):
        return "withdrawn"
    return status
