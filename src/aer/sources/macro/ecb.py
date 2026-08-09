"""Euro foreign-exchange reference rates from the ECB Data Portal.

**This exists because the Bank of England's does not.** ADR 0026 determined that the Bank
publishes a CSV download route for programmatic use and disallows that same route in its
own ``robots.txt``, so `aer.calc.fx` shipped complete and was never given a source: every
rate had to be supplied by hand. The ECB has no such conflict — a documented machine-readable
API, no `robots.txt` restriction on it, and a licence that expressly permits re-use with
attribution. See ADR 0045.

**Every rate here has the euro on one side, and that is a property of the source rather
than a limitation of this module.** The ECB publishes the daily reference rates *of the
euro*: one figure per currency, quoted as units of that currency per euro. A sterling
figure against a dollar one therefore does not exist here and never will, so a GBP/USD rate
is a **cross-rate** — a division of two published observations rather than a published
observation. :func:`aer.calc.fx.cross` is where that division happens, and it happens as a
recorded calculation for exactly the reason every other number does: a reader asking "where
did 0.7834 come from?" gets two source documents and a formula rather than a figure that
looks like it was published somewhere.

**The rates are daily, and they are not a market.** They are reference rates, published
around 16:00 CET each working day, and the ECB says plainly that they are "not intended to
be used in any market transaction". For converting a reported balance sheet — which is what
this platform does with them — that is exactly right, and it is what the accounting
standards contemplate. For anything execution-shaped they would be wrong, which is why no
part of this module offers a bid, an ask or an intraday value.

**No method takes a URL.** Every one takes a currency code that must be in
:data:`REFERENCE_CURRENCIES`, and the URL is built here. That is the same rule the SEC and
macro clients follow, and it carries the "no agent-callable tool takes a URL" property up
from the fetch layer.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from aer.calc.fx import FxRate
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import Provider
from aer.errors import ExternalServiceError, ValidationError

__all__ = [
    "API_ROOT",
    "BASE_CURRENCY",
    "ECB_LICENCE",
    "REFERENCE_CURRENCIES",
    "ReferenceRates",
    "as_fx_rates",
    "parse_reference_rates",
    "reference_rate_url",
]

API_ROOT: Final = "https://data-api.ecb.europa.eu/service/data"

# The dataflow and the shape of a series key within it. `D` is daily, `SP00` is the spot
# reference series and `A` is the average/standard suffix; the currency varies and the
# denominator is always the euro.
_DATAFLOW: Final = "EXR"
_FREQUENCY: Final = "D"
_EXR_TYPE: Final = "SP00"
_EXR_SUFFIX: Final = "A"

# Every ECB reference rate is quoted against the euro, so this is a property of the source
# and not a configurable choice.
BASE_CURRENCY: Final = "EUR"

ECB_LICENCE: Final = (
    "European Central Bank. Free to use, including commercially, with the ECB credited as "
    "the source; see the ECB's copyright and privacy statement. Euro foreign-exchange "
    "reference rates are indicative and not intended for market transactions."
)

# Which currencies this platform will ask for. A registry rather than a pass-through,
# because it is what stops a caller — or a string that reached one — from constructing a
# request for a series whose rights and shape nobody has established. The ECB publishes
# around thirty; these are the ones a UK or US equity mandate actually reports in.
REFERENCE_CURRENCIES: Final[frozenset[str]] = frozenset(
    {"USD", "GBP", "JPY", "CHF", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF"}
)

# The two SDMX-CSV columns this parser needs. Everything else in the response — the series
# key, the dimensions, the observation status — is ignored rather than validated, because
# a parser that insists on the whole shape breaks on a column the publisher adds.
_PERIOD_COLUMN: Final = "TIME_PERIOD"
_VALUE_COLUMN: Final = "OBS_VALUE"


@dataclass(frozen=True, slots=True)
class ReferenceRates:
    """One currency's reference rates against the euro, in date order.

    ``currency`` is the *quote*: a ``USD`` series holds dollars per euro.
    """

    currency: str
    observations: tuple[tuple[date, Decimal], ...]

    @property
    def latest(self) -> tuple[date, Decimal] | None:
        return self.observations[-1] if self.observations else None


def reference_rate_url(
    currency: str, *, start_date: date | None = None, end_date: date | None = None
) -> str:
    """The ECB Data Portal URL for one currency's daily reference rates.

    Args:
        currency: The quote currency, which must be in :data:`REFERENCE_CURRENCIES`.
        end_date: Should be the run's as-of date under point-in-time rules, so the API is
            not even asked for later observations. The bound is **still** applied after
            parsing — this is a saving and a courtesy, not the control.

    Raises:
        ValidationError: If the currency is not allowlisted, if it is the euro itself, or
            if the date range runs backwards.
    """
    code = currency.strip().upper()
    if code == BASE_CURRENCY:
        message = (
            "There is no EUR/EUR reference rate. Every series here is quoted against the "
            "euro, so asking for the euro is asking for the number one."
        )
        raise ValidationError(message, context={"currency": currency})
    if code not in REFERENCE_CURRENCIES:
        message = (
            f"{currency!r} is not a currency this platform retrieves. The allowlist is "
            f"{', '.join(sorted(REFERENCE_CURRENCIES))} — a currency absent from it has had "
            "no rights or shape determination made, and a URL built from an arbitrary code "
            "is the thing the registry exists to prevent."
        )
        raise ValidationError(message, context={"currency": currency})

    if start_date is not None and end_date is not None and start_date > end_date:
        message = (
            f"The rate range runs backwards: {start_date.isoformat()} is after "
            f"{end_date.isoformat()}."
        )
        raise ValidationError(
            message,
            context={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )

    key = f"{_FREQUENCY}.{code}.{BASE_CURRENCY}.{_EXR_TYPE}.{_EXR_SUFFIX}"
    query = ["format=csvdata"]
    if start_date is not None:
        query.append(f"startPeriod={start_date.isoformat()}")
    if end_date is not None:
        query.append(f"endPeriod={end_date.isoformat()}")
    return f"{API_ROOT}/{_DATAFLOW}/{key}?{'&'.join(query)}"


def parse_reference_rates(payload: bytes, *, currency: str) -> ReferenceRates:
    """Parse an SDMX-CSV reference-rate response.

    **Read by column name, and strict about exactly two of them.** SDMX-CSV carries a dozen
    dimension columns whose presence and order vary between the portal's versions, so
    positional parsing would be a silent wrong-column bug waiting for a release. The two
    that carry meaning are required by name and everything else is ignored; if they are
    absent this raises and names the columns it did find, which is the difference between
    "the API changed" and "the series is empty".

    Rows with no value are skipped rather than defaulted. The ECB publishes a row for every
    calendar day of the series, and the ones on weekends and TARGET holidays are genuinely
    blank — a zero there would be a rate, and a catastrophic one.

    Raises:
        ExternalServiceError: If the payload is not the shape of an SDMX-CSV response.
    """
    text = _decode(payload, currency=currency)
    reader = csv.DictReader(io.StringIO(text))
    columns = reader.fieldnames or []

    if _PERIOD_COLUMN not in columns or _VALUE_COLUMN not in columns:
        message = (
            f"The ECB response has no {_PERIOD_COLUMN}/{_VALUE_COLUMN} columns, so it is not "
            "an SDMX-CSV observation set. That is an error page, a changed format or a "
            "different endpoint, and none of them is an empty series."
        )
        raise ExternalServiceError(
            message,
            provider=Provider.ECB.value,
            retryable=False,
            context={"currency": currency, "columns": ",".join(columns[:12])},
        )

    observations: list[tuple[date, Decimal]] = []
    for row in reader:
        observed = _parse_date(row.get(_PERIOD_COLUMN))
        value = _parse_decimal(row.get(_VALUE_COLUMN))
        if observed is None or value is None or value <= 0:
            continue
        observations.append((observed, value))

    observations.sort()
    return ReferenceRates(currency=currency.strip().upper(), observations=tuple(observations))


def as_fx_rates(rates: ReferenceRates, *, source: SourceRef) -> tuple[FxRate, ...]:
    """The parsed observations as :class:`~aer.calc.fx.FxRate` values.

    Each carries ``base=EUR`` and ``quote=<currency>``, which is the direction the ECB
    publishes: a ``USD`` observation of ``1.0850`` means one euro buys 1.0850 dollars, so
    the rate converts euros into dollars and its unit is ``USD/EUR``.

    Getting that backwards is the single most likely error in this whole path, and it is
    the one :class:`FxRate` was built to make impossible — the unit is checked against the
    pair on construction, so an inverted rate raises here rather than producing a balance
    sheet wrong by the square of the rate.
    """
    unit = Unit.currency(rates.currency) / Unit.currency(BASE_CURRENCY)
    return tuple(
        FxRate(
            base=BASE_CURRENCY,
            quote=rates.currency,
            rate=Quantity.of(value, unit, source=source),
            observed_on=observed,
        )
        for observed, value in rates.observations
    )


def _decode(payload: bytes, *, currency: str) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        message = f"The ECB reference-rate response for {currency} is not UTF-8 text."
        raise ExternalServiceError(
            message,
            provider=Provider.ECB.value,
            retryable=True,
            context={"currency": currency, "bytes": len(payload)},
        ) from exc


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None
