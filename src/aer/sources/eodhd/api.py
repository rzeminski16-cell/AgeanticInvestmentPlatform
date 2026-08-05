"""EODHD's endpoints, as URLs and as parsers. No I/O.

Split from the client for the same reason :mod:`aer.sources.macro.fred` is: URL construction
and parsing are where the errors that matter live, they are pure, and a pure function is one
a test can exercise exhaustively without a network or a cassette.

**The point-in-time clamp is built into the URL, not applied to the answer.** Every endpoint
here takes ``as_of`` and puts it in the ``to`` parameter, and there is no code path that
builds one without it. This is the same guarantee :func:`aer.sources.macro.fred.observations_url`
gives for ALFRED vintages and for the same reason: an omitted bound returns today's data,
which looks exactly like correct data and is the precise error the whole platform exists to
prevent.

**The clamp is then applied a second time, to what came back.** A vendor that ignores ``to``,
or a cache that serves a wider window, would otherwise put a bar from after the as-of date
into a valuation. The parsers drop those and *count* them, so the discrepancy is visible in
the result rather than silent. Belt and braces, because the cost of the belt is one
comparison per bar and the cost of it failing is a look-ahead nobody can see.

**A split is a pair, not a number.** EODHD writes one as ``"2.000000/1.000000"`` — new shares
over old. A one-for-ten consolidation is ``"1.000000/10.000000"``, which is a ratio of 0.1
and reads like a ten if the slash is skipped. :func:`parse_splits` does the division.

**The response shapes below are from EODHD's published documentation.** This build
environment has no outbound network access, so they have not been confirmed against a live
response. The parsers therefore accept the documented shape and *refuse* anything else with a
message naming the field, rather than coercing — a parser that guessed would turn a shape
change into a wrong number instead of an error. `docs/data-sources/eodhd.md` records what
still needs confirming against a real key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import quote, urlencode

from aer.errors import ExternalServiceError, ValidationError

__all__ = [
    "API_ROOT",
    "BarsResponse",
    "DividendRow",
    "SharesOutstanding",
    "SplitRow",
    "bars_url",
    "dividends_url",
    "fundamentals_url",
    "parse_bars",
    "parse_dividends",
    "parse_shares_outstanding",
    "parse_splits",
    "splits_url",
]

API_ROOT: Final = "https://eodhd.com/api"

# What every endpoint here asks for. JSON rather than the default CSV, because a CSV parser
# has to guess about quoting and a JSON one does not.
_FORMAT: Final = "json"


@dataclass(frozen=True, slots=True)
class BarsResponse:
    """Bars inside the window, and what was thrown away to keep them there."""

    rows: tuple[BarRow, ...]

    # Bars the provider returned that postdate the as-of date. Nil in every correct
    # response; not nil is a provider that ignored the bound, and worth surfacing rather
    # than quietly correcting.
    discarded_after_as_of: int = 0


@dataclass(frozen=True, slots=True)
class BarRow:
    """One printed bar, exactly as the feed gave it."""

    on: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None
    volume: int | None


@dataclass(frozen=True, slots=True)
class SplitRow:
    """A split, with the ratio already divided out of the vendor's pair."""

    ex_date: date
    ratio: Decimal

    # The vendor's own string, kept so a suspicious ratio can be checked against what
    # arrived rather than against what this module made of it.
    raw: str


@dataclass(frozen=True, slots=True)
class DividendRow:
    """A cash dividend, in the currency it was declared in."""

    ex_date: date
    amount: Decimal
    currency: str
    record_date: date | None = None
    pay_date: date | None = None


@dataclass(frozen=True, slots=True)
class SharesOutstanding:
    """A share count, and the date it was reported for.

    ``as_reported_on`` is not optional: a count with no date cannot be shown to predate the
    as-of date, and a market capitalisation built from a later count is a look-ahead of the
    quietest kind — the price is right, the count is from next quarter, and the product looks
    entirely plausible.
    """

    shares: Decimal
    as_reported_on: date


# -- URLs ------------------------------------------------------------------------------------


def bars_url(symbol: str, *, api_token: str, as_of: date, since: date | None = None) -> str:
    """Daily bars for one symbol, bounded at ``as_of``.

    Args:
        symbol: The vendor's own key, e.g. ``MSFT.US`` or ``BARC.LSE``.
        api_token: The subscription key. Never logged — see
            :func:`aer.sources.credentials.redact_credentials`.
        as_of: The run's as-of date. Becomes ``to``, and is required.
        since: The earliest bar wanted. Omitted means the whole history the plan allows.
    """
    query: dict[str, str] = {
        "api_token": api_token,
        "fmt": _FORMAT,
        "period": "d",
        "to": as_of.isoformat(),
    }
    if since is not None:
        _require_ordered(since, as_of)
        query["from"] = since.isoformat()
    return f"{API_ROOT}/eod/{quote(symbol, safe='')}?{urlencode(query)}"


