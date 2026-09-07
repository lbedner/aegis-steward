"""
FastAPI Middleware Introspection Service

Utility functions for extracting comprehensive middleware information from FastAPI
applications for security auditing and dashboard display.
"""

from typing import Any

import fastapi

__all__ = ["MiddlewareMetadata", "FastAPIMiddlewareInspector"]

from app.core.log import logger
from app.services.backend.models import MiddlewareInfo, MiddlewareMetadata


class FastAPIMiddlewareInspector:
    """Utility class for extracting middleware information from FastAPI apps."""

    def __init__(self, app: fastapi.FastAPI) -> None:
        """Initialize with FastAPI application instance."""
        self.app = app

    def get_middleware_metadata(self) -> MiddlewareMetadata:
        """
        Extract comprehensive middleware information from FastAPI app.

        Returns:
            MiddlewareMetadata containing middleware stack, security info, and stats
        """
        try:
            middleware_stack = []

            # Inspect middleware stack using FastAPI's user_middleware attribute
            if hasattr(self.app, "user_middleware"):
                for idx, middleware_item in enumerate(self.app.user_middleware):
                    middleware_info = (
                        self._extract_middleware_info_from_middleware_item(
                            middleware_item, idx
                        )
                    )
                    if middleware_info:
                        middleware_stack.append(middleware_info)

            # Calculate statistics
            total_middleware = len(middleware_stack)
            security_middleware = self._identify_security_middleware(middleware_stack)
            security_count = len(security_middleware)

            return MiddlewareMetadata(
                middleware_stack=middleware_stack,
                total_middleware=total_middleware,
                security_middleware=security_middleware,
                security_count=security_count,
            )

        except Exception as e:
            logger.error(f"Failed to extract middleware metadata: {e}")
            return self._get_fallback_metadata(str(e))

    def _extract_middleware_info_from_middleware_item(
        self, middleware_item: Any, order: int
    ) -> MiddlewareInfo | None:
        """Extract information from a FastAPI Middleware item."""
        try:
            # FastAPI stores middleware as Middleware(cls, **kwargs) objects
            middleware_cls = middleware_item.cls
            middleware_kwargs = (
                middleware_item.kwargs if hasattr(middleware_item, "kwargs") else {}
            )

            middleware_type = middleware_cls.__name__
            middleware_module = middleware_cls.__module__

            # Use kwargs as configuration
            config = dict(middleware_kwargs)

            # Determine if this is security-related middleware
            is_security = self._is_security_middleware(
                middleware_type, middleware_module
            )

            # Extract docstring (full text)
            description = None
            if middleware_cls.__doc__:
                description = middleware_cls.__doc__.strip()

            return MiddlewareInfo(
                type=middleware_type,
                module=middleware_module,
                order=order,
                config=config,
                is_security=is_security,
                description=description,
            )
        except Exception as e:
            logger.warning(
                f"Failed to extract middleware info for {middleware_item}: {e}"
            )
            return None

    def _extract_middleware_info(
        self, middleware: Any, order: int
    ) -> MiddlewareInfo | None:
        """Extract information from a single middleware instance (legacy method).

        This method is kept for backward compatibility and is still used in tests.
        For production use, _extract_middleware_info_from_middleware_item is preferred.
        """
        try:
            middleware_type = type(middleware).__name__
            middleware_module = type(middleware).__module__

            # Extract middleware-specific configuration
            config = self._extract_middleware_config(middleware)

            # Determine if this is security-related middleware
            is_security = self._is_security_middleware(
                middleware_type, middleware_module
            )

            return MiddlewareInfo(
                type=middleware_type,
                module=middleware_module,
                order=order,
                config=config,
                is_security=is_security,
            )
        except Exception as e:
            logger.warning(
                f"Failed to extract middleware info for {type(middleware)}: {e}"
            )
            return None

    def _extract_middleware_config(self, middleware: Any) -> dict[str, Any]:
        """Extract configuration from middleware where possible."""
        config = {}

        try:
            # CORS Middleware configuration
            if hasattr(middleware, "allow_origins"):
                config.update(
                    {
                        "allow_origins": getattr(middleware, "allow_origins", []),
                        "allow_methods": getattr(middleware, "allow_methods", []),
                        "allow_headers": getattr(middleware, "allow_headers", []),
                        "allow_credentials": getattr(
                            middleware, "allow_credentials", False
                        ),
                    }
                )

            # JWT/Auth middleware configuration
            elif hasattr(middleware, "algorithm"):
                config.update(
                    {
                        "algorithm": getattr(middleware, "algorithm", "Unknown"),
                    }
                )

            # Rate limiting middleware
            elif hasattr(middleware, "calls") and hasattr(middleware, "period"):
                config.update(
                    {
                        "calls": getattr(middleware, "calls", 0),
                        "period": getattr(middleware, "period", 0),
                    }
                )

            # Security headers middleware
            elif hasattr(middleware, "csp"):
                config.update(
                    {
                        "csp": getattr(middleware, "csp", {}),
                    }
                )

        except Exception as e:
            logger.debug(f"Could not extract config from {type(middleware)}: {e}")

        return config

    def _is_security_middleware(
        self, middleware_type: str, middleware_module: str
    ) -> bool:
        """Determine if middleware is security-related based on type and module."""
        security_keywords = [
            "cors",
            "auth",
            "jwt",
            "rate",
            "security",
            "limit",
            "csrf",
            "xss",
        ]
        security_modules = ["starlette.middleware.cors", "fastapi.middleware.cors"]

        # Check type name for security keywords
        type_lower = middleware_type.lower()
        if any(keyword in type_lower for keyword in security_keywords):
            return True

        # Check module name for security patterns
        module_lower = middleware_module.lower()
        if any(keyword in module_lower for keyword in security_keywords):
            return True

        # Check for known security middleware modules
        if middleware_module in security_modules:
            return True

        return False

    def _identify_security_middleware(self, stack: list[MiddlewareInfo]) -> list[str]:
        """Identify security-related middleware types."""
        return [mw.type for mw in stack if mw.is_security]

    def _get_fallback_metadata(self, error: str) -> MiddlewareMetadata:
        """Return fallback metadata when introspection fails."""
        return MiddlewareMetadata(
            middleware_stack=[],
            total_middleware=0,
            security_middleware=[],
            security_count=0,
            error=error,
            fallback=True,
        )


def get_fastapi_middleware_metadata(app: fastapi.FastAPI) -> MiddlewareMetadata:
    """
    Convenience function to get middleware metadata from FastAPI app.

    Args:
        app: FastAPI application instance
    Returns:
        MiddlewareMetadata containing middleware stack and security information
    """
    inspector = FastAPIMiddlewareInspector(app)
    return inspector.get_middleware_metadata()
