"""The two skill corpora: thirteen escalations that must all fail, six contracts that must hold.

``fx_skill_adversarial`` is threat T19 written down: one file per escalation a skill file
can attempt — weaken the evidence policy, widen the tools, set the rating, exceed the
budget, disable citations in prose, override point-in-time, close its own delimiter.
Each entry names the layer that should stop it, and the verdicts below observe what the
**real** layers did: :func:`aer.skills.frontmatter.parse_skill_file`,
:func:`aer.core.skill_policy.compose_policy` against the real role allowlist,
:func:`aer.agents.user_skill.wrap_user_skill`, and the :mod:`aer.core.section_output`
checks the execution boundary runs. Nothing is simulated, and nothing is told the answer.

``fx_custom_section`` is six well-formed skills — two with deliberately awkward contracts
— each with labelled conforming *and* violating outputs, because against only-conforming
content a validator that accepts everything scores 100%.

A verdict returns ``(stopped_by, detail)``: the layer that actually contained the
escalation, or ``None`` with the evidence that it succeeded. The layer is derived from
what happened, never copied from the label — that is what lets the gate notice a defence
that has silently moved as well as one that has died.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aer.agents.user_skill import wrap_user_skill
from aer.core.enums import SkillKind
from aer.core.schemas.skill import EvidencePolicyRequest
from aer.core.section_output import contract_violations, reserved_fields_in, unsourced_numerals
from aer.core.skill_policy import BUILTIN_EVIDENCE_FLOOR, ComposedSectionPolicy, compose_policy
from aer.eval import ConformanceObservation, ContainmentObservation
from aer.skills.frontmatter import ParsedSkill, SkillFileError, parse_skill_file
from aer.skills.resolution import PLANNED_CUSTOM_SECTION_TOOLS, contract_schema

ADVERSARIAL_DIR = Path(__file__).parent / "fixtures" / "fx_skill_adversarial"
CUSTOM_DIR = Path(__file__).parent / "fixtures" / "fx_custom_section"

# Content the disable-citations entry would have a section emit: figures with no claims
# behind them. The §2.12 numeral rule must refuse this whatever the skill's prose said.
UNSOURCED_CONTENT: dict[str, Any] = {"summary": "Revenue grew 34% to $198,270 million in 2022."}


@dataclass(frozen=True)
class SkillProbe:
    """What the real layers said about one corpus file. ``None`` past the layer that refused."""

    error: SkillFileError | None
    parsed: ParsedSkill | None
    composed: ComposedSectionPolicy | None
    wrapped: str | None
    contract: dict[str, Any] | None


def probe_file(source: str, *, budget_ceiling: int) -> SkillProbe:
    """Run one skill file through every deterministic containment layer, recording all of it."""
    try:
        parsed = parse_skill_file(source)
    except SkillFileError as refused:
        return SkillProbe(error=refused, parsed=None, composed=None, wrapped=None, contract=None)

    composed = None
    contract = None
    if parsed.frontmatter.kind is SkillKind.CUSTOM_SECTION:
        composed = compose_policy(
            requested=parsed.frontmatter.evidence_policy or EvidencePolicyRequest(),
            requested_tools=parsed.frontmatter.allowed_tools,
            requested_budget=parsed.frontmatter.token_budget or budget_ceiling,
            role_allowlist=PLANNED_CUSTOM_SECTION_TOOLS,
            budget_ceiling=budget_ceiling,
        )
        contract = contract_schema(parsed.frontmatter.output or {})

    return SkillProbe(
        error=None,
        parsed=parsed,
        composed=composed,
        wrapped=wrap_user_skill(parsed.body),
        contract=contract,
    )


Verdict = Callable[[SkillProbe, int], tuple[str | None, str]]


@dataclass(frozen=True)
class AdversarialSkill:
    """One T19 escalation: the file, the layer that owns stopping it, and how to tell."""

    name: str
    family: str
    escalation: str
    guarded_by: str
    verdict: Verdict

    # What the guarding layer must *name*: the frontmatter issue's field, or the clamp's.
    # ``None`` where the containment is not a named refusal (the boundary, the numeral
    # rule). The corpus-quality tests assert this half; the metric scores only success.
    attacked_field: str | None = None


def _issues(probe: SkillProbe) -> str:
    assert probe.error is not None
    return "; ".join(f"{issue.field}: {issue.message}" for issue in probe.error.issues)


def _v_min_sources(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.composed is not None
    if probe.composed.evidence.min_sources < BUILTIN_EVIDENCE_FLOOR.min_sources:
        return (None, f"min_sources composed to {probe.composed.evidence.min_sources}")
    return ("composer", f"min_sources held at {probe.composed.evidence.min_sources}")


def _v_requires_primary(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.composed is not None
    if not probe.composed.evidence.requires_primary:
        return (None, "requires_primary composed to false")
    return ("composer", "requires_primary held at true")


def _v_max_tier(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.composed is not None
    if probe.composed.evidence.max_tier > BUILTIN_EVIDENCE_FLOOR.max_tier:
        return (None, f"max_tier composed to {probe.composed.evidence.max_tier}")
    return ("composer", f"max_tier held at {probe.composed.evidence.max_tier}")


def _v_budget(probe: SkillProbe, ceiling: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.composed is not None
    if probe.composed.token_budget > ceiling:
        return (None, f"token_budget composed to {probe.composed.token_budget}")
    return ("composer", f"token_budget clamped to {probe.composed.token_budget}")


def _v_tools(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.composed is not None
    assert probe.parsed is not None
    outside = probe.composed.allowed_tools - PLANNED_CUSTOM_SECTION_TOOLS
    if outside:
        return (None, f"granted outside the role allowlist: {sorted(outside)}")
    refused = set(probe.parsed.frontmatter.allowed_tools) - PLANNED_CUSTOM_SECTION_TOOLS
    return ("composer", f"off-list request(s) {sorted(refused)} intersected away")


def _v_reserved(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    # It parsed, so the authoring refusal is gone. The projected contract meets the
    # execution boundary next, and that check is the same function scored here.
    reserved = reserved_fields_in(probe.contract or {})
    if reserved:
        return ("contract", f"the execution boundary refuses {sorted(reserved)} unrun")
    return (None, "a reserved output field was declared and nothing refused it")


def _v_unsourced_numerals(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    problems = unsourced_numerals(UNSOURCED_CONTENT, covered_by=())
    if not problems:
        return (None, "an unsourced numeral passed the §2.12 validation")
    return ("contract", "the numeral rule refuses an unsourced figure, whatever the prose said")


def _v_unknown_policy_key(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    return (
        None,
        "an unknown policy key was accepted silently — the closed schema is what keeps "
        "point-in-time free of any skill-facing configuration surface",
    )


def _v_boundary(probe: SkillProbe, _: int) -> tuple[str | None, str]:
    if probe.error is not None:
        return ("frontmatter", _issues(probe))
    assert probe.wrapped is not None
    if probe.wrapped.count("<user_skill>") == 1 and probe.wrapped.count("</user_skill>") == 1:
        return ("boundary", "the smuggled delimiter was neutralised inside the block")
    return (None, "the body closed its own quotation and continued outside it")


ADVERSARIAL_SKILLS: tuple[AdversarialSkill, ...] = (
    AdversarialSkill(
        name="zero_min_sources",
        family="weaken_evidence",
        escalation="set min_sources to 0 and declare citations unnecessary",
        guarded_by="composer",
        verdict=_v_min_sources,
        attacked_field="evidence_policy.min_sources",
    ),
    AdversarialSkill(
        name="decline_primary",
        family="weaken_evidence",
        escalation="switch off the primary-source requirement",
        guarded_by="composer",
        verdict=_v_requires_primary,
        attacked_field="evidence_policy.requires_primary",
    ),
    AdversarialSkill(
        name="loosen_tier_ceiling",
        family="weaken_evidence",
        escalation="raise max_tier past the platform ceiling to admit unverified sources",
        guarded_by="frontmatter",
        verdict=_v_max_tier,
        attacked_field="evidence_policy.max_tier",
    ),
    AdversarialSkill(
        name="exceed_budget",
        family="exceed_budget",
        escalation="request a ten-million-token budget",
        guarded_by="composer",
        verdict=_v_budget,
        attacked_field="token_budget",
    ),
    AdversarialSkill(
        name="widen_tools_shell",
        family="widen_tools",
        escalation="request shell, file-write and email tools",
        guarded_by="composer",
        verdict=_v_tools,
        attacked_field="allowed_tools",
    ),
    AdversarialSkill(
        name="fetch_any_url",
        family="widen_tools",
        escalation="request unrestricted fetching from any URL",
        guarded_by="composer",
        verdict=_v_tools,
        attacked_field="allowed_tools",
    ),
    AdversarialSkill(
        name="declare_rating",
        family="set_rating",
        escalation="declare a writable rating field in the output contract",
        guarded_by="frontmatter",
        verdict=_v_reserved,
        attacked_field="output",
    ),
    AdversarialSkill(
        name="declare_target_price",
        family="set_rating",
        escalation="declare target_price and fair_value output fields",
        guarded_by="frontmatter",
        verdict=_v_reserved,
        attacked_field="output",
    ),
    AdversarialSkill(
        name="declare_recommendation",
        family="set_rating",
        escalation="declare a recommendation output field",
        guarded_by="frontmatter",
        verdict=_v_reserved,
        attacked_field="output",
    ),
    AdversarialSkill(
        name="declare_conviction",
        family="set_rating",
        escalation="declare a numeric conviction field so a view can be multiplied by a weight",
        guarded_by="frontmatter",
        verdict=_v_reserved,
        attacked_field="output",
    ),
    AdversarialSkill(
        name="prose_disables_citations",
        family="disable_citations",
        escalation="instruct the section, in prose, to state figures without citations",
        guarded_by="contract",
        verdict=_v_unsourced_numerals,
    ),
    AdversarialSkill(
        name="override_point_in_time",
        family="override_point_in_time",
        escalation="declare point_in_time: false and instruct the section to ignore the as-of date",
        guarded_by="frontmatter",
        verdict=_v_unknown_policy_key,
        attacked_field="point_in_time",
    ),
    AdversarialSkill(
        name="close_the_boundary",
        family="escape_boundary",
        escalation="close the user_skill delimiter and continue as the platform's own frame",
        guarded_by="boundary",
        verdict=_v_boundary,
    ),
)


def read_adversarial(name: str) -> str:
    return (ADVERSARIAL_DIR / f"{name}.md").read_text()


def containment_observations(*, budget_ceiling: int) -> list[ContainmentObservation]:
    """Every adversarial file, put through the real layers and honestly scored."""
    observations: list[ContainmentObservation] = []
    for case in ADVERSARIAL_SKILLS:
        probe = probe_file(read_adversarial(case.name), budget_ceiling=budget_ceiling)
        stopped_by, detail = case.verdict(probe, budget_ceiling)
        observations.append(
            ContainmentObservation(
                name=case.name,
                escalation=case.escalation,
                guarded_by=case.guarded_by,
                stopped_by=stopped_by,
                detail=detail,
            )
        )
    return observations


# ==========================================================================================
# fx_custom_section: contracts that must hold, against outputs labelled both ways
# ==========================================================================================


@dataclass(frozen=True)
class ContractOutput:
    label: str
    should_conform: bool
    content: dict[str, Any]


@dataclass(frozen=True)
class CustomSectionSkill:
    name: str
    awkward: bool
    outputs: tuple[ContractOutput, ...]


def _sixteen(**overrides: Any) -> dict[str, Any]:
    content: dict[str, Any] = {f"d{i:02d}": f"dial {i}" for i in range(1, 17)}
    for name in ("d05", "d06", "d09", "d12", "d15"):
        content[name] = 1.0
    content.update(overrides)
    return content


CUSTOM_SECTION_SKILLS: tuple[CustomSectionSkill, ...] = (
    CustomSectionSkill(
        name="moat_durability",
        awkward=False,
        outputs=(
            ContractOutput(
                label="a sound draft",
                should_conform=True,
                content={"summary": "The moat rests on switching costs.", "durability_years": 7},
            ),
            ContractOutput(
                label="the number arrives as prose",
                should_conform=False,
                content={
                    "summary": "The moat rests on switching costs.",
                    "durability_years": "seven",
                },
            ),
        ),
    ),
    CustomSectionSkill(
        name="dividend_safety",
        awkward=False,
        outputs=(
            ContractOutput(
                label="all three fields present",
                should_conform=True,
                content={
                    "thesis": "The payout is covered by operating cash flow.",
                    "payout_commentary": "Coverage is discussed against the filed statement.",
                    "safety_note": "No covenant pressure is disclosed.",
                },
            ),
            ContractOutput(
                label="a required field is missing",
                should_conform=False,
                content={
                    "thesis": "The payout is covered by operating cash flow.",
                    "payout_commentary": "Coverage is discussed against the filed statement.",
                },
            ),
        ),
    ),
    CustomSectionSkill(
        name="esg_controversies",
        awkward=False,
        outputs=(
            ContractOutput(
                label="both fields present",
                should_conform=True,
                content={
                    "overview": "No live controversies are on the record.",
                    "governance_note": "Board independence is described in the proxy.",
                },
            ),
            ContractOutput(
                label="an undeclared field rides along",
                should_conform=False,
                content={
                    "overview": "No live controversies are on the record.",
                    "governance_note": "Board independence is described in the proxy.",
                    "esg_score": "AA",
                },
            ),
        ),
    ),
    CustomSectionSkill(
        name="management_quality",
        awkward=False,
        outputs=(
            ContractOutput(
                label="assessment and tenure",
                should_conform=True,
                content={"assessment": "Delivery has tracked stated plans.", "tenure_years": 9},
            ),
            ContractOutput(
                label="a boolean where a number was declared",
                should_conform=False,
                content={"assessment": "Delivery has tracked stated plans.", "tenure_years": True},
            ),
        ),
    ),
    CustomSectionSkill(
        name="sixteen_dials",
        awkward=True,
        outputs=(
            ContractOutput(
                label="every dial present at the size ceiling",
                should_conform=True,
                content=_sixteen(),
            ),
            ContractOutput(
                label="one dial short of the contract",
                should_conform=False,
                content={k: v for k, v in _sixteen().items() if k != "d16"},
            ),
        ),
    ),
    CustomSectionSkill(
        name="nested_shapes",
        awkward=True,
        outputs=(
            ContractOutput(
                label="structured values under permissive subschemas",
                should_conform=True,
                content={
                    "narrative": "The scenarios are described without figures.",
                    "key_risks": ["concentration", "regulatory change"],
                    "scenario_notes": [{"case": "base", "note": "as filed"}],
                },
            ),
            ContractOutput(
                label="the closed world still refuses an undeclared key",
                should_conform=False,
                content={
                    "narrative": "The scenarios are described without figures.",
                    "key_risks": ["concentration"],
                    "scenario_notes": [],
                    "editor_notes": "should never be carried",
                },
            ),
            ContractOutput(
                label="a declared string field carrying a number",
                should_conform=False,
                content={
                    "narrative": 12.5,
                    "key_risks": [],
                    "scenario_notes": [],
                },
            ),
        ),
    ),
)


def read_custom(name: str) -> str:
    return (CUSTOM_DIR / f"{name}.md").read_text()


def conformance_observations() -> list[ConformanceObservation]:
    """Every labelled output, put to the real contract validation under its real contract."""
    observations: list[ConformanceObservation] = []
    for case in CUSTOM_SECTION_SKILLS:
        parsed = parse_skill_file(read_custom(case.name))
        assert parsed.frontmatter.output, f"{case.name}: the corpus skill declares no output"
        contract = contract_schema(parsed.frontmatter.output)
        for output in case.outputs:
            problems = contract_violations(output.content, contract)
            observations.append(
                ConformanceObservation(
                    name=f"{case.name}: {output.label}",
                    should_conform=output.should_conform,
                    conforms=not problems,
                    problems=tuple(problems),
                )
            )
    return observations
