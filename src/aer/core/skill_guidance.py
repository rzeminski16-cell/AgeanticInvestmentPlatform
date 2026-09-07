"""Which roles read the operator's standing guidance, and in what order it composes.

ADR 0108. A methodology, preference or house-view skill produces no section: it is the
operator's own text, composed into an existing role's prompt as the last block of the user
turn. Two things are decided here and nowhere else.

**Which roles.** The table below. A role absent from every row — the plan critic, the red
team, the verdict, the extractors, the validators, the risk analyst, the post-trade
reviewer — reads no guidance under any kind, and that is the decision rather than an
omission: the reader of the operator's text is never the grader of its result. A skill
cannot name its readers; the frontmatter has no field for it.

**In what order.** By kind — methodology, then house view, then preference — and then by
key, so two runs under the same pins compose the same bytes and the archived prompt is
reproducible from the pin rows alone.

Pure and ``mypy --strict``: the pins arrive already reduced to :class:`OperatorGuidance`
rows, so the table is testable exhaustively without a database or a registry in sight.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from aer.core.enums import SkillKind

__all__ = [
    "GUIDANCE_ROLES",
    "PLANNER",
    "SECTION_WRITER",
    "OperatorGuidance",
    "guidance_for_role",
    "roles_for",
]

PLANNER: Final = "planner"
SECTION_WRITER: Final = "report_writer"

GUIDANCE_ROLES: Final[Mapping[SkillKind, tuple[str, ...]]] = {
    SkillKind.METHODOLOGY: (PLANNER, SECTION_WRITER),
    SkillKind.HOUSE_VIEW: (PLANNER, SECTION_WRITER),
    SkillKind.PREFERENCE: (SECTION_WRITER,),
}
"""ADR 0108 §1. A custom section is absent because it is not guidance: it is a section."""

_KIND_ORDER: Final[Mapping[SkillKind, int]] = {
    SkillKind.METHODOLOGY: 0,
    SkillKind.HOUSE_VIEW: 1,
    SkillKind.PREFERENCE: 2,
}


@dataclass(frozen=True, slots=True)
class OperatorGuidance:
    """One pinned prompt-kind skill, reduced to what a prompt needs to quote it."""

    kind: SkillKind
    key: str
    title: str
    version: int
    body: str


def roles_for(kind: SkillKind) -> tuple[str, ...]:
    """The roles a kind composes into; empty for a custom section."""
    return GUIDANCE_ROLES.get(kind, ())


def guidance_for_role(items: Iterable[OperatorGuidance], role: str) -> tuple[OperatorGuidance, ...]:
    """The guidance one role reads, in composition order.

    Filtering and ordering live together so a caller cannot get one without the other:
    a role handed the right skills in pin order would compose different bytes on a
    different day.
    """
    chosen = [item for item in items if role in roles_for(item.kind)]
    chosen.sort(key=lambda item: (_KIND_ORDER.get(item.kind, len(_KIND_ORDER)), item.key))
    return tuple(chosen)
