"""Everything a discounted cash flow needs somebody to agree to, gathered into one gate.

Gap B2c. :mod:`aer.services.assumption_proposals` derives the six assumptions the filings
answer and :mod:`aer.agents.assumptions` proposes the two they cannot. This module is the
layer that runs both, works out what is *still* missing, and assembles the payload the
operator approves.

**The gate exists because the numbers behind it came partly from a model.** Every other gate
in this platform approves work already done — a plan, a peer set, a draft. This one approves
work about to be done, on inputs a model influenced, which is why ADR 0046 requires the
payload to state for every assumption what the value is, what unit it is in, who or what
proposed it, and the justification. An operator approving a list they cannot interrogate is
not a control.

**Nothing here confirms anything.** :func:`aer.services.assumptions.propose` writes an
unconfirmed row whatever its caller says, and `as_quantity` refuses one, so the valuation
stays blocked until a person has been through the list.

**What cannot be proposed is named, with a reason.** A missing assumption and a broken
platform look identical from a page that shows neither. Every name a discounted cash flow
needs and this run could not put a number against appears in ``outstanding`` with a sentence
saying why — usually that its source is not wired into this run, occasionally that the
filings do not support it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.assumptions import PROPOSED_BY as OPINION_PROPOSED_BY
from aer.agents.assumptions import (
    AssumptionProposalAgent,
    AssumptionProposalInput,
    BoundedProposal,
    within_bounds,
)
from aer.agents.base import AgentContext
from aer.calc.dcf import DRIVER_NAMES, MAX_FORECAST_YEARS
from aer.core.sectors import ValuationModel, profile_for
from aer.db.models import Assumption, ResearchRequest
from aer.services.analysis import AnalysisOutcome
from aer.services.assumption_proposals import ProposalOutcome, propose_derived
from aer.services.assumptions import assumptions_for_request, propose
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation import SCALAR_NAMES

__all__ = [
    "COST_OF_CAPITAL_NAMES",
    "COST_OF_DEBT_ASSUMPTION",
    "EQUITY_RISK_PREMIUM_ASSUMPTION",
    "PROPOSABLE_NAMES",
    "REQUIRED_NAMES",
    "RISK_FREE_ASSUMPTION",
    "AssumptionGateOutcome",
    "assemble",
    "cost_of_debt_required",
    "dcf_permitted",
    "gate_payload",
    "gate_required",
    "outstanding_for",
    "refreshed_payload",
]

_log = structlog.get_logger("aer.services.assumption_gate")

RISK_FREE_ASSUMPTION: Final = "risk_free_rate"
EQUITY_RISK_PREMIUM_ASSUMPTION: Final = "equity_risk_premium"

COST_OF_DEBT_ASSUMPTION: Final = "cost_of_debt"
"""The pre-tax rate the company's borrowings cost it — required only when it cannot be derived.

Not in :data:`REQUIRED_NAMES`, because the valuation prefers to *derive* it: interest
expense over average debt is arithmetic on two filed lines, and a run that can do that
arithmetic must not pause demanding an opinion for a number the filings already carry. It
joins the gate's outstanding list only when :func:`cost_of_debt_required` says the
derivation is impossible — debt on the balance sheet, no interest expense under any concept
this platform maps — which is the exact condition under which the valuation would otherwise
refuse to run with nobody able to do anything about it. The live CHRW run is why: the filer
tags no interest expense at all, and this was the one discounted-cash-flow input no person
was allowed to supply.
"""

COST_OF_CAPITAL_NAMES: Final[tuple[str, ...]] = (
    RISK_FREE_ASSUMPTION,
    BETA_ASSUMPTION,
    EQUITY_RISK_PREMIUM_ASSUMPTION,
)
"""The three the discount rate decomposes into.

Not `wacc`. ADR 0046 and :data:`aer.services.valuation.SCALAR_NAMES` both refuse the
discount rate as a bare assumption, because one unexplained number would then stand in for
the whole cost-of-capital chain. These are its parts, each confirmable on its own terms: a
published yield, a regression or a judgement, and a premium that is always a judgement.
"""

REQUIRED_NAMES: Final[tuple[str, ...]] = (*DRIVER_NAMES, *SCALAR_NAMES, *COST_OF_CAPITAL_NAMES)
"""Every name a discounted cash flow needs confirmed before it can run.

