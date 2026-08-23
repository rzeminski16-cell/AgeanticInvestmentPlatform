"""Persisting selected facts, and resolving the company they belong to.

Two operations, both deliberately dull. The interesting decisions — which tag means
revenue, which filing was current at the as-of date — were made in
:mod:`aer.core.concepts` and :mod:`aer.sources.sec.pit`, where they are pure functions
with exhaustive tests. What is left here is writing rows.

**Facts are inserted idempotently.** A re-run for the same company over the same filings
must not create a second copy of every number: the uniqueness index covers the observation
identity, and an insert that collides with an existing row is skipped rather than raised.
Re-running research is normal, and a pipeline that duplicated its output every time would
make every count downstream wrong.

**The basis is recorded, never inferred.** A row says ``as_reported`` because a
point-in-time selection produced it, not because that is the usual case. When a vendor
adapter eventually writes ``vendor_standardised`` rows, the distinction has to already be
in the data rather than being reconstructed from which table it came from.

**Facts arrive in tens of thousands, so the insert is batched.** One company's full filing
history is not a handful of rows — see :data:`_PARAMETER_LIMIT`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

import structlog
from sqlalchemy import Select, select
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import FactBasis
from aer.core.schemas.facts import RawFact
from aer.db.models import Company, FinancialFact, ResearchRequest, SourceDocument
from aer.sources.base import ResolvedEntity

__all__ = ["persist_facts", "upsert_company", "visible_facts"]

_log = structlog.get_logger("aer.services.facts")

# Postgres binds every value in a statement as a parameter, and the wire protocol carries the
# count in an ``int16`` — so 32,767 is a hard ceiling, not a tunable. A multi-row INSERT
# therefore has a row ceiling of ``32767 // columns``, and with sixteen columns that is 2,047.
#
# Microsoft's companyfacts, point-in-time selected at a 2022 as-of date, is **13,702 facts**:
# 219,232 parameters, nearly seven times over. The extract step failed on it with
# ``the number of query arguments cannot exceed 32767``. This is not an edge case — it is
# every US large cap with a decade of filings, which is the platform's whole subject.
_PARAMETER_LIMIT: Final = 32_767


def visible_facts(request: ResearchRequest, company_id: uuid.UUID | None) -> Select[Any]:
    """The facts a run may see: the subject's consolidated figures, as at the as-of date.

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

    *The date filter is part of the scope, not a separate improvement.* Request scope
    happened to bound a consumer to one acquisition; company scope does not, so without this
    a point-in-time run could be shown a fact filed after its as-of date by some later run.
    Filtered on ``filed_date``, because what matters is when the filing was filed, not when
    this platform happened to fetch it.

    *Consolidated only*, under ADR 0058: a segment's slice is indistinguishable from the
    company's own line once it is in a pack, and a writer citing it would state a fraction
    as the whole.
    """
    statement = select(FinancialFact).where(
        FinancialFact.company_id == company_id, FinancialFact.dimension_axis.is_(None)
    )
    if company_id is None:
        # Before `acquire` resolves the company there is nothing to show. `None` would match
        # no rows anyway; saying so here keeps that an intention rather than a coincidence
        # of SQL null semantics.
        return statement.where(sa_false())
    if request.point_in_time:
        statement = statement.where(FinancialFact.filed_date <= request.as_of_date)
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
