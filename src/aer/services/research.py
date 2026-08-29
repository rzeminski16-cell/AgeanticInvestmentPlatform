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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final
from urllib.parse import urlsplit

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
from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, Company, Cost, FinancialFact, ResearchRequest, SourceDocument
from aer.errors import AerError
from aer.extract import extract_text
from aer.extract.dates import extract_publication_date
from aer.providers.costs import price_usage, price_web_search
from aer.services.acquisition import acquisition_root, record_acquisition
from aer.services.facts import visible_facts
from aer.services.scope import scope_for_request, with_subject
from aer.services.sources import visible_sources
from aer.services.subject import subject_name
from aer.sources.tiering import DocumentKind, tier_for

__all__ = [
    "MAX_HITS",
    "MAX_WEB_SEARCHES",
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

# Web searches per worker node (ADR 0092). Counted in code, because the bound is what the
# step's cost estimate is priced against: five workers at three searches is at most
# fifteen billed searches per run, at the fee the ADR verifies. The fourth request is a
# refusal naming the bound, which the worker's own prompt already warns of.
MAX_WEB_SEARCHES: Final = 3

# What a page the worker chose enters at when nothing establishes more. Named once and
# used for both the row and the answer: a literal in each place is a literal that can
# drift, and the sabotage pass found exactly that — the recorded tier could be raised to
# T1 with the reported tier still reading T5, which is the one combination nobody would
# notice. A page the regulator's own index named is the exception — see _RegulatorHit.
_FETCHED_TIER: Final = SourceTier.T5_SECONDARY


@dataclass(frozen=True, slots=True)
class _RegulatorHit:
    """A filing the regulator's index returned to this worker, keyed by its URL.

    The live run recorded five EDGAR filings as undated T5 secondary material — the
    worker found them through full-text search, whose hits carry the regulator's own
    filed date and form, and then fetched them through a path that threw both away. The
    executors share this registry so a fetch of a searched-for URL enters at the tier and
    date the index established, exactly as the acquire step's documents do.

    Populated only from the index's replies — never from page content — so an injected
    URL in a fetched document cannot claim a tier this way.
    """

    filed: date
    form: str
    accession: str


# Which extractor reads which kind. Anything else is archived and cited but not read: the
# platform holds the bytes either way, and guessing at an extractor is how a parser meets
# content it was not written for.
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
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    """The tool executors for one run: searches over what the run already holds, a search
    of the regulator's index for what it does not, and — when a fetcher is bound — one more
    page from a host it already reads.

    The allowlist grants the *capability*, the run decides the *availability*, and an
    unavailable tool is a recorded refusal rather than an error. ``fetch_known_url`` is
    bound only when a fetcher, a store and settings are all supplied,
    ``search_filings_full_text`` only when a client is, and ``web_search`` (ADR 0092)
    only when an agent context is — the search is a billed provider call, and the context
    is what carries the provider, the route and the step its costs are metered against.
    A caller that omits them gets exactly the two searches it always did.
    """
    # Filings the index has named to this worker, by URL — the bridge that lets a fetch
    # of a searched-for filing keep the tier and date the search established.
    regulator_hits: dict[str, _RegulatorHit] = {}

    # One extraction per document per worker node (gap A56): the live run re-extracted
    # the same 1.5MB filing on every fetch of it, recomputing its ninety-six hidden-text
    # findings each pass. Keyed by digest, which is the artefact's identity, so a URL
    # variant of the same bytes still hits.
    extracted_texts: dict[str, tuple[str, str]] = {}

    async def search_facts(tool_request: ToolRequest) -> ExecutedTool:
        company_id = await _company_id_for(session, request=request)
        rows = await session.scalars(
            visible_facts(with_subject(await scope_for_request(session, request), company_id))
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
                    "period_start": (row.period_start.isoformat() if row.period_start else None),
                    "period_end": row.period_end.isoformat(),
                    "fiscal_period": row.fiscal_period,
                    "basis": row.basis.value if hasattr(row.basis, "value") else str(row.basis),
                }
                for row in rows
            ],
        )

    async def search_sources(tool_request: ToolRequest) -> ExecutedTool:
        needle = f"%{tool_request.query.strip()}%"
        # The column directly, with no ticker fallback, because this listing is already
        # scoped to *this run's* documents: the step that stamps them is the step that
        # writes `request.company_id`, so a NULL here means acquire has not run and there
        # is nothing stamped to find (ADR 0061).
        rows = await session.scalars(
            visible_sources(await scope_for_request(session, request))
            .where(or_(SourceDocument.title.ilike(needle), SourceDocument.url.ilike(needle)))
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
        return await _fetch_known_url(
            session,
            tool_request,
            request=request,
            fetcher=fetcher,
            store=store,
            settings=settings,
            job_id=job_id,
            regulator_hits=regulator_hits,
            texts=extracted_texts,
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
        # Newest first (gap O9): a live run spent two of its twelve fetches on
        # decade-old 10-Ks because the listing arrived in index order. Nothing is
        # excluded — an old filing can still be chosen — but the documents most able to
        # support a current claim lead the list the worker spends its budget from.
        usable = sorted(usable, key=lambda hit: hit.filed, reverse=True)
        # Ids, forms, dates and URLs are the index's, which is ours to trust — the
        # documents' own words are not here, and reading one costs a fetch. Trusted is
        # also why each hit joins the registry: a later fetch of this URL enters at the
        # tier and date the index established rather than as undated secondary material.
        for hit in usable:
            regulator_hits[hit.url] = _RegulatorHit(
                filed=hit.filed, form=hit.form, accession=hit.accession
            )
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

    # This worker node's search spend, counted where the bound is enforced.
    searches_spent = {"count": 0}

    async def web_search(tool_request: ToolRequest) -> ExecutedTool:
        assert agent_context is not None  # bound only when a context was supplied
        return await _web_search(
            session,
            tool_request,
            agent_context=agent_context,
            request=request,
            searches_spent=searches_spent,
        )

    executors: dict[str, Any] = {
        "search_facts": search_facts,
        "search_sources": search_sources,
    }
    if fetcher is not None and store is not None and settings is not None:
        executors["fetch_known_url"] = fetch_known_url
    if sec_client is not None:
        executors["search_filings_full_text"] = search_filings_full_text
    if agent_context is not None:
        executors["web_search"] = web_search
    return executors


async def _web_search(
    session: AsyncSession,
    tool_request: ToolRequest,
    *,
    agent_context: AgentContext,
    request: ResearchRequest,
    searches_spent: dict[str, int],
) -> ExecutedTool:
    """One web search: refused where it cannot be honest, metered where it can (ADR 0092).

    Three refusals, each deterministic and each stated:

    * **Point-in-time.** A live index cannot be bounded by an as-of date, and a result's
      own date line is external text — so a point-in-time run whose as-of date is in the
      past never searches. Invariant 4, enforced at acquisition, in code.
    * **The bound.** :data:`MAX_WEB_SEARCHES` per worker node, counted here.
    * **No route.** A deployment that never configured the ``web_search`` route gets a
      recorded refusal, not a silent default model — the router's own rule.

    What comes back is a listing — titles, URLs, age notes — and the titles and URLs are
    external text, so they reach the model only in the untrusted channel, labelled
    ``T6_UNVERIFIED`` and explicitly uncitable. Both halves of the bill — the per-search
    fee and the carrying call's tokens — land as ``costs`` rows against this step before
    the results are returned.
    """
    if request.point_in_time and request.as_of_date < datetime.now(UTC).date():
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=False,
            refusal=(
                "Refused: this is a point-in-time run with an as-of date in the past, and "
                "a live web search cannot be bounded by that date. Nothing published "
                "after the as-of date may inform this run, and a search result's own "
                "date is not evidence of when it was published."
            ),
        )

    if searches_spent["count"] >= MAX_WEB_SEARCHES:
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=False,
            refusal=(
                f"Refused: this investigation's web-search budget of {MAX_WEB_SEARCHES} "
                "is spent. Work from what the searches returned, or record what is left "
                "as a lead."
            ),
        )

    try:
        route = agent_context.router.resolve("web_search")
    except AerError as unrouted:
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=False,
            refusal=f"Refused: {unrouted.message}",
        )

    try:
        outcome = await agent_context.provider.search_web(
            tool_request.query.strip(), model=route.model, max_results=MAX_HITS
        )
    except AerError as failed:
        # A failed search is not billed — the vendor's published rule, and the reason
        # the meter below only runs on success.
        return ExecutedTool(
            tool=tool_request.tool,
            query=tool_request.query,
            executed=False,
            refusal=f"The web search failed: {failed.message}",
        )

    searches_spent["count"] += 1
    await _meter_search(session, agent_context, outcome=outcome)

    remaining = MAX_WEB_SEARCHES - searches_spent["count"]
    return ExecutedTool(
        tool=tool_request.tool,
        query=tool_request.query,
        executed=True,
        internal_results=[
            {
                "results": len(outcome.hits),
                "searches_remaining": remaining,
                "note": (
                    "A listing, not a reading: titles, URLs and the index's age notes. "
                    "Nothing here is citable evidence and no result carries an id."
                ),
            }
        ],
        # Titles, URLs and age notes are the search engine's text — external, so they
        # travel only in the wrapped channel, at the tier that says what they are:
        # hypothesis material, never evidence.
        untrusted_evidence=[
            {
                "source_document_id": "web-search-result (not citable)",
                "tier": SourceTier.T6_UNVERIFIED.value,
                "title": hit.title or "(untitled)",
                "text": f"{hit.title or '(untitled)'} — {hit.url}"
                + (f" ({hit.page_age})" if hit.page_age else ""),
            }
            for hit in outcome.hits
        ],
    )


