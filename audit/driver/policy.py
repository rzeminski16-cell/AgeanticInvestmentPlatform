"""The gate policy: what a careful operator would decide, written down before any run.

Pure functions over the gate payloads the platform itself builds, so the same rule can be
tested offline against the fake scene and applied live. A policy never overrides a
citation, never settles a disagreement, and never approves past a failed blocking metric:
those are the operator's own calls and the driver stops for them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from audit.subjects import RATIO_UNIT, OperatorAssumption, Subject

__all__ = [
    "BOUNDS",
    "SPINE_KEYS",
    "GateVerdict",
    "decide_assumptions",
    "decide_final",
    "decide_peer_set",
    "decide_plan",
    "decide_sector",
    "decide_theme_set",
    "decide_unmapped",
]

# The eighteen-section spine `migrations/versions/0023_the_eighteen_section_spine.py` seeds.
SPINE_KEYS: Final[tuple[str, ...]] = (
    "executive_summary",
    "investment_thesis",
    "business_overview",
    "segment_analysis",
    "industry_landscape",
    "management_governance",
    "historical_financial_analysis",
    "earnings_quality",
    "balance_sheet_liquidity",
    "cash_flow_analysis",
    "capital_allocation",
    "growth_outlook",
    "valuation_dcf",
    "scenarios_sensitivities",
    "key_risks",
    "catalysts",
    "prior_research_comparison",
    "validation_disagreements",
)

# Plausibility bounds for the numbers a valuation rests on, as fractions. A value outside
# is amended to the nearest bound with the reason recorded, which is what a careful operator
# would do at the gate, and the amendment is itself a finding about the proposal.
BOUNDS: Final[dict[str, tuple[Decimal, Decimal]]] = {
    "risk_free_rate": (Decimal("0.03"), Decimal("0.055")),
    "equity_risk_premium": (Decimal("0.04"), Decimal("0.065")),
    "terminal_growth": (Decimal("0.0"), Decimal("0.035")),
    "beta": (Decimal("0.3"), Decimal("2.0")),
    "exit_multiple": (Decimal("4"), Decimal("30")),
    "cost_of_debt": (Decimal("0.02"), Decimal("0.10")),
    "tax_rate": (Decimal("0.0"), Decimal("0.35")),
    "revenue_growth": (Decimal("-0.20"), Decimal("0.50")),
    "ebit_margin": (Decimal("-0.20"), Decimal("0.70")),
    "capex_intensity": (Decimal("0.0"), Decimal("0.50")),
    "depreciation_intensity": (Decimal("0.0"), Decimal("0.30")),
    "working_capital_intensity": (Decimal("-0.50"), Decimal("0.50")),
    "return_on_equity": (Decimal("-0.20"), Decimal("0.40")),
    "payout_ratio": (Decimal("0.0"), Decimal("1.0")),
}

MAX_PEERS: Final = 8
MIN_SECTIONS_GENERATED: Final = 17
UNMAPPED_SHARE_WORTH_NOTING: Final = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """What the policy decided at one gate, and everything it noticed on the way."""

    approve: bool
    rationale: str
    findings: tuple[str, ...] = ()
    # Operator-owned rows to write before approving (the assumptions gate only).
    supply: tuple[OperatorAssumption, ...] = ()
    # Rows whose value the policy amends to a bound before approving (name -> new value).
    amend: tuple[OperatorAssumption, ...] = ()
    stop_reason: str | None = None


def decide_plan(payload: Mapping[str, Any], subject: Subject, *, cap_gbp: Decimal) -> GateVerdict:
    """Gate 1: the plan names the spine and the subject's filings, and costs what it should."""
    findings: list[str] = []
    listed = {
        str(item.get("key", item)) if isinstance(item, dict) else str(item)
        for item in payload.get("section_listing", [])
    }
    if not listed:
        listed = {str(s.get("key", "")) for s in payload.get("sections", []) if isinstance(s, dict)}
    missing = [key for key in SPINE_KEYS if key not in listed]
    if missing:
        findings.append(
            f"plan lists {len(listed)} sections; spine keys missing: {', '.join(missing)}"
        )

    sources = " ".join(str(s) for s in payload.get("planned_sources", [])).lower()
    filing_words = ("10-k", "20-f", "annual report", "10-q", "edgar", "sec")
    if not any(word in sources for word in filing_words):
        return GateVerdict(
            approve=False,
            rationale="The plan names no SEC filing among its sources.",
            findings=tuple(findings),
            stop_reason="plan names no filing",
        )

    try:
        estimate = Decimal(str(payload.get("estimated_cost_gbp", "0")))
    except InvalidOperation:
        estimate = Decimal(0)
    if estimate > cap_gbp:
        findings.append(f"plan estimate £{estimate} exceeds the request cap £{cap_gbp}")

    prior = str(payload.get("prior_research", ""))
    if subject.key == "msft1" and prior:
        findings.append("the first MSFT run's planner was shown prior research; independence lost")
    if subject.key == "msft2" and not prior:
        findings.append("the refresh run's planner saw no prior research (ADR 0064 did not apply)")

    critique = payload.get("critique") or {}
    if not critique:
        findings.append("the plan carries no critique block")

    return GateVerdict(
        approve=True,
        rationale=(
            f"Audit policy: spine {len(SPINE_KEYS) - len(missing)}/{len(SPINE_KEYS)} listed, filings "
            f"named among {len(payload.get('planned_sources', []))} planned sources, estimate "
            f"£{estimate} against cap £{cap_gbp}."
        ),
        findings=tuple(findings),
    )


