"""Writing a thesis down, and every later act upon it, on the audit chain.

A thesis is a view a named person held at a time, with the evidence it rests on and the
questions that would defeat it (roadmap §3.5). This module is the only writer of the three
tables in :mod:`aer.db.models.judgement`, and every write here appends to the audit chain
with the thesis as its subject — the correlation ADR 0072 added to ``audit_events`` so that
"a thesis edit" would not be the least tamper-evident record in the system.

**What this module refuses to do is the point.** It never deletes a premise: a view held at
a time is a fact about that time, and a later change of mind is a withdrawal with a reason
on the row that was withdrawn. It never stores a conviction, a confidence or any figure a
calculation could consume, because a judgement is never a source reference (ADR 0074) and
the tables have no column that could hold one. And it never chooses a predicate for the
operator: a premise nothing can test is a premise a person reviews by a date, which is a
question the form asks rather than one this code answers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import JudgementKind, PremiseComparator
from aer.db.models import AuditEvent, Company, Judgement, Premise, Report, Thesis, User
from aer.errors import ConflictError, ValidationError

__all__ = [
    "COMPARATOR_WORDS",
    "SUBJECT_COMPANY",
    "Predicate",
    "add_premise",
    "companies_to_write_about",
    "premise_of",
    "reports_to_write_against",
    "retire_thesis",
    "subject_name",
    "theses_for",
    "thesis_of",
    "withdraw_premise",
    "write_thesis",
]

_log = structlog.get_logger("aer.services.theses")

# The one subject kind a thesis can be about today. A string rather than an enum for the
# reason `work_orders.subject_kind` is: the vocabulary is the tool registry's (ADR 0071),
# and a second kind arrives with the tool that resolves it.
SUBJECT_COMPANY = "company"

# What a comparator is called in a sentence. Words rather than symbols: ">=" reads fine in
# a formula and badly in a sentence, and a premise is a sentence — on the thesis page and
# in the predicate the monitor hands the model.
COMPARATOR_WORDS: dict[PremiseComparator, str] = {
    PremiseComparator.AT_LEAST: "at least",
    PremiseComparator.AT_MOST: "at most",
    PremiseComparator.ABOVE: "above",
    PremiseComparator.BELOW: "below",
}


# A threshold with more than this many decimal orders of magnitude either way overflows the
# NUMERIC(38, 12) column as a database error the form cannot explain, and no stored fact is
# compared at that scale in any case.
_THRESHOLD_EXPONENT_CEILING: Final = 25


@dataclass(frozen=True, slots=True)
class Predicate:
    """What would defeat a premise, as a test code can run: a metric against a threshold."""

    metric: str
    comparator: PremiseComparator
    threshold: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not self.metric.strip():
            message = "A predicate names the metric it tests; this one names nothing."
            raise ValidationError(message, context={"field": "metric"})
        too_large = abs(self.threshold.adjusted()) > _THRESHOLD_EXPONENT_CEILING
        if not self.threshold.is_finite() or too_large:
            message = (
                f"The threshold {self.threshold} is not a number a stored fact could be "
                "compared with. State a finite figure of ordinary size."
            )
            raise ValidationError(message, context={"field": "threshold"})
        if not self.unit.strip():
            message = (
                f"The threshold {self.threshold} has no unit. A bare number cannot be compared "
                "with a stored fact — a threshold in per cent must say so, or it will one day "
                "be compared against a figure in dollars."
            )
            raise ValidationError(message, context={"field": "unit"})


async def write_thesis(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    title: str,
    written_at: datetime | None = None,
    report_id: uuid.UUID | None = None,
) -> Thesis:
    """A new thesis about one company, with no premises yet.

    Raises:
        ValidationError: If the title is blank. A thesis is a document, and one with no
            name is a list nobody can refer to.
    """
    if not title.strip():
        message = "A thesis needs a title."
        raise ValidationError(message, context={"field": "title"})

    thesis = Thesis(
        user_id=user.id,
        subject_kind=SUBJECT_COMPANY,
        subject_id=company.id,
        title=title.strip(),
        report_id=report_id,
        written_at=written_at,
    )
    session.add(thesis)
    await session.flush()

    await _record(
        session,
        actor=user.email,
        event_type="thesis.written",
        thesis_id=thesis.id,
        payload={
            "thesis_id": str(thesis.id),
            "title": thesis.title,
            "subject_kind": thesis.subject_kind,
            "subject_id": str(thesis.subject_id),
            "ticker": company.ticker,
            "report_id": str(report_id) if report_id else None,
            "written_at": (written_at or datetime.now(UTC)).isoformat(),
        },
    )
    _log.info("thesis.written", thesis_id=str(thesis.id), ticker=company.ticker)
    return thesis


async def add_premise(
    session: AsyncSession,
    *,
    thesis: Thesis,
    actor: User,
    statement: str,
    basis: str,
    predicate: Predicate | None,
    review_by: date | None,
    held_at: datetime | None = None,
) -> Premise:
    """One more thing the thesis asserts, and what would defeat it.

    Either a predicate or a review date, and the caller decides which — never both absent.
    ADR 0079: a premise with no predicate is not second-class, but it must be somebody's to
    look at again, or the platform has stored a view it will silently stop asking about.

    Raises:
        ConflictError: If the thesis is retired. A retired thesis is a record of what was
            believed; adding to it would be rewriting that record.
        ValidationError: If the statement or the basis is blank, or if the premise has
            neither a predicate nor a review date.
    """
    if thesis.is_retired:
        message = (
            f"The thesis {thesis.title!r} was retired on {thesis.retired_at:%Y-%m-%d}. A "
            "retired thesis is a record of what was believed; write a new one rather than "
            "adding to it."
        )
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})
    if not statement.strip():
        message = "A premise states something; this one states nothing."
        raise ValidationError(message, context={"field": "statement"})
    if not basis.strip():
        message = (
            "A premise needs a stated basis — what you read, saw or reasoned that makes you "
            "hold it. A view with no grounds is a guess wearing a label."
        )
        raise ValidationError(message, context={"field": "basis"})
    if predicate is None and review_by is None:
        message = (
            "A premise nothing can test needs a date somebody will look at it again by. "
            "Otherwise it is a view the platform would stop asking about."
        )
        raise ValidationError(message, context={"field": "review_by"})

    judgement = Judgement(
        kind=JudgementKind.PREMISE,
        held_by=actor.email,
        held_at=held_at or datetime.now(UTC),
        basis=basis.strip(),
    )
    session.add(judgement)
    await session.flush()

    next_position = await session.scalar(
        select(func.coalesce(func.max(Premise.position), 0) + 1).where(
            Premise.thesis_id == thesis.id
        )
    )
    premise = Premise(
        judgement_id=judgement.id,
        thesis_id=thesis.id,
        position=int(next_position or 1),
        statement=statement.strip(),
        metric=predicate.metric.strip() if predicate else None,
        comparator=predicate.comparator if predicate else None,
        threshold=predicate.threshold if predicate else None,
        unit=predicate.unit.strip() if predicate else None,
        review_by=review_by,
    )
    session.add(premise)
    await session.flush()
    # Read the row back through its own loader. `recorded_at` is the database's, and the
    # judgement arrives on the premise by the joined load its relationship declares — both
    # would otherwise be first-touch lazy loads, which in an async session raise rather
    # than read. Assigning `premise.judgement` instead would fire the one-to-one backref,
    # which loads the parent's current value first and raises the same way.
    await session.refresh(judgement)
    loaded = await session.scalar(select(Premise).where(Premise.judgement_id == judgement.id))
    assert loaded is not None
    premise = loaded

    await _record(
        session,
        actor=actor.email,
        event_type="thesis.premise_added",
        thesis_id=thesis.id,
        payload={
            "thesis_id": str(thesis.id),
            "judgement_id": str(judgement.id),
            "position": premise.position,
            "statement": premise.statement,
            "basis": judgement.basis,
            "held_at": judgement.held_at.isoformat(),
            "predicate": (
                {
                    "metric": premise.metric,
                    "comparator": premise.comparator.value if premise.comparator else None,
                    "threshold": str(premise.threshold),
                    "unit": premise.unit,
                }
                if predicate
                else None
            ),
            "review_by": review_by.isoformat() if review_by else None,
        },
    )
    return premise


async def withdraw_premise(
    session: AsyncSession, *, premise: Premise, actor: User, reason: str
) -> Premise:
    """The holder no longer holds this, and says why. The row stays.

    Raises:
        ConflictError: If it is already withdrawn — a second withdrawal would overwrite the
            first reason, and the first is the one that was true at the time.
        ValidationError: If the reason is blank.
    """
    judgement = premise.judgement
    if judgement.is_withdrawn:
        message = "This premise was already withdrawn, and the reason given then stands."
        raise ConflictError(message, context={"judgement_id": str(judgement.id)})
    if not reason.strip():
        message = (
            "Withdrawing a premise needs a reason. A view given up without saying why is the "
            "least reviewable row this table could hold."
        )
        raise ValidationError(message, context={"field": "reason"})

    judgement.withdrawn_at = datetime.now(UTC)
    judgement.withdrawn_reason = reason.strip()
    await session.flush()

    await _record(
        session,
        actor=actor.email,
        event_type="thesis.premise_withdrawn",
        thesis_id=premise.thesis_id,
        payload={
            "thesis_id": str(premise.thesis_id),
            "judgement_id": str(judgement.id),
            "reason": judgement.withdrawn_reason,
        },
    )
    return premise


async def retire_thesis(
    session: AsyncSession, *, thesis: Thesis, actor: User, reason: str
) -> Thesis:
    """Put the thesis away with a stated reason. Nothing is deleted.

    Raises:
        ConflictError: If it is already retired.
        ValidationError: If the reason is blank.
    """
    if thesis.is_retired:
        message = "This thesis is already retired, and the reason given then stands."
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})
    if not reason.strip():
        message = (
            "Retiring a thesis needs a reason. What it was replaced by, or why it stopped "
            "mattering, is the first thing a later review asks."
        )
        raise ValidationError(message, context={"field": "reason"})

    thesis.retired_at = datetime.now(UTC)
    thesis.retirement_reason = reason.strip()
    await session.flush()

    await _record(
        session,
        actor=actor.email,
        event_type="thesis.retired",
        thesis_id=thesis.id,
        payload={"thesis_id": str(thesis.id), "reason": thesis.retirement_reason},
    )
    _log.info("thesis.retired", thesis_id=str(thesis.id))
    return thesis


# -- Reading ---------------------------------------------------------------------------------


async def theses_for(
    session: AsyncSession, *, user_id: uuid.UUID, retired: bool = False
) -> list[Thesis]:
    """This person's theses, newest first, premises loaded."""
    condition = Thesis.retired_at.is_not(None) if retired else Thesis.retired_at.is_(None)
    rows = await session.scalars(
        select(Thesis)
        .options(selectinload(Thesis.premises))
        .where(Thesis.user_id == user_id, condition)
        .order_by(Thesis.created_at.desc())
    )
    return list(rows)


