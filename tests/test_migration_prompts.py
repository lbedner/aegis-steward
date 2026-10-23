"""The migration prompt works when nothing can answer it: ``make
migrate-fix`` applies what it wrote."""

import io

import pytest

migrate_fix = pytest.importorskip(
    "app.cli.migrate_fix",
    reason="no migrations in this stack",
    exc_type=ImportError,
)


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_without_a_terminal_it_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a script or ``docker compose exec -T``: no crash on EOF."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert migrate_fix._apply_now() is True


@pytest.mark.parametrize(("answer", "applies"), [("", True), ("y", True), ("n", False)])
def test_at_a_terminal_it_asks(
    monkeypatch: pytest.MonkeyPatch, answer: str, applies: bool
) -> None:
    monkeypatch.setattr("sys.stdin", _Terminal(f"{answer}\n"))

    assert migrate_fix._apply_now() is applies
