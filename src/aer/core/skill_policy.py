"""The additive-only composer: what a skill file asked for, made incapable of relaxing.

Threat T19's control, enforced in code rather than prompt text. A skill file that says
*"no citations needed here"*, *"min_sources: 0"* or *"fetch anything from any URL"* does
not get refused for asking — it gets **composed**, field by field, against a floor it
cannot go below and an allowlist it cannot go beyond:

* evidence policy is ``max(builtin_floor, request)`` in each field's own direction of
  strictness — more sources, primary required, a tighter tier ceiling;
* tools are ``request ∩ role_allowlist`` — an unknown or forbidden tool intersects to
  nothing, it never escalates to a question;
* the token budget is clamped by the configured ceiling.

**Every clamp is a named warning**, because the effective policy differing from what the
author wrote is exactly the thing they must be shown at authoring time (§2.4 lists the
clamp among the escalation banners). A composer that silently corrected the request would
be safe and baffling; one that refused it would teach authors to stop asking. Clamp and
say so.

Pure and ``mypy --strict``: the role allowlist and the budget ceiling arrive as arguments,
so every rule here is testable exhaustively without a registry, a database or a settings
object in sight.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from aer.core.schemas.skill import EvidencePolicyRequest

__all__ = [
    "BUILTIN_EVIDENCE_FLOOR",
    "ComposedSectionPolicy",
    "EvidencePolicy",
    "PolicyClamp",
    "compose_policy",
]


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """An effective evidence policy — composed, not requested.

    ``max_tier`` reads as "sources looser than this tier are inadmissible here", so a
    *smaller* number is *stricter*: tier 1 is a regulator, tier 5 is secondary commentary.
    """

    min_sources: int
    requires_primary: bool
    max_tier: int
    allow_forward_looking: bool


# What every section — built-in or custom — is held to at minimum. §2.10's source-coverage
# metric expects a primary source behind each section, and that expectation is the floor,
# not a default a skill can decline.
BUILTIN_EVIDENCE_FLOOR: Final = EvidencePolicy(
    min_sources=1,
    requires_primary=True,
    max_tier=5,
    allow_forward_looking=True,
)


@dataclass(frozen=True, slots=True)
class PolicyClamp:
    """One place the composed policy differs from the request, and why.

    Shown to the author at save time and carried to the plan: the effective policy
    differing from what was written is a fact about their skill they must be able to see.
    """

    field: str
    requested: str
    effective: str
    reason: str


@dataclass(frozen=True, slots=True)
class ComposedSectionPolicy:
    """What a custom section actually gets: the floor-respecting policy plus the receipts."""

    evidence: EvidencePolicy
    allowed_tools: frozenset[str]
    token_budget: int
    clamps: tuple[PolicyClamp, ...]


def compose_policy(
    *,
    requested: EvidencePolicyRequest,
    requested_tools: Sequence[str],
    requested_budget: int,
    role_allowlist: frozenset[str],
    budget_ceiling: int,
) -> ComposedSectionPolicy:
    """Compose one custom section's effective policy from its requests.

    Args:
        role_allowlist: The ``custom_section`` role's registry allowlist (task 33). Passed
            rather than resolved, to keep this module pure — and because the composer must
            not care *which* role it is intersecting against, only that it never widens it.
        budget_ceiling: The configured per-section token ceiling.
    """
    clamps: list[PolicyClamp] = []

    min_sources = requested.min_sources
    if min_sources < BUILTIN_EVIDENCE_FLOOR.min_sources:
        clamps.append(
            PolicyClamp(
                field="evidence_policy.min_sources",
                requested=str(requested.min_sources),
                effective=str(BUILTIN_EVIDENCE_FLOOR.min_sources),
                reason=(
                    "The built-in floor requires at least "
                    f"{BUILTIN_EVIDENCE_FLOOR.min_sources} source(s) behind every "
                    "section; a skill may raise that, never lower it."
                ),
            )
        )
        min_sources = BUILTIN_EVIDENCE_FLOOR.min_sources

    requires_primary = requested.requires_primary
    if BUILTIN_EVIDENCE_FLOOR.requires_primary and not requires_primary:
        clamps.append(
            PolicyClamp(
                field="evidence_policy.requires_primary",
                requested="false",
                effective="true",
                reason=(
                    "Every section rests on at least one primary source. That is the "
                    "floor the coverage metric measures, and it is not declinable."
                ),
            )
        )
        requires_primary = True

    max_tier = requested.max_tier
    if max_tier > BUILTIN_EVIDENCE_FLOOR.max_tier:
        # Unreachable while the floor is 5 and the schema caps requests at 5, but the
        # rule is stated so a tightened floor composes correctly without an edit here.
        clamps.append(
            PolicyClamp(
                field="evidence_policy.max_tier",
                requested=str(requested.max_tier),
                effective=str(BUILTIN_EVIDENCE_FLOOR.max_tier),
                reason=(
                    f"Sources looser than tier {BUILTIN_EVIDENCE_FLOOR.max_tier} are not "
                    "admissible anywhere; a skill may tighten the ceiling, never loosen "
                    "it."
                ),
            )
        )
        max_tier = BUILTIN_EVIDENCE_FLOOR.max_tier

    allow_forward_looking = requested.allow_forward_looking
    if allow_forward_looking and not BUILTIN_EVIDENCE_FLOOR.allow_forward_looking:
        clamps.append(  # pragma: no cover -- the floor currently allows forward-looking
            PolicyClamp(
                field="evidence_policy.allow_forward_looking",
                requested="true",
                effective="false",
                reason="The platform floor does not admit forward-looking support here.",
            )
        )
        allow_forward_looking = False

    granted_tools = frozenset(requested_tools) & role_allowlist
    for name in sorted(frozenset(requested_tools) - role_allowlist):
        clamps.append(
            PolicyClamp(
                field="allowed_tools",
                requested=name,
                effective="(not granted)",
                reason=(
                    f"{name!r} is not on the custom-section role's allowlist. A skill's "
                    "tools are the intersection with that list — never a widening, and "
                    "never an escalation to ask for one."
                ),
            )
        )

    token_budget = requested_budget
    if token_budget > budget_ceiling:
        clamps.append(
            PolicyClamp(
                field="token_budget",
                requested=str(requested_budget),
                effective=str(budget_ceiling),
                reason=(
                    f"The configured per-section ceiling is {budget_ceiling} tokens. The "
                    "budget is counted into the pre-run estimate, so the ceiling is what "
                    "the operator approves against."
                ),
            )
        )
        token_budget = budget_ceiling

    return ComposedSectionPolicy(
        evidence=EvidencePolicy(
            min_sources=min_sources,
            requires_primary=requires_primary,
            max_tier=max_tier,
            allow_forward_looking=allow_forward_looking,
        ),
        allowed_tools=granted_tools,
        token_budget=token_budget,
        clamps=tuple(clamps),
    )