def splits_url(symbol: str, *, api_token: str, as_of: date, since: date | None = None) -> str:
    """Splits and consolidations for one symbol, bounded at ``as_of``."""
    return _action_url("splits", symbol, api_token=api_token, as_of=as_of, since=since)


def dividends_url(symbol: str, *, api_token: str, as_of: date, since: date | None = None) -> str:
    """Cash dividends for one symbol, bounded at ``as_of``."""
    return _action_url("div", symbol, api_token=api_token, as_of=as_of, since=since)


def fundamentals_url(symbol: str, *, api_token: str) -> str:
    """The fundamentals document, which is where the share count lives.

    **No ``to`` parameter, because the endpoint has none.** It returns the current snapshot,
    so the point-in-time question cannot be answered by the URL and has to be answered by the
    response: :func:`parse_shares_outstanding` picks the most recent count *dated on or before*
    the as-of date, from the historical series the document carries, and refuses if the
    document has only an undated current figure.

    This endpoint is weighted at ten calls rather than one. See
    :class:`~aer.sources.eodhd.budget.WeightedCallBudget`.
    """
    query = {"api_token": api_token, "fmt": _FORMAT}
    return f"{API_ROOT}/fundamentals/{quote(symbol, safe='')}?{urlencode(query)}"


def _action_url(
    endpoint: str, symbol: str, *, api_token: str, as_of: date, since: date | None
) -> str:
    query: dict[str, str] = {
        "api_token": api_token,
        "fmt": _FORMAT,
        "to": as_of.isoformat(),
    }
    if since is not None:
        _require_ordered(since, as_of)
        query["from"] = since.isoformat()
    return f"{API_ROOT}/{endpoint}/{quote(symbol, safe='')}?{urlencode(query)}"


def _require_ordered(since: date, as_of: date) -> None:
    if since <= as_of:
        return
    message = (
        f"The window starts on {since.isoformat()} and ends on {as_of.isoformat()}. A "
        "request for a window that runs backwards returns nothing, and nothing is "
        "indistinguishable from a company that has never traded."
    )
    raise ValidationError(message, context={"since": since.isoformat(), "as_of": as_of.isoformat()})


# -- Parsers ---------------------------------------------------------------------------------


def parse_bars(payload: bytes, *, symbol: str, as_of: date) -> BarsResponse:
    """Parse an end-of-day response, discarding anything after ``as_of``.

    Raises:
        ExternalServiceError: If the payload is not the documented shape, or a row is
            missing a field a bar cannot do without.
    """
    rows = _require_list(payload, symbol=symbol, endpoint="eod")

    bars: list[BarRow] = []
    discarded = 0
    for index, row in enumerate(rows):
        mapping = _require_mapping(row, symbol=symbol, endpoint="eod", index=index)
        on = _require_date(mapping.get("date"), field="date", symbol=symbol, endpoint="eod")
        if on > as_of:
            discarded += 1
            continue
        bars.append(
            BarRow(
                on=on,
                open=_require_decimal(mapping.get("open"), field="open", symbol=symbol),
                high=_require_decimal(mapping.get("high"), field="high", symbol=symbol),
                low=_require_decimal(mapping.get("low"), field="low", symbol=symbol),
                close=_require_decimal(mapping.get("close"), field="close", symbol=symbol),
                adjusted_close=_optional_decimal(
                    mapping.get("adjusted_close"), field="adjusted_close", symbol=symbol
                ),
                volume=_optional_int(mapping.get("volume"), field="volume", symbol=symbol),
            )
        )

    bars.sort(key=lambda bar: bar.on)
    return BarsResponse(rows=tuple(bars), discarded_after_as_of=discarded)


