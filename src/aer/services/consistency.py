"""The cross-section consistency check: one concept, one period, one value.

Gap C6. The live AAPL report contradicted itself — page 11's EBITDA against page 12's
own balance-sheet lines — and the contradiction was caught by the *red team*, a language
model, hours and pounds after the rows that disagreed were sitting in one database. That
is backwards for this platform: same concept, same period, two different published values
is arithmetic, and arithmetic belongs to code.

This pass runs at the validate step, over exactly what the report publishes — the facts
its claims name and the facts its sections' figure rows carry — never over the whole fact
store, because two facts nobody printed cannot contradict a reader. Rows are grouped by
what they measure (concept and period span), and any group holding more than one value
goes through the resolution ladder (:mod:`aer.core.disagreement`), which is already the
platform's one way of saying "these two numbers fight": the outcome lands as an ordinary
``disagreements`` row, deduplicated by fingerprint, shown at gate 2 and escalated when
material, exactly as a source conflict found any other way would be.

Facts on *different* periods are deliberately not compared. An annual EBITDA beside a
quarterly revenue is not a contradiction — it was the live report's failure to *label*
them that read as one, and the period stamp (gap C1) is that cure. This check owns the
other half: values that claim the same span and still disagree.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from itertools import combinations
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import SourceTier
from aer.db.models import Claim, FinancialFact, ReportSection, SourceDocument
from aer.services.disagreements import position_from_fact, resolve_and_record

__all__ = ["check_report_consistency"]

_log = structlog.get_logger("aer.services.consistency")

# Pairwise comparisons per group, bounded. A group needs three distinct values before
# this bites (three values are three pairs), and a group with more than five is telling
# us something is wrong with acquisition, not asking for twenty rows about it.
_MAX_PAIRS_PER_GROUP: Final = 10


async def check_report_consistency(session: AsyncSession, *, job_id: uuid.UUID) -> int:
    """Compare every published fact against its same-period neighbours.

    Returns the number of disagreement rows recorded (agreeing pairs record nothing).
    """
    facts, tiers = await _published_facts(session, job_id=job_id)

    grouped: dict[tuple[str, str, str, str], list[FinancialFact]] = {}
    for fact in facts:
        key = (
            fact.concept,
            fact.period_start.isoformat() if fact.period_start else "",
            fact.period_end.isoformat(),
            # Two segments' revenue for one span are two numbers, not a contradiction —
            # the dimension is part of what a fact measures, exactly as the period is.
            f"{fact.dimension_axis}={fact.dimension_member}" if fact.dimension_axis else "",
        )
        grouped.setdefault(key, []).append(fact)

    recorded = 0
    for (concept, _, _, _), rows in sorted(grouped.items()):
        distinct = _one_per_value(rows)
        if len(distinct) == 1:
            # One value for the span, however many rows and sections carry it: the
            # state every group should be in, and nothing to compare.
            continue
        topic = f"{concept}, {_period_label(distinct[0])}"
        for first, second in list(combinations(distinct, 2))[:_MAX_PAIRS_PER_GROUP]:
            row = await resolve_and_record(
                session,
                job_id=job_id,
                topic=topic,
                first=position_from_fact(
                    first, tier=tiers[first.source_document_id], label=_label_for(first)
                ),
                second=position_from_fact(
                    second, tier=tiers[second.source_document_id], label=_label_for(second)
                ),
            )
            if row is not None:
                recorded += 1

    _log.info(
        "consistency.checked",
        job_id=str(job_id),
        published_facts=len(facts),
        groups=len(grouped),
        conflicts_recorded=recorded,
    )
    return recorded


async def _published_facts(
    session: AsyncSession, *, job_id: uuid.UUID
) -> tuple[list[FinancialFact], dict[uuid.UUID, SourceTier]]:
    """The facts this report actually shows a reader, with their documents' tiers.

    Two ways a fact reaches the page, both collected: a numeric claim naming it, and a
    section figure row carrying its id — the same two channels the numeral rule accepts
    as lineage, which is what makes this the set of *published* values rather than the
    whole store.
    """
    sections = list(
        await session.scalars(select(ReportSection).where(ReportSection.job_id == job_id))
    )
    wanted: set[uuid.UUID] = set()

    section_ids = [section.id for section in sections]
    if section_ids:
        claims = await session.scalars(
            select(Claim).where(Claim.report_section_id.in_(section_ids))
        )
        wanted.update(
            claim.financial_fact_id for claim in claims if claim.financial_fact_id is not None
        )

    for section in sections:
        if isinstance(section.content, dict):
            wanted.update(_fact_ids_in(section.content))

    if not wanted:
        return [], {}

    rows = list(await session.scalars(select(FinancialFact).where(FinancialFact.id.in_(wanted))))
    documents = {
        document.id: document.source_tier
        for document in await session.scalars(
            select(SourceDocument).where(
                SourceDocument.id.in_({row.source_document_id for row in rows})
            )
        )
    }
    return rows, documents


def _fact_ids_in(value: Any) -> Iterable[uuid.UUID]:
    """Every ``financial_fact_id`` in a section's content, by the figure-row convention."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "financial_fact_id" and isinstance(item, str) and item:
                try:
                    yield uuid.UUID(item)
                except ValueError:
                    continue  # The execution boundary refuses fabricated ids; be lenient here.
            else:
                yield from _fact_ids_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _fact_ids_in(item)


def _one_per_value(rows: Sequence[FinancialFact]) -> list[FinancialFact]:
    """One representative per distinct (value, unit), earliest id for determinism.

    Two sections citing the *same* stored row — or two rows that agree exactly — are not
    a disagreement, and running agreeing pairs through the ladder would record nothing
    while costing comparisons.
    """
    seen: dict[tuple[str, str], FinancialFact] = {}
    for row in sorted(rows, key=lambda item: str(item.id)):
        key = (str(row.value), row.unit)
        seen.setdefault(key, row)
    return list(seen.values())


def _period_label(fact: FinancialFact) -> str:
    """The span as a reader names it: "FY2025", "Q3 2026", or the end date."""
    if fact.fiscal_period == "FY" and fact.fiscal_year:
        return f"FY{fact.fiscal_year}"
    if fact.fiscal_period and fact.fiscal_year:
        return f"{fact.fiscal_period} {fact.fiscal_year}"
    return fact.period_end.isoformat()


def _label_for(fact: FinancialFact) -> str:
    """How a position names itself in the ladder's rationale."""
    form = f" ({fact.form})" if fact.form else ""
    return f"{fact.concept} filed {fact.filed_date.isoformat()}{form}"
