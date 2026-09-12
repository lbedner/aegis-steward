"""Say how long a stream has been quiet, whatever is producing it.

Between the ``connect`` frame and the first token the chat SSE stream
sent nothing, so a 40-second Ollama model load looked exactly like a
hang: three pulsing dots, and no way to tell waiting from dead.

Not a heartbeat - a heartbeat ticks regardless, and a stream that is
answering has nothing to explain. This is silence, named: it fires only
while nothing is arriving, and says how long that has been true.

It lives in core, over a bare async iterator, because the silence is not
a chat problem. A cold provider, a slow tool, a long-running job step -
anything a browser follows can go quiet, and a helper that only knows
"this went quiet for N seconds" has no opinion about what it is
wrapping.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextvars
from dataclasses import dataclass
import time
from typing import TypeVar

T = TypeVar("T")

# Long enough that an answering stream never trips it, short enough that
# a stalled one is named before the user decides the app is broken.
QUIET_SECONDS = 2.0


@dataclass(frozen=True)
class Waiting:
    """``seconds`` of silence so far - since the LAST item, not since the
    stream opened. A mid-stream tool call reporting the whole turn's age
    would say nothing about whether to keep waiting.

    ``reason`` is whatever the caller's explainer could find out, when it
    can find out anything: "a model is loading" is a different wait from
    "the network is slow", and the one the user can do nothing about is
    still worth naming.
    """

    seconds: int
    reason: str | None = None


async def announce_waiting(
    source: AsyncIterator[T],
    *,
    every: float = QUIET_SECONDS,
    explain: Callable[[], Awaitable[str | None]] | None = None,
) -> AsyncIterator[T | Waiting]:
    """Pass ``source`` through, emitting ``Waiting`` while it is quiet.

    A stream that answers promptly is untouched: the frame exists to
    explain a pause, and no pause means nothing to explain. Whatever the
    source raises is raised here - the browser's error frame depends on
    it arriving, not on being tidied away.

    ``explain`` is asked ONCE per quiet stretch, not once per frame: it
    is the caller's business what a wait means, and whatever it costs to
    find out should not be paid every two seconds. An explainer that
    fails is ignored - a missing reason must never turn a slow answer
    into a failed one.

    The name matches the wire: the SSE event a caller sends for one of
    these is ``waiting``.
    """
    stepping = source.__aiter__()
    # ONE context for every step. Each ``__anext__`` runs in a task so a
    # timeout can cancel the wait without cancelling it, and a task
    # copies the current context - so a fresh task per item gave the
    # source a fresh context per item. An async generator has no context
    # of its own (PEP 568 was deferred): it runs in whichever one calls
    # it, so a ContextVar set while producing one item and reset while
    # producing a later one straddled two copies and raised "Token ...
    # was created in a different Context" mid-answer. Pinning every step
    # to the same context makes the source see exactly what it would
    # have seen being iterated directly.
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    since = time.monotonic()
    while True:
        # Shielded, so the timeout cancels the WAIT and never the item
        # in flight - the token that finally arrives is the one the user
        # is waiting for.
        pending = loop.create_task(stepping.__anext__(), context=context)
        reason: str | None = None
        asked = False
        while True:
            try:
                item = await asyncio.wait_for(asyncio.shield(pending), timeout=every)
            except TimeoutError:
                if explain is not None and not asked:
                    asked = True
                    try:
                        reason = await explain()
                    except Exception:  # noqa: BLE001 - see the docstring
                        reason = None
                yield Waiting(seconds=int(time.monotonic() - since), reason=reason)
                continue
            except StopAsyncIteration:
                return
            break
        since = time.monotonic()
        yield item
