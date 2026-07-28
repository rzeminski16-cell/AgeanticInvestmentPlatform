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
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import FactBasis
from aer.core.schemas.facts import RawFact
from aer.db.models import Company, FinancialFact, SourceDocument
from aer.sources.base import ResolvedEntity

__all__ = ["persist_facts", "upsert_company"]

_log = structlog.get_logger("aer.services.facts")


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
            "filed_date": fact.filed_date,
            "form": fact.form,
            "accession": fact.accession,
            "basis": basis,
        }
        for fact in facts
    ]

    statement = (
        pg_insert(FinancialFact)
        .values(rows)
        .on_conflict_do_nothing(index_elements=_OBSERVATION_KEY)
        .returning(FinancialFact.id)
    )
    inserted = len((await session.scalars(statement)).all())

    _log.info(
        "facts.persisted",
        company_id=str(company.id),
        source_document_id=str(source_document.id),
        supplied=len(rows),
        inserted=inserted,
        basis=basis.value,
    )
    return inserted


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
)
