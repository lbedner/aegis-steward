"""The entrypoint knows it is in a container without being told.

It decided by ``$DOCKER_CONTAINER``, which each compose service had to
remember to set. The dev build-static-watcher did not, and ``$USER`` is
unset in Docker, so it took the "local environment" branch, unset
UV_PROJECT_ENVIRONMENT, and ran uv against ``/code/.venv`` - the host's
venv, through the bind mount. Its python was ``/code/.venv/bin/python``.
A Linux interpreter went into the host venv, the host's next uv rebuilt
it bare, and pytest, ruff and ty were gone after every ``make serve``
(#213, three times in one day).
"""

from __future__ import annotations

from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "scripts" / "entrypoint.sh"


def _detection() -> str:
    source = ENTRYPOINT.read_text()
    start = source.index("# Configure UV environment")
    return source[start : source.index("\nfi\n", start)]


def test_a_container_is_known_by_the_file_the_runtime_writes() -> None:
    detection = _detection()
    assert "/.dockerenv" in detection
    assert "/run/.containerenv" in detection


def test_the_variable_still_forces_it() -> None:
    assert "DOCKER_CONTAINER" in _detection()
