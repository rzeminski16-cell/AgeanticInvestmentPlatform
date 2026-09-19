"""The document is checked against itself: one figure, one period, one value, no denials.

Gap C6, then ADR 0125. The live AAPL report contradicted itself — page 11's EBITDA against
page 12's own balance-sheet lines — and the contradiction was caught by the *red team*, a
language model, hours and pounds after the rows that disagreed were sitting in one database.
That is backwards for this platform: same concept, same period, two different published
values is arithmetic, and arithmetic belongs to code.

This runs at the validate step, over exactly what the report publishes — the figures its
claims name and the ones its sections' figure rows carry — never over the whole store,
because two rows nobody printed cannot contradict a reader. It makes three passes:

1. **Published facts.** Grouped by what they measure — concept, period span, dimension —
   and any group holding more than one value goes through the resolution ladder
   (:mod:`aer.core.disagreement`), because two *sources* disagreeing is exactly what the
   ladder is for.
2. **Published calculations** (ADR 0125). One name, one case, one period, one value. The
   ladder is **not** run: both sides are this run's own arithmetic, so there is no tier to
   prefer and no later filing to take, and :func:`~aer.core.disagreement.figure_contradiction`
   states that reason instead of borrowing a rung's.
3. **Negative assertions** (ADR 0125). A sentence whose subject is the missing disclosure,
   in a document that prints the figure it says is missing. msft1's executive summary
   denied a discounted cash flow thirty lines above the valuation table; its key risks
   called operating cash flow unavailable while six sections cited $182.9bn. Every judge who
   read a platform report named this defect, and the fact pass above could not see it,
   because the contradiction is between a sentence and a number rather than between two rows.

Every finding lands as an ordinary ``disagreements`` row, deduplicated by fingerprint, shown
at gate 2 and carried into the report's appendix. **Nothing here refuses a run.** ADR 0125
fixes the route the refusal will take when it comes — the final gate's own evidence rule,
after two consecutive clean live runs — and fixes that it will never be an evaluation metric,
whose rows are scores and never a pause.

Facts and calculations on *different* periods are deliberately not compared. An annual
EBITDA beside a quarterly revenue is not a contradiction — it was the live report's failure
to *label* them that read as one, and the period stamp (gap C1) is that cure. This check owns
the other half: values that claim the same span and still disagree.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import SENSITIVITY_CASE
from aer.core.disagreement import (
    THESIS_UNIT,
    DisagreementKind,
    Position,
    denied_figure,
    figure_contradiction,
)
from aer.core.enums import FactBasis, SourceTier
from aer.core.figure_names import (
    clauses,
    denial_span,
    denies,
    mentions,
    narrows,
    opens_with,
    periods_named,
    phrases_for,
)
from aer.core.figures import numeral_tokens, plain_decimal, reads_as
from aer.core.hashing import sha256_hex
from aer.core.section_output import prose_sentences
from aer.db.models import (
    Calculation,
    Claim,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SourceDocument,
)
from aer.render.glance import glance_content
from aer.services.disagreements import position_from_fact, record_resolution, resolve_and_record

__all__ = ["ConsistencyReading", "check_report_consistency"]

_log = structlog.get_logger("aer.services.consistency")

# Pairwise comparisons per group, bounded. A group needs three distinct values before
# this bites (three values are three pairs), and a group with more than five is telling
# us something is wrong with acquisition, not asking for twenty rows about it.
_MAX_PAIRS_PER_GROUP: Final = 10

# How many denials one run may record. A section that lost its evidence pack writes gap
# sentences by the dozen, and a gate-2 page with two hundred rows on it is a page nobody
# reads — which is the same failure as recording none. Sections are walked in report order,
# so the cap takes the end of the document rather than an arbitrary slice.
_MAX_DENIALS: Final = 40

# The label a period-less figure is indexed under: a discount rate or a value per share is
# struck as at a date rather than for a span. Empty rather than a word, because it is a key
# and never printed.
_NO_PERIOD: Final = ""


@dataclass(frozen=True, slots=True)
class ConsistencyReading:
    """What the three passes found, so the step output can say which pass found what.

    One number would answer "did the document contradict itself?" and nothing else. The
    breakdown is what lets the promotion to blocking (ADR 0125) be argued from counts: the
    committed run records all carry ``consistency_conflicts: 0`` from the fact pass alone,
    and a total that mixed the three would make them incomparable.
    """

    facts: int = 0
    calculations: int = 0
    denials: int = 0

    @property
    def total(self) -> int:
        return self.facts + self.calculations + self.denials

    def as_dict(self) -> dict[str, int]:
        return {
            "facts": self.facts,
            "calculations": self.calculations,
            "denials": self.denials,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class _PublishedFigure:
    """One figure the report prints, and where it prints it."""

    position: Position
    section: str


async def check_report_consistency(
    session: AsyncSession, *, job_id: uuid.UUID
) -> ConsistencyReading:
    """Compare everything this report publishes against everything else it publishes.

    Returns the count per pass (agreeing pairs record nothing).
    """
    sections = list(
        await session.scalars(
            select(ReportSection)
            .where(ReportSection.job_id == job_id)
            .order_by(ReportSection.position, ReportSection.id)
        )
    )
    definitions = await _definitions_by_key(session, sections)
    front_page = await _front_page(session, job_id=job_id)

    facts, tiers = await _published_facts(session, sections=sections, front_page=front_page)
    calculations = await _published_calculations(
        session, job_id=job_id, sections=sections, front_page=front_page
    )

    reading = ConsistencyReading(
        facts=await _compare_facts(session, job_id=job_id, facts=facts, tiers=tiers),
        calculations=await _compare_calculations(session, job_id=job_id, calculations=calculations),
        denials=await _find_denials(
            session,
            job_id=job_id,
            sections=sections,
            definitions=definitions,
            index=_figure_index(
                facts, tiers, calculations, sections=sections, definitions=definitions
            ),
        ),
    )

    _log.info(
        "consistency.checked",
        job_id=str(job_id),
        published_facts=len(facts),
        published_calculations=len(calculations),
        **reading.as_dict(),
    )
    return reading


# -- Pass 1: published facts ---------------------------------------------------------------


async def _compare_facts(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    facts: Sequence[FinancialFact],
    tiers: Mapping[uuid.UUID, SourceTier],
) -> int:
    """Every published fact against its same-period neighbours, down the ladder."""
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
        distinct = _one_fact_per_value(rows)
        if len(distinct) == 1:
            # One value for the span, however many rows and sections carry it: the
            # state every group should be in, and nothing to compare.
            continue
        topic = f"{_in_words(concept)}, {_fact_period(distinct[0])}"
        for first, second in list(combinations(distinct, 2))[:_MAX_PAIRS_PER_GROUP]:
            row = await resolve_and_record(
                session,
                job_id=job_id,
                topic=topic,
                first=position_from_fact(
                    first, tier=tiers[first.source_document_id], label=_fact_label(first)
                ),
                second=position_from_fact(
                    second, tier=tiers[second.source_document_id], label=_fact_label(second)
                ),
            )
            if row is not None:
                recorded += 1
    return recorded


async def _front_page(session: AsyncSession, *, job_id: uuid.UUID) -> dict[str, Any] | None:
    """The at-a-glance block's own content, or ``None`` when the run renders none.

    **Asked of the renderer rather than restated here.** `aer.render.glance` curates which
    calculations reach the front page, and a second copy of that list in this module would
    be a third statement of one rule — which is how the first two came apart. The cost is a
    `services` module importing a `render` one; the alternative was this check believing a
    stale list of what the reader sees.

    A refusal renders no block (the glance withholds itself when the figures offered to it
    are not all the subject's), and a withheld block publishes nothing, so ``None`` is the
    honest answer rather than an empty dict pretending to be a front page.
    """
    job = await session.get(Job, job_id)
    if job is None:  # pragma: no cover -- the caller holds the job
        return None
    request = await session.get(ResearchRequest, job.work_order_id)
    if request is None:  # pragma: no cover -- a job without its request
        return None
    glance = await glance_content(session, job=job, request=request)
    return glance.content


async def _published_facts(
    session: AsyncSession,
    *,
    sections: Sequence[ReportSection],
    front_page: Mapping[str, Any] | None = None,
) -> tuple[list[FinancialFact], dict[uuid.UUID, SourceTier]]:
    """The facts this report actually shows a reader, with their documents' tiers.

    Three ways a fact reaches the page, all collected: a numeric claim naming it, a section
    figure row carrying its id — the two channels the numeral rule accepts as lineage — and
    the front page, which is rendered rather than stored. See :func:`_published_ids`.
    """
    wanted = await _published_ids(
        session,
        sections=sections,
        claim_column=Claim.financial_fact_id,
        content_key="financial_fact_id",
        front_page=front_page,
    )
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


# -- Pass 2: published calculations --------------------------------------------------------


async def _compare_calculations(
    session: AsyncSession, *, job_id: uuid.UUID, calculations: Sequence[Calculation]
) -> int:
    """One name, one case, one period, one value (ADR 0125).

    **The inputs are part of the key, and that is the whole difficulty.** ``Calculation.name``
    is the name of the *function*, not of the figure: ``days_outstanding`` is struck three
    times a period — days sales, days inventory, days payable — from three different pairs of
    facts, and on the committed msft2 record those three read 115.2, 90.6 and 3.9 days for
    FY2025. Grouping on name and period alone would call that a contradiction, three times a
    year, on every run, on a ratio suite that is working exactly as designed. Sixteen such
    groups sit in one record's ledger.

    So two rows are the same figure when the same function ran over the same inputs for the
    same period. Different inputs are different figures however they are named, and identical
    inputs that produced different answers are a defect in the run — which is precisely what
    this pass is for, and all it is for.

    A **sensitivity cell is excluded**, for the reason
    :func:`aer.services.calculations.indexed_calculations` excludes it and one more that is
    specific to this pass: a grid cell's inputs differ only in the assumption being varied,
    which the grid records in its parameters rather than in its sources. A grid point is not
    an answer.
    """
    grouped: dict[tuple[str, ...], list[Calculation]] = {}
    for calculation in calculations:
        if _case_of(calculation) == SENSITIVITY_CASE:
            continue
        grouped.setdefault(_question_of(calculation), []).append(calculation)

    recorded = 0
    for _, rows in sorted(grouped.items()):
        distinct = _one_calculation_per_value(rows)
        if len(distinct) == 1:
            continue
        leader = distinct[0]
        topic = _calculation_topic(leader.name, case=_case_of(leader), period=leader.period_label)
        for first, second in list(combinations(distinct, 2))[:_MAX_PAIRS_PER_GROUP]:
            row = await record_resolution(
                session,
                job_id=job_id,
                topic=topic,
                kind=DisagreementKind.SELF_CONTRADICTION,
                resolution=figure_contradiction(
                    first=_position_from_calculation(first),
                    second=_position_from_calculation(second),
                    topic=topic,
                ),
            )
            if row is not None:
                recorded += 1
    return recorded


async def _published_calculations(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    sections: Sequence[ReportSection],
    front_page: Mapping[str, Any] | None = None,
) -> list[Calculation]:
    """The calculations this report shows a reader, by the same three channels as the facts."""
    wanted = await _published_ids(
        session,
        sections=sections,
        claim_column=Claim.calculation_id,
        content_key="calculation_id",
        front_page=front_page,
    )
    if not wanted:
        return []
    return list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job_id, Calculation.id.in_(wanted))
            .order_by(Calculation.sequence, Calculation.id)
        )
    )


# -- Pass 3: negative assertions -----------------------------------------------------------


async def _find_denials(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    sections: Sequence[ReportSection],
    definitions: Mapping[str, SectionDefinition],
    index: Mapping[str, dict[str, _PublishedFigure]],
) -> int:
    """A clause saying a figure is unavailable, in a document that prints it (ADR 0125).

    A conjunction of narrow tests over one clause, none of them safe alone and every one of
    them measured against the re-seeded corpus rather than reasoned about. The clause must
    **deny** (:func:`aer.core.figure_names.denies` — a negator and then a word about the
    record) and must **name a figure this report publishes** by a phrase of at least two
    words. It is then let go for any of four reasons:

    * the clause **narrows** the figure — a segment-level or quarterly absence beside a
      consolidated annual figure is two subjects, not a contradiction;
    * the clause **quotes the figure's own value**, which makes it a sentence *using* the
      figure however many negators it also carries: "a 7.59% cost of equity rests on a beta
      of 0.57 … with no estimation window recorded" denies the window, not the rate;
    * the clause names a **period** the report does not publish that figure for;
    * the clause comes from a section **the platform filled itself** — see the caller.

    A section denying a figure **it prints itself** still counts, and is the worse case:
    msft1's executive summary denied a value per share thirty lines above one.
    """
    if not index:
        return 0

    recorded = 0
    for section in sections:
        if not isinstance(section.content, dict) or _platform_filled(section, definitions):
            continue
        speaker = _title_of(section, definitions)
        for sentence in prose_sentences(section.content):
            # The period may be stated anywhere in the sentence and still bind the clause
            # that denies; the denial and the figure it denies must share one clause.
            named = periods_named(sentence)
            for clause in clauses(sentence):
                if not denies(clause) or narrows(clause):
                    continue
                quoted = [Decimal(token) for token in numeral_tokens(clause)]
                # What the negation reaches, and what the clause is about: a figure
                # named outside both is mentioned, not denied.
                span = denial_span(clause)
                for name in sorted(index):
                    if recorded >= _MAX_DENIALS:
                        return recorded
                    if not any(
                        mentions(span, phrase) or opens_with(clause, phrase)
                        for phrase in phrases_for(name)
                    ):
                        continue
                    published = _denied_by(index[name], named)
                    if published is None:
                        continue
                    if any(
                        reads_as(figure, published.position.value, sign_matters=False)
                        for figure in quoted
                    ):
                        continue
                    row = await record_resolution(
                        session,
                        job_id=job_id,
                        topic=f"{_in_words(name)} is denied and printed",
                        kind=DisagreementKind.SELF_CONTRADICTION,
                        resolution=denied_figure(
                            denial=_denial_position(
                                section=section, speaker=speaker, sentence=clause
                            ),
                            published=published.position,
                            topic=f"{_in_words(name)} in {published.section}",
                            sentence=clause,
                        ),
                    )
                    if row is not None:
                        recorded += 1
    return recorded


def _denied_by(
    published: Mapping[str, _PublishedFigure], named: frozenset[str]
) -> _PublishedFigure | None:
    """The published figure a denial contradicts, or ``None`` where it contradicts none.

    A sentence naming no period is an unqualified denial and is contradicted by whichever
    period the document prints. A sentence naming periods is contradicted only where the
    document prints the figure *for one of them*: "free cash flow for FY2021 is not
    disclosed" is not answered by an FY2025 free cash flow, and recording it as though it
    were would be the check's first and most obvious false positive.

    Which period is shown when several qualify is decided by the label's own sort order,
    which puts the newest fiscal year first among ``FY2024``-shaped labels and is simply
    stable among mixed ones. The choice is arbitrary rather than meaningful — every period
    in the overlap is equally contradicted — and what matters is that it is the same on
    every run, so a re-run does not record the same denial against a different period.
    """
    if not published:
        return None
    if not named:
        return published[max(published)]
    overlap = named & set(published)
    return published[max(overlap)] if overlap else None


def _figure_index(
    facts: Sequence[FinancialFact],
    tiers: Mapping[uuid.UUID, SourceTier],
    calculations: Sequence[Calculation],
    *,
    sections: Sequence[ReportSection],
    definitions: Mapping[str, SectionDefinition],
) -> dict[str, dict[str, _PublishedFigure]]:
    """Every figure this report prints, by name and then by period.

    Facts first, calculations second, so a name carried by both — ``revenue`` is a concept
    and also a calculation — resolves to the computed one, which is what a section denying
    it would have been shown. The section named is the first one in report order that
    carries the figure, which is the earliest place a reader would have met it.
    """
    where = _sections_carrying(sections, definitions)
    index: dict[str, dict[str, _PublishedFigure]] = {}

    for fact in facts:
        tier = tiers.get(fact.source_document_id)
        if tier is None:  # pragma: no cover -- a fact whose document vanished mid-run
            continue
        index.setdefault(fact.concept, {})[_fact_period(fact)] = _PublishedFigure(
            position=position_from_fact(fact, tier=tier, label=_fact_label(fact)),
            section=where.get(str(fact.id), "this report"),
        )

    for calculation in calculations:
        if _case_of(calculation) == SENSITIVITY_CASE:
            continue
        index.setdefault(calculation.name, {})[calculation.period_label or _NO_PERIOD] = (
            _PublishedFigure(
                position=_position_from_calculation(calculation),
                section=where.get(str(calculation.id), "this report"),
            )
        )

    return index


def _sections_carrying(
    sections: Sequence[ReportSection], definitions: Mapping[str, SectionDefinition]
) -> dict[str, str]:
    """Which section first prints each figure id, in report order."""
    seen: dict[str, str] = {}
    for section in sections:
        if not isinstance(section.content, dict):
            continue
        title = _title_of(section, definitions)
        for identifier in _figure_ids_in(section.content):
            seen.setdefault(identifier, title)
    return seen


def _denial_position(*, section: ReportSection, speaker: str, sentence: str) -> Position:
    """The denying sentence as a ladder position.

    No quantity, because the sentence asserts an absence and a zero would read as agreement
    (the reason :data:`aer.core.disagreement.THESIS_UNIT` exists), and ``computed`` because
    the draft is this run's own output rather than something a publisher said. The reference
    digests the sentence, so the same denial re-recorded by a retried step is one row while
    two denials in one section are two.
    """
    return Position(
        reference=f"denial:{section.section_key}:{sha256_hex(sentence)[:12]}",
        label=f"{speaker} (denies it)",
        value=Decimal(0),
        unit=THESIS_UNIT,
        tier=SourceTier.T6_UNVERIFIED,
        filed_date=section.created_at.date(),
        basis=FactBasis.AS_REPORTED,
        computed=True,
    )


# -- Shared -------------------------------------------------------------------------------


async def _published_ids(
    session: AsyncSession,
    *,
    sections: Sequence[ReportSection],
    claim_column: Any,
    content_key: str,
    front_page: Mapping[str, Any] | None = None,
) -> set[uuid.UUID]:
    """Every figure id of one kind that this report publishes.

    Three channels, all collected: a numeric claim naming the figure, a section figure row
    carrying its id — the two the numeral rule accepts as lineage — and **the front page**,
    which is neither.

    **The front page had to be added, and the Phase 5 round is why.** MSFT's Historical
    Financial Analysis said *"free cash flow cannot be stated at all"* while the very first
    table in the document read `Free cash flow | FY2026 | $66,987m`. The denial was detected
    correctly; it had nothing to contradict, because the at-a-glance block is assembled by
    :mod:`aer.render.glance` at render time from stored rows and is never a
    ``report_sections`` row. A check that reads only sections cannot see the first thing a
    reader sees. The judge who read that document wrote that it *"cannot be trusted on its
    own numbers"*, and the disagreement the platform should have raised itself was instead
    raised about it, by somebody else, after the fact.
    """
    wanted: set[uuid.UUID] = set()
    section_ids = [section.id for section in sections]
    if section_ids:
        claims = await session.scalars(
            select(claim_column).where(Claim.report_section_id.in_(section_ids))
        )
        wanted.update(identifier for identifier in claims if identifier is not None)

    for section in sections:
        if isinstance(section.content, dict):
            wanted.update(_ids_under_key(section.content, key=content_key))

    if front_page is not None:
        wanted.update(_ids_under_key(front_page, key=content_key))
    return wanted


async def _definitions_by_key(
    session: AsyncSession, sections: Sequence[ReportSection]
) -> dict[str, SectionDefinition]:
    """Each section's definition by its key.

    Two things are read from it: the title, so a record names a section as the report does,
    and the token budget, which is how a section the *platform* filled is told from one a
    model wrote — see :func:`_find_denials`.
    """
    definition_ids = {section.section_definition_id for section in sections}
    if not definition_ids:
        return {}
    rows = await session.scalars(
        select(SectionDefinition).where(SectionDefinition.id.in_(definition_ids))
    )
    by_id = {row.id: row for row in rows}
    return {
        section.section_key: by_id[section.section_definition_id]
        for section in sections
        if section.section_definition_id in by_id
    }


def _ids_under_key(value: Any, *, key: str) -> Iterable[uuid.UUID]:
    """Every value of ``key`` in a section's content, by the figure-row convention."""
    if isinstance(value, dict):
        for found, item in value.items():
            if str(found) == key and isinstance(item, str) and item:
                try:
                    yield uuid.UUID(item)
                except ValueError:
                    continue  # The execution boundary refuses fabricated ids; be lenient here.
            else:
                yield from _ids_under_key(item, key=key)
    elif isinstance(value, list):
        for item in value:
            yield from _ids_under_key(item, key=key)


def _figure_ids_in(value: Any) -> Iterable[str]:
    """Every figure id a section's content names, of either kind, as written."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in ("financial_fact_id", "calculation_id") and isinstance(item, str):
                if item:
                    yield item
            else:
                yield from _figure_ids_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _figure_ids_in(item)


def _one_fact_per_value(rows: Sequence[FinancialFact]) -> list[FinancialFact]:
    """One representative per distinct (value, unit), earliest id for determinism.

    Two sections citing the *same* stored row — or two rows that agree exactly — are not
    a disagreement, and running agreeing pairs through the ladder would record nothing
    while costing comparisons.
    """
    seen: dict[tuple[str, str], FinancialFact] = {}
    for row in sorted(rows, key=lambda item: str(item.id)):
        seen.setdefault((str(row.value), row.unit), row)
    return list(seen.values())


def _one_calculation_per_value(rows: Sequence[Calculation]) -> list[Calculation]:
    """One representative per distinct (value, unit), lowest sequence for determinism.

    A run legitimately re-strikes the same calculation — a resumed step, a revised section —
    and two identical results are one figure however many rows carry it. The earliest is
    kept because it is the one the rest of the ledger was built on.
    """
    seen: dict[tuple[str, str], Calculation] = {}
    for row in sorted(rows, key=lambda item: (item.sequence, str(item.id))):
        # By value rather than by the stored string: a NUMERIC round-trip gives 9.3 twelve
        # decimal places, and two spellings of one number are not two figures.
        seen.setdefault((plain_decimal(row.output_value), row.output_unit), row)
    return list(seen.values())


def _position_from_calculation(calculation: Calculation) -> Position:
    """A recorded calculation as a ladder position (ADR 0125).

    ``computed`` is the field that matters and the one the page reads. ``tier`` and
    ``filed_date`` are required by the shared type and are never read for a computed
    position; the least authoritative tier is chosen so that a reader this record did not
    anticipate under-claims rather than attributing this platform's arithmetic to a
    regulator.
    """
    return Position(
        reference=str(calculation.id),
        label=_calculation_label(calculation),
        value=calculation.output_value,
        unit=calculation.output_unit,
        tier=SourceTier.T6_UNVERIFIED,
        filed_date=calculation.created_at.date(),
        basis=FactBasis.AS_REPORTED,
        computed=True,
    )


def _case_of(calculation: Calculation) -> str:
    return str((calculation.parameters or {}).get("case", ""))


def _question_of(calculation: Calculation) -> tuple[str, ...]:
    """The question a recorded calculation answers — its identity minus its answer.

    **Taken from the engine's own definition, deliberately.** ``aer.calc.engine`` reuses a
    record rather than appending one when every field but the id matches: name, formula,
    function, code version, inputs, output, parameters, assumptions and period. The output
    is the one field this drops, because "the same question with two answers" is exactly
    what this pass looks for; a second definition of what makes two calculations the same
    figure would eventually disagree with the ledger's.

    Two fields do the work the naive key missed. **Inputs**, because ``days_outstanding``
    is one function serving days sales, days inventory and days payable, and only its
    sources say which. **Parameters**, because a discount factor takes the year as a
    parameter rather than an input, so ten forecast years are ten rows with one name, one
    absent period and identical inputs. The model's own docstring calls parameters
    "structural choices … the calculation is not reproducible without them", which is the
    same statement seen from the other side.
    """
    sources: list[str] = []
    for item in calculation.inputs:
        source = item.get("source")
        identifier = str(source.get("id", "")) if isinstance(source, dict) else ""
        # An input with no resolvable source falls back to its own name and value, so a
        # calculation over a bare constant has an identity rather than colliding with
        # every other one.
        sources.append(identifier or f"{item.get('name', '')}={item.get('value', '')}")

    parameters = sorted(f"{key}={value!r}" for key, value in (calculation.parameters or {}).items())
    return (
        calculation.name,
        calculation.formula,
        calculation.function_ref,
        calculation.code_version,
        calculation.period_start.isoformat() if calculation.period_start else "",
        calculation.period_end.isoformat() if calculation.period_end else "",
        *sorted(sources),
        *parameters,
    )


def _calculation_topic(name: str, *, case: str, period: str | None) -> str:
    """How a contradiction about a calculated figure names itself on the gate page."""
    parts = [_in_words(name)]
    if case:
        parts.append(_in_words(case))
    if period:
        parts.append(period)
    return ", ".join(parts)


def _calculation_label(calculation: Calculation) -> str:
    """How a computed position names itself in a rationale."""
    period = f" for {calculation.period_label}" if calculation.period_label else ""
    case = _case_of(calculation)
    named = f" ({_in_words(case)} case)" if case else ""
    return f"{_in_words(calculation.name)}{period}{named}"


def _fact_period(fact: FinancialFact) -> str:
    """The span as a reader names it: "FY2025", "Q3 2026", or the end date."""
    if fact.fiscal_period == "FY" and fact.fiscal_year:
        return f"FY{fact.fiscal_year}"
    if fact.fiscal_period and fact.fiscal_year:
        return f"{fact.fiscal_period} {fact.fiscal_year}"
    return fact.period_end.isoformat()


def _fact_label(fact: FinancialFact) -> str:
    """How a position names itself in the ladder's rationale."""
    form = f" ({fact.form})" if fact.form else ""
    return f"{_in_words(fact.concept)} filed {fact.filed_date.isoformat()}{form}"


def _title_of(section: ReportSection, definitions: Mapping[str, SectionDefinition]) -> str:
    """How the report names this section, or its key in words where it has no definition."""
    found = definitions.get(section.section_key)
    return found.title if found is not None else _in_words(section.section_key)


def _platform_filled(section: ReportSection, definitions: Mapping[str, SectionDefinition]) -> bool:
    """Whether the platform wrote this section rather than a model (ADR 0125).

    **A zero token budget is the marker**, and it is the same column the draft step routes
    on, so a section that becomes deterministic becomes exempt without an edit here.

    The exemption exists because these sections' subject is the run's own record. The
    validation and disagreements section *reports* contradictions — "the executive summary
    asserts that cash generation is not addressed by the figures available here while the
    same run records free cash flow" — and scanning it produces contradictions about
    contradictions. Seven of the eighteen denials the first version recorded over the
    re-seeded corpus came from there, every one of them the platform quoting itself.
    """
    found = definitions.get(section.section_key)
    return found is not None and found.token_budget == 0


def _in_words(name: str) -> str:
    """A stored name as a sentence says it. The gate page and the appendix read these."""
    return name.replace("_", " ").strip()
