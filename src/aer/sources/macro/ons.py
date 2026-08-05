"""UK statistics from the Office for National Statistics, and what a vintage means there.

The ONS publishes UK CPI itself, under the Open Government Licence, through a documented
public API. That makes it both the better licence and the better source than FRED's UK
series, which are OECD-sourced and carry OECD copyright — so for once the rights question and
the quality question have the same answer.

**The ONS is not an archive, and this module does not pretend otherwise.** ALFRED can hand
back a series exactly as it stood on any past date; the ONS timeseries endpoint returns the
current series and tells you when it was last released. There is no ``realtime_start``.

So a UK observation's vintage is **the release date the API reports**, and that is a weaker
claim than a FRED vintage: it says "this is what the ONS was publishing as of that release",
not "this is what it was publishing on your as-of date". :attr:`OnsSeries.is_archived` is
``False`` for exactly that reason, and the persistence layer records it, so a UK figure used
point-in-time carries the limitation rather than borrowing the confidence of a US one.

**A release after the as-of date is refused.** It is the one point-in-time check this source
can support honestly, and it is the one that matters: a CPI series released in September
cannot inform a valuation dated to June, whatever its observations say.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from aer.core.enums import Provider
from aer.errors import ExternalServiceError, ValidationError
from aer.sources.macro.fred import MacroObservation
from aer.sources.macro.series import Frequency, MacroSeries

__all__ = [
    "API_ROOT",
    "LookAheadReleaseError",
    "OnsSeries",
    "observations_for",
    "parse_timeseries",
    "timeseries_url",
]

API_ROOT: Final = "https://api.ons.gov.uk"

# Which block of the response holds observations at each frequency. The ONS returns all three
# in one document, and reading the wrong one gives figures that are real, correctly formed
# and at the wrong frequency -- a monthly CPI index read from the annual block is off by a
# factor nobody would question.
_BLOCK_FOR: Final[dict[Frequency, str]] = {
    Frequency.MONTHLY: "months",
    Frequency.QUARTERLY: "quarters",
    Frequency.ANNUAL: "years",
}

_MONTHS: Final[dict[str, int]] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class LookAheadReleaseError(ValidationError):
    """The series was released after the as-of date."""

    code = "macro_release_after_as_of"


@dataclass(frozen=True, slots=True)
class OnsSeries:
    """An ONS series, its observations, and how far its vintage claim reaches."""

    series_id: str
    dataset: str
    title: str

    # When the ONS published this edition. Stands in for a vintage — see the module
    # docstring on why that is a weaker claim than ALFRED's.
    release_date: date

    observations: tuple[MacroObservation, ...]

    # Always ``False`` for this source. A field rather than a constant because the
    # persistence layer stores it per observation, and a UK figure that silently inherited
    # a US figure's point-in-time guarantee would be the whole problem.
    is_archived: bool = False

    def as_at(self, cutoff: date) -> MacroObservation | None:
        eligible = [o for o in self.observations if o.observed_on <= cutoff]
        return max(eligible, key=lambda o: o.observed_on, default=None)


def timeseries_url(series: MacroSeries) -> str:
    """The documented ONS endpoint for a series within a dataset.

    Built from the registry's identifiers, never from a caller's string — the same rule the
    SEC client follows, and for the same reason: no method here takes a URL, so no fetched
    document can cause one to be requested.

    Raises:
        ValidationError: If the series is not an ONS series, or names no dataset. An ONS
            series code is meaningless without one: ``D7BT`` exists in several datasets.
    """
    if series.provider is not Provider.ONS:
        message = (
            f"{series.key!r} is a {series.provider.value} series, and this module builds ONS URLs."
        )
        raise ValidationError(
            message, context={"series": series.key, "provider": series.provider.value}
        )
    if not series.dataset:
        message = (
            f"The ONS series {series.identifier!r} names no dataset. A series code is not "
            "unique on its own — the same code appears in several datasets meaning different "
            "things — so a URL cannot be built from it."
        )
        raise ValidationError(message, context={"series": series.key})

    return (
        f"{API_ROOT}/timeseries/{series.identifier.lower()}/dataset/{series.dataset.lower()}/data"
    )


def parse_timeseries(payload: bytes, *, series: MacroSeries) -> OnsSeries:
    """Parse an ONS timeseries response.

    Raises:
        ExternalServiceError: If the payload is not an ONS timeseries document, or holds no
            observations at the series' declared frequency.
    """
    document = _load(payload)

    description = document.get("description")
    if not isinstance(description, dict):
        message = (
            "The ONS response has no description block. That is an error page or a different "
            "endpoint, not a timeseries."
        )
        raise ExternalServiceError(
            message, provider=Provider.ONS.value, context={"series": series.identifier}
        )

    release = _release_date(description, series=series)

    block = _BLOCK_FOR.get(series.frequency)
    if block is None:
        message = (
            f"{series.key!r} is declared as {series.frequency.value}, and the ONS timeseries "
            "endpoint publishes months, quarters and years only."
        )
        raise ValidationError(
            message, context={"series": series.key, "frequency": series.frequency.value}
        )

    rows = document.get(block)
    if not isinstance(rows, list) or not rows:
        message = (
            f"The ONS response for {series.identifier} holds no {block}. The series exists at "
            "a different frequency from the one the registry declares, and reading the wrong "
            "block would give figures that are real and at the wrong frequency."
        )
        raise ExternalServiceError(
            message,
            provider=Provider.ONS.value,
            context={"series": series.identifier, "frequency": block},
        )

    observations = [
        parsed
        for row in rows
        if isinstance(row, dict)
        and (parsed := _observation(row, frequency=series.frequency, vintage=release)) is not None
    ]
    if not observations:
        message = (
            f"The ONS response for {series.identifier} holds {len(rows)} {block} and none "
            "could be read. That is a change in the response shape, not an empty series."
        )
        raise ExternalServiceError(
            message, provider=Provider.ONS.value, context={"series": series.identifier}
        )

    return OnsSeries(
        series_id=series.identifier,
        dataset=series.dataset,
        title=str(description.get("title", series.label)),
        release_date=release,
        observations=tuple(sorted(observations, key=lambda o: o.observed_on)),
    )


def observations_for(parsed: OnsSeries, *, as_of: date) -> OnsSeries:
    """The series, having checked its release does not postdate the as-of date.

    The one point-in-time guarantee this source can honestly make. A CPI edition released in
    September cannot inform a valuation dated to June, whatever periods its observations
    cover, and the check is on the *release* rather than on the observations because the
    observations of a September release describe June perfectly well — revised.

    Raises:
        LookAheadReleaseError: If the release postdates ``as_of``.
    """
    if parsed.release_date > as_of:
        message = (
            f"The ONS released {parsed.series_id} on {parsed.release_date.isoformat()}, after "
            f"the as-of date of {as_of.isoformat()}. Its observations cover earlier periods, "
            "but they are the revised figures published later — using them would put "
            "information nobody had into an analysis dated before it existed."
        )
        raise LookAheadReleaseError(
            message,
            context={
                "series": parsed.series_id,
                "released": parsed.release_date.isoformat(),
                "as_of": as_of.isoformat(),
            },
        )
    return parsed


# -- Internals ---------------------------------------------------------------------------


def _load(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = "The ONS response is not JSON."
        raise ExternalServiceError(
            message, provider=Provider.ONS.value, context={"bytes": len(payload)}
        ) from exc

    if not isinstance(document, dict):
        message = "The ONS response is not a JSON object."
        raise ExternalServiceError(
            message, provider=Provider.ONS.value, context={"bytes": len(payload)}
        )
    return document


def _release_date(description: dict[str, Any], *, series: MacroSeries) -> date:
    """When this edition was published.

    Required, not defaulted to today. A release date is what stands in for a vintage here,
    and defaulting it would make an undated response look like a fresh one — which is the
    same silent-fallback failure the FRED adapter refuses.
    """
    raw = description.get("releaseDate")
    if raw is None:
        message = (
            f"The ONS response for {series.identifier} carries no release date. Without one "
            "there is nothing to check the as-of date against, and a figure with no date is "
            "not point-in-time evidence."
        )
        raise ExternalServiceError(
            message, provider=Provider.ONS.value, context={"series": series.identifier}
        )

    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        message = f"The ONS release date {raw!r} is not a date this platform can read."
        raise ExternalServiceError(
            message,
            provider=Provider.ONS.value,
            context={"series": series.identifier, "release_date": str(raw)},
        ) from exc


def _observation(
    row: dict[str, Any], *, frequency: Frequency, vintage: date
) -> MacroObservation | None:
    raw = row.get("value")
    if raw is None or str(raw).strip() == "":
        return None

    observed_on = _period_start(row, frequency=frequency)
    if observed_on is None:
        return None

    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None

    return MacroObservation(observed_on=observed_on, vintage=vintage, value=value)


def _period_start(row: dict[str, Any], *, frequency: Frequency) -> date | None:
    """The first day of the period a row describes.

    The ONS writes a period as a year plus a name — ``{"year": "2024", "month": "June"}`` —
    rather than as a date. Normalised to the period's first day so that observations from
    different frequencies sort together and compare against an as-of date without a second
    calendar rule at every call site.
    """
    try:
        year = int(str(row["year"]))
    except (KeyError, ValueError):
        return None

    if frequency is Frequency.ANNUAL:
        return date(year, 1, 1)

    if frequency is Frequency.QUARTERLY:
        quarter = str(row.get("quarter", "")).strip().upper()
        if quarter not in {"Q1", "Q2", "Q3", "Q4"}:
            return None
        return date(year, (int(quarter[1]) - 1) * 3 + 1, 1)

    month = _MONTHS.get(str(row.get("month", "")).strip().lower())
    if month is None:
        return None
    return date(year, month, 1)
