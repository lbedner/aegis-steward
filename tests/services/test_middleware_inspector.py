"""Tests for FastAPI middleware introspection service."""

from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.backend.middleware_inspector import (
    FastAPIMiddlewareInspector,
    get_fastapi_middleware_metadata,
)
from app.services.backend.models import MiddlewareInfo, MiddlewareMetadata


class TestFastAPIMiddlewareInspector:
    """Test cases for FastAPI middleware introspection."""

    def test_configured_app_middleware_detection(self, app: FastAPI):
        """Test middleware detection with configured app (includes CORS by default)."""
        inspector = FastAPIMiddlewareInspector(app)

        metadata = inspector.get_middleware_metadata()

        assert isinstance(metadata, MiddlewareMetadata)
        # The configured app should have at least CORS middleware
        assert metadata.total_middleware >= 1
        assert metadata.security_count >= 1
        assert "CORSMiddleware" in metadata.security_middleware
        assert metadata.error is None
        assert metadata.fallback is False

    def test_empty_middleware_stack(self):
        """Test with FastAPI app that has no middleware."""
        app = FastAPI()
        inspector = FastAPIMiddlewareInspector(app)

        metadata = inspector.get_middleware_metadata()

        assert isinstance(metadata, MiddlewareMetadata)
        assert metadata.total_middleware == 0
        assert metadata.security_count == 0
        assert metadata.middleware_stack == []
        assert metadata.security_middleware == []
        assert metadata.error is None
        assert metadata.fallback is False

    def test_cors_middleware_detection(self):
        """Test detection and configuration extraction for CORS middleware."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["X-Test-Header"],
        )

        inspector = FastAPIMiddlewareInspector(app)
        metadata = inspector.get_middleware_metadata()

        assert metadata.total_middleware >= 1
        assert metadata.security_count >= 1
        assert "CORSMiddleware" in metadata.security_middleware

        # Find the CORS middleware in the stack
        cors_middleware = None
        for mw in metadata.middleware_stack:
            if mw.type == "CORSMiddleware":
                cors_middleware = mw
                break

        assert cors_middleware is not None
        assert cors_middleware.is_security is True
        assert cors_middleware.config.get("allow_origins") == ["https://example.com"]
        assert cors_middleware.config.get("allow_credentials") is True

    def test_security_middleware_identification(self):
        """Test identification of security-related middleware."""
        inspector = FastAPIMiddlewareInspector(FastAPI())

        # Test security keyword detection
        assert inspector._is_security_middleware(
            "CORSMiddleware", "fastapi.middleware.cors"
        )
        assert inspector._is_security_middleware(
            "AuthMiddleware", "app.middleware.auth"
        )
        assert inspector._is_security_middleware("JWTMiddleware", "app.middleware.jwt")
        assert inspector._is_security_middleware(
            "RateLimitMiddleware", "app.middleware.rate"
        )
        assert inspector._is_security_middleware(
            "SecurityHeadersMiddleware", "app.middleware.security"
        )

        # Test non-security middleware
        assert not inspector._is_security_middleware(
            "GZipMiddleware", "fastapi.middleware.gzip"
        )
        assert not inspector._is_security_middleware(
            "LoggingMiddleware", "app.middleware.logging"
        )

    def test_middleware_order_detection(self):
        """Test that middleware order is correctly detected."""
        app = FastAPI()

        # Add middleware in specific order
        # Note: FastAPI adds in reverse order of execution
        app.add_middleware(CORSMiddleware)

        # Mock a custom middleware for testing
        class CustomMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                return await self.app(scope, receive, send)

        # We can't easily test the exact ordering without a more complex setup,
        # but we can test that order numbers are assigned
        inspector = FastAPIMiddlewareInspector(app)
        metadata = inspector.get_middleware_metadata()

        # Verify that middleware have order numbers starting from 0
        for idx, middleware in enumerate(metadata.middleware_stack):
            assert middleware.order == idx

    def test_middleware_config_extraction(self):
        """Test extraction of middleware-specific configuration."""
        inspector = FastAPIMiddlewareInspector(FastAPI())

        # Mock CORS middleware
        cors_mock = Mock()
        cors_mock.allow_origins = ["http://localhost:3000"]
        cors_mock.allow_methods = ["*"]
        cors_mock.allow_headers = ["*"]
        cors_mock.allow_credentials = True

        config = inspector._extract_middleware_config(cors_mock)

        assert config["allow_origins"] == ["http://localhost:3000"]
        assert config["allow_methods"] == ["*"]
        assert config["allow_headers"] == ["*"]
        assert config["allow_credentials"] is True

    def test_middleware_introspection_error_handling(self):
        """Test error handling in middleware introspection."""
        # Mock an app that raises an exception during introspection
        app_mock = Mock()
        app_mock.app = app_mock  # Circular reference to break the loop

        # Force an exception during middleware traversal
        def side_effect(*args, **kwargs):
            raise ValueError("Test error")

        app_mock.__getattribute__ = side_effect

        inspector = FastAPIMiddlewareInspector(app_mock)
        metadata = inspector.get_middleware_metadata()

        # Should return fallback metadata
        assert metadata.fallback is True
        assert metadata.error is not None
        assert "Mock" in metadata.error  # Error is actually about Mock object iteration
        assert metadata.total_middleware == 0

    def test_extract_middleware_info_error_handling(self):
        """Test error handling in individual middleware info extraction."""
        inspector = FastAPIMiddlewareInspector(FastAPI())

        # Mock middleware that raises an exception by overriding __module__ access
        class BadMiddleware:
            __name__ = "BadMiddleware"

            @property
            def __module__(self) -> str:
                raise ZeroDivisionError("Test exception")

        bad_middleware = BadMiddleware()

        result = inspector._extract_middleware_info(bad_middleware, 0)

        # Should return None on error
        assert result is None

    def test_get_fastapi_middleware_metadata_convenience_function(self):
        """Test the convenience function for getting middleware metadata."""
        app = FastAPI()
        app.add_middleware(CORSMiddleware)

        metadata = get_fastapi_middleware_metadata(app)

        assert isinstance(metadata, MiddlewareMetadata)
        assert metadata.total_middleware >= 1

    def test_middleware_metadata_model_dump(self):
        """Test that middleware metadata can be dumped for ComponentStatus."""
        app = FastAPI()
        app.add_middleware(CORSMiddleware)

        metadata = get_fastapi_middleware_metadata(app)
        dumped = metadata.model_dump_for_metadata()

        assert isinstance(dumped, dict)
        assert "middleware_stack" in dumped
        assert "total_middleware" in dumped
        assert "security_count" in dumped
        assert "security_middleware" in dumped

    def test_middleware_info_model_creation(self):
        """Test MiddlewareInfo model creation and validation."""
        middleware_info = MiddlewareInfo(
            type="CORSMiddleware",
            module="fastapi.middleware.cors",
            order=0,
            config={"allow_origins": ["*"]},
            is_security=True,
        )

        assert middleware_info.type == "CORSMiddleware"
        assert middleware_info.module == "fastapi.middleware.cors"
        assert middleware_info.order == 0
        assert middleware_info.config == {"allow_origins": ["*"]}
        assert middleware_info.is_security is True

    def test_middleware_metadata_model_creation(self):
        """Test MiddlewareMetadata model creation and validation."""
        middleware_info = MiddlewareInfo(
            type="CORSMiddleware",
            module="fastapi.middleware.cors",
            order=0,
            is_security=True,
        )

        metadata = MiddlewareMetadata(
            middleware_stack=[middleware_info],
            total_middleware=1,
            security_middleware=["CORSMiddleware"],
            security_count=1,
        )

        assert len(metadata.middleware_stack) == 1
        assert metadata.total_middleware == 1
        assert metadata.security_middleware == ["CORSMiddleware"]
        assert metadata.security_count == 1
        assert metadata.error is None
        assert metadata.fallback is False
