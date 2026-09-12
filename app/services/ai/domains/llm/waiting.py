"""Why the answer has not started yet, when the provider can say.

``core.streaming.announce_waiting`` reports that a stream is quiet and
for how long; it deliberately knows nothing about what is producing it.
This is the other half for local inference: Ollama evicts a model after
its ``keep_alive`` expires and reloads it on the next request, which on
a large model is tens of seconds of total silence before the first
token. "Waiting 40s" is honest but alarming; "loading - 40s" is the same
wait with the reason attached, and the reason is the part that tells the
user it will finish.

What comes back is the STATE, never the model name: the composer's chip
already shows the model, and which of the two waits this is - a load
that ends on its own, or a model already working - is the fact that is
nowhere else on screen.

Ollama has no event API - ``/api/ps`` is the only signal, the same one
the dashboard's Activity tab polls - so the question this answers is
narrow on purpose: is the model this turn needs warm right now? A
provider that cannot say returns None and the wait goes out unexplained.
"""

from __future__ import annotations

from app.services.ai.domains.llm.ollama import OllamaClient
from app.services.ai.models import AIProvider


async def waiting_reason(provider: AIProvider, model: str) -> str | None:
    """A short phrase for why nothing has arrived, or None."""
    if provider is not AIProvider.OLLAMA or not model:
        return None
    running = await OllamaClient().fetch_running_models()
    # ``name`` and ``model`` are the two spellings /api/ps returns, and
    # they are the same string today; both are read so a tag written one
    # way in config still matches. There is no ``model_id`` here - that
    # lives on OllamaModel (/api/tags), and reaching for it raised an
    # AttributeError that the explainer's own except swallowed, so the
    # pause went out unexplained and looked like the feature had simply
    # not shipped.
    # The STATE, not the model: the composer's chip already names the
    # model, so repeating it says nothing new, while "is it loading or
    # is it working" is the part that is not on screen anywhere and the
    # part that says how long this is likely to last.
    if any(row.name == model or row.model == model for row in running):
        # Warm and quiet: it has the prompt and has not emitted yet. A
        # 30B at 128k context chews a long one for twenty seconds with
        # nothing to show.
        return "thinking"
    return "loading"
