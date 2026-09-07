"""Tests for CLI docs command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from app.cli.docs import (
    AEGIS_BASE,
    COMPONENT_DOCS,
    SERVICE_DOCS,
    _detect_installed,
    _format_docs_section,
    app,
)

runner = CliRunner()


class TestDocsConstants:
    """Test documentation constants."""

    def test_aegis_base_url(self) -> None:
        """Test AEGIS_BASE is valid URL."""
        assert AEGIS_BASE.startswith("https://")
        assert "aegis-stack" in AEGIS_BASE

    def test_component_docs_has_required_keys(self) -> None:
        """Test COMPONENT_DOCS has core components."""
        assert "backend" in COMPONENT_DOCS
        assert "frontend" in COMPONENT_DOCS

    def test_component_docs_structure(self) -> None:
        """Test each component doc entry has correct structure."""
        for name, (aegis_path, external_url, description) in COMPONENT_DOCS.items():
            assert aegis_path.startswith("/"), f"{name} aegis_path should start with /"
            assert description, f"{name} should have description"
            # external_url can be None or a URL
            if external_url:
                assert external_url.startswith("http"), f"{name} external_url invalid"

    def test_service_docs_structure(self) -> None:
        """Test each service doc entry has correct structure."""
        for name, (aegis_path, _external_url, description) in SERVICE_DOCS.items():
            assert aegis_path.startswith("/"), f"{name} aegis_path should start with /"
            assert description, f"{name} should have description"


class TestDetectInstalled:
    """Test component/service detection."""

    @patch("app.cli.docs._get_app_path")
    def test_detect_core_components(
        self, mock_app_path: MagicMock, tmp_path: Path
    ) -> None:
        """Test backend and frontend are always detected."""
        mock_app_path.return_value = tmp_path

        # Create minimal structure
        (tmp_path / "components" / "backend").mkdir(parents=True)
        (tmp_path / "components" / "frontend").mkdir(parents=True)

        components, services = _detect_installed()

        assert "backend" in components
        assert "frontend" in components

    @patch("app.cli.docs._get_app_path")
    def test_detect_optional_components(
        self, mock_app_path: MagicMock, tmp_path: Path
    ) -> None:
        """Test optional components are detected when present."""
        mock_app_path.return_value = tmp_path

        # Create structure with optional components
        (tmp_path / "components" / "backend").mkdir(parents=True)
        (tmp_path / "components" / "frontend").mkdir(parents=True)
        (tmp_path / "components" / "scheduler").mkdir(parents=True)
        (tmp_path / "components" / "worker").mkdir(parents=True)

        components, services = _detect_installed()

        assert "scheduler" in components
        assert "worker" in components

    @patch("app.cli.docs._get_app_path")
    def test_detect_database_from_models(
        self, mock_app_path: MagicMock, tmp_path: Path
    ) -> None:
        """Test database component detected from models directory."""
        mock_app_path.return_value = tmp_path

        # Create models directory with a model file
        models_dir = tmp_path / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "__init__.py").touch()
        (models_dir / "user.py").write_text("class User: pass")

        components, _ = _detect_installed()

        assert "database" in components

    @patch("app.cli.docs._get_app_path")
    def test_detect_services(self, mock_app_path: MagicMock, tmp_path: Path) -> None:
        """Test services are detected when present."""
        mock_app_path.return_value = tmp_path

        # Create services
        (tmp_path / "services" / "auth").mkdir(parents=True)
        (tmp_path / "services" / "ai").mkdir(parents=True)

        _, services = _detect_installed()

        assert "auth" in services
        assert "ai" in services

    @patch("app.cli.docs._get_app_path")
    def test_no_services_when_empty(
        self, mock_app_path: MagicMock, tmp_path: Path
    ) -> None:
        """Test empty services list when none installed."""
        mock_app_path.return_value = tmp_path

        # Create only services directory, no actual services
        (tmp_path / "services").mkdir(parents=True)

        _, services = _detect_installed()

        assert services == []


class TestFormatDocsSection:
    """Test documentation section formatting."""

    def test_format_components_section(self) -> None:
        """Test formatting a components section."""
        items = ["backend", "frontend"]
        lines = _format_docs_section("Components", items, COMPONENT_DOCS)

        assert any("Components:" in line for line in lines)
        assert any("backend" in line for line in lines)
        assert any("frontend" in line for line in lines)
        assert any("Guide:" in line for line in lines)

    def test_format_includes_external_docs(self) -> None:
        """Test external docs URL is included when available."""
        items = ["backend"]
        lines = _format_docs_section("Components", items, COMPONENT_DOCS)

        # Backend has external docs (FastAPI)
        assert any("Docs:" in line for line in lines)
        assert any("fastapi" in line.lower() for line in lines)

    def test_format_skips_unknown_items(self) -> None:
        """Test unknown items are skipped."""
        items = ["backend", "unknown_component"]
        lines = _format_docs_section("Components", items, COMPONENT_DOCS)

        assert any("backend" in line for line in lines)
        assert not any("unknown_component" in line for line in lines)


class TestDocsCLI:
    """Test docs CLI command."""

    @patch("app.cli.docs._detect_installed")
    def test_docs_command_success(self, mock_detect: MagicMock) -> None:
        """Test docs command runs successfully."""
        mock_detect.return_value = (["backend", "frontend"], [])

        result = runner.invoke(app)

        assert result.exit_code == 0
        assert "Documentation" in result.output

    @patch("app.cli.docs._detect_installed")
    def test_docs_shows_components(self, mock_detect: MagicMock) -> None:
        """Test docs shows detected components."""
        mock_detect.return_value = (["backend", "frontend", "scheduler"], [])

        result = runner.invoke(app)

        assert "backend" in result.output
        assert "frontend" in result.output
        assert "scheduler" in result.output

    @patch("app.cli.docs._detect_installed")
    def test_docs_shows_services(self, mock_detect: MagicMock) -> None:
        """Test docs shows detected services."""
        mock_detect.return_value = (["backend", "frontend"], ["auth", "ai"])

        result = runner.invoke(app)

        assert "auth" in result.output
        assert "ai" in result.output

    @patch("app.cli.docs._detect_installed")
    def test_docs_shows_urls(self, mock_detect: MagicMock) -> None:
        """Test docs shows documentation URLs."""
        mock_detect.return_value = (["backend"], [])

        result = runner.invoke(app)

        assert AEGIS_BASE in result.output
        assert "fastapi.tiangolo.com" in result.output

    @patch("app.cli.docs._detect_installed")
    def test_docs_empty_shows_message(self, mock_detect: MagicMock) -> None:
        """Test docs shows message when nothing detected."""
        mock_detect.return_value = ([], [])

        result = runner.invoke(app)

        assert "No components or services detected" in result.output
