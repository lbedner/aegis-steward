"""Windows-friendly developer commands with no bash-only shell logic.

Both the Makefile and the ``uv run poe`` tasks call into this module, so
there is exactly one implementation of anything that used to need bash (the
``serve``/``serve-bg`` port-resolution + ``eval`` dance, ``serve-prod``'s
env-file ternary, ``migrate-reset``'s confirmation prompt).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.resolve_ports import docs_port, resolve_ports

_COMPOSE_DEV = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.dev.yml",
]
_COMPOSE_PROD = ["docker", "compose", "-f", "docker-compose.yml"]


def serve(*, detach: bool = False) -> None:
    print(
        "Starting services in background..."
        if detach
        else "Starting services... (Press Ctrl+C to stop)"
    )
    ports = resolve_ports(
        ingress=False,
        postgres=False,
        redis=True,
        ollama=False,
    )
    env = {**os.environ, **{key: str(value) for key, value in ports.items()}}
    cmd = [*_COMPOSE_DEV, "--profile", "dev", "up", "--remove-orphans"]
    if detach:
        cmd.append("-d")
    subprocess.run(cmd, env=env, check=True)


def serve_bg() -> None:
    serve(detach=True)


def serve_prod() -> None:
    env_file = ".env.deploy" if Path(".env.deploy").exists() else ".env"
    print(f"Starting services (production profile), reading {env_file}...")
    print(
        "Set APP_ENV=production and a real SECRET_KEY there before booting outside dev."
    )
    env = {**os.environ, "AEGIS_STACK_ENV_FILE": env_file}
    subprocess.run([*_COMPOSE_PROD, "build", "webserver"], env=env, check=True)
    subprocess.run(
        [*_COMPOSE_PROD, "--profile", "prod", "up", "--remove-orphans"],
        env=env,
        check=True,
    )


def docs() -> None:
    """Serve the documentation on a port that is actually free."""
    port = docs_port()
    subprocess.run(
        ["uv", "run", "mkdocs", "serve", "--dev-addr", f"0.0.0.0:{port}"],
        check=True,
    )


def clean_cache() -> None:
    """Remove __pycache__ dirs and .pyc files (cross-platform, no find/rm)."""
    root = Path(".")
    for pycache in root.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)
    for pyc in root.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)


def status() -> None:
    print("Current system status:")
    print()
    print("Docker containers:")
    try:
        subprocess.run(["docker", "compose", "ps"], check=True)
    except FileNotFoundError:
        print("Docker not installed (or not on PATH)")
    except subprocess.CalledProcessError:
        print("No containers running")
    print()
    print("Dependencies:")
    try:
        result = subprocess.run(
            ["uv", "pip", "list"], check=True, capture_output=True, text=True
        )
        print("\n".join(result.stdout.splitlines()[:20]))
    except FileNotFoundError:
        print("uv not installed (or not on PATH)")
    except subprocess.CalledProcessError:
        print("Dependencies not installed")


def migrate_reset() -> None:
    print("WARNING: This will destroy all data in the database!")
    if input("Are you sure? Type 'yes' to continue: ") != "yes":
        sys.exit(1)
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "webserver",
            "uv",
            "run",
            "alembic",
            "-c",
            "alembic/alembic.ini",
            "downgrade",
            "base",
        ],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "webserver",
            "uv",
            "run",
            "alembic",
            "-c",
            "alembic/alembic.ini",
            "upgrade",
            "head",
        ],
        check=True,
    )
    print("Database reset complete")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "serve",
            "serve-bg",
            "serve-prod",
            "docs",
            "migrate-reset",
        ],
    )
    args = parser.parse_args(argv)

    try:
        if args.action == "serve":
            serve()
        elif args.action == "serve-bg":
            serve_bg()
        elif args.action == "serve-prod":
            serve_prod()
        elif args.action == "docs":
            docs()
        else:
            migrate_reset()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


if __name__ == "__main__":
    main()
