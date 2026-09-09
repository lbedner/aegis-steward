"""Every container sees the shared object store.

A compose service that declares its own ``environment`` list replaces
the anchor's (YAML merge keys never join lists), so a value put only on
the anchor reached no container and object storage fell back to the
code directory. The Dockerfile's ENV is what every container sees.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_sets_the_storage_root() -> None:
    assert "ENV STORAGE_ROOT=/data/storage" in (ROOT / "Dockerfile").read_text()


def test_no_compose_environment_list_carries_it() -> None:
    for name in (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.prod.yml",
    ):
        compose = yaml.safe_load((ROOT / name).read_text()) or {}
        for service, spec in (compose.get("services") or {}).items():
            for entry in (spec or {}).get("environment") or []:
                assert not str(entry).startswith("STORAGE_ROOT="), f"{name}: {service}"
