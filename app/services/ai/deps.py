"""The one AI service instance, and where everything asks for it.

It used to be a module-level name in the API router, which meant every
other caller reached into ``app.components.backend`` for it: the chat
route, the conversations endpoint, and the health check - which is a
SERVICE, so it inverted the layering CLAUDE.md sets out and pulled
FastAPI in behind it just to read conversation stats.

One instance is the point: the health check has to see the same
conversation state the API is serving from, which is why it went
looking in the router in the first place. Here, both can have it
without one importing the other's layer. Mirrors
``app/services/finance/deps.py``.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.ai.service import AIService

ai_service = AIService(settings)
