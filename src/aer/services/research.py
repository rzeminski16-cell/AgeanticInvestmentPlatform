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
from urllib.parse import urlsplit

import structlog
from sqlalchemy import Select, or_, select
from sqlalchemy import false as sa_false
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
from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, Company, FinancialFact, ResearchRequest, SourceDocument
from aer.errors import AerError
from aer.extract import extract_text
from aer.services.acquisition import record_acquisition

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

# How much of a fetched page reaches the model. The role's input cap is 30k tokens and the
# loop accumulates evidence across rounds, so one page must not be able to fill it.
MAX_FETCHED_CHARS: Final = 20_000

# Which extractor reads which kind. Anything else is archived and cited but not read: the
# platform holds the bytes either way, and guessing at an extractor is how a parser meets
# content it was not written for.
# What a page the worker chose enters at. Named once and used for both the row and the
# answer: a literal in each place is a literal that can drift, and the sabotage pass
# found exactly that — the recorded tier could be raised to T1 with the reported tier
# still reading T5, which is the one combination nobody would notice.
_FETCHED_TIER: Final = SourceTier.T5_SECONDARY

_EXTRACTORS: Final[dict[str, str]] = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/xml": "html",
    "text/xml": "html",
    "application/pdf": "pdf",
}


def build_executors(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    fetcher: Any = None,
    store: Any = None,
    settings: Settings | None = None,
    job_id: uuid.UUID | None = None,
    sec_client: Any = None,
) -> dict[str, Any]:
    """The tool executors for one run: searches over what the run already holds, a search
    of the regulator's index for what it does not, and — when a fetcher is bound — one more
    page from a host it already reads.

    The allowlist grants the *capability*, the run decides the *availability*, and an
    unavailable tool is a recorded refusal rather than an error. ``fetch_known_url`` is
    bound only when a fetcher, a store and settings are all supplied, and
    ``search_filings_full_text`` only when a client is; a caller that omits them gets
    exactly the two searches it always did.
    """

    async def search_facts(tool_request: ToolRequest) -> ExecutedTool:
        company_id = await _company_id_for(session, request=request)
        rows = await session.scalars(
            _visible_facts(request, company_id)
            .where(FinancialFact.concept.ilike(f"%{tool_request.query.strip()}%"))
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

    async def fetch_known_url(tool_request: ToolRequest) -> ExecutedTool:
        """Fetch one page, from a host this run already holds a document from.

        **The model chooses the path. Code chooses the host.** That split is the whole
        control, and `aer.sources.issuer` states why: "there is no code path that learns a
        new domain from a page and then fetches it, because the one thing an attacker who
        controls a page wants is exactly that." A worker reads untrusted evidence, so a URL
        it hands back is untrusted text. Honouring the host would be that forbidden path
        with a language model in the middle of it.

        So the host must already be established — some earlier fetch, driven by a regulator
        identifier or an operator-supplied domain, put a document from it in this run — and
        that document's provider is reused, because provider is what decides the licence,
        the rate limit and the standing allowlist the host was admitted under.

        The tier is not inherited. A page a model picked off a host is not the artefact the
        adapter for that host was built to fetch, so it enters at the weakest tier and has
        to earn its weight like any other secondary source.
        """
        url = tool_request.query.strip()
        established = await _established_host(session, request=request, url=url)
        if established is None:
            return ExecutedTool(
                tool=tool_request.tool,
                query=url,
                executed=False,
                refusal=(
                    "Refused: this run holds no document from that host, and a host is "
                    "never taken from a request. Fetch only from a host whose documents "
                    "search_sources has already shown you."
                ),
            )

        host, provider = established
        try:
            result = await fetcher.fetch(url, provider=provider, extra_hosts=(host,))
        except AerError as exc:
            # Every control in the fetch layer refuses by raising — robots, SSRF, the size
            # cap, the breaker. A refusal is information the worker can act on, not a
            # reason to fail the node.
            return ExecutedTool(
                tool=tool_request.tool, query=url, executed=False, refusal=f"Refused: {exc.message}"
            )

        # A page the run already holds is answered from the record it already has —
        # highest tier, unquarantined first (gap A43). Recording it again minted a fresh
        # id at T5 with a quarantine flag for the very bytes the acquire step held at T1,
        # and the competing ids poisoned every citation of the document downstream.
        held = await _already_held(session, request=request, sha256=result.sha256)
        if held is not None:
            document_id = str(held.id)
            text, note = await _text_of(store, result=result, settings=settings)
            return ExecutedTool(
                tool=tool_request.tool,
                query=url,
                executed=True,
                internal_results=[
                    {
                        "source_document_id": document_id,
                        "tier": held.source_tier.value,
                        "status_code": result.status_code,
                        "media_type": result.media_type,
                        "quarantined": held.quarantined,
                        "extraction": note,
                    }
                ],
                untrusted_evidence=[
                    {
                        "source_document_id": document_id,
                        "tier": held.source_tier.value,
                        "title": url,
                        "text": text,
                    }
                ],
            )

        acquisition = await record_acquisition(
            session,
            store,
            request=request,
            job_id=job_id,
            result=result,
            provider=provider,
            source_tier=_FETCHED_TIER,
            title=url,
        )
        document_id = str(acquisition.source_document.id)
        text, note = await _text_of(store, result=result, settings=settings)

        return ExecutedTool(
            tool=tool_request.tool,
            query=url,
            executed=True,
            internal_results=[
                {
                    "source_document_id": document_id,
                    "tier": _FETCHED_TIER.value,
                    "status_code": result.status_code,
                    "media_type": result.media_type,
                    "quarantined": acquisition.quarantined,
                    "extraction": note,
                }
            ],
            # The page's own words, which is the one thing here that a hostile server
            # controls. It reaches the model only inside the wrapper, with its id beside it
            # so a finding can cite what it read.
            untrusted_evidence=[
                {
                    "source_document_id": document_id,
                    "tier": _FETCHED_TIER.value,
                    "title": url,
                    "text": text,
                }
            ],
        )

    async def search_filings_full_text(tool_request: ToolRequest) -> ExecutedTool:
        """Which of this company's filings discuss a thing.

        **The phrase is the model's; the scope is not.** The CIK and the as-of bound are
        supplied here, from the run, and are the difference between a search of this
        company's filings and a search of everybody's: an unscoped hit is a competitor's
        document, and acquiring one would mean citing it for this company's figures.

        A listing, not a reading. Hits come back as metadata — form, date, URL — and the
        worker spends a `fetch_known_url` call to read one. That keeps the twelve-call
        budget meaningful: a search that silently fetched ten documents would spend the
        budget without the worker choosing to.
        """
        company_id = await _company_id_for(session, request=request)
        cik = await session.scalar(select(Company.cik).where(Company.id == company_id))
        if not cik:
            return ExecutedTool(
                tool=tool_request.tool,
                query=tool_request.query,
                executed=False,
                refusal=(
                    "This run has not resolved the company against a registry yet, so a "
                    "filing search cannot be scoped to it. An unscoped search would return "
                    "other companies' filings."
                ),
            )

        try:
            found = await sec_client.search_full_text(
                tool_request.query.strip(),
                cik=cik,
                as_of_date=request.as_of_date if request.point_in_time else None,
                size=MAX_HITS,
            )
        except AerError as refused:
            return ExecutedTool(
                tool=tool_request.tool,
                query=tool_request.query,
                executed=False,
                refusal=f"The filing index refused the search: {refused.message}",
            )

        usable, excluded = found.data.admissible(
            request.as_of_date if request.point_in_time else None
        )
        # Ids, forms, dates and URLs are the index's, which is ours to trust — the
        # documents' own words are not here, and reading one costs a fetch.
        results: list[dict[str, Any]] = [
            {
                "form": hit.form,
                "filed": hit.filed.isoformat(),
                "accession": hit.accession,
                "url": hit.url,
            }
            for hit in usable
        ]
        if excluded:
            # Said rather than silently dropped: "the search found nothing" and "the search
            # found things you may not read" call for different next moves.
            results.append(
                {
                    "note": (
                        f"{len(excluded)} further hit(s) were published after this run's "
                        "as-of date and are not available to it."
                    )
                }
            )

        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=True,
            internal_results=results,
        )

    executors: dict[str, Any] = {
        "search_facts": search_facts,
        "search_sources": search_sources,
    }
    if fetcher is not None and store is not None and settings is not None:
        executors["fetch_known_url"] = fetch_known_url
    if sec_client is not None:
        executors["search_filings_full_text"] = search_filings_full_text
    return executors


