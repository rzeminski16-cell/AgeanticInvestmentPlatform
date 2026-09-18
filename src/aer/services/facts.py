"""Persisting selected facts, and resolving the company they belong to.

Two operations, both deliberately dull. The interesting decisions — which tag means
revenue, which filing's figure stands for a period — were made in
:mod:`aer.core.concepts` and :mod:`aer.sources.sec.selection`, where they are pure
functions with exhaustive tests. What is left here is writing rows.

**Facts are inserted idempotently.** A re-run for the same company over the same filings
must not create a second copy of every number: the uniqueness index covers the observation
identity, and an insert that collides with an existing row is skipped rather than raised.
Re-running research is normal, and a pipeline that duplicated its output every time would
make every count downstream wrong.

**The basis is recorded, never inferred.** A row says ``as_reported`` because a
selection over the filings produced it, not because that is the usual case. When a vendor
adapter eventually writes ``vendor_standardised`` rows, the distinction has to already be
in the data rather than being reconstructed from which table it came from.

**Facts arrive in tens of thousands, so the insert is batched.** One company's full filing
history is not a handful of rows — see :data:`_PARAMETER_LIMIT`.

**One sector's top line is assembled here rather than reported** (ADR 0114). A bank files
the components of its revenue and no caption for the sum, so :func:`derive_sector_revenue`
computes it from stored facts and writes it back as a fact carrying its own arithmetic.
That is the one place this module makes a number rather than moving one, and it is
deliberately at the fact layer: six readers take the row without knowing it was derived,
and doing it at each of them would be six chances to miss one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

import structlog
from sqlalchemy import Select, select
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, UnitMismatchError
from aer.core.enums import FactBasis
from aer.core.schemas.facts import RawFact
from aer.core.scope import EvidenceScope
from aer.core.sectors import RevenueComposition, SectorProfile
from aer.db.models import Company, FinancialFact, SourceDocument
from aer.sources.base import ResolvedEntity

__all__ = [
    "DerivedRevenue",
    "Dimensions",
    "derive_sector_revenue",
    "persist_facts",
    "retag_for_sector",
    "upsert_company",
    "visible_facts",
]

_log = structlog.get_logger("aer.services.facts")

# Postgres binds every value in a statement as a parameter, and the wire protocol carries the
# count in an ``int16`` — so 32,767 is a hard ceiling, not a tunable. A multi-row INSERT
# therefore has a row ceiling of ``32767 // columns``, and with sixteen columns that is 2,047.
#
# Microsoft's companyfacts, selected as it stood in 2022, is **13,702 facts**:
# 219,232 parameters, nearly seven times over. The extract step failed on it with
# ``the number of query arguments cannot exceed 32767``. This is not an edge case — it is
# every US large cap with a decade of filings, which is the platform's whole subject.
_PARAMETER_LIMIT: Final = 32_767


class Dimensions(StrEnum):
    """Whether a reader may see a fact that describes part of the company.

    ADR 0058 excluded dimensioned facts from every reader, and was right about five of
    them: each assumes one value per concept-period, and a segment winning a period from
    the aggregate makes every ratio downstream divide a fraction by the whole. ADR 0118
    carves out the sixth — the section whose entire subject is the breakdown, which was
    handed a pack with every segment row removed and then wrote, truthfully, that no
    segment-level figures were available to cite.

    **The default is the exclusion**, so a reader that has not thought about this gets
    ADR 0058's rule, and opting in is a visible act at one call site.
    """

    EXCLUDE = "exclude"
    """Consolidated figures only. What every reader got before ADR 0118, and what five of
    the six still get."""

    INCLUDE_SINGLE_AXIS = "single_axis"
    """The breakdown as well, on one axis at a time.

    A fact tagged on two axes at once — a product within a geography — stays out even
    here. It is not wrong; it is a cell in a cross-tab, and a writer handed cells without
    the table will state one as a total. ADR 0058's sweep already persists only
    single-axis facts, so this names the boundary rather than adding a filter.
    """


def visible_facts(
    scope: EvidenceScope, *, dimensions: Dimensions = Dimensions.EXCLUDE
) -> Select[Any]:
    """The facts a run may see for its subject, as the store holds them.

    **Scoped by company, not by request** (ADR 0061). Every consumer of a fact needs the
    same three predicates, and each one exists because getting it wrong produced a specific
    live failure.

    *Company, because a request is not a company.* Peer acquisition put eight other issuers'
    filings under one request, and the two consumers that joined through
    ``source_documents.request_id`` handed a section writer an annual pool in which the
    subject did not appear at all. An Amazon note cited Walmart, Alibaba, eBay, JD.com,
    MercadoLibre and Target as its evidence.

    *Company rather than company **and** request, because facts outlive the run that
    fetched them.* Facts deduplicate on an observation key that deliberately excludes the
    source document — an observation is an observation — so the *second* run of a company
    inserts nothing, and "supplied 18588, inserted 0" is the dedupe working. Those rows hang
    off the first run's document, so adding the request back would hide them: five research
    workers once spent sixty tool calls searching a table that was full and looked empty.

    *No date filter, since ADR 0113.* A run reads the filings as they stand, and a fact a
    later run fetched is a fact the company filed: the store's most recent observation of
    each figure is the one every run should see.

    *Consolidated only by default*, under ADR 0058: a segment's slice is indistinguishable
    from the company's own line once it is in a pack, and a writer citing it would state a
    fraction as the whole. ``dimensions`` is ADR 0118's carve-out for the one reader whose
    subject *is* the breakdown, and it is a parameter rather than a second query so the
    scoping, the tier caps and the licence checks cannot drift between two places.
    """
    company_id = scope.company_id
    statement = select(FinancialFact).where(FinancialFact.company_id == company_id)
    if dimensions is Dimensions.EXCLUDE:
        statement = statement.where(FinancialFact.dimension_axis.is_(None))
    if company_id is None:
        # Before `acquire` resolves the company there is nothing to show. `None` would match
        # no rows anyway; saying so here keeps that an intention rather than a coincidence
        # of SQL null semantics.
        return statement.where(sa_false())
    return statement


async def upsert_company(
    session: AsyncSession,
    *,
    entity: ResolvedEntity,
    ticker: str,
    exchange: str,
    sic: str | None = None,
    sic_description: str | None = None,
    fiscal_year_end: str | None = None,
    isin: str | None = None,
) -> Company:
    """Find or create the company row for a resolved entity.

    Matched on the registry identifier first and the listing second. The identifier is the
    stronger key: a company can change ticker or move exchange, and matching on the listing
    alone would create a second row for the same company the first time it did.
    """
    company = await session.scalar(select(Company).where(Company.cik == entity.identifier))
    if company is None:
        company = await session.scalar(
            select(Company).where(Company.ticker == ticker, Company.exchange == exchange)
        )

    if company is None:
        company = Company(
            name=entity.name or ticker,
            cik=entity.identifier,
            ticker=ticker,
            exchange=exchange,
            sic=sic,
            sic_description=sic_description,
            fiscal_year_end=fiscal_year_end,
            isin=isin,
        )
        session.add(company)
        await session.flush()
        _log.info("company.created", cik=company.cik, ticker=ticker, exchange=exchange)
        return company

    # Refreshed rather than left as first seen. A name change or a reclassification is
    # information, and an identity row that never updates slowly stops describing the
    # company it identifies.
    company.name = entity.name or company.name
    company.cik = company.cik or entity.identifier
    company.sic = sic or company.sic
    company.sic_description = sic_description or company.sic_description
    company.fiscal_year_end = fiscal_year_end or company.fiscal_year_end
    company.isin = isin or company.isin
    await session.flush()
    return company


async def persist_facts(
    session: AsyncSession,
    *,
    company: Company,
    source_document: SourceDocument,
    facts: tuple[RawFact, ...] | list[RawFact],
    basis: FactBasis = FactBasis.AS_REPORTED,
) -> int:
    """Write facts against a company and the document they came from.

    Returns the number of rows actually inserted, which is less than the number of facts
    supplied whenever some were already stored.

    ``ON CONFLICT DO NOTHING`` against the observation index. The alternative — select,
    compare, insert — has a race between the two statements and needs the same constraint
    behind it anyway to be correct, so it buys nothing but a round trip.

    Written in batches, all inside the caller's transaction: either every fact from this
    document lands or none does. A partial fact set is worse than none, because a
    calculation reading it would produce a real-looking number from half a filing history.
    """
    if not facts:
        return 0

    rows = [
        {
            "company_id": company.id,
            "source_document_id": source_document.id,
            "concept": fact.concept,
            "raw_concept": fact.raw_concept,
            "taxonomy": fact.taxonomy,
            "value": fact.value,
            "unit": fact.unit,
            "scale": 0,
            "period_start": fact.period_start,
            "period_end": fact.period_end,
            "fiscal_year": fact.fiscal_year,
            "fiscal_period": fact.fiscal_period,
            "dimension_axis": fact.dimension_axis,
            "dimension_member": fact.dimension_member,
            "filed_date": fact.filed_date,
            "form": fact.form,
            "accession": fact.accession,
            "basis": basis,
        }
        for fact in facts
    ]

    collisions = _colliding_tags(rows)
    if collisions:
        # Named, not merely counted (gap A55). `on_conflict_do_nothing` is the right
        # behaviour for re-persisting a filing already stored, and it is the wrong thing
        # happening here: two *different* filed tags reduced to one canonical concept in
        # one filing, where the row that survives is whichever the batch reached first.
        # `ShortTermBorrowings` and `LongTermDebtCurrent` are disjoint components of
        # short-term debt today; `OperatingLeaseLiability` and its `...Noncurrent` child
        # both mean `lease_liabilities`, where keeping the child understates. Which of a
        # colliding pair is right is a judgement per pair, so this reports rather than
        # decides — but a figure a filer reported and this platform silently discarded
        # must not be invisible while the map is curated.
        _log.warning(
            "facts.concept_collisions",
            company_id=str(company.id),
            source_document_id=str(source_document.id),
            collisions=collisions,
        )

    inserted = 0
    for batch in _batched(rows, _rows_per_statement(rows[0])):
        statement = (
            pg_insert(FinancialFact)
            .values(batch)
            .on_conflict_do_nothing(index_elements=_OBSERVATION_KEY)
            .returning(FinancialFact.id)
        )
        inserted += len((await session.scalars(statement)).all())

    _log.info(
        "facts.persisted",
        company_id=str(company.id),
        source_document_id=str(source_document.id),
        supplied=len(rows),
        inserted=inserted,
        basis=basis.value,
    )
    return inserted


def retag_for_sector(
    facts: Iterable[RawFact], *, profile: SectorProfile | None
) -> tuple[RawFact, ...]:
    """The same facts, under the canonical concepts this sector gives their tags.

    ADR 0114's fourth part, and the half that has to happen before anything is written: a
    bank's ASC 606 disclosure is fee income, not the top line, so it is stored as
    ``revenue_from_contracts``. The figure is not discarded and not re-labelled into
    something it is not — it keeps its filed tag, its value and its document, and loses
    only the claim to be the whole of revenue.

    Every other tag, and every sector without a composition, passes through untouched.
    """
    composition = profile.revenue_composition if profile is not None else None
    if composition is None or not composition.reassigned:
        return tuple(facts)

    retagged: list[RawFact] = []
    for fact in facts:
        concept = composition.concept_for(fact.raw_concept, fact.concept)
        if concept == fact.concept:
            retagged.append(fact)
        else:
            retagged.append(fact.model_copy(update={"concept": concept}))
    return tuple(retagged)


async def _retag_stored_facts(
    session: AsyncSession, *, company: Company, composition: RevenueComposition
) -> int:
    """Move rows a build before ADR 0114 filed under the wrong concept.

    Written for one case and it is not hypothetical: a store holding a bank's facts from
    before this rule has the ASC 606 caption sitting under ``revenue``, and the derivation
    would read it as a total the filer stated and leave the figure that produced a 172.1%
    margin exactly where it is — silently, which is the word that makes it unacceptable.

    Only the canonical concept moves. The value, the unit, the filed date, the accession
    and the source document are the filer's statement and are untouched; ``concept`` is
    *this platform's* reading of the filer's tag, and a human has now confirmed the sector
    that changes it. A stored report still replays: its claims name facts by id, and every
    figure behind them is the same.

    A row whose corrected identity already exists is deleted rather than updated, because
    under the observation index the two are one observation and the update would raise.
    """
    tags = [tag for tag, _ in composition.reassigned]
    by_tag = dict(composition.reassigned)
    misfiled = list(
        await session.scalars(
            select(FinancialFact).where(
                FinancialFact.company_id == company.id,
                FinancialFact.raw_concept.in_(tags),
                FinancialFact.concept.not_in(set(by_tag.values())),
            )
        )
    )
    if not misfiled:
        return 0

    moved = 0
    for fact in misfiled:
        corrected = by_tag[str(fact.raw_concept)]
        clash = await session.scalar(
            select(FinancialFact.id).where(
                FinancialFact.company_id == fact.company_id,
                FinancialFact.concept == corrected,
                FinancialFact.unit == fact.unit,
                FinancialFact.period_end == fact.period_end,
                FinancialFact.fiscal_period.is_not_distinct_from(fact.fiscal_period),
                FinancialFact.basis == fact.basis,
                FinancialFact.filed_date == fact.filed_date,
                FinancialFact.dimension_axis.is_not_distinct_from(fact.dimension_axis),
                FinancialFact.dimension_member.is_not_distinct_from(fact.dimension_member),
            )
        )
        if clash is not None:
            await session.delete(fact)
        else:
            fact.concept = corrected
        moved += 1

    await session.flush()
    _log.info(
        "facts.retagged_for_sector",
        company_id=str(company.id),
        moved=moved,
    )
    return moved


@dataclass(frozen=True, slots=True)
class DerivedRevenue:
    """What a sector's revenue derivation did, period by period.

    ``refused`` is as much of the answer as ``written``: a period with one half of the sum
    has no revenue at all under ADR 0114, and a run whose report is missing a year's top
    line must be able to say why rather than leaving a reader to infer a data gap.
    """

    formula: str
    written: int
    derived_periods: tuple[str, ...] = ()
    refused: tuple[dict[str, str], ...] = ()
    corrected: int = 0
    """Rows a build before ADR 0114 filed under the wrong concept, moved to the right one."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "derived_revenue_corrected": self.corrected,
            "derived_revenue_formula": self.formula,
            "derived_revenue_written": self.written,
            "derived_revenue_periods": list(self.derived_periods),
            "derived_revenue_refused": [dict(row) for row in self.refused],
        }


