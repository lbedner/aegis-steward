"""Per-request context builders: health, usage, catalog.

Each ``_build_*`` method assembles one "what's true right now" block that
the prompt layer injects into the system prompt. Failures degrade to
``None`` rather than failing the chat turn.
"""

from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.core.db import engine
from app.core.log import logger
from app.services.ai.domains.chat.health_context import HealthContext
from app.services.ai.domains.chat.llm_catalog_context import get_llm_catalog_context
from app.services.ai.domains.chat.usage_context import UsageContext
from app.services.ai.service.usage import UsageMixin

# Module-level import for the forward reference used in ``_parse_http_health``
# (``SystemStatus``). ``ty`` / pyright can't resolve string-forward refs when
# the symbol is only imported lazily inside a method body.
from app.services.system.models import SystemStatus as SystemStatus


class ContextsMixin(UsageMixin):
    """Builders for the dynamic context blocks injected per request."""

    async def _build_health_context(
        self,
    ) -> tuple[HealthContext | None, str | None]:
        """
        Build health context by fetching current system status.

        Tries HTTP endpoint first (has all registered components from FastAPI),
        falls back to local get_system_status() if server not running.

        Returns:
            Tuple of (HealthContext, warning_message):
            - HealthContext with system status, or None if complete failure
            - Warning message if server unreachable, otherwise None
        """
        # Try HTTP endpoint first - has all registered components
        try:
            import httpx

            base_url = getattr(self.settings, "API_BASE_URL", "http://localhost:8000")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/health/detailed")
                if resp.status_code == 200:
                    data = resp.json()
                    status = self._parse_http_health(data)
                    return HealthContext(status=status), None  # Success, no warning
        except Exception as e:
            # Server not running or other error, falling back to local
            logger.debug(
                "ai_service.http_health_check_failed",
                exc_type=type(e).__name__,
                exc_message=str(e),
            )

        # HTTP failed - note this for the prompt
        warning = (
            "Backend webserver is not running. I can't check full system health. "
            "Start it with the run command to get complete status."
        )

        # Fallback to local (limited components without FastAPI startup)
        try:
            from app.services.system.health import get_system_status

            status = await get_system_status()
            return HealthContext(status=status), warning
        except Exception as e:
            logger.warning(f"Failed to build health context: {e}")
            return None, warning

    def _parse_http_health(self, data: dict[str, Any]) -> SystemStatus:
        """Parse HTTP health response into SystemStatus model."""
        # ``SystemStatus`` is module-level imported above; ``ComponentStatus``
        # is only used inside this method so we keep it local.
        from app.services.system.models import ComponentStatus

        def parse_component(comp: dict[str, Any]) -> ComponentStatus:
            # ``healthy`` is a ``@computed_field`` on ``ComponentStatus`` derived
            # from ``status``, not a constructor input — drop the kwarg and
            # let the property compute itself from the status enum value.
            return ComponentStatus(
                name=comp["name"],
                status=comp["status"],
                message=comp["message"],
                response_time_ms=comp.get("response_time_ms"),
                metadata=comp.get("metadata", {}),
                sub_components={
                    k: parse_component(v)
                    for k, v in comp.get("sub_components", {}).items()
                },
            )

        return SystemStatus(
            overall_healthy=data["healthy"],
            components={k: parse_component(v) for k, v in data["components"].items()},
            timestamp=datetime.now(),
        )

    def _build_usage_context(self) -> UsageContext | None:
        """
        Build usage context from AI service statistics.

        Gives Illiana self-awareness about her own usage patterns,
        token consumption, costs, and success rates.

        Returns:
            UsageContext with current stats, or None if unavailable
        """

        try:
            stats = self.get_usage_stats(recent_limit=5)

            # Find top model by request count
            top_model = None
            top_pct = 0.0
            models = stats.get("models", [])
            if models:
                top = max(models, key=lambda m: m.get("requests", 0))
                top_model = top.get("model_title")
                top_pct = top.get("percentage", 0.0)

            return UsageContext(
                total_tokens=stats.get("total_tokens", 0),
                total_requests=stats.get("total_requests", 0),
                total_cost=stats.get("total_cost", 0.0),
                success_rate=stats.get("success_rate", 100.0),
                top_model=top_model,
                top_model_percentage=top_pct,
                recent_requests=len(stats.get("recent_activity", [])),
            )
        except Exception as e:
            logger.debug(f"Failed to build usage context: {e}")
            return None

    def _build_catalog_context(self) -> str | None:
        """
        Build LLM catalog context from database.

        Provides Illiana with awareness of available LLM models,
        their pricing, and capabilities.

        Returns:
            Formatted catalog context string, or None if unavailable
        """

        try:
            with Session(engine) as session:
                return get_llm_catalog_context(session)
        except Exception as e:
            logger.debug(f"Failed to build catalog context: {e}")
            return None