def decide_sector(payload: Mapping[str, Any], subject: Subject) -> GateVerdict:
    """The sector gate fires only on a specialist proposal; it must match the expectation."""
    proposed = str(payload.get("sector_key", ""))
    if subject.expected_sector is None:
        return GateVerdict(
            approve=False,
            rationale=f"The classifier proposed {proposed!r} for a subject expected to be ordinary.",
            findings=(f"unexpected sector gate: {proposed!r} proposed for {subject.ticker}",),
            stop_reason="unexpected sector proposal",
        )
    if proposed != subject.expected_sector:
        return GateVerdict(
            approve=False,
            rationale=f"The classifier proposed {proposed!r}; the audit expected {subject.expected_sector!r}.",
            findings=(
                f"sector mismatch: {proposed!r} proposed, {subject.expected_sector!r} expected",
            ),
            stop_reason="sector mismatch",
        )
    return GateVerdict(
        approve=True,
        rationale=(
            f"Audit policy: {proposed!r} as expected; blocked models "
            f"{payload.get('blocked_models', [])}, allowed {payload.get('allowed_models', [])}."
        ),
        findings=tuple(f"sector warning: {w}" for w in payload.get("warnings", [])),
    )


def decide_peer_set(payload: Mapping[str, Any], subject: Subject) -> GateVerdict:
    peers = payload.get("peers", [])
    names = [str(p.get("identifier", "")) for p in peers]
    findings: list[str] = []
    if len(peers) > MAX_PEERS:
        findings.append(f"{len(peers)} peers proposed, above the {MAX_PEERS} the policy expects")
    if len(set(names)) != len(names):
        findings.append("duplicate peers in the slate")
    if subject.ticker in names or subject.company_name.lower() in " ".join(
        str(p.get("name", "")).lower() for p in peers
    ):
        findings.append("the subject appears in its own peer set")
    return GateVerdict(
        approve=True,
        rationale=f"Audit policy: {len(peers)} peer(s) proposed by {payload.get('proposed_by', '')!r}: {', '.join(names)}.",
        findings=tuple(findings),
    )


def decide_theme_set(payload: Mapping[str, Any], subject: Subject) -> GateVerdict:
    themes = payload.get("themes", [])
    labels = [str(t.get("label", "")) for t in themes]
    findings = tuple("a theme with an empty label" for label in labels if not label.strip())
    return GateVerdict(
        approve=True,
        rationale=f"Audit policy: {len(themes)} theme(s) proposed for {subject.ticker}: {', '.join(labels)}.",
        findings=findings,
    )


def decide_unmapped(
    payload: Mapping[str, Any], subject: Subject, *, facts_chosen: int
) -> GateVerdict:
    """Unmapped concepts are a completeness finding, never a reason to stop a run."""
    errors = list(payload.get("load_errors", []))
    if errors:
        return GateVerdict(
            approve=False,
            rationale="The extract step recorded load errors.",
            findings=tuple(f"load error: {e}" for e in errors),
            stop_reason="extract load errors",
        )
    if facts_chosen <= 0:
        return GateVerdict(
            approve=False,
            rationale="The extract step chose no facts; there is nothing to calculate from.",
            findings=("extract chose zero facts",),
            stop_reason="no facts",
        )
    findings: list[str] = []
    for concept in payload.get("unmapped_concepts", []):
        share = concept.get("share") if isinstance(concept, dict) else None
        try:
            share_value = Decimal(str(share)) if share is not None else None
        except InvalidOperation:
            share_value = None
        if share_value is not None and share_value >= UNMAPPED_SHARE_WORTH_NOTING:
            findings.append(
                f"unmapped {concept.get('tag') or concept.get('name')!s} at {share_value:.0%} of "
                f"{concept.get('reference', 'its reference line')}"
            )
    tags = list(payload.get("unmapped_tags", []))
    return GateVerdict(
        approve=True,
        rationale=(
            f"Audit policy for {subject.ticker}: {len(tags)} unmapped tag(s), "
            f"{len(payload.get('refused_tags', []))} refused, "
            f"{len(payload.get('mapped_concepts', []))} mapped concept(s), {facts_chosen} fact(s) chosen; "
            "gaps recorded as completeness findings."
        ),
        findings=tuple(findings),
    )


def _as_fraction(value: Any, unit: str) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if unit.lower() in {"percent", "%", "pct"}:
        return number / Decimal(100)
    return number


