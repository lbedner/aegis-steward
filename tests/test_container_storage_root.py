"""Every container sees the shared object store and backup directory.

A compose service that declares its own ``environment`` list replaces
the anchor's (YAML merge keys never join lists), so a value put only on
the anchor reached no container and object storage fell back to the
code directory. The Dockerfile's ENV is what every container sees; the
backup dir is there for the reason its comment gives.
"""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHARED_ENV = ("STORAGE_ROOT", "DATABASE_BACKUP_DIR")


def _dockerfile_env(name: str) -> str:
    found = re.search(
        rf"^ENV {name}=(\S+)$", (ROOT / "Dockerfile").read_text(), re.MULTILINE
    )
    assert found, f"Dockerfile does not set {name}"
    return found.group(1)


def test_dockerfile_sets_the_storage_root() -> None:
    assert _dockerfile_env("STORAGE_ROOT") == "/data/storage"


def test_dockerfile_sets_an_absolute_backup_dir() -> None:
    assert _dockerfile_env("DATABASE_BACKUP_DIR").startswith("/")


def test_no_compose_environment_list_carries_it() -> None:
    for name in (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.prod.yml",
    ):
        compose = yaml.safe_load((ROOT / name).read_text()) or {}
        for service, spec in (compose.get("services") or {}).items():
            for entry in (spec or {}).get("environment") or []:
                key = str(entry).split("=", 1)[0]
                assert key not in SHARED_ENV, f"{name}: {service} sets {key}"


def test_every_app_service_shares_the_database_and_store() -> None:
    # A service-level ``volumes`` list replaces the anchor's the same way,
    # which once left the scheduler on its own empty SQLite file.
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    backups = f"backup-data:{_dockerfile_env('DATABASE_BACKUP_DIR')}"
    for service, spec in compose["services"].items():
        if spec.get("image") != compose["x-app"]["image"]:
            continue
        assert "aegis-data:/data/db" in spec["volumes"], service
        assert "storage-data:/data/storage" in spec["volumes"], service
        assert backups in spec["volumes"], service