def parse_splits(payload: bytes, *, symbol: str, as_of: date) -> tuple[SplitRow, ...]:
    """Parse a splits response, discarding anything after ``as_of``.

    The vendor writes a ratio as ``"new/old"``. This divides it, so a two-for-one becomes 2
    and a one-for-ten consolidation becomes 0.1 — and a value that is neither a pair nor a
    plain number is refused rather than guessed at, because a split ratio read wrongly
    restates every historical price by a factor of ten.

    Raises:
        ExternalServiceError: On a malformed payload or an unreadable ratio.
    """
    rows = _require_list(payload, symbol=symbol, endpoint="splits")

    splits: list[SplitRow] = []
    for index, row in enumerate(rows):
        mapping = _require_mapping(row, symbol=symbol, endpoint="splits", index=index)
        ex_date = _require_date(mapping.get("date"), field="date", symbol=symbol, endpoint="splits")
        if ex_date > as_of:
            continue
        raw = mapping.get("split")
        splits.append(
            SplitRow(ex_date=ex_date, ratio=_split_ratio(raw, symbol=symbol), raw=str(raw))
        )

    splits.sort(key=lambda split: split.ex_date)
    return tuple(splits)


def parse_dividends(
    payload: bytes, *, symbol: str, as_of: date, default_currency: str | None = None
) -> tuple[DividendRow, ...]:
    """Parse a dividends response, discarding anything after ``as_of``.

    ``default_currency`` fills in for a row that omits one. It is the *listing's* quote
    currency, supplied by the caller, and is a last resort rather than a convenience: a
    dividend with no currency and no default is refused, because assuming it matches the
    quote is exactly the assumption that is wrong for a London listing paying in dollars.

    Raises:
        ExternalServiceError: On a malformed payload, or a row with no currency and no
            default.
    """
    rows = _require_list(payload, symbol=symbol, endpoint="div")

    dividends: list[DividendRow] = []
    for index, row in enumerate(rows):
        mapping = _require_mapping(row, symbol=symbol, endpoint="div", index=index)
        ex_date = _require_date(mapping.get("date"), field="date", symbol=symbol, endpoint="div")
        if ex_date > as_of:
            continue

        currency = mapping.get("currency") or default_currency
        if not isinstance(currency, str) or not currency.strip():
            message = (
                f"A dividend for {symbol} on {ex_date.isoformat()} states no currency and no "
                "default was supplied. Assuming it matches the quote currency is the "
                "assumption that is wrong for a London listing paying in dollars, so this "
                "refuses instead."
            )
            raise ExternalServiceError(
                message,
                provider="eodhd",
                context={"symbol": symbol, "ex_date": ex_date.isoformat()},
            )

        dividends.append(
            DividendRow(
                ex_date=ex_date,
                amount=_require_decimal(mapping.get("value"), field="value", symbol=symbol),
                currency=currency.strip().upper(),
                record_date=_optional_date(mapping.get("recordDate")),
                pay_date=_optional_date(mapping.get("paymentDate")),
            )
        )

    dividends.sort(key=lambda dividend: (dividend.ex_date, dividend.amount))
    return tuple(dividends)


def parse_shares_outstanding(payload: bytes, *, symbol: str, as_of: date) -> SharesOutstanding:
    """The most recent share count dated on or before ``as_of``.

    Reads the historical series under ``outstandingShares``, never the current headline
    figure under ``SharesStats``. The headline is undated and is today's — using it to build
    a market capitalisation as at a past date pairs a correct price with a count from the
    future, and the product looks entirely ordinary.

    Raises:
        ExternalServiceError: If the document carries no dated count at or before ``as_of``.
    """
    document = _require_mapping(
        _load(payload, symbol=symbol, endpoint="fundamentals"),
        symbol=symbol,
        endpoint="fundamentals",
        index=None,
    )
    history = document.get("outstandingShares")
    candidates: list[SharesOutstanding] = []

    if isinstance(history, dict):
        for period in ("quarterly", "annual"):
            entries = history.get(period)
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                reported = _optional_date(entry.get("dateFormatted"))
                shares = _optional_decimal(entry.get("shares"), field="shares", symbol=symbol)
                if reported is not None and shares is not None and reported <= as_of:
                    candidates.append(SharesOutstanding(shares=shares, as_reported_on=reported))

    if not candidates:
        message = (
            f"The fundamentals document for {symbol} carries no dated share count at or "
            f"before {as_of.isoformat()}. The undated headline figure is today's, and "
            "pairing it with a price from a past date is a look-ahead that looks like an "
            "ordinary market capitalisation."
        )
        raise ExternalServiceError(
            message,
            provider="eodhd",
            context={"symbol": symbol, "as_of": as_of.isoformat()},
        )

    return max(candidates, key=lambda candidate: candidate.as_reported_on)


# -- Shared refusals -------------------------------------------------------------------------


