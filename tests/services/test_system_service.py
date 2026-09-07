"""Test system monitoring functions."""

import pytest

from app.services.system import (
    ComponentStatus,
    get_system_status,
    is_system_healthy,
    register_health_check,
)


class TestSystemService:
    """Test the system monitoring functions."""

    @pytest.mark.asyncio
    async def test_component_status_creation(self) -> None:
        """Test component status Pydantic model."""
        status = ComponentStatus(
            name="test_component",
            message="All good",
            response_time_ms=100.0,
            metadata={"version": "1.0"},
        )

        assert status.name == "test_component"
        assert status.healthy is True
        assert status.message == "All good"
        assert status.response_time_ms == 100.0
        assert status.metadata == {"version": "1.0"}

    @pytest.mark.asyncio
    async def test_system_status_properties(self) -> None:
        """Test system status Pydantic model properties."""
        status = await get_system_status()

        assert isinstance(status.overall_healthy, bool)
        assert len(status.components) >= 1
        assert isinstance(status.healthy_components, list)
        assert isinstance(status.unhealthy_components, list)
        assert isinstance(status.health_percentage, float)

    @pytest.mark.asyncio
    async def test_system_health_checks(self) -> None:
        """Test basic health checks functionality."""
        status = await get_system_status()

        # Test that we get valid system information
        assert hasattr(status, "components")
        assert hasattr(status, "overall_healthy")
        assert hasattr(status, "timestamp")
        assert hasattr(status, "system_info")

        # Verify components exist (at least core system checks)
        assert len(status.components) > 0

        # Check that each component has required fields
        for component_name, component_status in status.components.items():
            assert isinstance(component_name, str)
            assert isinstance(component_status.healthy, bool)
            assert isinstance(component_status.message, str)
            assert isinstance(component_status.name, str)

    @pytest.mark.asyncio
    async def test_is_system_healthy(self) -> None:
        """Test quick health check function."""
        healthy = await is_system_healthy()
        assert isinstance(healthy, bool)

    @pytest.mark.asyncio
    async def test_custom_health_check_registration(self) -> None:
        """Test custom health check registration."""

        async def custom_check() -> ComponentStatus:
            return ComponentStatus(
                name="custom_test",
                message="Custom check passed",
                response_time_ms=None,
            )

        # Register custom check
        register_health_check("custom_test", custom_check)

        try:
            # Get status and verify custom check is included under aegis component
            status = await get_system_status()
            assert "aegis" in status.components
            aegis_component = status.components["aegis"]
            assert "components" in aegis_component.sub_components
            components_group = aegis_component.sub_components["components"]
            assert "custom_test" in components_group.sub_components
            assert components_group.sub_components["custom_test"].name == "custom_test"
            assert components_group.sub_components["custom_test"].healthy is True
        finally:
            # Clean up the custom health check registration
            from app.services.system.health import _health_checks

            if "custom_test" in _health_checks:
                del _health_checks["custom_test"]
