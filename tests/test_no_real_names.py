"""The household's real names and places stay out of the code (#496).

The repo is public. Examples are written with stand-ins (a role, or an
obviously made-up name), never the people they came from. The names are
kept here as sha256 hashes of the lowercased word, so this file does not
put them back; every word in every file under ``app/``, ``alembic/`` and
``tests/`` is hashed and checked. Bytecode caches are build output, not source.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent

REAL_NAMES = frozenset(
    {
        "d3e8b1bbeaba471bd7b40127176d784080cc22433a3861c41234dd11bf1ee927",
        "7b85f25bc9d03ae6cd57829bdbaeff297000575e3f1e71a598dd2edb814c2405",
        "b76aefa8fe4304f650962fb4af77b4db4b13d34ac9615391126027232ec16822",
        "a051e62c16c385ab646c4161a67a338bc8e7efdff2c797e53fcdb72b9fc2b4d0",
        "34517c83d2a1c75c246a3565d1abbe1ddac6420d43cc2fc3023ae2e534dd9d3a",
        "ee47201b643ce77fde99740aa2bfb8233fdc5c321cbf7657f778512f840f8f4e",
        "f7e5792b9f896f0f5803195cbe8d11e52af78f9cb0feb9001b652f881a769d28",
        "65ba5b14ee9236cb2ea813d1b432bf33ee6c3005e552c0bd90e519d619ccfd38",
    }
)
WORD = re.compile(r"[a-z]+")


def _named(path: Path) -> bool:
    words = set(WORD.findall(path.read_bytes().decode(errors="ignore").lower()))
    return any(hashlib.sha256(w.encode()).hexdigest() in REAL_NAMES for w in words)


def test_no_real_household_name_in_the_code() -> None:
    found = [
        str(path.relative_to(ROOT))
        for top in ("app", "alembic", "tests")
        for path in sorted((ROOT / top).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and _named(path)
    ]
    assert found == []
