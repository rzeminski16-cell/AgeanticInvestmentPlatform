"""ALFRED: a series as it stood on a date, not as it stands now.

GDP for the first quarter of 2020 was first published at one number, revised a month later,
revised again at the annual revision, and revised again when the chain base moved. A backtest
that discounts a 2020 valuation using today's series is using four years of hindsight, and
the error is invisible because the number looks exactly like a GDP figure.

ALFRED is FRED's archive: the same endpoints with ``realtime_start`` and ``realtime_end``,
which select the vintage. Setting both to the as-of date returns the series **as somebody
could have seen it on that day**.

**The two parameters are not optional here, and that is enforced.** Omitting them makes
ALFRED return today's data, which is precisely the error this module exists to prevent — so
:func:`observations_url` requires a vintage date and there is no code path that builds a URL
without one. The failure would otherwise be silent: the same endpoint, the same shape, a
plausible number, and no way to tell from the response that it came from the wrong day.

**A vintage that does not exist is refused, never rounded.** ALFRED answers a request for a
date before the series existed with an empty observation list rather than an error, and a
caller that treated empty as "nothing happened" would fall through to whatever it does when
data is missing. :func:`parse_observations` raises instead.

Pure parsing and URL construction. The fetching is
:class:`~aer.sources.macro.client.MacroClient`; the network safety is
:class:`~aer.fetch.client.SafeFetcher`, as for every other source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import urlencode

from aer.core.enums import Provider
from aer.errors import ExternalServiceError, ValidationError
from aer.sources.macro.series import MacroSeries

__all__ = [
    "API_ROOT",
    "MISSING_VALUE",
    "MacroObservation",
    "SeriesMetadata",
    "VintageMissingError",
    "VintageSeries",
    "observations_url",
    "parse_observations",
    "parse_series_metadata",
    "series_url",
    "vintage_dates_url",
]

API_ROOT: Final = "https://api.stlouisfed.org/fred"

# What ALFRED writes where a value does not exist for a period. A single full stop, which
# `Decimal(".")` would raise on and `float(".")` would too -- but a naive parser that fell
# back to zero would turn "not published" into "was zero", and a zero CPI reading is a very
# different claim from a missing one.
MISSING_VALUE: Final = "."


class VintageMissingError(ValidationError):
    """The archive holds nothing for this series as at this date.

    Its own class because the caller's response differs from a malformed request: a missing
    vintage usually means the as-of date predates the series, and the fix is a different
    date or a different series rather than a corrected parameter.
    """

    code = "macro_vintage_missing"


@dataclass(frozen=True, slots=True)
class MacroObservation:
    """One observation, as it stood on the vintage date."""

    # The period the figure describes.
    observed_on: date

    # The date the archive was read as at. Two observations of the same period at different
    # vintages are different facts, not a duplicate to be deduplicated.
    vintage: date

    value: Decimal


@dataclass(frozen=True, slots=True)
class VintageSeries:
    """A series at one vintage: its observations, and what was asked for."""

    series_id: str
    vintage: date
    observations: tuple[MacroObservation, ...]

    @property
    def latest(self) -> MacroObservation | None:
        """The most recent observation in this vintage."""
        return max(self.observations, key=lambda o: o.observed_on, default=None)

    def as_at(self, cutoff: date) -> MacroObservation | None:
        """The most recent observation on or before ``cutoff``.

        Distinct from :attr:`latest` because the vintage and the period are different dates:
        a series read at the 30 June vintage may hold observations only to 31 March, and a
        caller asking for "the value at 30 June" wants the March one rather than nothing.
        """
        eligible = [o for o in self.observations if o.observed_on <= cutoff]
        return max(eligible, key=lambda o: o.observed_on, default=None)


@dataclass(frozen=True, slots=True)
class SeriesMetadata:
    """What ALFRED says a series is. Used to check the map against the source."""

    series_id: str
    title: str
    units: str
    frequency: str
    seasonal_adjustment: str


def observations_url(series: MacroSeries, *, vintage: date, api_key: str) -> str:
    """The URL for this series as it stood on ``vintage``.

    ``realtime_start`` and ``realtime_end`` are both set to the vintage date, which is what
    makes this ALFRED rather than FRED. They are required arguments and not defaults: a
    default would be a code path that silently returns today's data, and today's data is the
    entire failure this module exists to prevent.

    Raises:
        ValidationError: If the series is not a FRED series. The registry decides which
            provider a key belongs to, and asking FRED for an ONS series would produce a
            confident 400 from a URL nobody meant to build.
    """
    _require_fred(series)
    query = urlencode(
        {
            "series_id": series.identifier,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": vintage.isoformat(),
            "realtime_end": vintage.isoformat(),
        }
    )
    return f"{API_ROOT}/series/observations?{query}"


def series_url(series: MacroSeries, *, vintage: date, api_key: str) -> str:
    """The metadata URL for this series at a vintage.

    Also vintage-stamped: a series' title, units and seasonal-adjustment basis change over
    time, and reading today's metadata against an archived observation is how a figure ends
    up labelled with units it was never published in.
    """
    _require_fred(series)
    query = urlencode(
        {
            "series_id": series.identifier,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": vintage.isoformat(),
            "realtime_end": vintage.isoformat(),
        }
    )
    return f"{API_ROOT}/series?{query}"


def vintage_dates_url(series: MacroSeries, *, api_key: str) -> str:
    """Every date on which this series was revised.

    Not vintage-stamped, because the question is "when did this change?" rather than "what
    did it say?". Used to tell "this series has no vintage on that date" apart from "this
    series did not change that day", which look identical in an observations response.
    """
    _require_fred(series)
    query = urlencode({"series_id": series.identifier, "api_key": api_key, "file_type": "json"})
    return f"{API_ROOT}/series/vintagedates?{query}"


def parse_observations(payload: bytes, *, series: MacroSeries, vintage: date) -> VintageSeries:
    """Parse an ALFRED observations response.

    Args:
        vintage: What was asked for. Checked against what came back — an archive that
            answered a different vintage from the one requested would be returning data from
            a date nobody chose, and every check downstream would pass.

    Raises:
        ExternalServiceError: If the payload is not an ALFRED observations document.
        VintageMissingError: If the archive holds no observations at this vintage. Refused
            rather than returned empty: a caller treating empty as "nothing to report" would
            fall through to whatever it does for missing data, which is exactly the silent
            fallback this module exists to prevent.
    """
    document = _load(payload)

    rows = document.get("observations")
    if not isinstance(rows, list):
        message = (
            "The FRED response has no observations list. That is an error document or a "
            "different endpoint, not a series."
        )
        raise ExternalServiceError(
            message, provider=Provider.FRED.value, context={"series": series.identifier}
        )

    _refuse_a_different_vintage(document, series=series, vintage=vintage)

    observations: list[MacroObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _observation(row, vintage=vintage)
        if parsed is not None:
            observations.append(parsed)

    if not observations:
        message = (
            f"The archive holds no observations for {series.identifier} as at "
            f"{vintage.isoformat()}. The as-of date probably precedes the series. Refused "
            "rather than falling back to the current series, which would put figures nobody "
            "had on that date into an analysis dated to it."
        )
        raise VintageMissingError(
            message,
            context={"series": series.identifier, "vintage": vintage.isoformat()},
        )

    return VintageSeries(
        series_id=series.identifier,
        vintage=vintage,
        observations=tuple(sorted(observations, key=lambda o: o.observed_on)),
    )


def parse_series_metadata(payload: bytes, *, series: MacroSeries) -> SeriesMetadata:
    """Parse a FRED series metadata response.

    Raises:
        ExternalServiceError: If the payload is not a series document.
        VintageMissingError: If the series did not exist at the requested vintage.
    """
    document = _load(payload)

    rows = document.get("seriess")
    if not isinstance(rows, list):
        message = "The FRED response has no seriess list. That is not a series document."
        raise ExternalServiceError(
            message, provider=Provider.FRED.value, context={"series": series.identifier}
        )

    if not rows:
        message = (
            f"{series.identifier} did not exist at the requested vintage. A series added "
            "after the as-of date cannot describe it."
        )
        raise VintageMissingError(message, context={"series": series.identifier})

    first = rows[0]
    if not isinstance(first, dict):
        message = "The FRED seriess list holds something that is not a series."
        raise ExternalServiceError(
            message, provider=Provider.FRED.value, context={"series": series.identifier}
        )

    return SeriesMetadata(
        series_id=str(first.get("id", series.identifier)),
        title=str(first.get("title", "")),
        units=str(first.get("units", "")),
        frequency=str(first.get("frequency", "")),
        seasonal_adjustment=str(first.get("seasonal_adjustment", "")),
    )


# -- Internals ---------------------------------------------------------------------------


def _require_fred(series: MacroSeries) -> None:
    if series.provider is not Provider.FRED:
        message = (
            f"{series.key!r} is a {series.provider.value} series, and this module builds "
            "FRED URLs. Asking one provider for another's identifier produces a confident "
            "error from a URL nobody meant to build."
        )
        raise ValidationError(
            message, context={"series": series.key, "provider": series.provider.value}
        )


def _load(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = "The FRED response is not JSON."
        raise ExternalServiceError(
            message, provider=Provider.FRED.value, context={"bytes": len(payload)}
        ) from exc

    if not isinstance(document, dict):
        message = "The FRED response is not a JSON object."
        raise ExternalServiceError(
            message, provider=Provider.FRED.value, context={"bytes": len(payload)}
        )
    return document


def _refuse_a_different_vintage(
    document: dict[str, Any], *, series: MacroSeries, vintage: date
) -> None:
    """Check the archive answered the vintage that was asked for.

    Cheap, and it closes the one failure mode that would otherwise be undetectable: a
    response whose figures are real, correctly formed and from the wrong day.
    """
    answered = document.get("realtime_start")
    if answered is None:
        return
    if str(answered) != vintage.isoformat():
        message = (
            f"Asked the archive for {series.identifier} as at {vintage.isoformat()} and it "
            f"answered for {answered}. Every figure in that response is real and from the "
            "wrong day, which nothing downstream could detect."
        )
        raise ExternalServiceError(
            message,
            provider=Provider.FRED.value,
            context={
                "series": series.identifier,
                "requested": vintage.isoformat(),
                "answered": str(answered),
            },
        )


def _observation(row: dict[str, Any], *, vintage: date) -> MacroObservation | None:
    """One observation, or ``None`` for a period the series does not cover.

    A missing value is skipped rather than defaulted. ALFRED writes ``"."`` for a period with
    no figure, and a parser that read that as zero would turn "not published" into "was
    zero" — which for an inflation series is not a gap, it is a claim.
    """
    raw = row.get("value")
    if raw is None or str(raw).strip() == MISSING_VALUE:
        return None

    try:
        observed_on = date.fromisoformat(str(row["date"]))
    except (KeyError, ValueError):
        return None

    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None

    return MacroObservation(observed_on=observed_on, vintage=vintage, value=value)
