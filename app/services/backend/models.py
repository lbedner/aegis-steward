"""
Backend service models using Pydantic for type safety and validation.

These models define the structure for route introspection data and middleware
detection data for backend service metadata.
"""

from typing import Any

from pydantic import BaseModel


class RouteInfo(BaseModel):
    """Information about a single FastAPI route."""

    path: str
    methods: list[str]
    name: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] = []
    deprecated: bool = False
    path_params: list[str] = []
    dependencies: list[str] = []
    requires_auth: bool = False
    response_model: str | None = None


class RouteMetadata(BaseModel):
    """Comprehensive route metadata for FastAPI application."""

    routes: list[RouteInfo]
    total_routes: int
    total_endpoints: int
    method_counts: dict[str, int] = {}
    route_groups: dict[str, int] = {}
    tag_groups: dict[str, int] = {}
    has_docs: bool = False
    has_health: bool = False
    deprecated_count: int = 0
    error: str | None = None
    fallback: bool = False

    def model_dump_for_metadata(self) -> dict[str, Any]:
        """Convert to dict format suitable for ComponentStatus metadata."""
        return self.model_dump(mode="json")


class MiddlewareInfo(BaseModel):
    """Information about a single middleware in the stack."""

    type: str
    module: str
    order: int
    config: dict[str, Any] = {}
    is_security: bool = False
    description: str | None = None


class MiddlewareMetadata(BaseModel):
    """Comprehensive middleware metadata for FastAPI application."""

    middleware_stack: list[MiddlewareInfo]
    total_middleware: int
    security_middleware: list[str] = []
    security_count: int = 0
    error: str | None = None
    fallback: bool = False

    def model_dump_for_metadata(self) -> dict[str, Any]:
        """Convert to dict format suitable for ComponentStatus metadata."""
        return self.model_dump(mode="json")