async def derive_sector_revenue(
    session: AsyncSession,
    *,
    company: Company,
    profile: SectorProfile | None,
    code_version: str,
) -> DerivedRevenue:
    """Assemble the top line a sector's accounting states in parts (ADR 0114).

    For a filer the sector gate has **confirmed** as a bank, revenue is net interest income
    plus non-interest income. The caller supplies the profile; this function does not guess
    one, because a rule that decided "this looks like a bank" from the concept set would
    quietly rewrite revenue for an insurer, a specialty lender or a fintech with an
    interest line.

    Three refusals, each a recorded reason rather than a silent absence:

    - **Both inputs, or nothing.** One half is not a top line, and falling back to the ASC
      606 caption is how the original defect happened.
    - **One filing.** The sum is two captions of one income statement, so the halves must
      share an accession — not merely a document, because EDGAR's companyfacts response is
      one document carrying every filing a company has made. A figure assembled across two
      reports has no page a footnote could point at.
    - **Nothing to replace.** Where the filer did state a total of its own
      (``RevenuesNetOfInterestExpense``), that total is the revenue and this adds nothing.
      An earlier run's derived row is not such a total, so a re-run derives again and the
      observation index absorbs it.

    A mismatch of currency or scale between the halves is none of those: it raises, because
    coercing either is the one thing invariant 5 forbids outright.

    Rows a build before this rule filed under the wrong concept are moved first, so a store
    restored from such a build does not read one of them as a total the filer stated.
    """
    composition = profile.revenue_composition if profile is not None else None
    if composition is None:
        return DerivedRevenue(formula="", written=0)

    corrected = await _retag_stored_facts(session, company=company, composition=composition)

    rows = list(
        await session.scalars(
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company.id,
                FinancialFact.dimension_axis.is_(None),
                FinancialFact.concept.in_([*composition.components, "revenue"]),
            )
            .order_by(FinancialFact.period_end, FinancialFact.filed_date)
        )
    )

    held: dict[tuple[date, str | None], dict[str, FinancialFact]] = {}
    stated: set[tuple[date, str | None]] = set()
    for row in rows:
        period = (row.period_end, row.fiscal_period)
        if row.concept == "revenue":
            # Only a filer's own caption counts as a stated total. This platform's earlier
            # derivation is not one, and treating it as one would make every re-run report
            # that the filer had stated a figure no filing contains.
            if row.basis is FactBasis.AS_REPORTED:
                stated.add(period)
            continue
        # Ordered by `filed_date` ascending, so the last writer for a concept in a period is
        # the latest filing's word on it — the same rule `select_latest` applies upstream.
        held.setdefault(period, {})[row.concept] = row

    written: list[dict[str, Any]] = []
    derived_periods: list[str] = []
    refused: list[dict[str, str]] = []
    for period, facts in sorted(held.items(), key=lambda item: item[0][0]):
        period_end, fiscal_period = period
        label = _period_label(facts)
        if period in stated:
            refused.append({"period": label, "reason": "the filer stated a revenue total"})
            continue
        parts = [facts.get(concept) for concept in composition.components]
        if any(part is None for part in parts):
            missing = ", ".join(
                concept for concept in composition.components if concept not in facts
            )
            refused.append({"period": label, "reason": f"no {missing} was filed for it"})
            continue

        present = [part for part in parts if part is not None]
        # One income statement, which is the accession rather than the document: EDGAR's
        # companyfacts response is a single document carrying every filing this company has
        # made, so two halves can share it and still be captions of different years' reports.
        if len({(part.accession, part.source_document_id) for part in present}) > 1:
            refused.append(
                {"period": label, "reason": "the two halves were not stated in one filing"}
            )
            continue
        if len({part.scale for part in present}) > 1:
            # Invariant 5's other half. Two figures at different powers of ten are as
            # unaddable as two currencies, and rescaling one to match is the coercion the
            # invariant forbids.
            message = (
                f"{company.name}'s {label} revenue cannot be derived: its components are "
                "recorded at different scales, and rescaling one to match would be a "
                "coercion rather than a conversion."
            )
            raise UnitMismatchError(
                message,
                context={"period": label, "scales": sorted({part.scale for part in present})},
            )

        total = Quantity.of(present[0].value, present[0].unit)
        for part in present[1:]:
            total = total + Quantity.of(part.value, part.unit)

        first = present[0]
        written.append(
            {
                "company_id": company.id,
                "source_document_id": first.source_document_id,
                "concept": "revenue",
                "raw_concept": None,
                "taxonomy": None,
                "value": total.value,
                "unit": first.unit,
                "scale": first.scale,
                "period_start": first.period_start,
                "period_end": period_end,
                "fiscal_year": first.fiscal_year,
                "fiscal_period": fiscal_period,
                "dimension_axis": None,
                "dimension_member": None,
                "filed_date": first.filed_date,
                "form": first.form,
                "accession": first.accession,
                "basis": FactBasis.DERIVED,
                "derivation": {
                    "formula": composition.formula,
                    "sector": profile.key if profile is not None else "",
                    "code_version": code_version,
                    "inputs": [
                        {
                            "fact_id": str(part.id),
                            "concept": part.concept,
                            "value": str(part.value),
                            "unit": part.unit,
                            "period_end": part.period_end.isoformat(),
                            "source_document_id": str(part.source_document_id),
                        }
                        for part in present
                    ],
                },
            }
        )
        derived_periods.append(label)

    inserted = 0
    if written:
        statement = (
            pg_insert(FinancialFact)
            .values(written)
            .on_conflict_do_nothing(index_elements=_OBSERVATION_KEY)
            .returning(FinancialFact.id)
        )
        inserted = len((await session.scalars(statement)).all())

    _log.info(
        "facts.revenue_derived",
        company_id=str(company.id),
        sector=profile.key if profile is not None else "",
        periods=len(derived_periods),
        inserted=inserted,
        refused=len(refused),
    )
    return DerivedRevenue(
        formula=composition.formula,
        written=inserted,
        derived_periods=tuple(derived_periods),
        refused=tuple(refused),
        corrected=corrected,
    )


