"""Retrieving licensed market data, with the point-in-time clamp inside the adapter.

Thin, like every source client here. SSRF guarding, allowlists, the request-rate token
bucket, retries, the byte cap and archiving all belong to
:class:`~aer.fetch.client.SafeFetcher`.

What is left is EODHD-specific and worth stating:

**No method takes a URL, and no method takes an optional as-of date.** Every one takes a
symbol and a date, and builds the URL itself with the date in the ``to`` parameter. A caller
cannot express "give me everything you have" — there is no argument for it — which is what
makes the look-ahead guarantee a property of the type signature rather than of everybody
remembering. `docs/phase-3-plan.md` task 29 asks for the clamp to be in the adapter rather
than in the caller, and this is what that means in practice.

**The daily weighted allowance is reserved before every request.** See
:mod:`aer.sources.eodhd.budget`: the fetch layer's token bucket meters requests per second
and cannot see that a fundamentals document costs ten calls where a price series costs one.

**The API key never appears in a log or in a stored URL.** EODHD takes it as a query
parameter, so it is part of the URL, so it would otherwise reach every log line and the
``source_documents.url`` column of every price document this platform ever archives.
:func:`~aer.sources.credentials.redact_credentials` is what is recorded instead.

**A missing subscription fails by name.** Without a key configured, every method here raises
:class:`~aer.errors.ConfigError` naming ``AER_EODHD_API_KEY`` — rather than returning an
empty series, which is indistinguishable from a company that has never traded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.fetch.client import FetchResult, SafeFetcher
from aer.fetch.credentials import redact_credentials
from aer.fetch.policy import DEFAULT_POLICIES
from aer.sources.eodhd import api
from aer.sources.eodhd.budget import EndpointCost, WeightedCallBudget
from aer.storage.protocol import ArtefactStore

__all__ = [
    "ActionsResponse",
    "EodhdClient",
    "PriceResponse",
    "SharesResponse",
]

_log = structlog.get_logger("aer.sources.eodhd")

# EODHD answers JSON when asked to. Declared so a proxy error page or an HTML maintenance
# notice is refused by the fetcher rather than reaching a parser that would call it a
# malformed series.
_JSON_TYPES: Final[frozenset[str]] = frozenset({"application/json"})

_SETTING: Final = "eodhd_api_key"


@dataclass(frozen=True, slots=True)
class PriceResponse:
    """A price series inside the window, with the fetch that produced it."""

    symbol: str
    as_of: date
    bars: tuple[api.BarRow, ...]

    # Rows the provider returned dated after the as-of date, which this adapter dropped.
    # Should always be nil; anything else is the provider ignoring the bound and is worth
    # seeing rather than silently correcting.
    discarded_after_as_of: int

    fetch: FetchResult

    @property
    def tier(self) -> SourceTier:
        return SourceTier.T4_LICENSED_MARKET


@dataclass(frozen=True, slots=True)
class ActionsResponse:
    """Splits and dividends inside the window, each with the fetch that produced it."""

    symbol: str
    as_of: date
    splits: tuple[api.SplitRow, ...]
    dividends: tuple[api.DividendRow, ...]

    splits_fetch: FetchResult
    dividends_fetch: FetchResult

    @property
    def tier(self) -> SourceTier:
        return SourceTier.T4_LICENSED_MARKET


@dataclass(frozen=True, slots=True)
class SharesResponse:
    """A dated share count, with the fetch that produced it."""

    symbol: str
    as_of: date
    shares: api.SharesOutstanding
    fetch: FetchResult

    @property
    def tier(self) -> SourceTier:
        return SourceTier.T4_LICENSED_MARKET


class EodhdClient:
    """Retrieves licensed market data, clamped to an as-of date. Never takes a URL."""

    def __init__(
        self,
        fetcher: SafeFetcher,
        store: ArtefactStore,
        *,
        settings: Settings,
        budget: WeightedCallBudget,
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._settings = settings
        self._budget = budget

    @property
    def provider(self) -> Provider:
        return Provider.EODHD

    @property
    def source_tier(self) -> SourceTier:
        return SourceTier.T4_LICENSED_MARKET

    @property
    def licence_note(self) -> str:
        """What may be done with anything this client returns.

        Recorded on every source document. Under ADR 0030 route 2 the answer is "nothing
        leaves the machine", and the note says so in the words the terms use rather than in
        a summary somebody would have to trust.
        """
        return DEFAULT_POLICIES[Provider.EODHD].licence_note

    async def fetch_bars(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> PriceResponse:
        """Daily bars for one listing, none of them later than ``as_of``.

        Raises:
            ConfigError: If no API key is configured.
            CircuitOpenError: If today's weighted allowance is spent.
            ExternalServiceError: If the response is not the documented shape.
        """
        url = api.bars_url(symbol, api_token=self._token(), as_of=as_of, since=since)
        result, payload = await self._get(url, EndpointCost.EOD)
        parsed = api.parse_bars(payload, symbol=symbol, as_of=as_of)

        if parsed.discarded_after_as_of:
            # Loud, because it means the `to` parameter did not do what it says. The bars
            # were dropped either way; what is being reported is that the first line of
            # defence failed and the second one caught it.
            _log.warning(
                "eodhd.bars.after_as_of_discarded",
                symbol=symbol,
                as_of=as_of.isoformat(),
                discarded=parsed.discarded_after_as_of,
            )

        _log.info(
            "eodhd.bars.retrieved",
            symbol=symbol,
            as_of=as_of.isoformat(),
            bars=len(parsed.rows),
            url=redact_credentials(url),
        )
        return PriceResponse(
            symbol=symbol,
            as_of=as_of,
            bars=parsed.rows,
            discarded_after_as_of=parsed.discarded_after_as_of,
            fetch=result,
        )

    async def fetch_actions(
        self,
        symbol: str,
        *,
        as_of: date,
        since: date | None = None,
        quote_currency: str | None = None,
    ) -> ActionsResponse:
        """Splits and dividends for one listing, none of them later than ``as_of``.

        ``quote_currency`` fills in for a dividend row that states none. Supply the
        *listing's* currency and nothing else: it is the last resort described in
        :func:`aer.sources.eodhd.api.parse_dividends`, not a default.

        Raises:
            ConfigError: If no API key is configured.
            CircuitOpenError: If today's weighted allowance is spent.
            ExternalServiceError: If either response is not the documented shape.
        """
        splits_url = api.splits_url(symbol, api_token=self._token(), as_of=as_of, since=since)
        splits_result, splits_payload = await self._get(splits_url, EndpointCost.SPLITS)
        splits = api.parse_splits(splits_payload, symbol=symbol, as_of=as_of)

        dividends_url = api.dividends_url(symbol, api_token=self._token(), as_of=as_of, since=since)
        dividends_result, dividends_payload = await self._get(dividends_url, EndpointCost.DIVIDENDS)
        dividends = api.parse_dividends(
            dividends_payload, symbol=symbol, as_of=as_of, default_currency=quote_currency
        )

        _log.info(
            "eodhd.actions.retrieved",
            symbol=symbol,
            as_of=as_of.isoformat(),
            splits=len(splits),
            dividends=len(dividends),
            url=redact_credentials(splits_url),
        )
        return ActionsResponse(
            symbol=symbol,
            as_of=as_of,
            splits=splits,
            dividends=dividends,
            splits_fetch=splits_result,
            dividends_fetch=dividends_result,
        )

    async def fetch_shares_outstanding(self, symbol: str, *, as_of: date) -> SharesResponse:
        """The most recent dated share count at or before ``as_of``.

        **The expensive one.** The fundamentals document is weighted at ten calls and is
        several megabytes; it is fetched for the share count and nothing else, and a caller
        that needs only a price should not be calling it.

        Raises:
            ConfigError: If no API key is configured.
            CircuitOpenError: If today's weighted allowance is spent.
            ExternalServiceError: If the document carries no dated count at or before
                ``as_of``.
        """
        url = api.fundamentals_url(symbol, api_token=self._token())
        result, payload = await self._get(url, EndpointCost.FUNDAMENTALS)
        shares = api.parse_shares_outstanding(payload, symbol=symbol, as_of=as_of)

        _log.info(
            "eodhd.shares.retrieved",
            symbol=symbol,
            as_of=as_of.isoformat(),
            as_reported_on=shares.as_reported_on.isoformat(),
            url=redact_credentials(url),
        )
        return SharesResponse(symbol=symbol, as_of=as_of, shares=shares, fetch=result)

    # -- Internals -----------------------------------------------------------------------

    def _token(self) -> str:
        """The subscription key, or a refusal naming the setting.

        Asked for at the point of use rather than at construction, so a run that never
        touches market data works on a machine with no EODHD subscription — and one that
        does touch it fails with the environment variable to set rather than with an empty
        series.
        """
        return self._settings.require_secret(_SETTING)

    async def _get(self, url: str, endpoint: EndpointCost) -> tuple[FetchResult, bytes]:
        """Reserve the allowance, fetch, reconcile against the provider's own count."""
        await self._budget.reserve(endpoint)

        result = await self._fetcher.fetch(
            url, provider=Provider.EODHD, expected_media_types=_JSON_TYPES
        )

        # The provider's header is authoritative; the local ledger is a model that drifts.
        # Reconciled after every response rather than only on failure, because a ledger
        # corrected only when something has already gone wrong is a ledger that reports the
        # problem after it has happened.
        await self._budget.reconcile(result.headers)

        # `result.url` is already redacted: `SafeFetcher` does it for every provider, so
        # nothing here has to remember to. The `redact_credentials` calls in the logging
        # above are for the URLs this module built and holds itself, which the fetcher never
        # saw a redacted copy of.

        return result, await self._store.read(result.sha256)