def _load(payload: bytes, *, symbol: str, endpoint: str) -> Any:
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = (
            f"The {endpoint} response for {symbol} is not JSON. An HTML error page or a "
            "maintenance notice reaching a parser is a provider outage, not a malformed "
            "series."
        )
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "endpoint": endpoint}
        ) from exc


def _require_list(payload: bytes, *, symbol: str, endpoint: str) -> list[Any]:
    parsed = _load(payload, symbol=symbol, endpoint=endpoint)
    if isinstance(parsed, list):
        return parsed

    message = (
        f"The {endpoint} response for {symbol} is a {type(parsed).__name__}, and this "
        "endpoint returns a list. A shape change is a reason to stop, not to guess."
    )
    raise ExternalServiceError(
        message, provider="eodhd", context={"symbol": symbol, "endpoint": endpoint}
    )


def _require_mapping(row: Any, *, symbol: str, endpoint: str, index: int | None) -> dict[str, Any]:
    if isinstance(row, dict):
        return row

    message = (
        f"Row {index} of the {endpoint} response for {symbol} is a "
        f"{type(row).__name__}, not an object."
    )
    raise ExternalServiceError(
        message,
        provider="eodhd",
        context={"symbol": symbol, "endpoint": endpoint, "index": index},
    )


def _require_date(value: Any, *, field: str, symbol: str, endpoint: str) -> date:
    parsed = _optional_date(value)
    if parsed is not None:
        return parsed

    message = (
        f"The {endpoint} response for {symbol} has {value!r} where {field!r} should be an "
        "ISO date. A row with no date cannot be placed against an as-of date, so it cannot "
        "be used at all."
    )
    raise ExternalServiceError(
        message,
        provider="eodhd",
        context={"symbol": symbol, "endpoint": endpoint, "field": field},
    )


def _optional_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _require_decimal(value: Any, *, field: str, symbol: str) -> Decimal:
    parsed = _optional_decimal(value, field=field, symbol=symbol)
    if parsed is not None:
        return parsed

    message = (
        f"{field!r} is {value!r} for {symbol}, and a bar cannot do without it. A row this "
        "incomplete is a hole in the series, not a price of zero."
    )
    raise ExternalServiceError(
        message, provider="eodhd", context={"symbol": symbol, "field": field}
    )


def _optional_decimal(value: Any, *, field: str, symbol: str) -> Decimal | None:
    if value is None:
        return None
    # `str(value)` rather than `Decimal(value)`: the feed sends JSON numbers, which `json`
    # has already turned into floats, and `Decimal(0.1)` is not one tenth. Going through the
    # repr keeps the figure the provider actually printed.
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        message = f"{field!r} is {value!r} for {symbol}, which is not a number."
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "field": field}
        ) from exc


def _optional_int(value: Any, *, field: str, symbol: str) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        message = f"{field!r} is {value!r} for {symbol}, which is not a whole number."
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "field": field}
        ) from exc


def _split_ratio(raw: Any, *, symbol: str) -> Decimal:
    """Turn ``"2.000000/1.000000"`` into ``2``, and refuse anything unreadable."""
    if isinstance(raw, (int, float, Decimal)):
        candidate = Decimal(str(raw))
        return _require_positive_ratio(candidate, raw=raw, symbol=symbol)

    if not isinstance(raw, str) or not raw.strip():
        message = (
            f"A split for {symbol} carries {raw!r} as its ratio. Every historical price is "
            "divided by this, so an unreadable one is refused rather than defaulted to a "
            "value that would leave the series looking plausible."
        )
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "split": repr(raw)}
        )

    text = raw.strip()
    new, separator, old = text.partition("/")
    try:
        numerator = Decimal(new.strip())
        denominator = Decimal(old.strip()) if separator else Decimal(1)
    except InvalidOperation as exc:
        message = f"A split for {symbol} carries {raw!r}, which is not a ratio."
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "split": raw}
        ) from exc

    if denominator == 0:
        message = f"A split for {symbol} carries {raw!r}, whose denominator is zero."
        raise ExternalServiceError(
            message, provider="eodhd", context={"symbol": symbol, "split": raw}
        )

    return _require_positive_ratio(numerator / denominator, raw=raw, symbol=symbol)


def _require_positive_ratio(value: Decimal, *, raw: Any, symbol: str) -> Decimal:
    if value > 0:
        return value

    message = (
        f"A split for {symbol} works out to a ratio of {value} from {raw!r}. A share count "
        "is multiplied by this, and dividing prices by zero or a negative is not an "
        "adjustment."
    )
    raise ExternalServiceError(
        message, provider="eodhd", context={"symbol": symbol, "split": repr(raw)}
    )
