"""Talking to EDGAR: URL construction, pacing, and parsing the response.

A thin layer. Everything that could go wrong on the network — SSRF, allowlists, retries,
the byte cap, archiving — is already handled by :class:`~aer.fetch.client.SafeFetcher`,
and duplicating any of it here would mean two implementations to keep correct. What is
left is EDGAR-specific: which URL, how fast, and what the bytes mean.

**URLs are built here and nowhere else.** Every method takes an identifier the SEC issued
— a CIK, an accession number — and constructs the URL from it. No method accepts a URL.
That is what carries the "no agent-callable tool takes a URL" property up from the fetch
layer: even a filing whose text says ``fetch https://attacker.test/`` produces no method
call that could act on it, because no such method exists.

**Why there is a delay on top of a rate limiter.** :mod:`aer.fetch.policy` caps EDGAR at
eight requests per second through a shared Redis token bucket, against the SEC's stated
limit of ten. This client adds a further pause between sequential requests. The two guard
different things: the bucket is the ceiling across every worker, while the pause keeps a
single sequential loop from spending its whole allowance in one burst and leaving nothing
for a concurrent run. The SEC blocks rather than throttles, and an IP block affects
everything on this machine for as long as it lasts, so the margin is worth its cost.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

import structlog

from aer.core.enums import Provider, SourceTier
from aer.core.schemas.facts import RawFact
from aer.errors import ValidationError
from aer.fetch.client import FetchResult, SafeFetcher
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.sec.companyfacts import CompanyFacts, parse_company_facts
from aer.sources.sec.submissions import PERIODIC_FORMS, SubmissionsIndex, parse_submissions
from aer.sources.sec.tickers import (
    TickerRecord,
    format_cik,
    parse_company_tickers,
    resolve_ticker,
)
from aer.storage.protocol import ArtefactStore

__all__ = [
    "COMPANY_TICKERS_URL",
    "INTER_REQUEST_DELAY_SECONDS",
    "SecEdgarClient",
    "SecResponse",
]

_log = structlog.get_logger("aer.sources.sec")

# The exchange-bearing variant. The plain company_tickers.json omits the exchange, and
# without it a ticker listed in two places cannot be told apart.
COMPANY_TICKERS_URL: Final = "https://www.sec.gov/files/company_tickers_exchange.json"

_SUBMISSIONS_URL: Final = "https://data.sec.gov/submissions/CIK{cik}.json"
_COMPANYFACTS_URL: Final = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# 100 ms between sequential requests: ten per second at the very most from one loop, which
# is the SEC's own published figure, while the shared bucket holds the true ceiling at
# eight. See the module docstring for why both exist.
INTER_REQUEST_DELAY_SECONDS: Final = 0.1

_JSON_TYPES: Final[frozenset[str]] = frozenset({"application/json"})

Sleeper = Callable[[float], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class SecResponse[T]:
    """A parsed response, together with the fetch that produced it.

    Both halves travel together because the provenance layer needs the
    :class:`~aer.fetch.client.FetchResult` — the hash, the status, the licence note — and
    the caller needs the parsed data. Returning only the parsed data would make the
    artefact unreachable, and every fact in it uncitable.
    """

    data: T
    fetch: FetchResult

    @property
    def sha256(self) -> str:
        return self.fetch.sha256


class SecEdgarClient:
    """Fetches and parses EDGAR's four endpoints.

    Args:
        fetcher: The single door to the network. Supplies the User-Agent EDGAR requires.
        store: Where the fetcher archived the bytes, so they can be read back to parse.
        sleep: Injected so the inter-request pause is assertable without waiting.
        inter_request_delay: Seconds between sequential requests.
    """

    provider: Final = Provider.SEC_EDGAR
    source_tier: Final = SourceTier.T1_REGULATORY

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        store: ArtefactStore,
        sleep: Sleeper | None = None,
        inter_request_delay: float = INTER_REQUEST_DELAY_SECONDS,
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._sleep: Sleeper = sleep or asyncio.sleep
        self._delay = inter_request_delay
        self._requests_made = 0

    # -- Endpoints -------------------------------------------------------------------------

    async def fetch_company_tickers(self) -> SecResponse[tuple[TickerRecord, ...]]:
        """The full ticker-to-CIK table.

        About a megabyte, and it changes slowly. A caller resolving several companies
        should hold the result rather than fetching it again.
        """
        result = await self._get(COMPANY_TICKERS_URL)
        return SecResponse(data=parse_company_tickers(await self._body(result)), fetch=result)

    async def fetch_submissions(self, cik: str) -> SecResponse[SubmissionsIndex]:
        """An entity's filing history."""
        result = await self._get(_SUBMISSIONS_URL.format(cik=format_cik(cik)))
        return SecResponse(data=parse_submissions(await self._body(result)), fetch=result)

    async def fetch_company_facts(self, cik: str) -> SecResponse[CompanyFacts]:
        """Every XBRL fact an entity ever tagged.

        Large — tens of megabytes for a big filer with a long history. The configured
        artefact cap applies, and a filer exceeding it fails loudly rather than being
        silently truncated into a partial fact set.
        """
        result = await self._get(_COMPANYFACTS_URL.format(cik=format_cik(cik)))
        return SecResponse(data=parse_company_facts(await self._body(result)), fetch=result)

    async def fetch_document(self, ref: DocumentRef) -> FetchResult:
        """Fetch a filing document referenced by an index this client produced.

        The URL comes from a :class:`~aer.sources.base.DocumentRef`, which is only ever
        built by :meth:`discover_documents` from an accession number EDGAR issued. It is
        still validated against the allowlist by the fetch layer, because a chain of
        trusted construction is only as good as its weakest link and this one crosses a
        module boundary.
        """
        return await self._get(ref.url, expected_media_types=None)

    # -- Adapter surface -------------------------------------------------------------------

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        """Turn a ticker into a CIK.

        Raises:
            ValidationError: If the ticker is not in EDGAR, or is ambiguous.
        """
        response = await self.fetch_company_tickers()
        record = resolve_ticker(response.data, ticker, exchange=exchange)
        _log.info(
            "sec.entity_resolved",
            ticker=record.ticker,
            cik=record.cik,
            exchange=record.exchange,
        )
        return ResolvedEntity(
            identifier=record.cik,
            name=record.name,
            ticker=record.ticker,
            exchange=record.exchange,
        )

    async def discover_documents(
        self,
        entity: ResolvedEntity,
        *,
        as_of_date: date | None = None,
        forms: frozenset[str] | None = None,
    ) -> tuple[DocumentRef, ...]:
        """List an entity's filings, newest first, filtered at acquisition.

        ``as_of_date`` filters here rather than downstream. A filing accepted after the
        as-of date is never turned into a reference, so no later code path can fetch it by
        forgetting to check.
        """
        response = await self.fetch_submissions(entity.identifier)
        index = response.data

        filings = index.filed_on_or_before(as_of_date) if as_of_date else index.filings
        wanted = forms if forms is not None else PERIODIC_FORMS
        selected = [f for f in filings if f.form in wanted and f.primary_document]

        return tuple(
            filing.to_ref(index.cik, entity_name=index.name or entity.name) for filing in selected
        )

    async def fetch_facts(
        self,
        entity: ResolvedEntity,
        *,
        as_of_date: date | None = None,  # noqa: ARG002 -- see the docstring
    ) -> tuple[RawFact, ...]:
        """Every fact EDGAR holds for the entity, **unfiltered**.

        ``as_of_date`` is part of the :class:`~aer.sources.base.SourceAdapter` interface
        and is deliberately ignored here: point-in-time selection happens in
        :mod:`aer.sources.sec.pit`, on the complete set, so that what was rejected and why
        is recoverable. Filtering at this point would discard the rejected facts before
        anyone could look at them, and "why is this figure missing?" would have no answer.
        """
        response = await self.fetch_company_facts(entity.identifier)
        return response.data.facts

    # -- Internals -------------------------------------------------------------------------

    async def _get(
        self, url: str, *, expected_media_types: frozenset[str] | None = _JSON_TYPES
    ) -> FetchResult:
        if self._requests_made and self._delay > 0:
            await self._sleep(self._delay)
        self._requests_made += 1
        return await self._fetcher.fetch(
            url, provider=self.provider, expected_media_types=expected_media_types
        )

    async def _body(self, result: FetchResult) -> bytes:
        """Read back the bytes the fetcher archived.

        Read from the store by hash rather than carried through in memory: the artefact is
        the authoritative copy, and re-reading it means the parser sees exactly what was
        stored. If those two could differ, the citation verifier would be checking a
        different document from the one the facts came from.
        """
        if not result.ok:
            message = (
                f"EDGAR returned HTTP {result.status_code} for {result.final_url}. The "
                "response was archived, so what it said is recoverable."
            )
            raise ValidationError(
                message,
                context={
                    "url": result.final_url,
                    "status_code": result.status_code,
                    "sha256": result.sha256,
                },
            )
        return await self._store.read(result.sha256)
