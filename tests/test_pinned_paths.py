"""Every documentation path a test pins must exist.

A test that reads a document is making a claim about the tree, and the tree moves under it.
On 14 September 2026 the design system moved from a redesign folder to `docs/design-system.md`,
and the test that parses its type-scale table kept the old path. Because the path is read at
import, collection of the whole default suite aborted, and CI reported nothing but that for
every push afterwards — the browser job stayed green, so the summary line still looked half
right.

This scans every Python module under `src/`, `tests/` and `audit/` for a quoted `docs/…`
file path and asserts each one resolves. It is deliberately dumb: a string literal that looks
like a documentation path is treated as a pin, because the cost of a false positive is a
one-line fixture and the cost of a false negative was two days of a suite nobody ran.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parent.parent
SCANNED: Final = ("src", "tests", "audit")
PINNED_PATH: Final = re.compile(
    r"""["'](docs/[A-Za-z0-9_./-]+\.(?:md|json|jsonl|html|css|png|pdf|svg))["']"""
)


def _pins() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for folder in SCANNED:
        for module in sorted((ROOT / folder).rglob("*.py")):
            source = module.read_text(encoding="utf-8")
            for match in PINNED_PATH.finditer(source):
                found.append((module.relative_to(ROOT).as_posix(), match.group(1)))
    return found


PINS: Final = _pins()


def test_something_is_pinned_at_all() -> None:
    """If the scan finds nothing the parametrised test below asserts nothing, silently."""
    assert PINS, "no test pins a documentation path any more; check the pattern"


@pytest.mark.parametrize(("module", "pinned"), PINS, ids=[f"{m}:{p}" for m, p in PINS])
def test_a_pinned_documentation_path_exists(module: str, pinned: str) -> None:
    assert (ROOT / pinned).exists(), (
        f"{module} pins {pinned}, which is not in the tree. Either the document moved and the "
        "pin must follow it, or the pin is stale and should go."
    )
