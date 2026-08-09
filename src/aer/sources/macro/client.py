"""Fetching macro series, from two providers with very different point-in-time guarantees.

Thin, like every source client here. SSRF guarding, allowlists, rate limits, retries, the
byte cap and archiving all belong to :class:`~aer.fetch.client.SafeFetcher`, and a second
implementation of any of them would be one to keep correct twice.

What is left is provider-specific and worth stating:

**No method takes a URL.** Every one takes a registry *key* and a date. The URL is built from
the registry entry, which is what carries the "no agent-callable tool takes a URL" property
up from the fetch layer — and here it does a second job, because the registry is also the
copyright allowlist. A caller cannot ask for a series whose rights have not been established,
because there is no argument that would express it.

**The API key never appears in a log.** FRED takes it as a query parameter, which means it is
part of the URL, which means it would otherwise reach every log line and every stored
artefact URL that a fetch produces. :func:`redacted` is what the client records instead, and
a test asserts the real key reaches neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from aer.calc.fx import FxRate
from aer.calc.units import SourceRef
from aer.core.enums import Provider, SourceTier
from aer.errors import ValidationError
from aer.fetch.client import FetchResult, SafeFetcher
from aer.fetch.credentials import redact_credentials
from aer.sources.macro import ecb, fred, ons
from aer.sources.macro.series import MacroSeries, series_for
from aer.storage.protocol import ArtefactStore

__all__ = ["MacroClient", "MacroResponse", "ReferenceRateResponse", "redacted"]

_log = structlog.get_logger("aer.sources.macro")

# Both providers answer JSON. Declared so a proxy error page or an HTML maintenance
# notice is refused by the fetcher rather than reaching a parser that would call it a
# malformed series.
_JSON_TYPES: Final[frozenset[str]] = frozenset({"application/json"})

# The ECB Data Portal serves SDMX-CSV. Declared for the same reason as the JSON set above:
# a maintenance page or a portal error would otherwise reach a parser that would report it
# as a series with no observations, which reads as "no rates today" rather than "the
# request failed".
_CSV_TYPES: Final[frozenset[str]] = frozenset({"text/csv", "application/vnd.sdmx.data+csv"})


def redacted(url: str) -> str:
    """The URL with its API key removed, for logging and for the stored source document.

    Kept as a name because callers and tests use it; the implementation moved to
    :mod:`aer.fetch.credentials` once it turned out that redacting here was not enough.
    `SafeFetcher` logs `url` and `final_url` itself on every completed fetch and every retry,
    and those lines went out with the FRED key in them for as long as this function was the
    only defence. The fetch layer redacts now, and this is the same function.
    """
    return redact_credentials(url)


@dataclass(frozen=True, slots=True)
class MacroResponse:
    """A retrieved series, with the fetch that produced it."""

    series: MacroSeries
    vintage: date

    # Observations in period order, each stamped with the vintage they were read at.
    observations: tuple[fred.MacroObservation, ...]

    # Whether the provider genuinely served an archive. `False` for the ONS: see
    # :mod:`aer.sources.macro.ons`.
    is_archived: bool

    fetch: FetchResult

    @property
    def tier(self) -> SourceTier:
        return SourceTier.T3_OFFICIAL_STATS


@dataclass(frozen=True, slots=True)
class ReferenceRateResponse:
    """A currency's euro reference rates, with the fetch that produced them.

    Its own type rather than a :class:`MacroResponse`: that one carries a `MacroSeries` and
    a vintage, and an exchange rate has neither. The ECB Data Portal is not an archive — it
    serves the series as it stands — so there is no vintage to record and pretending
    otherwise would borrow ALFRED's guarantee for a source that does not offer it.
    """

    rates: ecb.ReferenceRates
    as_of: date
    fetch: FetchResult

    @property
    def tier(self) -> SourceTier:
        return SourceTier.T3_OFFICIAL_STATS

    def fx_rates(self, *, source: SourceRef) -> tuple[FxRate, ...]:
        """The observations as `FxRate` values, each quoted as ``<currency>/EUR``."""
        return ecb.as_fx_rates(self.rates, source=source)


class MacroClient:
    """Retrieves macro series at a vintage, and euro reference rates. Never takes a URL."""

    def __init__(
        self, fetcher: SafeFetcher, store: ArtefactStore, *, fred_api_key: str | None = None
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._fred_api_key = fred_api_key

    async def fetch_series(self, key: str, *, as_of: date) -> MacroResponse:
        """One series, as it stood on ``as_of``.

        Args:
            key: A registry key, never an identifier and never a URL. See
                :func:`~aer.sources.macro.series.series_for` — the registry is the copyright
                allowlist as well as the URL source.
            as_of: The run's as-of date. For FRED this is the vintage requested from the
                archive; for the ONS it is the date the release is checked against.

        Raises:
            SeriesRefusedError: If the key is not allowlisted.
            ConfigError: If a FRED series is asked for with no API key configured.
            VintageMissingError: If the archive holds nothing at that vintage.
            LookAheadReleaseError: If an ONS release postdates the as-of date.
        """
        series = series_for(key)

        if series.provider is Provider.FRED:
            return await self._fetch_fred(series, as_of=as_of)
        if series.provider is Provider.ONS:
            return await self._fetch_ons(series, as_of=as_of)

        message = (
            f"{key!r} names provider {series.provider.value}, which this client does not "
            "retrieve. The registry and the client have drifted apart."
        )
        raise ValidationError(message, context={"series": key, "provider": series.provider.value})

    async def _fetch_fred(self, series: MacroSeries, *, as_of: date) -> MacroResponse:
        url = fred.observations_url(series, vintage=as_of, api_key=self._require_key(series))

        result = await self._fetcher.fetch(
            url, provider=Provider.FRED, expected_media_types=_JSON_TYPES
        )
        payload = await self._store.read(result.sha256)
        parsed = fred.parse_observations(payload, series=series, vintage=as_of)

        _log.info(
            "macro.retrieved",
            series=series.key,
            provider=Provider.FRED.value,
            vintage=as_of.isoformat(),
            observations=len(parsed.observations),
            # Redacted, not omitted: the URL is what makes the fetch reproducible, and the
            # key is the only part of it that must not be here.
            url=redacted(url),
        )

        return MacroResponse(
            series=series,
            vintage=as_of,
            observations=parsed.observations,
            is_archived=True,
            fetch=result,
        )

    async def _fetch_ons(self, series: MacroSeries, *, as_of: date) -> MacroResponse:
        url = ons.timeseries_url(series)

        result = await self._fetcher.fetch(
            url, provider=Provider.ONS, expected_media_types=_JSON_TYPES
        )
        payload = await self._store.read(result.sha256)
        parsed = ons.observations_for(ons.parse_timeseries(payload, series=series), as_of=as_of)

        _log.info(
            "macro.retrieved",
            series=series.key,
            provider=Provider.ONS.value,
            release_date=parsed.release_date.isoformat(),
            observations=len(parsed.observations),
            url=url,
        )

        return MacroResponse(
            series=series,
            # The release date, not the as-of date. Stamping an ONS observation with the
            # as-of date would claim the archive was read at that date, which is the one
            # thing this source cannot do.
            vintage=parsed.release_date,
            observations=parsed.observations,
            is_archived=False,
            fetch=result,
        )

    async def fetch_reference_rates(
        self, currency: str, *, as_of: date, start_date: date | None = None
    ) -> ReferenceRateResponse:
        """One currency's daily euro reference rates, up to and including ``as_of``.

        Args:
            currency: The quote currency, which must be in
                :data:`~aer.sources.macro.ecb.REFERENCE_CURRENCIES`. Never a URL, and never
                a code that has not had a determination made — the allowlist is the control.
            as_of: Bounds the request itself, so the portal is not asked for observations
                the run may not use. **The bound is a saving, not the point-in-time check**:
                :func:`aer.calc.fx.select_rate` applies that again over what comes back,
                because a control that lives only in a query parameter is a control that
                disappears the day somebody caches a response.

        Raises:
            ValidationError: If the currency is not allowlisted.
            ExternalServiceError: If the response is not an SDMX-CSV observation set.
        """
        url = ecb.reference_rate_url(currency, start_date=start_date, end_date=as_of)

        result = await self._fetcher.fetch(
            url, provider=Provider.ECB, expected_media_types=_CSV_TYPES
        )
        payload = await self._store.read(result.sha256)
        parsed = ecb.parse_reference_rates(payload, currency=currency)

        _log.info(
            "macro.reference_rates_retrieved",
            currency=parsed.currency,
            provider=Provider.ECB.value,
            as_of=as_of.isoformat(),
            observations=len(parsed.observations),
            url=url,
        )
        return ReferenceRateResponse(rates=parsed, as_of=as_of, fetch=result)

    def _require_key(self, series: MacroSeries) -> str:
        if not self._fred_api_key:
            from aer.errors import ConfigError  # noqa: PLC0415 -- only this path needs it

            message = (
                f"Retrieving {series.key!r} needs a FRED API key and none is configured. Set "
                "AER_FRED_API_KEY; a free key is issued at "
                "https://fredaccount.stlouisfed.org/apikeys."
            )
            raise ConfigError(message, context={"series": series.key})
        return self._fred_api_key
