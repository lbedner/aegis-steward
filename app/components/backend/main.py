from fastapi import FastAPI

from app.components.backend.api.routing import include_routers
from app.components.backend.hooks import backend_hooks

# Store the configured FastAPI app instance for introspection
_configured_app: FastAPI | None = None


def create_backend_app(app: FastAPI) -> FastAPI:
    """Configure FastAPI app with all backend concerns"""
    global _configured_app

    # Store the app instance for later introspection
    _configured_app = app

    # Auto-discover and register middleware
    backend_hooks.discover_and_register_middleware(app)

    # Include all routes
    include_routers(app)

    return app


def get_configured_app() -> FastAPI | None:
    """
    Get the configured backend FastAPI app instance.
    Returns:
        The configured FastAPI app instance, or None if not yet configured.
    """
    return _configured_app
