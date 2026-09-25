"""Proposing, amending and confirming the numbers a valuation rests on.

A fact is fetched and hashed. An assumption is *chosen*, and the whole of its defensibility
is the record of who chose it and why. This module is that record.

**A model may propose; only a person may confirm.** :func:`propose` writes a proposal and
leaves the assumption unconfirmed however confident the proposer was — there is no argument,
no confidence threshold and no agent role that makes a model's choice usable on its own.
:func:`confirm` takes a :class:`~aer.db.models.user.User` and nothing else can call it,
which is the enforcement rather than a convention anybody has to remember.

**An amendment keeps the original on the record.** Amending writes a *new* proposal that
supersedes the old one; the old row is never touched. An operator who overrides a model's 9%
with 11% has made a judgement, and a report resting on 11% with no trace of the 9% has thrown
away the most useful thing about it.

**Confirmation does not survive a change.** Amending a confirmed assumption un-confirms it.
Otherwise "approved" would mean "approved at some value, possibly not this one", which is the
same failure the gate payload hashes exist to prevent one layer up.

**An unconfirmed assumption cannot enter a calculation.** :func:`as_quantity` refuses one, so
the refusal happens where the number would be used rather than at a review step somebody can
forget to run. That is the acceptance criterion of `docs/archive/phase-3-plan.md` task 24, in code.

**A confirmed assumption the current report rests on moves only when a person moves it.** An
assumption is unique per request and name, and every run on a request reads the same rows —
so a step of a later run that proposes over a confirmed row un-confirms the decision an
earlier run's operator took. On a full re-run that is the design: a request may only be run
again once it has no current report, and the new run's assumptions gate asks again. On a
refresh it was the defect the verdict round found (roadmap §3.19 item 73): the price step
proposed a day-later beta, the confirmed one became unconfirmed, a refresh has no gate to
confirm it again, and the valuation was not computed. So :func:`propose` refuses a machine's
proposal over a confirmed row while the request has a current report, whichever step makes
it. A person's amendment is unchanged, because changing a decision is theirs to do.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.assumption_scales import scale_complaint, unit_complaint
from aer.db.models import Assumption, AssumptionProposal, Report, User
from aer.errors import AerError, ValidationError

__all__ = [
    "AssumptionHeldError",
    "UnconfirmedAssumptionError",
    "amend",
    "as_quantity",
    "assumptions_for_request",
    "confirm",
    "confirmed_values",
    "history_of",
    "propose",
    "unconfirmed_for_request",
]

_log = structlog.get_logger("aer.services.assumptions")


class UnconfirmedAssumptionError(AerError):
    """A number nobody has agreed to was about to be used as though somebody had.

    Its own class rather than a `ValidationError`, because the caller that should catch this
    is a workflow step deciding whether a run can continue, and it needs to tell this apart
    from a malformed request.
    """

    code = "unconfirmed_assumption"
    http_status = 409


class AssumptionHeldError(AerError):
    """A machine proposed over a confirmed figure the request's current report rests on.

    Its own class because the one caller that meets it in the ordinary course — the price
    step of a refresh, regressing a beta a day later — must tell it apart from a regression
    that could not run: here the regression ran and is recorded as evidence, and the
    decision it would have displaced stands.
    """

    code = "assumption_held_by_current_report"
    http_status = 409


async def propose(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    name: str,
    value: Decimal,
    unit: str,
    justification: str,
    proposed_by: str,
    by_human: bool = False,
    confidence: float | None = None,
    job_id: uuid.UUID | None = None,
    accepted_anyway: bool = False,
) -> Assumption:
    """Put a value forward for an assumption, creating it if this is the first.

    **The result is never confirmed**, whatever ``by_human`` says. A person typing a value
    into the assumptions page has proposed it; confirming is a separate act, because the
    page shows a list and a reviewer who scrolls past a row has not agreed to it.

    Proposing again for an existing assumption supersedes the previous proposal and, if the
    assumption was confirmed, un-confirms it — except that a machine may not do so while the
    request has a current report, which rests on the confirmed value. See the module
    docstring.

    Args:
        accepted_anyway: Proceed despite an implausible value. The operator saying they
            mean it — hyperinflation, a distressed year — because a check they cannot get
            past is a gate nobody can clear. Recorded by the caller in the justification,
            which is where a reader will look for the reason.

    Raises:
        ValidationError: If the justification is blank, the unit is not one this platform
            understands, the unit is not the one this *assumption* is measured in, or the
            value is outside the plausible range for its name and nobody said otherwise.
            All refused here rather than at the database, so the message names the
            assumption — and here rather than at first use, so a valuation does not die
            several layers from the row that caused it.
        AssumptionHeldError: If a machine proposes over a confirmed assumption while the
            request has a current report. Nothing is written.
    """
    _require_justification(name, justification)
    _require_unit(name, unit)
    _require_expected_unit(name, unit)
    if not accepted_anyway:
        _require_plausible(name, value)

    existing = await session.scalar(
        select(Assumption).where(Assumption.request_id == request_id, Assumption.name == name)
    )

    if existing is not None and existing.approved and not by_human:
        await _refuse_if_held(session, existing, value=value, unit=unit, proposed_by=proposed_by)

    if existing is None:
        assumption = Assumption(
            request_id=request_id,
            job_id=job_id,
            name=name,
            value=value,
            unit=unit,
            justification=justification,
            confidence=confidence,
            proposed_by=proposed_by,
            approved=False,
        )
        session.add(assumption)
        await session.flush()
    else:
        assumption = existing
        assumption.value = value
        assumption.unit = unit
        assumption.justification = justification
        assumption.confidence = confidence
        assumption.proposed_by = proposed_by
        if job_id is not None:
            assumption.job_id = job_id
        # Re-proposing un-confirms. An approval recorded against a different value is not an
        # approval of this one.
        assumption.approved = False
        assumption.approved_at = None
        assumption.approved_by = None

    latest = await _latest_proposal(session, assumption.id)
    proposal = AssumptionProposal(
        assumption_id=assumption.id,
        value=value,
        unit=unit,
        justification=justification,
        confidence=confidence,
        proposed_by=proposed_by,
        by_human=by_human,
        supersedes_id=latest.id if latest is not None else None,
        sequence=1 if latest is None else latest.sequence + 1,
    )
    session.add(proposal)
    await session.flush()

    # Refreshed before anybody reads them. `value` is NUMERIC(38,12): a Decimal assigned in
    # Python keeps whatever places it was written with, and the database returns twelve. The
    # assumptions payload is hashed, so a row read from memory and one read from the database
    # produce different hashes for the same assumption -- and confirming what the page showed
    # would be refused because the page showed the unrefreshed one. The plan gate hit exactly
    # this in task 10 and for exactly this reason.
    await session.refresh(assumption)
    await session.refresh(proposal)

    _log.info(
        "assumption.proposed",
        assumption_id=str(assumption.id),
        name=name,
        value=str(value),
        unit=unit,
        proposed_by=proposed_by,
        by_human=by_human,
    )
    return assumption


async def amend(
    session: AsyncSession,
    *,
    assumption: Assumption,
    value: Decimal,
    justification: str,
    actor: User,
    unit: str | None = None,
    accepted_anyway: bool = False,
) -> Assumption:
    """A person replaces the current value with one of their own.

    Distinct from :func:`propose` only in that the actor is a :class:`User` and is recorded
    as such. Keeping it separate makes "which assumptions did a person change?" a query on
    ``by_human`` rather than a guess from the shape of ``proposed_by``.

    Raises:
        ValidationError: If the justification is blank. An amendment without a reason
            overrides a reasoned figure with an unreasoned one and leaves no way to tell.
            Also for a unit or a value :func:`propose` refuses — the checks belong to the
            value, not to the route that supplied it.
    """
    return await propose(
        session,
        request_id=assumption.request_id,
        name=assumption.name,
        value=value,
        unit=unit if unit is not None else assumption.unit,
        justification=justification,
        proposed_by=actor.email,
        by_human=True,
        confidence=None,
        job_id=assumption.job_id,
        accepted_anyway=accepted_anyway,
    )


async def confirm(session: AsyncSession, *, assumption: Assumption, actor: User) -> Assumption:
    """A person agrees that this value may be used.

    **The only way an assumption becomes usable.** Takes a ``User`` because there is no
    agent-shaped argument that could be passed instead — the type is the control.

    Confirming twice is refused. An approval is a decision, not a state to re-assert; a
    second one is either noise in the audit trail or a change of mind, and a change of mind
    about a value is an amendment.

    Raises:
        ValidationError: If it is already confirmed.
    """
    if assumption.approved:
        message = (
            f"The assumption {assumption.name!r} was already confirmed by "
            f"{assumption.approved_by} at {assumption.approved_at}. Confirming again would "
            "record a second decision about the same value; changing it is an amendment."
        )
        raise ValidationError(
            message,
            context={"assumption_id": str(assumption.id), "name": assumption.name},
        )

    assumption.approved = True
    assumption.approved_at = datetime.now(UTC)
    assumption.approved_by = actor.email
    await session.flush()

    _log.info(
        "assumption.confirmed",
        assumption_id=str(assumption.id),
        name=assumption.name,
        value=str(assumption.value),
        confirmed_by=actor.email,
    )
    return assumption


def as_quantity(assumption: Assumption) -> Quantity:
    """The assumption as something a calculation can take.

    **Refuses an unconfirmed one.** This is where invariant 3's "no figure reaches a report
    unless it is a stored fact, a recorded calculation or an attestation" meets assumptions:
    a value somebody *chose* is not on that list at all, and reaches a report only through
    the calculation it feeds — so "somebody" has to mean a person.

    The returned quantity carries a :class:`~aer.calc.units.SourceRef` of kind
    ``assumption``, so anything computed from it resolves back to this row and its
    justification rather than to a bare number.

    Raises:
        UnconfirmedAssumptionError: If nobody has confirmed it.
    """
    if not assumption.approved:
        message = (
            f"The assumption {assumption.name!r} ({assumption.value} {assumption.unit}) has "
            "not been confirmed by anybody. A model may propose a value; only a person may "
            "confirm one, and a valuation may not rest on a number nobody agreed to."
        )
        raise UnconfirmedAssumptionError(
            message,
            context={
                "assumption_id": str(assumption.id),
                "name": assumption.name,
                "proposed_by": assumption.proposed_by,
            },
        )

    return Quantity.of(
        assumption.value,
        Unit.parse(assumption.unit),
        source=SourceRef.assumption(assumption.id, label=assumption.name),
    )


async def assumptions_for_request(session: AsyncSession, request_id: uuid.UUID) -> list[Assumption]:
    """Every assumption on a request, by name. What the assumptions page lists."""
    rows = await session.scalars(
        select(Assumption).where(Assumption.request_id == request_id).order_by(Assumption.name)
    )
    return list(rows)


async def unconfirmed_for_request(session: AsyncSession, request_id: uuid.UUID) -> list[Assumption]:
    """The assumptions still waiting on a person. What blocks a valuation."""
    rows = await session.scalars(
        select(Assumption)
        .where(Assumption.request_id == request_id, Assumption.approved.is_(False))
        .order_by(Assumption.name)
    )
    return list(rows)


async def confirmed_values(session: AsyncSession, request_id: uuid.UUID) -> dict[str, Quantity]:
    """The confirmed assumptions as quantities, keyed by name.

    The base case. :func:`~aer.services.scenarios.resolve` layers a scenario's overrides on
    top of this rather than on a copy of it, which is why correcting the base case propagates.
    """
    return {
        assumption.name: as_quantity(assumption)
        for assumption in await assumptions_for_request(session, request_id)
        if assumption.approved
    }


async def history_of(
    session: AsyncSession, assumption_id: uuid.UUID
) -> Sequence[AssumptionProposal]:
    """Every value ever put forward for this assumption, oldest first."""
    rows = await session.scalars(
        select(AssumptionProposal)
        .where(AssumptionProposal.assumption_id == assumption_id)
        .order_by(AssumptionProposal.sequence)
    )
    return list(rows)


# -- Internals ---------------------------------------------------------------------------


async def _latest_proposal(
    session: AsyncSession, assumption_id: uuid.UUID
) -> AssumptionProposal | None:
    # By `sequence`, not by `created_at`. Postgres `now()` is transaction-start time, so a
    # propose-then-amend in one transaction writes two rows with identical timestamps and
    # "the latest" would be whichever the planner returned first.
    latest: AssumptionProposal | None = await session.scalar(
        select(AssumptionProposal)
        .where(AssumptionProposal.assumption_id == assumption_id)
        .order_by(AssumptionProposal.sequence.desc())
        .limit(1)
    )
    return latest


async def _refuse_if_held(
    session: AsyncSession,
    existing: Assumption,
    *,
    value: Decimal,
    unit: str,
    proposed_by: str,
) -> None:
    """Refuse a machine's proposal over a confirmed row the current report rests on.

    "Current" is the report the platform asserts now: approved, neither superseded nor
    withdrawn. A report names its request, and an assumption belongs to the request every
    job on it shares, so a refresh's step meets the report it is refreshing here.
    """
    report: Report | None = await session.scalar(
        select(Report)
        .where(
            Report.request_id == existing.request_id,
            Report.immutable.is_(True),
            Report.superseded_by.is_(None),
            Report.superseded_at.is_(None),
        )
        .limit(1)
    )
    if report is None:
        return
    message = (
        f"The {existing.name.replace('_', ' ')} the current report rests on stands at "
        f"{existing.value.normalize():f} {existing.unit}, as confirmed. This run's "
        f"{value.normalize():f} {unit} is recorded as evidence and not proposed: a later run "
        "does not replace a decision the current report rests on. Changing it is an "
        "amendment, made by a person."
    )
    _log.info(
        "assumption.held",
        assumption_id=str(existing.id),
        name=existing.name,
        held=str(existing.value),
        offered=str(value),
        proposed_by=proposed_by,
        report_id=str(report.id),
    )
    raise AssumptionHeldError(
        message,
        context={
            "name": existing.name,
            "assumption_id": str(existing.id),
            "report_id": str(report.id),
        },
    )


def _require_justification(name: str, justification: str) -> None:
    if not justification.strip():
        message = (
            f"The assumption {name!r} was proposed with no justification. An assumption "
            "without a stated reason is a guess wearing a label, and the moment one is "
            "allowed the table fills with them."
        )
        raise ValidationError(message, context={"name": name})


def _require_expected_unit(name: str, unit: str) -> None:
    """Refuse a unit that parses but is not what this assumption is measured in.

    `_require_unit` asks whether the algebra can read it; this asks whether it is *right*.
    `USD` for a tax rate parses perfectly and is nonsense, and a dimensioned quantity where
    the forecast expects a fraction fails much later, inside arithmetic that cannot say
    which of its inputs was wrong.
    """
    complaint = unit_complaint(name, unit)
    if complaint is None:
        return
    raise ValidationError(complaint, context={"name": name, "unit": unit})


def _require_plausible(name: str, value: Decimal) -> None:
    """Refuse a value that reads as a typing mistake, naming the mistake.

    The one this exists for is the decimal fraction: `4.5` where `0.045` was meant is a
    well-formed number in the right unit that discounts at 450%, and nothing downstream can
    tell it from a figure somebody chose. Refused rather than corrected — a value quietly
    moved into range is a number nobody chose standing where one somebody chose should be —
    and escapable, because the operator may mean it.
    """
    complaint = scale_complaint(name, value)
    if complaint is None:
        return
    raise ValidationError(complaint, context={"name": name, "value": str(value)})


def _require_unit(name: str, unit: str) -> None:
    """Refuse a unit the platform cannot parse, here rather than at first use.

    A unit string is stored as text and only becomes a :class:`~aer.calc.units.Unit` when a
    calculation reads it. Without this, a typo sits in the database looking correct until a
    valuation fails halfway through, several layers from the row that caused it.
    """
    try:
        Unit.parse(unit)
    except Exception as exc:
        message = (
            f"The assumption {name!r} is stated in {unit!r}, which is not a unit this "
            "platform understands. A unit that cannot be parsed is one no calculation can "
            "check, and the check is the reason units are carried at all."
        )
        raise ValidationError(message, context={"name": name, "unit": unit}) from exc
