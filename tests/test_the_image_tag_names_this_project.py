"""This project's image tag belongs to this project.

Generated before aegis-stack scoped the tag per project, steward asks
for ``aegis-stack:latest`` - and so do aegis-site, sector-7g and
test-basic-stack on the same machine. One mutable tag, four stacks:
whichever one builds last silently re-points the image the others run.

That is not theoretical. On 2026-09-19 the worker was recreated and
picked up a tag another project had rebuilt - an image with no
``STORAGE_ROOT`` - and every import failed with "The uploaded file is no
longer there" while the file sat on the shared volume the whole time.
The webserver, whose container predated the swap, kept working, so the
two halves of one stack disagreed about where storage was.

Upstream fixed this by defaulting the tag to the project slug. Steward
carries the old form, so it is pinned here instead.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHARED = "aegis-stack:latest"
OURS = "aegis-steward"


def _tag_default(raw: str) -> str:
    """The ``:-default`` out of ``${AEGIS_STACK_TAG:-default}``."""
    return raw.split(":-", 1)[1].rstrip("}")


def test_compose_defaults_to_this_projects_tag() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text()) or {}
    # The anchor every app service merges from carries the image.
    images = {
        spec["image"]
        for spec in (compose.get("services") or {}).values()
        if isinstance(spec, dict) and "AEGIS_STACK_TAG" in str(spec.get("image", ""))
    }
    assert images, "no service resolves its image through AEGIS_STACK_TAG"
    for image in images:
        assert _tag_default(image).startswith(OURS), image


def test_the_example_env_does_not_hand_out_the_shared_tag() -> None:
    # .env is untracked, so the example is what a fresh clone copies - and
    # what put the shared tag in this project's own .env in the first place.
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith("AEGIS_STACK_TAG="):
            assert line.split("=", 1)[1].strip() != SHARED, line
            assert line.split("=", 1)[1].strip().startswith(OURS), line
