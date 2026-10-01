"""`make serve ENGINE=granian` reaches the container.

Make exports command-line variables to its recipes, so ``ENGINE`` arrives
in this process's environment; compose only forwards what the compose file
names, which is ``WEBSERVER_ENGINE``. The translation happens here, and a
plain ``make serve`` must not pin an engine at all.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import pytest

from scripts import dev_tasks


@pytest.fixture
def serve_kwargs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """What ``serve()`` hands to ``subprocess.run``: the keyword arguments,
    and the compose command under ``cmd``. The env file is an empty temp
    file until a test writes to it."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> None:
        captured.update(kwargs, cmd=cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dev_tasks, "resolve_ports", lambda **_: {})
    monkeypatch.setattr(dev_tasks, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("ENGINE", raising=False)
    monkeypatch.delenv("WEBSERVER_ENGINE", raising=False)
    return captured


def test_engine_becomes_the_webserver_setting(
    serve_kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGINE", "granian")

    dev_tasks.serve()

    assert serve_kwargs["env"]["WEBSERVER_ENGINE"] == "granian"


def test_plain_serve_pins_no_engine(serve_kwargs: dict[str, Any]) -> None:
    dev_tasks.serve()

    assert "WEBSERVER_ENGINE" not in serve_kwargs["env"]


class TestThePlaidTunnel:
    """The tunnel only carries Plaid's webhook, so ``make serve`` starts it
    only when the env file has Plaid credentials (#306). Without them it
    served nothing and still put the whole app on a public address."""

    def _profiles(self, cmd: list[str]) -> list[str]:
        return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--profile"]

    def test_plaid_credentials_start_the_tunnel(
        self, serve_kwargs: dict[str, Any]
    ) -> None:
        dev_tasks._ENV_FILE.write_text("PLAID_CLIENT_ID=abc123\n")

        dev_tasks.serve()

        assert self._profiles(serve_kwargs["cmd"]) == ["dev", "plaid"]

    @pytest.mark.parametrize("env", ["", "PLAID_CLIENT_ID=\n"], ids=["absent", "empty"])
    def test_without_plaid_the_tunnel_stays_off(
        self, serve_kwargs: dict[str, Any], env: str
    ) -> None:
        dev_tasks._ENV_FILE.write_text(env)

        dev_tasks.serve()

        assert self._profiles(serve_kwargs["cmd"]) == ["dev"]