def _period_label(facts: Mapping[str, FinancialFact]) -> str:
    """The period as the console and the gate payloads name one.

    Read from a fact rather than from the grouping key, so the label uses the filer's own
    fiscal year — ``FY2025`` for a year ending in June 2025 as much as for one ending in
    December — which is what every other surface shows.
    """
    fact = next(iter(facts.values()))
    year = fact.fiscal_year if fact.fiscal_year is not None else fact.period_end.year
    if fact.fiscal_period in (None, "", "FY"):
        return f"FY{year}"
    return f"{fact.fiscal_period} FY{year}"


def _colliding_tags(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Where one filing's rows put two different tags on one canonical observation.

    Read off the supplied batch rather than from the database, because that is where the
    question is answerable: after the insert, the losing row simply does not exist, and
    ``supplied`` minus ``inserted`` cannot say whether the gap was a re-run of a filing
    already stored — the ordinary, wanted case — or two lines of one filing collapsed onto
    each other.

    Keyed on the observation the unique constraint arbitrates, so what this reports is
    exactly what the database will drop.
    """
    seen: dict[tuple[Any, ...], set[str]] = {}
    for row in rows:
        tag = str(row.get("raw_concept") or "")
        if not tag:
            continue
        key = tuple(row.get(column) for column in _OBSERVATION_KEY)
        seen.setdefault(key, set()).add(tag)

    found: list[dict[str, Any]] = []
    for key, tags in seen.items():
        if len(tags) == 1:
            continue
        keyed = dict(zip(_OBSERVATION_KEY, key, strict=True))
        period = keyed.get("period_end")
        found.append(
            {
                "concept": keyed.get("concept"),
                "period_end": period.isoformat() if isinstance(period, date) else str(period),
                "tags": sorted(tags),
            }
        )
    return sorted(found, key=lambda item: (str(item["concept"]), str(item["period_end"])))


def _rows_per_statement(row: dict[str, Any]) -> int:
    """How many rows fit under Postgres's parameter ceiling.

    Derived from the row itself rather than written down, so adding a column cannot silently
    push the batch over the limit — which is a failure that only appears against a company
    large enough to reach it, on a run that has already been paid for.
    """
    return _PARAMETER_LIMIT // len(row)


def _batched(rows: Sequence[dict[str, Any]], size: int) -> list[Sequence[dict[str, Any]]]:
    return [rows[start : start + size] for start in range(0, len(rows), size)]


# Must match `uq_financial_facts_observation` exactly. Postgres identifies the arbiter
# index by its columns, so a divergence here does not warn -- it raises at runtime saying
# no matching constraint exists.
_OBSERVATION_KEY = (
    "company_id",
    "concept",
    "unit",
    "period_end",
    "fiscal_period",
    "basis",
    "filed_date",
    "dimension_axis",
    "dimension_member",
)