async def _already_held(
    session: AsyncSession, *, request: ResearchRequest, sha256: str
) -> SourceDocument | None:
    """The request's best existing record of these bytes, or ``None`` for new bytes.

    Best means highest tier and unquarantined first: the acquire step's T1 record of a
    filing must answer for it, not the T5 quarantined duplicate a re-fetch would mint.
    Matching is by artefact digest — the content-addressed store's own identity — so a
    URL variant of the same document still resolves to the record already held.
    """
    rows = list(
        await session.scalars(
            select(SourceDocument)
            .join(Artefact, Artefact.id == SourceDocument.artefact_id)
            .where(SourceDocument.request_id == request.id, Artefact.sha256 == sha256)
        )
    )
    if not rows:
        return None
    return min(rows, key=lambda row: (row.quarantined, row.source_tier.rank))


async def _established_host(
    session: AsyncSession, *, request: ResearchRequest, url: str
) -> tuple[str, Provider] | None:
    """The host and provider under which this run already reads that host, or ``None``.

    Matched on the host alone rather than the whole URL: fetching a URL the run already
    holds would return a document it already has. What this admits is a *different page*
    on a host the platform has already been allowed to read.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None

    rows = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.request_id == request.id)
        .order_by(SourceDocument.retrieved_at.desc())
    )
    for row in rows:
        if (urlsplit(row.url or "").hostname or "").lower() == host:
            return host, row.provider
    return None


async def _text_of(store: Any, *, result: Any, settings: Settings | None) -> tuple[str, str]:
    """The page's text, read back from the archived copy, and a note about how it went.

    Read by hash rather than from the bytes in hand: that is what demonstrates the text
    came from the copy the citation will point at. An extractor that refuses returns its
    reason as the note — a page that could not be read is a fact about the page, and the
    worker can record it as a lead instead of guessing at contents.
    """
    extractor = _EXTRACTORS.get(result.media_type)
    if extractor is None:
        return "", f"no extractor for {result.media_type}"
    if settings is None:  # pragma: no cover -- bound only when settings are supplied
        return "", "extraction unavailable"
    try:
        extracted = await extract_text(
            store, sha256=result.sha256, extractor=extractor, settings=settings
        )
    except AerError as exc:
        return "", f"not extracted: {exc.message}"

    body = extracted.text.text
    if len(body) <= MAX_FETCHED_CHARS:
        return body, "extracted"
    return body[:MAX_FETCHED_CHARS], f"extracted, truncated to {MAX_FETCHED_CHARS} characters"


async def _company_id_for(session: AsyncSession, *, request: ResearchRequest) -> uuid.UUID | None:
    """The company this request researches, or ``None`` before it has been resolved."""
    found: uuid.UUID | None = await session.scalar(
        select(Company.id).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    return found


def _visible_facts(request: ResearchRequest, company_id: uuid.UUID | None) -> Select[Any]:
    """Facts a worker on this run may see: the company's, as at the as-of date.

    **Scoped by company, not by request, and that is the fix for a run that found nothing.**
    Facts are deduplicated on an observation key that deliberately excludes the source
    document — an observation is an observation, and storing MSFT's 2023 revenue twice
    because two runs both read the same filing would be a second copy of the same truth. So
    the *second* run of a company inserts nothing: "supplied 18588, inserted 0" is the
    dedupe working. But every consumer here joined through ``source_documents`` to
    ``request_id``, so those facts belonged to the earlier run's document and this run could
    not see one of them. Five workers spent sixty tool calls searching an empty table, three
    exhausted, and the two that finished reported no findings at all.

    ``calculate`` never had the problem because it scopes by ``company_id``, which is the
    established shape: the fact is about the company, and the source document records where
    it came from — possibly an earlier run, whose artefact is hashed and traceable exactly
    the same way.

    **The date filter is part of the fix, not a separate improvement.** Request scope
    happened to bound what a worker saw to one acquisition; company scope does not, so
    without this a point-in-time run could now be shown a fact filed after its as-of date by
    some later run's acquisition. Filtered on ``filed_date``, because what matters is when
    the filing was filed, not when this platform happened to fetch it.
    """
    statement = select(FinancialFact).where(FinancialFact.company_id == company_id)
    if company_id is None:
        # Before `acquire` resolves the company there is nothing to show. `None` would
        # match no rows anyway; saying so here keeps that an intention rather than a
        # coincidence of SQL null semantics.
        return statement.where(sa_false())
    if request.point_in_time:
        statement = statement.where(FinancialFact.filed_date <= request.as_of_date)
    return statement


async def validate_report(
    session: AsyncSession, report: WorkerReport, *, request: ResearchRequest
) -> list[str]:
    """Every cited id must resolve inside this run. Returns the problems, named.

    The ids a worker may cite are exactly the ids its searches surfaced; anything else —
    a fabrication, a typo, an id from some other run — is refused with the finding and
    the id spelt out, so the worker's next turn can fix it or drop it.

    **A refusal that does not say what was wrong is a refusal the worker cannot act on.**
    A live worker cited ``'9541000000'`` — the *value* of a fact, taken from the ``value``
    field of a ``search_facts`` result instead of the ``fact_id`` beside it. Told only that
    this run "does not hold" that fact, it did the reasonable thing and reached for a
    different number, and burned five rounds and eleven tool calls doing it. Something that
    is not an id at all is now said to be exactly that, because the fix is a different one:
    not *find another fact*, but *cite the id, not the number*.
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
    # The same reach the worker was given. A validator narrower than the search would
    # refuse the worker's own evidence back at it, which is a loop with no exit.
    valid_facts = await _existing(
        session,
        _visible_facts(request, await _company_id_for(session, request=request)).with_only_columns(
            FinancialFact.id
        ),
        column=FinancialFact.id,
        cited=cited_facts,
    )

    for index, finding in enumerate(report.findings):
        for identifier in finding.source_document_ids:
            if identifier not in valid_sources:
                problems.append(_refusal(index, identifier, kind="source document"))
        for identifier in finding.fact_ids:
            if identifier not in valid_facts:
                problems.append(_refusal(index, identifier, kind="fact"))
    return problems


