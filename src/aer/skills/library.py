"""The starter library: example custom sections that ship with the platform.

A custom-section editor opening on a blank page is a feature nobody uses. These are three
worked examples an operator can read, import and edit into something of their own — chosen
to show different shapes rather than to be exhaustive: one with a numeric output field, one
that reads calculations as well as facts, and one deliberately argumentative.

**They are examples, not defaults.** Nothing here is loaded automatically. An example
reaches the platform only through the ordinary import path, which shows a diff and asks for
confirmation like any other skill file — including one from a stranger (threat T20). Shipping
them pre-installed would make the import step look optional, which is exactly the habit the
diff exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from aer.skills.frontmatter import SkillFileError, parse_skill_file

__all__ = ["EXAMPLES_DIR", "StarterSkill", "starter_library", "starter_source"]

EXAMPLES_DIR = Path(__file__).parent / "examples"


@dataclass(frozen=True, slots=True)
class StarterSkill:
    """One shipped example, as the library page lists it."""

    key: str
    title: str
    summary: str
    source: str


@cache
def starter_library() -> tuple[StarterSkill, ...]:
    """Every example that parses, by key.

    An example that no longer parses is **skipped rather than raised**: the frontmatter
    schema will change, and a stale file in the starter library must not stop the skills
    page from rendering. It fails the test suite instead, which is where it belongs.
    """
    found: list[StarterSkill] = []
    for path in sorted(EXAMPLES_DIR.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        try:
            parsed = parse_skill_file(source)
        except SkillFileError:
            continue
        found.append(
            StarterSkill(
                key=parsed.frontmatter.key,
                title=parsed.frontmatter.title,
                summary=_first_line(parsed.body),
                source=source,
            )
        )
    return tuple(found)


def starter_source(key: str) -> str | None:
    """One example's file, for pre-filling the import form."""
    return next((item.source for item in starter_library() if item.key == key), None)


def _first_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""