async def thesis_of(
    session: AsyncSession, thesis_id: uuid.UUID, *, user_id: uuid.UUID
) -> Thesis | None:
    """One thesis belonging to ``user_id``, or ``None`` — the same answer for "no such
    thesis" and "not yours", so ids cannot be enumerated."""
    found: Thesis | None = await session.scalar(
        select(Thesis)
        .options(selectinload(Thesis.premises))
        .where(Thesis.id == thesis_id, Thesis.user_id == user_id)
    )
    return found


async def premise_of(
    session: AsyncSession, judgement_id: uuid.UUID, *, thesis: Thesis
) -> Premise | None:
    """One premise of this thesis, or ``None``."""
    found: Premise | None = await session.scalar(
        select(Premise).where(Premise.judgement_id == judgement_id, Premise.thesis_id == thesis.id)
    )
    return found


async def subject_name(session: AsyncSession, thesis: Thesis) -> str:
    """What the thesis is about, as a reader would name it.

    A company the registry no longer holds is still what the thesis was about; the row
    says so rather than rendering an id, because a thesis outlives its subject by design.
    """
    if thesis.subject_kind != SUBJECT_COMPANY:  # pragma: no cover -- one kind exists today
        return f"{thesis.subject_kind} {thesis.subject_id}"
    company = await session.get(Company, thesis.subject_id)
    if company is None:
        return "a company no longer on record"
    return f"{company.name} ({company.ticker})"