async def _meter_search(
    session: AsyncSession, agent_context: AgentContext, *, outcome: Any
) -> None:
    """Both halves of a search's bill, as ``costs`` rows against the worker's step.

    The carrying call's tokens through the ordinary pricer, and the per-search fee at the
    verified rate — separate lines, because they are different units with different
    published prices and the ledger must reconcile against the vendor's own bill.
    """
    lines = price_usage(
        outcome.usage,
        provider=agent_context.provider.name,
        usd_to_gbp=agent_context.settings.usd_to_gbp,
    )
    fee = price_web_search(
        outcome.searches,
        provider=agent_context.provider.name,
        model=outcome.usage.model,
        usd_to_gbp=agent_context.settings.usd_to_gbp,
    )
    if fee is not None:
        lines.append(fee)

    for line in lines:
        session.add(
            Cost(
                job_id=agent_context.job_step.job_id,
                job_step_id=agent_context.job_step.id,
                category=line.category.value,
                provider=line.provider,
                model=line.model,
                units=line.units,
                unit_type=line.unit_type,
                amount_usd=line.amount_usd,
                amount_gbp=line.amount_gbp,
                fx_rate=line.fx_rate,
            )
        )
        agent_context.spend_gbp += line.amount_gbp
    await session.flush()


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
            .where(SourceDocument.work_order_id == request.id, Artefact.sha256 == sha256)
        )
    )
    if not rows:
        return None
    return min(rows, key=lambda row: (row.quarantined, row.source_tier.rank))