# Which field of a search result carries the id, by what the worker was citing. Named in
# the refusal because "cite an id" is advice; "copy the fact_id field" is an instruction.
_ID_FIELDS: Final[dict[str, str]] = {
    "fact": "fact_id",
    "source document": "source_document_id",
}


def _refusal(index: int, identifier: str, *, kind: str) -> str:
    """Why one cited id was not accepted, in terms the worker can act on."""
    finding = f"Finding {index + 1} cites {kind} {identifier!r},"
    if _looks_like_an_id(identifier):
        return f"{finding} which this run does not hold."
    return (
        f"{finding} which is not an id at all. Every id is a UUID: copy the "
        f"{_ID_FIELDS[kind]} field of a search result exactly. The other fields — a value, "
        "a concept, a title — are never cited."
    )


def _looks_like_an_id(identifier: str) -> bool:
    try:
        uuid.UUID(identifier)
    except ValueError:
        return False
    return True


async def _existing(
    session: AsyncSession, base_query: Any, *, column: Any, cited: set[str]
) -> set[str]:
    """The subset of cited ids that resolve. Non-UUID strings never resolve."""
    parseable: dict[uuid.UUID, str] = {
        uuid.UUID(identifier): identifier for identifier in cited if _looks_like_an_id(identifier)
    }
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