def decide_assumptions(payload: Mapping[str, Any], subject: Subject) -> GateVerdict:
    """Supply what is outstanding from the operator's list; amend what is out of bounds."""
    findings: list[str] = []
    supply: list[OperatorAssumption] = []
    amend: list[OperatorAssumption] = []
    stated = {a.name: a for a in subject.assumptions}

    for item in payload.get("outstanding", []):
        name = str(item.get("name", ""))
        reason = str(item.get("reason", ""))
        if name in stated:
            supply.append(stated[name])
            findings.append(f"outstanding {name}: supplied by the operator ({reason[:80]}…)")
        else:
            return GateVerdict(
                approve=False,
                rationale=f"{name!r} is outstanding and the operator stated no value for it.",
                findings=(*findings, f"outstanding {name}: no operator value ({reason})"),
                stop_reason=f"outstanding assumption {name}",
            )

    for row in payload.get("assumptions", []):
        name = str(row.get("name", ""))
        base = name.rsplit("_y", 1)[0] if "_y" in name else name
        bounds = BOUNDS.get(base)
        if bounds is None:
            continue
        fraction = _as_fraction(row.get("value"), str(row.get("unit", RATIO_UNIT)))
        if fraction is None:
            findings.append(f"{name}: value {row.get('value')!r} is not a number")
            continue
        low, high = bounds
        if fraction < low or fraction > high:
            clamped = min(max(fraction, low), high)
            amend.append(
                OperatorAssumption(
                    name=name,
                    value=clamped,
                    unit=RATIO_UNIT,
                    justification=(
                        f"Audit policy: the proposed {fraction} lies outside the {low}-{high} band; "
                        f"amended to the nearest bound by the operator's standing policy."
                    ),
                )
            )
            findings.append(
                f"{name} proposed at {fraction} by {row.get('proposed_by')!r}, outside {low}-{high}; amended to {clamped}"
            )

    return GateVerdict(
        approve=True,
        rationale=(
            f"Audit policy: {len(payload.get('assumptions', []))} row(s) on the gate, "
            f"{len(supply)} supplied by the operator, {len(amend)} amended to a bound, "
            f"{len(payload.get('refused', []))} refused by the platform, "
            f"{len(payload.get('skipped', []))} skipped."
        ),
        findings=tuple(findings),
        supply=tuple(supply),
        amend=tuple(amend),
    )


@dataclass(frozen=True, slots=True)
class FinalGateFacts:
    """What the final gate shows, reduced to what the policy reads."""

    sections: tuple[dict[str, Any], ...]
    triggers: tuple[dict[str, Any], ...]
    escalations: tuple[dict[str, Any], ...]
    revisions: tuple[dict[str, Any], ...]
    failed_metrics: tuple[str, ...] = field(default_factory=tuple)


def decide_final(facts: FinalGateFacts) -> GateVerdict:
    """Approve a draft that is whole and passed its own checks; stop on anything else."""
    generated = [s for s in facts.sections if str(s.get("status", "")).lower() == "generated"]
    pending = [s for s in facts.sections if str(s.get("status", "")).lower() == "pending"]
    failed = [s for s in facts.sections if str(s.get("status", "")).lower() == "failed"]
    findings = [
        f"section {s.get('key')} {str(s.get('status', '')).lower()}: {s.get('note') or 'no note'}"
        for s in facts.sections
        if str(s.get("status", "")).lower() != "generated" or s.get("note")
    ]
    look_ahead = [t for t in facts.triggers if "look" in str(t.get("kind", "")).lower()]
    if pending:
        return GateVerdict(
            False, "Sections are still pending.", tuple(findings), stop_reason="pending sections"
        )
    if len(generated) < MIN_SECTIONS_GENERATED:
        return GateVerdict(
            False,
            f"Only {len(generated)} of {len(facts.sections)} sections generated ({len(failed)} failed).",
            tuple(findings),
            stop_reason="too many sections lost",
        )
    if look_ahead:
        return GateVerdict(
            False, "A look-ahead trigger fired.", tuple(findings), stop_reason="look-ahead trigger"
        )
    if facts.failed_metrics:
        return GateVerdict(
            False,
            f"Blocking metric(s) failed: {', '.join(facts.failed_metrics)}.",
            tuple(findings),
            stop_reason="failed metrics",
        )
    triggers = [str(t.get("kind", "")) for t in facts.triggers]
    material = [e for e in facts.escalations if e.get("material")]
    return GateVerdict(
        approve=True,
        rationale=(
            f"Audit policy: {len(generated)}/{len(facts.sections)} sections generated, "
            f"{len(failed)} failed, triggers {triggers or 'none'}, {len(facts.escalations)} "
            f"challenge(s) ({len(material)} material) left unsettled for the reader, "
            f"{len(facts.revisions)} revision note(s), no metric failing."
        ),
        findings=tuple(findings) + tuple(f"trigger fired: {t}" for t in triggers),
    )