async def _fetch_known_url(
    session: AsyncSession,
    tool_request: ToolRequest,
    *,
    request: ResearchRequest,
    fetcher: Any,
    store: Any,
    settings: Settings | None,
    job_id: uuid.UUID | None,
    regulator_hits: dict[str, _RegulatorHit],
    texts: dict[str, tuple[str, str]],
) -> ExecutedTool:
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

    The tier is not inherited from the host — but it is granted by the index. A page a
    model picked off a host is not the artefact the adapter for that host was built to
    fetch, so it enters at the weakest tier; a filing the regulator's *own index* named
    to this worker, with its form and filed date, is exactly that artefact, and the
    live run mis-tiering five EDGAR filings as undated T5 is what this distinction
    fixes. Anything else gets its publication date derived from the response headers
    where they establish one, and stays honestly undated where they do not.
    """
    url = tool_request.query.strip()
    # Before any network work (gap A56): a URL this run has already acquired is
    # answered from its own record and archive. `_already_held` can only dedupe
    # *after* a fetch, because it keys on the response's digest — so the live run
    # fetched the same 1.5MB filing six times to be told six times that it held it.
    held = await _held_by_url(session, request=request, url=url)
    held_artefact = await session.get(Artefact, held.artefact_id) if held is not None else None
    if held is not None and held_artefact is not None:
        text, note = await _memoised_text(
            texts, store, settings, sha256=held_artefact.sha256, media_type=held_artefact.media_type
        )
        return ExecutedTool(
            tool=tool_request.tool,
            query=url,
            executed=True,
            internal_results=[
                {
                    "source_document_id": str(held.id),
                    "tier": held.source_tier.value,
                    "media_type": held_artefact.media_type,
                    "quarantined": held.quarantined,
                    "extraction": note,
                    "note": (
                        "already held by this run; served from the archive without refetching"
                    ),
                }
            ],
            untrusted_evidence=[
                {
                    "source_document_id": str(held.id),
                    "tier": held.source_tier.value,
                    "title": url,
                    "text": text,
                }
            ],
        )

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
        text, note = await _memoised_text(
            texts, store, settings, sha256=result.sha256, media_type=result.media_type
        )
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

    hit = regulator_hits.get(url) or regulator_hits.get(result.final_url)
    if hit is not None:
        # The regulator's index named this URL to this worker, with its form and
        # filed date — the same authority the acquire step's documents enter under.
        tier = tier_for(provider, DocumentKind.REGULATORY_FILING)
        acquisition = await record_acquisition(
            session,
            store,
            work_order=await acquisition_root(session, request),
            job_id=job_id,
            # The full-text search is scoped to the subject's CIK, so a filing the
            # index named to this worker is the subject's own (ADR 0061).
            company_id=request.company_id,
            result=result,
            provider=provider,
            source_tier=tier,
            publication_date=hit.filed,
            publication_date_confidence=1.0,
            title=f"{hit.form} {hit.accession}",
        )
    else:
        tier = _FETCHED_TIER
        acquisition = await record_acquisition(
            session,
            store,
            work_order=await acquisition_root(session, request),
            job_id=job_id,
            result=result,
            provider=provider,
            source_tier=tier,
            # Best effort from the response headers alone. Page content is a hostile
            # surface — a date parsed out of attacker-controlled text would let the
            # page pick its own admissibility — so an undatable page stays undated.
            published=extract_publication_date(
                headers=result.headers, not_after=datetime.now(UTC).date()
            ),
            title=url,
        )
    document_id = str(acquisition.source_document.id)
    text, note = await _memoised_text(
        texts, store, settings, sha256=result.sha256, media_type=result.media_type
    )

    return ExecutedTool(
        tool=tool_request.tool,
        query=url,
        executed=True,
        internal_results=[
            {
                "source_document_id": document_id,
                "tier": tier.value,
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
                "tier": tier.value,
                "title": url,
                "text": text,
            }
        ],
    )


async def _memoised_text(
    texts: dict[str, tuple[str, str]],
    store: Any,
    settings: Settings | None,
    *,
    sha256: str,
    media_type: str,
) -> tuple[str, str]:
    """One extraction per document per worker node (gap A56).

    The live run re-extracted the same 1.5MB filing on every fetch of it, recomputing
    its ninety-six hidden-text findings each pass. Keyed by digest — the artefact's
    identity — so a URL variant of the same bytes still hits.
    """
    if sha256 not in texts:
        texts[sha256] = await _text_of(
            store, sha256=sha256, media_type=media_type, settings=settings
        )
    return texts[sha256]


async def _held_by_url(
    session: AsyncSession, *, request: ResearchRequest, url: str
) -> SourceDocument | None:
    """The request's best existing record of this exact URL, or ``None``.

    Checked *before* fetching, where :func:`_already_held`'s digest check can only run
    after (gap A56). Best means highest tier and unquarantined first, for the same
    reason it does there: the acquire step's T1 record must answer for the document.
    """
    rows = list(
        await session.scalars(
            select(SourceDocument).where(
                SourceDocument.work_order_id == request.id, SourceDocument.url == url
            )
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
        .where(SourceDocument.work_order_id == request.id)
        .order_by(SourceDocument.retrieved_at.desc())
    )
    for row in rows:
        if (urlsplit(row.url or "").hostname or "").lower() == host:
            return host, row.provider
    return None


async def _text_of(
    store: Any, *, sha256: str, media_type: str, settings: Settings | None
) -> tuple[str, str]:
    """The page's text, read back from the archived copy, and a note about how it went.

    Read by hash rather than from the bytes in hand: that is what demonstrates the text
    came from the copy the citation will point at. An extractor that refuses returns its
    reason as the note — a page that could not be read is a fact about the page, and the
    worker can record it as a lead instead of guessing at contents.
    """
    extractor = _EXTRACTORS.get(media_type)
    if extractor is None:
        return "", f"no extractor for {media_type}"
    if settings is None:  # pragma: no cover -- bound only when settings are supplied
        return "", "extraction unavailable"
    try:
        extracted = await extract_text(store, sha256=sha256, extractor=extractor, settings=settings)
    except AerError as exc:
        return "", f"not extracted: {exc.message}"

    body = extracted.text.text
    if len(body) <= MAX_FETCHED_CHARS:
        return body, "extracted"
    return body[:MAX_FETCHED_CHARS], f"extracted, truncated to {MAX_FETCHED_CHARS} characters"


async def _company_id_for(session: AsyncSession, *, request: ResearchRequest) -> uuid.UUID | None:
    """The company this request researches, or ``None`` before it has been resolved.

    The column first, because ``acquire`` writes the id it actually resolved against the
    registry and that is authoritative. The ticker-and-exchange lookup stays as the fallback
    it was written to be: a request can be looked at before ``acquire`` has run, and the
    listing is the only key there is until then. It is the weaker key — a re-used or
    re-listed ticker defeats it — which is why nothing prefers it any more.
    """
    if request.company_id is not None:
        return request.company_id
    found: uuid.UUID | None = await session.scalar(
        select(Company.id).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    return found


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
        select(SourceDocument.id).where(SourceDocument.work_order_id == request.id),
        column=SourceDocument.id,
        cited=cited_sources,
    )
    # The same reach the worker was given. A validator narrower than the search would
    # refuse the worker's own evidence back at it, which is a loop with no exit.
    valid_facts = await _existing(
        session,
        visible_facts(
            with_subject(
                await scope_for_request(session, request),
                await _company_id_for(session, request=request),
            )
        ).with_only_columns(FinancialFact.id),
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
        # The filer's own name, not the one typed into the form (gap A67).
        company_name=await subject_name(session, request),
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