The flat form of each driver. `aer.services.valuation._path_for` accepts a per-year path
instead, so an operator who enters `revenue_growth_y1..y5` satisfies `revenue_growth`; that
is a lookup convention rather than a second list, and :func:`outstanding_for` honours it.
"""

PROPOSABLE_NAMES: Final[tuple[str, ...]] = (
    *REQUIRED_NAMES,
    COST_OF_DEBT_ASSUMPTION,
    *(f"{driver}_y{year}" for driver in DRIVER_NAMES for year in range(1, MAX_FORECAST_YEARS + 1)),
)
"""Every name a person may put a value against by hand.

The flat names, the conditionally required cost of debt, and each driver's per-year form.
Bounded rather than free text because
:func:`aer.services.valuation.inputs_from` looks assumptions up *by name*: an operator who
typed `terminal_growth_rate` would see it stored, listed and confirmed, and would then
watch the valuation refuse to run for want of `terminal_growth` with no indication that the
two were different things.
"""

# Why a name has no proposal, when the reason is structural rather than about this company's
# filings. Stated per name because "beta is missing" tells an operator nothing about whether
# to wait for it or type it.
_NO_SOURCE_WIRED: Final[dict[str, str]] = {
    RISK_FREE_ASSUMPTION: (
        "No macroeconomic series has been acquired for this run, so there is no published "
        "government yield to propose. Enter the rate you are using and say which instrument "
        "and date it is from."
    ),
    BETA_ASSUMPTION: (
        "No price history has been acquired for this run, so no beta could be regressed. "
        "Enter one and say what it is measured against — beta is a first-class assumption "
        "here, and a documented, confirmed figure is more defensible than a regression "
        "nobody inspected."
    ),
    EQUITY_RISK_PREMIUM_ASSUMPTION: (
        "The equity risk premium is a judgement with no series behind it, and no role in "
        "this platform proposes one. Enter the premium you are using and cite where it "
        "comes from."
    ),
}

# Why the cost of debt is on the gate at all, said when it is. Not in `_NO_SOURCE_WIRED`
# because that dict holds reasons about the platform; this one is about the company's own
# filings, and it only ever appears when `cost_of_debt_required` established the condition.
_COST_OF_DEBT_UNDERIVABLE: Final = (
    "The balance sheet carries debt and the income statement shows no interest expense "
    "under any concept this platform maps, so the cost of that debt cannot be derived and "
    "the valuation will refuse to run without it. Enter the pre-tax rate the company's "
    "borrowings cost it and say where it comes from — a debt footnote, a traded bond "
    "yield, or cash interest paid over average borrowings."
)


@dataclass(frozen=True, slots=True)
class AssumptionGateOutcome:
    """What this run put in front of the operator, and what it could not.

    ``refused`` holds the model's proposals that failed the deterministic bounds. They are
    carried rather than dropped for the reason ADR 0046 gives: an assumption missing from
    the gate with no explanation is indistinguishable from a defect, and "the model proposed
    9% and this platform does not accept a perpetual rate above 4%" is something an operator
    can act on.
    """

    derived: ProposalOutcome = field(default_factory=ProposalOutcome)
    opinions: tuple[BoundedProposal, ...] = ()
    outstanding: tuple[tuple[str, str], ...] = ()
    model_consulted: bool = False

    @property
    def accepted_opinions(self) -> tuple[BoundedProposal, ...]:
        return tuple(item for item in self.opinions if item.accepted)

    @property
    def refused(self) -> tuple[BoundedProposal, ...]:
        return tuple(item for item in self.opinions if not item.accepted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "derived": self.derived.as_dict(),
            "opinions": [
                {
                    "name": item.name,
                    "value": str(item.value),
                    "justification": item.justification,
                    "confidence": item.confidence,
                    "accepted": item.accepted,
                    "refusal": item.refusal,
                }
                for item in self.opinions
            ],
            "outstanding": [{"name": name, "reason": reason} for name, reason in self.outstanding],
            "model_consulted": self.model_consulted,
        }


def dcf_permitted(sector_key: str) -> bool:
    """Whether a discounted cash flow may be built for a company of this kind.

    **An empty key means an ordinary company, and ordinary companies get the standard
    model.** That is the trap this function exists to close: `_classify` emits
    ``allowed_models`` as an empty list for a company matching no specialist profile, so a
    caller testing "is DCF in the allowed list?" would refuse a forecast for almost every
    company on the exchange. The permission lives in the profile, and no profile is the
    permissive state.
    """
    if not sector_key:
        return True
    profile = profile_for(sector_key)
    if profile is None:
        # A key naming no profile is a classification this build does not understand. The
        # cautious reading is the right one: an unknown specialist is still a specialist.
        return False
    return profile.permits(ValuationModel.DCF_FCFF)


def gate_required(produced: dict[str, Any]) -> bool:
    """Whether stopping this run for a person would achieve anything.

    A run whose sector mandate blocks a discounted cash flow never reaches a forecast, so
    it must not wait to approve one. Otherwise the run stops whenever there is anything
    for the operator to act on: proposals to confirm, or gaps to fill.

    **A run with outstanding assumptions used to proceed rather than pause** — a gate is
    only a control if the operator can clear it, and the assumptions surface could amend
    and confirm rows that existed but not create one that did not, so pausing over a
    missing beta left a run pausable and not resumable. The surface now creates rows (gap
    S2), which is what turned this branch around: the live AAPL run sailed through this
    gate in 9ms with four inputs missing and produced a report whose red team called the
    absent valuation material. Stopping is now the useful act — the operator supplies the
    risk-free rate or the beta, confirms, and resumes into a forecast.
    """
    if not produced.get("dcf_permitted", False):
        return False
    return bool(produced.get("assumptions")) or bool(produced.get("outstanding"))


def gate_payload(rows: Sequence[Assumption], outcome: AssumptionGateOutcome) -> dict[str, Any]:
    """Exactly what the assumptions gate approves, as one structure.

    Every row carries its justification and its proposer, so the operator can see at a
    glance which numbers came from the filings, which from a model, and which from them.
    Values are strings because they are ``Decimal`` and a JSON number would round them — a
    hash over a rounded figure is a hash over something nobody displayed.

    **Whether a row is confirmed is deliberately not in here.** The payload is what is being
    approved, and what is being approved is the *values*. Including the confirmation flag
    would change the hash the moment somebody confirmed a row on the assumptions page, so
    the approval recorded against the page they were shown would no longer match — a gate
    that invalidates itself the moment the operator does what it asked.
    """
    return {
        "assumptions": _row_dicts(rows),
        "outstanding": [{"name": name, "reason": reason} for name, reason in outcome.outstanding],
        "refused": [
            {"name": item.name, "value": str(item.value), "reason": item.refusal}
            for item in outcome.refused
        ],
        "skipped": list(outcome.derived.skipped),
    }


def refreshed_payload(
    rows: Sequence[Assumption], produced: dict[str, Any], *, years: int
) -> dict[str, Any]:
    """The gate payload as the rows now stand, not as the step recorded them.

    Every other gate approves work the run produced, so its payload is rightly frozen in
    the step output. This gate approves *inputs to work that has not happened yet*, and the
    inputs are rows an operator can amend or add while the run waits — which the live run's
    operator did, and then watched the gate page keep calling their values outstanding,
    because the page rendered the step's frozen record (gap A52). Worse than confusing: the
    valuation reads the rows, so a frozen payload lets an approval's hash cover figures the
    forecast will not use.

    ``assumptions`` and ``outstanding`` are therefore re-read from the rows. ``refused`` and
    ``skipped`` stay the step's own — they describe what the run did, and no row edit
    rewrites history. A name still outstanding keeps the reason the step recorded for it;
    one that only became outstanding since (a row somebody deleted) falls back to the
    structural reasons above. Unchanged rows reproduce the step's payload byte for byte, so
    the recorded hash still matches until somebody actually changes something.
    """
    recorded = {
        str(item.get("name", "")): str(item.get("reason", ""))
        for item in produced.get("outstanding", [])
        if isinstance(item, dict)
    }
    # A recorded name beyond REQUIRED_NAMES is a conditional requirement the step
    # established from the analysis — the cost of debt, when it could not be derived. The
    # condition cannot be re-derived here (there is no analysis to consult), and it does not
    # need to be: the filings do not change while a run waits, so the step's record *is* the
    # condition, and re-checking only "has a row appeared since?" is what keeps an unchanged
    # gate reproducing the step's payload byte for byte.
    conditional = tuple(name for name in recorded if name not in REQUIRED_NAMES)
    derived = ProposalOutcome(skipped=tuple(str(note) for note in produced.get("skipped", [])))
    return {
        "assumptions": _row_dicts(rows),
        "outstanding": [
            {"name": name, "reason": recorded.get(name) or _reason_for(name, outcome=derived)}
            for name in outstanding_for(rows, years=years, conditional=conditional)
        ],
        "refused": list(produced.get("refused", [])),
        "skipped": list(produced.get("skipped", [])),
    }


def _row_dicts(rows: Sequence[Assumption]) -> list[dict[str, Any]]:
    """One shape for a row in a gate payload, wherever the payload is assembled from."""
    return [
        {
            "name": row.name,
            "value": str(row.value),
            "unit": row.unit,
            "justification": row.justification,
            "proposed_by": row.proposed_by,
            "confidence": row.confidence,
        }
        for row in sorted(rows, key=lambda row: row.name)
    ]


def outstanding_for(
    rows: Sequence[Assumption], *, years: int, conditional: Sequence[str] = ()
) -> tuple[str, ...]:
    """The names a discounted cash flow still has no value for at all.

    A name is satisfied by a row of that name *or* by a complete per-year path, because
    :func:`aer.services.valuation._path_for` accepts either. A partial path does not satisfy
    it — a fade missing its third year is a mistake, and that module refuses it rather than
    filling the gap.

    ``conditional`` holds names this particular run needs beyond :data:`REQUIRED_NAMES` —
    today only :data:`COST_OF_DEBT_ASSUMPTION`, when :func:`cost_of_debt_required` says the
    derivation cannot run. They are the caller's to establish because the condition lives in
    the analysis, which this function deliberately does not see: :func:`refreshed_payload`
    has only the rows and the step's record, and both callers must produce the same list for
    the recorded hash to keep matching.

    Confirmed or not is deliberately not consulted here: this answers "is there a number to
    look at?", and the gate answers "has somebody agreed to it?".
    """
    present = {row.name for row in rows}
    missing: list[str] = []
    for name in REQUIRED_NAMES:
        if name in present:
            continue
        per_year = [f"{name}_y{year}" for year in range(1, years + 1)]
        if name in DRIVER_NAMES and all(key in present for key in per_year):
            continue
        missing.append(name)
    for name in conditional:
        if name not in present and name not in missing:
            missing.append(name)
    return tuple(missing)


def cost_of_debt_required(analysis: AnalysisOutcome) -> bool:
    """Whether this run needs a person to supply the cost of debt.

    The exact condition under which :func:`aer.services.valuation_run._cost_of_debt` will
    refuse: borrowings on the latest balance sheet and no interest expense to price them
    with. Mirrored here rather than imported because the refusal happens *after* the gate,
    which is the whole defect (report-quality R13): the CHRW run's operator confirmed every
    assumption the gate named, and the valuation then refused over a line the gate had never
    mentioned — a dependency named only in a report that arrives too late to act on.

    ``total_debt`` specifically, not any debt line, because that is what the valuation
    reads: a filer stating long-term debt alone yields no ``total_debt`` and is valued as
    all-equity, so demanding a rate for it here would pause the run over a number the
    forecast will never use.
    """
    latest = analysis.latest
    if latest is None:
        return False
    debt = latest.statements.get("total_debt")
    if debt is None or debt.value <= 0:
        return False
    return latest.statements.get("interest_expense") is None


async def assemble(
    session: AsyncSession,
    agent_context: AgentContext | None,
    *,
    request: ResearchRequest,
    analysis: AnalysisOutcome,
    sector_key: str = "",
    findings: Sequence[str] = (),
    years: int,
    job_id: uuid.UUID | None = None,
) -> AssumptionGateOutcome:
    """Propose everything this run can, and name everything it cannot.

    Args:
        agent_context: Where the two opinions come from. ``None`` skips the model call
            entirely — which is what a run with nothing to brief it on should do, and what
            every test that does not care about the model passes.
        sector_key: The confirmed classification, or ``""`` for an ordinary company. A run
            whose sector blocks a discounted cash flow proposes nothing at all: the
            assumptions would be for a forecast that is never going to be built.
    """
    if not dcf_permitted(sector_key):
        return AssumptionGateOutcome()

    derived, _ = await propose_derived(
        session, request_id=request.id, analysis=analysis, job_id=job_id
    )

    opinions: tuple[BoundedProposal, ...] = ()
    consulted = False
    if agent_context is not None:
        opinions = await _propose_opinions(
            session,
            agent_context,
            request=request,
            derived=derived,
            findings=findings,
            sector=sector_key,
            job_id=job_id,
        )
        consulted = True

    rows = await assumptions_for_request(session, request.id)
    conditional = (COST_OF_DEBT_ASSUMPTION,) if cost_of_debt_required(analysis) else ()
    missing = outstanding_for(rows, years=years, conditional=conditional)

    outcome = AssumptionGateOutcome(
        derived=derived,
        opinions=opinions,
        outstanding=tuple((name, _reason_for(name, outcome=derived)) for name in missing),
        model_consulted=consulted,
    )

    _log.info(
        "assumptions.gate_assembled",
        request_id=str(request.id),
        derived=len(derived.derived),
        proposed_opinions=len(outcome.accepted_opinions),
        refused_opinions=len(outcome.refused),
        outstanding=len(outcome.outstanding),
    )
    return outcome


# -- Internals ---------------------------------------------------------------------------


async def _propose_opinions(
    session: AsyncSession,
    agent_context: AgentContext,
    *,
    request: ResearchRequest,
    derived: ProposalOutcome,
    findings: Sequence[str],
    sector: str,
    job_id: uuid.UUID | None,
) -> tuple[BoundedProposal, ...]:
    """Ask for the two opinions, apply the bounds, and write the ones that pass.

    The discount rate is passed as ``None`` because the run does not have one yet: it
    decomposes into three assumptions that are themselves on this gate. See
    :class:`aer.agents.assumptions.AssumptionProposalInput` — the ceiling still applies, and
    the binding comparison happens in the calculation.
    """
    draft = await AssumptionProposalAgent().run(
        agent_context,
        AssumptionProposalInput(
            company_name=request.company_name,
            ticker=request.ticker,
            as_of_date=request.as_of_date.isoformat(),
            base_currency=request.base_currency,
            discount_rate=None,
            derived=tuple(
                f"{item.name} = {item.value} — {item.justification}" for item in derived.derived
            ),
            findings=tuple(findings),
            sector=sector,
        ),
    )

    bounded = within_bounds(draft, discount_rate=None)
    for item in bounded:
        if not item.accepted:
            continue
        await propose(
            session,
            request_id=request.id,
            name=item.name,
            value=item.value,
            # Both are dimensionless: a perpetual rate is a fraction, and an EV/EBITDA
            # multiple is money over money.
            unit="pure",
            justification=item.justification,
            proposed_by=OPINION_PROPOSED_BY,
            confidence=item.confidence,
            job_id=job_id,
        )
    return bounded


def _reason_for(name: str, *, outcome: ProposalOutcome) -> str:
    """Why this name has no proposal.

    The structural reasons first, because they are about the platform rather than about the
    company and an operator should not go looking through the filings for a beta this run
    never tried to compute.
    """
    structural = _NO_SOURCE_WIRED.get(name)
    if structural is not None:
        return structural

    # Only ever asked about when `cost_of_debt_required` established the condition, so the
    # sentence can assert what the filings lack rather than hedging.
    if name == COST_OF_DEBT_ASSUMPTION:
        return _COST_OF_DEBT_UNDERIVABLE

    for sentence in outcome.skipped:
        # The derivations phrase their refusals in prose starting with the assumption's own
        # name, so a prefix match attributes each one without a second data structure to
        # keep in step with them.
        if sentence.lower().startswith(name.replace("_", " ")):
            return sentence

    return (
        f"No proposal could be made for {name.replace('_', ' ')} from what this run acquired. "
        "Enter a value and say what it rests on."
    )


def provisional_discount_rate(values: dict[str, Decimal]) -> Decimal | None:
    """The cost of equity implied by three proposed values, or ``None``.

    **Not a valuation input and never persisted.** The real discount rate is decomposed by
    :mod:`aer.calc.wacc` from confirmed assumptions and recorded as calculations. This is a
    convenience for a caller that wants to show an operator roughly where the bounds will
    fall, and it returns ``None`` rather than a partial answer the moment any leg is absent.
    """
    try:
        risk_free = values[RISK_FREE_ASSUMPTION]
        beta = values[BETA_ASSUMPTION]
        premium = values[EQUITY_RISK_PREMIUM_ASSUMPTION]
    except KeyError:
        return None
    return risk_free + beta * premium
