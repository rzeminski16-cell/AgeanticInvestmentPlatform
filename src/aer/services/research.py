"""The deterministic half of the research workers: executors and validation.

The worker asks; this module answers. Every executor here is ordinary code over the run's
own tables — no model in sight — and everything an executor returns is labelled for the
loop: structured rows from our own database travel as internal data, while anything whose
text originated outside the platform (document titles, fetched content) travels as
untrusted evidence and reaches the model only inside delimiters.

**Validation closes the loop.** A worker's findings cite source-document and fact ids; the
validator re-reads them against the run's request and refuses anything that does not
resolve — an id the model was never shown, a typo, a fabrication. The refusal goes back to
the worker with the problem named, and a worker that cannot fix it fails. The model may
propose which evidence matters; only code confirms the evidence exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Final

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.worker import (
    ExecutedTool,
    Investigation,
    ResearchTopic,
    ToolRequest,
    WorkerReport,
    investigate,
)
from aer.db.models import FinancialFact, ResearchRequest, SourceDocument

__all__ = [
    "MAX_HITS",
    "build_executors",
    "run_worker",
    "validate_report",
]

_log = structlog.get_logger("aer.services.research")

# Rows per search. Enough to work with, small enough that the loop's accumulated evidence
# stays inside the role's input cap across twelve calls.
MAX_HITS: Final = 10


def build_executors(session: AsyncSession, *, request: ResearchRequest) -> dict[str, Any]:
    """The tool executors for one run: searches over what the run already holds.

    ``fetch_known_url`` is deliberately absent until something binds a fetcher: the
    allowlist grants the *capability*, the run decides the *availability*, and an
    unavailable tool is a recorded refusal rather than an error.
    """

    async def search_facts(tool_request: ToolRequest) -> ExecutedTool:
        rows = await session.scalars(
            select(FinancialFact)
            .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
            .where(
                SourceDocument.request_id == request.id,
                FinancialFact.concept.ilike(f"%{tool_request.query.strip()}%"),
            )
            .order_by(FinancialFact.period_end.desc())
            .limit(MAX_HITS)
        )
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=True,
            internal_results=[
                {
                    "fact_id": str(row.id),
                    "concept": row.concept,
                    "value": str(row.value),
                    "unit": row.unit,
                    "period_end": row.period_end.isoformat(),
                    "basis": row.basis.value if hasattr(row.basis, "value") else str(row.basis),
                }
                for row in rows
            ],
        )

    async def search_sources(tool_request: ToolRequest) -> ExecutedTool:
        needle = f"%{tool_request.query.strip()}%"
        rows = await session.scalars(
            select(SourceDocument)
            .where(
                SourceDocument.request_id == request.id,
                or_(SourceDocument.title.ilike(needle), SourceDocument.url.ilike(needle)),
            )
            .order_by(SourceDocument.retrieved_at.desc())
            .limit(MAX_HITS)
        )
        found = list(rows)
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=True,
            # Ids, tiers and dates are ours; titles came from fetched pages and are
            # untrusted text, so they travel in the wrapped channel with the id alongside.
            internal_results=[
                {
                    "source_document_id": str(row.id),
                    "tier": row.source_tier.value,
                    "publication_date": (
                        row.publication_date.isoformat() if row.publication_date else None
                    ),
                    "quarantined": row.quarantined,
                }
                for row in found
            ],
            untrusted_evidence=[
                {
                    "source_document_id": str(row.id),
                    "tier": row.source_tier.value,
                    "title": row.title or "(untitled)",
                    "text": row.title or "(untitled)",
                }
                for row in found
            ],
        )

    return {"search_facts": search_facts, "search_sources": search_sources}


async def validate_report(
    session: AsyncSession, report: WorkerReport, *, request: ResearchRequest
) -> list[str]:
    """Every cited id must resolve inside this run. Returns the problems, named.

    The ids a worker may cite are exactly the ids its searches surfaced; anything else —
    a fabrication, a typo, an id from some other run — is refused with the finding and
    the id spelt out, so the worker's next turn can fix it or drop it.
    """
    problems: list[str] = []

    cited_sources = {
        identifier for finding in report.findings for identifier in finding.source_document_ids
    }
    cited_facts = {identifier for finding in report.findings for identifier in finding.fact_ids}

    valid_sources = await _existing(
        session,
        select(SourceDocument.id).where(SourceDocument.request_id == request.id),
        column=SourceDocument.id,
        cited=cited_sources,
    )
    valid_facts = await _existing(
        session,
        select(FinancialFact.id)
        .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
        .where(SourceDocument.request_id == request.id),
        column=FinancialFact.id,
        cited=cited_facts,
    )

    for index, finding in enumerate(report.findings):
        for identifier in finding.source_document_ids:
            if identifier not in valid_sources:
                problems.append(
                    f"Finding {index + 1} cites source document {identifier!r}, which "
                    "this run does not hold."
                )
        for identifier in finding.fact_ids:
            if identifier not in valid_facts:
                problems.append(
                    f"Finding {index + 1} cites fact {identifier!r}, which this run does not hold."
                )
    return problems


async def _existing(
    session: AsyncSession, base_query: Any, *, column: Any, cited: set[str]
) -> set[str]:
    """The subset of cited ids that resolve. Non-UUID strings never resolve."""
    parseable: dict[uuid.UUID, str] = {}
    for identifier in cited:
        try:
            parseable[uuid.UUID(identifier)] = identifier
        except ValueError:
            continue
    if not parseable:
        return set()
    rows = await session.scalars(base_query.where(column.in_(parseable)))
    return {parseable[row] for row in rows}


async def run_worker(
    context: AgentContext,
    session: AsyncSession,
    *,
    topic: ResearchTopic,
    request: ResearchRequest,
    executors: Mapping[str, Any] | None = None,
) -> Investigation:
    """One topic's investigation, wired to this run's executors and validator."""

    async def validator(report: WorkerReport) -> list[str]:
        return await validate_report(session, report, request=request)

    outcome = await investigate(
        context,
        topic=topic,
        company_name=request.company_name,
        ticker=request.ticker,
        as_of_date=request.as_of_date.isoformat(),
        executors=executors if executors is not None else build_executors(session, request=request),
        validate=validator,
    )
    _log.info(
        "research.worker_finished",
        topic=topic.value,
        findings=len(outcome.report.findings),
        leads=len(outcome.report.leads),
        tool_calls=outcome.tool_calls,
        rounds=outcome.rounds,
    )
    return outcome
