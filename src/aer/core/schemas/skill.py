"""The skill-file frontmatter schema (`docs/PLAN.md` §2.12).

The split that matters: everything the system **acts on** — budgets, tools, evidence
policy, output shape, placement — is structured and validated here; everything the model
**interprets** is the free-text body, which this module never reads. A skill file that
fails this schema is rejected at authoring time with line-level errors (the line mapping
lives in :mod:`aer.skills.frontmatter`, which owes YAML a dependency this pure module does
not), never at run time.

**The reserved output fields are the ADR 0034 pattern applied to authorship.** A custom
section cannot write ``reports.rating``, a valuation range or a recommendation — not
because a validator checks the value, but because an output contract *declaring such a
field* refuses to validate, so there is nothing downstream to police. The one writable
confidence is the section's own, about itself, which §2.12's example carries deliberately.

**Requests here are requests, not grants.** ``evidence_policy`` and ``token_budget`` and
``allowed_tools`` record what the author asked for; what a section actually gets is what
:mod:`aer.core.skill_policy` composes from them, and it is never looser than the built-in
floor. This schema refuses only the malformed, not the over-ambitious — an over-ask is
clamped and warned about, because an author who asks for the world should learn what the
composer did to the request, not be refused for asking.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aer.core.enums import SkillKind

__all__ = [
    "FRONTMATTER_VERSION",
    "KEY_PATTERN",
    "MAX_OUTPUT_FIELDS",
    "POSITION_PATTERN",
    "RESERVED_OUTPUT_FIELDS",
    "SCOPE_PATTERN",
    "Applicability",
    "EvidencePolicyRequest",
    "SkillFrontmatter",
]

# `aer_skill: 1`. A version on the *format*, so a future format change can tell old files
# from malformed ones.
FRONTMATTER_VERSION: Final = 1

# Lower snake case, like a section key — because a custom section's key becomes one.
KEY_PATTERN: Final = re.compile(r"\A[a-z][a-z0-9_]{1,63}\Z")

# global | sector:<code> | company:<ticker> | run
SCOPE_PATTERN: Final = re.compile(
    r"\A(global|run|sector:[A-Za-z0-9_\-]{1,32}|company:[A-Z0-9.\-]{1,12})\Z"
)

# after:<key> | before:<key> | a bare position number
POSITION_PATTERN: Final = re.compile(
    r"\A(after:[a-z][a-z0-9_]{1,63}|before:[a-z][a-z0-9_]{1,63}|[0-9]{1,3})\Z"
)

MAX_OUTPUT_FIELDS: Final = 16

# The fields a custom-section output contract may not declare, under any spelling the
# report model owns. Declaring one is refused at validation — there is deliberately no
# runtime check downstream, because after this there is nothing for one to catch.
RESERVED_OUTPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "rating",
        "recommendation",
        "target_price",
        "price_target",
        "valuation_range",
        "fair_value",
    }
)

_IDENTIFIER: Final = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")


def _all_markets() -> list[Literal["US", "UK"]]:
    return ["US", "UK"]


class Applicability(BaseModel):
    """Where a skill applies. Matched at plan time; a mismatch is a skip, not an error."""

    model_config = ConfigDict(extra="forbid")

    markets: list[Literal["US", "UK"]] = Field(default_factory=_all_markets)
    analysis_modes: list[str] = Field(default_factory=list, max_length=8)
    exclude_sectors: list[str] = Field(default_factory=list, max_length=16)


class EvidencePolicyRequest(BaseModel):
    """What the author asked the section's evidence floor to be.

    A *request*: the composer takes ``max(builtin_floor, request)`` field by field, so
    nothing here can weaken anything. Bounds exist only to catch nonsense — a
    ``min_sources`` of 10,000 is a typo, not ambition.
    """

    model_config = ConfigDict(extra="forbid")

    min_sources: int = Field(default=0, ge=0, le=50)
    requires_primary: bool = False
    max_tier: int = Field(default=5, ge=1, le=5)
    allow_forward_looking: bool = True


class SkillFrontmatter(BaseModel):
    """The structured half of a skill file, validated on save.

    Field order follows §2.12's example, because this schema *is* that example's contract.
    """

    model_config = ConfigDict(extra="forbid")

    aer_skill: Literal[1]
    key: str
    kind: SkillKind
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    scope: str = "global"
    position: str | None = None
    required: bool = False
    applicability: Applicability = Field(default_factory=Applicability)
    evidence_policy: EvidencePolicyRequest | None = None
    output: dict[str, Any] | None = None
    token_budget: int | None = Field(default=None, ge=1)
    allowed_tools: list[str] = Field(default_factory=list, max_length=8)
    charts: list[dict[str, Any]] = Field(default_factory=list, max_length=4)

    @field_validator("key")
    @classmethod
    def _key_is_a_section_key(cls, value: str) -> str:
        if not KEY_PATTERN.match(value):
            message = (
                f"{value!r} is not a valid skill key. Lower snake case, starting with a "
                "letter, at most 64 characters — it becomes a section key."
            )
            raise ValueError(message)
        return value

    @field_validator("scope")
    @classmethod
    def _scope_is_one_of_the_four(cls, value: str) -> str:
        if not SCOPE_PATTERN.match(value):
            message = (
                f"{value!r} is not a scope. One of: global, run, sector:<code>, company:<ticker>."
            )
            raise ValueError(message)
        return value

    @field_validator("position")
    @classmethod
    def _position_is_relative_or_numeric(cls, value: str | None) -> str | None:
        if value is not None and not POSITION_PATTERN.match(value):
            message = (
                f"{value!r} is not a position. One of: after:<section_key>, "
                "before:<section_key>, or a number."
            )
            raise ValueError(message)
        return value

    @field_validator("allowed_tools")
    @classmethod
    def _tools_are_identifiers(cls, value: list[str]) -> list[str]:
        for name in value:
            if not _IDENTIFIER.match(name):
                message = f"{name!r} is not a tool name."
                raise ValueError(message)
        return value

    @field_validator("output")
    @classmethod
    def _output_fields_are_lawful(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Field names must be identifiers, must not be reserved, and must be few.

        The reserved check is the load-bearing one: it is the *only* place the platform
        decides that a custom section has no writable path to a rating, and it works by
        making the field impossible to declare rather than by checking what was written
        into it later.
        """
        if value is None:
            return value
        if len(value) > MAX_OUTPUT_FIELDS:
            message = (
                f"The output contract declares {len(value)} fields; the most a section "
                f"may carry is {MAX_OUTPUT_FIELDS}."
            )
            raise ValueError(message)
        for name in value:
            if not _IDENTIFIER.match(name):
                message = f"{name!r} is not a valid output field name."
                raise ValueError(message)
            if name in RESERVED_OUTPUT_FIELDS:
                message = (
                    f"The output field {name!r} is reserved. Ratings, recommendations and "
                    "valuation ranges are owned by built-in sections; a custom section "
                    "has no writable path to them, and that starts with not being able "
                    "to declare the field."
                )
                raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _kind_and_shape_agree(self) -> SkillFrontmatter:
        """A custom section needs its section-shaped fields; the prompt kinds refuse them.

        A methodology skill carrying an output contract is a section wearing the wrong
        kind — and whichever of the two the author meant, silently accepting it would
        surprise them at plan time, which is the wrong time.
        """
        if self.kind is SkillKind.CUSTOM_SECTION:
            missing = [
                name
                for name, value in (
                    ("evidence_policy", self.evidence_policy),
                    ("output", self.output),
                    ("token_budget", self.token_budget),
                )
                if value is None
            ]
            if missing:
                message = (
                    f"A custom_section skill must declare {', '.join(missing)}. Without "
                    "them the section has no evidence floor, no shape, or no budget."
                )
                raise ValueError(message)
            if not self.output:
                message = "The output contract declares no fields."
                raise ValueError(message)
        else:
            declared = [
                name
                for name, value in (
                    ("position", self.position),
                    ("output", self.output),
                    ("token_budget", self.token_budget),
                )
                if value is not None
            ]
            if declared:
                message = (
                    f"A {self.kind.value} skill may not declare {', '.join(declared)}: "
                    "it composes into an existing agent's prompt and produces no section "
                    "of its own."
                )
                raise ValueError(message)
        return self