async def reports_to_write_against(session: AsyncSession) -> list[tuple[Report, Company]]:
    """Every approved report with the company it is about, newest first.

    A thesis may name the report it was written against (``report_id``), and this is the
    list the form offers. Approved only: a draft is still being argued with, and a thesis
    written against it would be written against a document that may yet change.
    """
    rows = await session.execute(
        select(Report, Company)
        .join(Company, Company.id == Report.company_id)
        .where(Report.immutable.is_(True))
        .order_by(Report.as_of_date.desc(), Report.created_at.desc())
    )
    return [(report, company) for report, company in rows.all()]


async def companies_to_write_about(session: AsyncSession) -> list[Company]:
    """Every company the platform can resolve, which is every company a thesis may be about.

    A thesis about a ticker nobody has looked up would be a view about a string; the
    research tool is what turns a string into a company, and the empty state says so.
    """
    rows = await session.scalars(select(Company).order_by(Company.name))
    return list(rows)


# -- The chain -------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    thesis_id: uuid.UUID,
) -> None:
    """Append one link to the audit chain, correlated to the thesis.

    Always via :meth:`AuditEvent.create_linked`, reading the current tail first, for the
    reason every other writer gives: a hand-built row with a hand-written hash is how a
    chain silently stops verifying. The subject correlation is what ADR 0072 added the two
    columns for — a thesis edit landing outside the chain would make the most consequential
    record in the system the least tamper-evident.
    """
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            subject_kind="thesis",
            subject_id=thesis_id,
        )
    )
    await session.flush()
