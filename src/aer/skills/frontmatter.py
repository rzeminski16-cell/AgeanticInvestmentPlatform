"""Skill files into validated frontmatter and body, with errors that name their line.

§2.12: *"A skill file that fails frontmatter validation is rejected at authoring time with
line-level errors, never at run time."* The line numbers are the point of this module. A
schema error that says ``evidence_policy.min_sources`` is findable in a ten-line file and
hopeless in a two-hundred-line one; an author fixing their file should be sent to a line,
the way a compiler would send them.

Two passes over the same YAML, deliberately. ``yaml.safe_load`` produces the values;
``yaml.compose`` produces the node tree whose marks carry positions, walked into a
``field path → line`` map. One pass with a custom constructor could do both, but it would
be a loader subclass nobody can read at a glance, maintained for the sake of parsing a
document that is rarely fifty lines long.

The **content hash is over the exact source text** — fences, body, trailing newline and
all. Any edit is a new hash, which is what makes version pinning (task 36) and the import
diff (threat T20) mean something: two skills are the same skill only if their bytes are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import yaml
from pydantic import ValidationError

from aer.core.hashing import sha256_hex
from aer.core.schemas.skill import SkillFrontmatter
from aer.errors import AerError

__all__ = ["ParsedSkill", "SkillFileError", "SkillIssue", "parse_skill_file"]

_FENCE: Final = "---"

# The opening fence is line 1, so a YAML mark's 0-based line N is file line N + 2.
_YAML_LINE_OFFSET: Final = 2


@dataclass(frozen=True, slots=True)
class SkillIssue:
    """One thing wrong with a skill file, and where.

    ``line`` is 1-based in the *file as the author sees it*, fences included. ``None``
    only where nothing positional exists to point at — a missing field's natural home is
    the mapping it is missing from, and that is what gets pointed at instead wherever the
    map allows.
    """

    line: int | None
    field: str
    message: str


class SkillFileError(AerError):
    """A skill file failed validation. Carries every issue, each with its line.

    All of them, not the first: fixing a file should take one round trip, the same
    decision the settings loader took in task 2.
    """

    code = "skill_file"
    http_status = 422

    def __init__(self, message: str, *, issues: list[SkillIssue]) -> None:
        super().__init__(
            message,
            context={
                "issues": [
                    {"line": issue.line, "field": issue.field, "message": issue.message}
                    for issue in issues
                ]
            },
        )
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class ParsedSkill:
    """A skill file that passed: the structured half, the prose half, and the hash."""

    frontmatter: SkillFrontmatter
    body: str
    content_hash: str
    source: str


def parse_skill_file(source: str) -> ParsedSkill:
    """Parse and validate one skill file.

    Raises:
        SkillFileError: With every issue found, each carrying the line it lives on. A
            file that fails here never reaches the database — which is the acceptance
            criterion: a skill row cannot exist with invalid frontmatter.
    """
    frontmatter_text, body = _split(source)

    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + _YAML_LINE_OFFSET
        issue = SkillIssue(line=line, field="frontmatter", message=f"YAML does not parse: {exc}")
        raise SkillFileError("The frontmatter is not valid YAML.", issues=[issue]) from exc

    if not isinstance(data, dict):
        issue = SkillIssue(
            line=_YAML_LINE_OFFSET,
            field="frontmatter",
            message="The frontmatter must be a mapping of fields, not "
            f"{type(data).__name__ if data is not None else 'empty'}.",
        )
        raise SkillFileError("The frontmatter is not a mapping.", issues=[issue])

    lines = _line_map(frontmatter_text)

    try:
        frontmatter = SkillFrontmatter.model_validate(data)
    except ValidationError as exc:
        issues = [
            SkillIssue(
                line=_nearest_line(lines, error["loc"]),
                field=".".join(str(part) for part in error["loc"]) or "frontmatter",
                message=error["msg"],
            )
            for error in exc.errors()
        ]
        raise SkillFileError(
            f"The frontmatter has {len(issues)} problem(s).", issues=issues
        ) from exc

    return ParsedSkill(
        frontmatter=frontmatter,
        body=body,
        content_hash=sha256_hex(source.encode("utf-8")),
        source=source,
    )


def _split(source: str) -> tuple[str, str]:
    """The text between the fences, and everything after the closing one."""
    lines = source.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        issue = SkillIssue(
            line=1,
            field="frontmatter",
            message='A skill file begins with a "---" frontmatter fence on line 1.',
        )
        raise SkillFileError("The file has no frontmatter fence.", issues=[issue])

    for index, line in enumerate(lines[1:], start=2):
        if line.strip() == _FENCE:
            frontmatter_text = "\n".join(lines[1 : index - 1])
            body = "\n".join(lines[index:]).strip("\n")
            return frontmatter_text, body

    issue = SkillIssue(
        line=len(lines),
        field="frontmatter",
        message='The frontmatter fence is never closed: no second "---" line.',
    )
    raise SkillFileError("The frontmatter fence is not closed.", issues=[issue])


def _line_map(frontmatter_text: str) -> dict[tuple[Any, ...], int]:
    """Every field path in the YAML, mapped to the 1-based file line it starts on."""
    try:
        root = yaml.compose(frontmatter_text)
    except yaml.YAMLError:  # pragma: no cover -- safe_load already accepted this text
        return {}
    if root is None:
        return {}

    found: dict[tuple[Any, ...], int] = {}

    def walk(node: yaml.Node, path: tuple[Any, ...]) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key = getattr(key_node, "value", None)
                if not isinstance(key, str):  # pragma: no cover -- schema refuses these
                    continue
                found[(*path, key)] = key_node.start_mark.line + _YAML_LINE_OFFSET
                walk(value_node, (*path, key))
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                found[(*path, index)] = item.start_mark.line + _YAML_LINE_OFFSET
                walk(item, (*path, index))

    walk(root, ())
    return found


def _nearest_line(lines: dict[tuple[Any, ...], int], loc: tuple[Any, ...]) -> int | None:
    """The line for a validation error's location, or its closest declared ancestor.

    A *missing* field has no line of its own; its nearest home is the mapping it should
    have been in, which is the most useful place to send the author.
    """
    for length in range(len(loc), -1, -1):
        found = lines.get(tuple(loc[:length]))
        if found is not None:
            return found
    return None
