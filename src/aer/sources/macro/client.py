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

import re
from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from aer.core.enums import Provider, SourceTier
from aer.errors import ValidationError
from aer.fetch.client import FetchResult, SafeFetcher
from aer.sources.macro import fred, ons
from aer.sources.macro.series import MacroSeries, series_for
from aer.storage.protocol import ArtefactStore

__all__ = ["MacroClient", "MacroResponse", "redacted"]

_log = structlog.get_logger("aer.sources.macro")

# What replaces the key in anything recorded. Matched on the parameter rather than on the
# key's value, so a key that has been rotated is still hidden in an old log line and a key
# that happens to look like ordinary text is still hidden in a new one.
_API_KEY_PARAM: Final = re.compile(r"(api_key=)[^&]*")
_REDACTED: Final = "api_key=REDACTED"

# Both providers answer JSON. Declared so a proxy error page or an HTML maintenance
# notice is refused by the fetcher rather than reaching a parser that would call it a
# malformed series.
_JSON_TYPES: Final[frozenset[str]] = frozenset({"application/json"})


def redacted(url: str) -> str:
    """The URL with its API key removed, for logging and for the stored source document.

    FRED takes the key as a query parameter. Without this it would reach every log line, the
    artefact's recorded URL, and the sources appendix of a published report — three places a
    credential must never be, all reached by doing nothing wrong.
    """
    return _API_KEY_PARAM.sub(_REDACTED, url)


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


class MacroClient:
    """Retrieves macro series at a vintage. Never takes a URL."""

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
