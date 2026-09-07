"""
FastAPI Route Introspection Service

Utility functions for extracting comprehensive route information from FastAPI
applications for health monitoring and dashboard display.
"""

import fastapi
from fastapi.routing import APIRoute

__all__ = ["RouteMetadata", "FastAPIRouteInspector"]

from app.core.log import logger
from app.core.route_auth import route_requires_auth
from app.services.backend.models import RouteInfo, RouteMetadata


class FastAPIRouteInspector:
    """Utility class for extracting route information from FastAPI apps."""

    def __init__(self, app: fastapi.FastAPI) -> None:
        """Initialize with FastAPI application instance."""
        self.app = app

    def get_routes_metadata(self) -> RouteMetadata:
        """
        Extract comprehensive route information from FastAPI app.

        Returns:
            RouteMetadata containing route data, statistics, and groupings
        """
        try:
            routes_data = []

            # Extract route information
            for route in self.app.routes:
                if isinstance(route, APIRoute):
                    route_info = RouteInfo(
                        path=route.path,
                        methods=sorted(list(route.methods)),
                        name=route.name,
                        summary=getattr(route, "summary", "") or None,
                        description=getattr(route, "description", "") or None,
                        tags=getattr(route, "tags", []) or [],
                        deprecated=bool(getattr(route, "deprecated", False)),
                        path_params=self._extract_path_params(route.path),
                        dependencies=self._extract_dependency_names(route),
                        requires_auth=route_requires_auth(route),
                        response_model=(
                            str(route.response_model) if route.response_model else None
                        ),
                    )
                    routes_data.append(route_info)

            # Calculate statistics
            total_routes = len(routes_data)
            total_endpoints = sum(len(r.methods) for r in routes_data)
            method_counts = self._count_methods(routes_data)
            route_groups = self._group_routes_by_prefix(routes_data)
            tag_groups = self._group_routes_by_tags(routes_data)

            return RouteMetadata(
                routes=routes_data,
                total_routes=total_routes,
                total_endpoints=total_endpoints,
                method_counts=method_counts,
                route_groups=route_groups,
                tag_groups=tag_groups,
                has_docs=any(
                    "/docs" in r.path or "/redoc" in r.path for r in routes_data
                ),
                has_health=any("/health" in r.path for r in routes_data),
                deprecated_count=sum(1 for r in routes_data if r.deprecated),
            )

        except Exception as e:
            logger.error(f"Failed to extract route metadata: {e}")
            return self._get_fallback_metadata(str(e))

    def _extract_path_params(self, path: str) -> list[str]:
        """Extract path parameters from route path."""
        import re

        return re.findall(r"\{([^}]+)\}", path)

    def _extract_dependency_names(self, route: APIRoute) -> list[str]:
        """Extract dependency names from router and parameter dependencies."""
        import inspect

        from fastapi.params import Depends

        dependency_names = []

        # Extract router-level dependencies
        if route.dependencies:
            for dep in route.dependencies:
                if dep.dependency and hasattr(dep.dependency, "__name__"):
                    dep_name = dep.dependency.__name__
                    dependency_names.append(dep_name)

        # Extract parameter-level dependencies from function signature
        if route.endpoint:
            try:
                sig = inspect.signature(route.endpoint)
                for param_name, param in sig.parameters.items():
                    # Check if parameter has a default that's a Depends instance
                    if param.default != inspect.Parameter.empty:
                        if isinstance(param.default, Depends):
                            if param.default.dependency:
                                # Try to get the name from the dependency
                                dep = param.default.dependency
                                if hasattr(dep, "__name__"):
                                    dep_name = dep.__name__
                                else:
                                    # For objects use class name
                                    dep_cls = param.default.dependency.__class__
                                    dep_name = dep_cls.__name__

                                # Avoid duplicates
                                if dep_name not in dependency_names:
                                    dependency_names.append(dep_name)
            except Exception:
                # If we can't inspect the signature, just skip parameter deps
                pass

        return dependency_names

    def _count_methods(self, routes: list[RouteInfo]) -> dict[str, int]:
        """Count routes by HTTP method."""
        method_counts: dict[str, int] = {}
        for route in routes:
            for method in route.methods:
                if method != "HEAD":  # Skip HEAD methods as they're automatic
                    method_counts[method] = method_counts.get(method, 0) + 1
        return method_counts

    def _group_routes_by_prefix(self, routes: list[RouteInfo]) -> dict[str, int]:
        """Group routes by path prefix for organization."""
        groups: dict[str, int] = {}
        for route in routes:
            path_parts = route.path.strip("/").split("/")
            prefix = path_parts[0] if path_parts[0] else "root"

            # Special handling for common patterns
            if prefix in ["health", "docs", "redoc", "openapi.json"]:
                groups[prefix] = groups.get(prefix, 0) + 1
            elif prefix == "api" and len(path_parts) > 2:
                # Skip API version prefix, group by actual resource
                # /api/v1/users -> group as "users"
                resource = path_parts[2]
                groups[resource] = groups.get(resource, 0) + 1
            elif prefix.startswith("api"):
                # Fallback for other API patterns
                groups[prefix] = groups.get(prefix, 0) + 1
            else:
                groups[prefix] = groups.get(prefix, 0) + 1

        return groups

    def _group_routes_by_tags(self, routes: list[RouteInfo]) -> dict[str, int]:
        """Group routes by OpenAPI tags."""
        tag_counts: dict[str, int] = {}
        for route in routes:
            if not route.tags:
                tag_counts["untagged"] = tag_counts.get("untagged", 0) + 1
            else:
                for tag in route.tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return tag_counts

    def _get_fallback_metadata(self, error: str) -> RouteMetadata:
        """Return fallback metadata when introspection fails."""
        return RouteMetadata(
            routes=[],
            total_routes=0,
            total_endpoints=0,
            method_counts={},
            route_groups={},
            tag_groups={},
            has_docs=False,
            has_health=False,
            deprecated_count=0,
            error=error,
            fallback=True,
        )


def get_fastapi_route_metadata(app: fastapi.FastAPI) -> RouteMetadata:
    """
    Convenience function to get route metadata from FastAPI app.

    Args:
        app: FastAPI application instance
    Returns:
        RouteMetadata containing route metadata and statistics
    """
    inspector = FastAPIRouteInspector(app)
    return inspector.get_routes_metadata()
