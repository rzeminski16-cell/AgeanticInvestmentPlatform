"""Ticker and exchange to CIK.

The CIK — Central Index Key — is EDGAR's identifier for a filer, and every other endpoint
is keyed by it. Resolution is therefore the first thing any US research run does, and the
first place a mistake becomes cheap to catch.

**Two files, two shapes.** The SEC publishes ``company_tickers.json`` (ticker and name)
and ``company_tickers_exchange.json`` (ticker, name and exchange). The first is an object
keyed by a meaningless row number; the second is a columnar table with a ``fields`` header.
Both are parsed here, because the shape is unambiguous from the payload and a caller
holding one should not have to know which.

**Zero-padding is not cosmetic.** ``data.sec.gov/submissions/CIK789019.json`` is a 404;
``CIK0000789019.json`` is Microsoft. The API states the CIK as a bare integer and requires
it padded to ten digits on the way back in, so the padding is applied here, once, at the
boundary — rather than at each of the three call sites that would each get it wrong.

**Ambiguity is refused, not resolved.** A ticker can be listed on more than one exchange,
and the same ticker means different companies in different places. Where the requested
exchange does not disambiguate, this module raises with every candidate in the message
rather than picking the first — guessing an identity would make every downstream fact
about the wrong company, silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from aer.errors import ExternalServiceError, ValidationError

__all__ = [
    "CIK_LENGTH",
    "TickerRecord",
    "format_cik",
    "normalise_exchange",
    "parse_company_tickers",
    "resolve_ticker",
]

CIK_LENGTH: Final = 10

# EDGAR's exchange labels, mapped onto the identifiers this platform's universe rules use.
# EDGAR writes "Nasdaq"; the request form offers "NASDAQ"; neither is going to change to
# suit the other, so the translation lives in one table.
_EXCHANGE_ALIASES: Final[dict[str, str]] = {
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "NYSEAMERICAN": "NYSE_AMERICAN",
    "NYSE AMERICAN": "NYSE_AMERICAN",
    "NYSEARCA": "NYSE_ARCA",
    "NYSE ARCA": "NYSE_ARCA",
    "CBOE": "CBOE",
    "BATS": "CBOE",
    "OTC": "OTC",
}


@dataclass(frozen=True, slots=True)
class TickerRecord:
    """One row of the SEC's ticker table."""

    cik: str
    ticker: str
    name: str

    # NULL when the payload was ``company_tickers.json``, which does not carry it. Absent
    # is different from unknown-exchange, and a caller filtering on exchange needs to be
    # able to tell that the file simply did not say.
    exchange: str | None = None


def format_cik(value: int | str) -> str:
    """Zero-pad a CIK to the ten digits every EDGAR URL requires.

    Raises:
        ValidationError: If the value is not a CIK at all.
    """
    raw = str(value).strip().upper().removeprefix("CIK")

    # Checked before the leading zeros are stripped, not after. Stripping first turns ""
    # and "0000000000" into the same empty string, and a fallback that substitutes "0"
    # for it would quietly resolve an empty ticker file cell to CIK 0 -- a URL that is
    # not a 404 and not the company anyone asked for.
    if not raw or not raw.isdigit() or len(raw.lstrip("0")) > CIK_LENGTH:
        message = f"{value!r} is not a CIK."
        raise ValidationError(message, context={"cik": str(value)})

    significant = raw.lstrip("0")
    if not significant:
        message = f"{value!r} is not a CIK: zero is not a filer."
        raise ValidationError(message, context={"cik": str(value)})

    return significant.zfill(CIK_LENGTH)


def normalise_exchange(value: str | None) -> str | None:
    """Translate an EDGAR exchange label to this platform's identifier.

    An unrecognised label is upper-cased and returned rather than refused: EDGAR can add
    a venue at any time, and refusing to parse the whole file because one row names a new
    exchange would be a poor trade.
    """
    if value is None:
        return None
    key = value.strip().upper()
    if not key:
        return None
    return _EXCHANGE_ALIASES.get(key, key.replace(" ", "_"))


def parse_company_tickers(payload: bytes) -> tuple[TickerRecord, ...]:
    """Parse either ticker file into records.

    Raises:
        ExternalServiceError: If the payload is not JSON, or is JSON in neither shape.
    """
    document = _load(payload)

    if isinstance(document, dict) and "fields" in document and "data" in document:
        return _parse_columnar(document)
    if isinstance(document, dict):
        return _parse_row_keyed(document)

    message = (
        "The SEC ticker file was neither the row-keyed nor the columnar shape. The "
        "endpoint has changed, or the response is an error page."
    )
    raise ExternalServiceError(
        message, provider="sec_edgar", retryable=False, context={"type": type(document).__name__}
    )


def _load(payload: bytes) -> Any:
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = f"The SEC ticker file is not valid JSON ({exc})."
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=True, context={"bytes": len(payload)}
        ) from exc


def _parse_columnar(document: dict[str, Any]) -> tuple[TickerRecord, ...]:
    """``{"fields": ["cik", "name", "ticker", "exchange"], "data": [[...], ...]}``.

    The column order is read from the header rather than assumed. Assuming it would make
    a future reordering swap every company's name and ticker without any error — the kind
    of failure that is only noticed once it is in a report.
    """
    fields = [str(name).strip().lower() for name in document.get("fields", [])]
    missing = [column for column in ("cik", "ticker") if column not in fields]
    if missing:
        message = (
            f"The SEC ticker file has no {' and no '.join(missing)} column. Without both, "
            "a row cannot be turned into an identity."
        )
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=False, context={"fields": fields}
        )

    cik_at = fields.index("cik")
    ticker_at = fields.index("ticker")
    name_at = fields.index("name") if "name" in fields else None
    exchange_at = fields.index("exchange") if "exchange" in fields else None

    records: list[TickerRecord] = []
    for row in document.get("data", []):
        if not isinstance(row, list) or len(row) != len(fields):
            # A row that does not match the header is not recoverable by guessing which
            # column is missing. Skipping it loses one company; guessing would attach one
            # company's CIK to another's ticker.
            continue
        ticker = _clean(row[ticker_at])
        if not ticker:
            continue
        records.append(
            TickerRecord(
                cik=format_cik(row[cik_at]),
                ticker=ticker.upper(),
                name=_clean(row[name_at]) if name_at is not None else "",
                exchange=normalise_exchange(_clean(row[exchange_at]) or None)
                if exchange_at is not None
                else None,
            )
        )
    return tuple(records)


def _parse_row_keyed(document: dict[str, Any]) -> tuple[TickerRecord, ...]:
    """``{"0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}, ...}``."""
    records: list[TickerRecord] = []
    for entry in document.values():
        if not isinstance(entry, dict):
            continue
        ticker = _clean(entry.get("ticker"))
        cik = entry.get("cik_str", entry.get("cik"))
        if not ticker or cik is None:
            continue
        records.append(
            TickerRecord(
                cik=format_cik(cik),
                ticker=ticker.upper(),
                name=_clean(entry.get("title") or entry.get("name")),
            )
        )
    return tuple(records)


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def resolve_ticker(
    records: tuple[TickerRecord, ...], ticker: str, *, exchange: str | None = None
) -> TickerRecord:
    """Find the one record matching a ticker, and optionally an exchange.

    Args:
        records: Every row of the ticker file.
        ticker: The symbol to resolve, in any case.
        exchange: Narrows the search when the file carries exchanges. Ignored for records
            that have none, so a match from ``company_tickers.json`` still succeeds.

    Raises:
        ValidationError: If nothing matches, or if more than one does and the exchange did
            not separate them.
    """
    wanted = ticker.strip().upper()
    matches = [record for record in records if record.ticker == wanted]

    if not matches:
        message = (
            f"{wanted} is not in the SEC's ticker file. EDGAR covers companies that file "
            "with the SEC, so a UK-listed company with no US listing will not appear — "
            "and neither will a mistyped symbol."
        )
        raise ValidationError(message, context={"ticker": wanted, "exchange": exchange})

    if exchange is not None:
        wanted_exchange = normalise_exchange(exchange)
        narrowed = [
            record
            for record in matches
            if record.exchange is None or record.exchange == wanted_exchange
        ]
        if not narrowed:
            listed = sorted({record.exchange or "unknown" for record in matches})
            message = (
                f"{wanted} is in the SEC's ticker file, but on {' and '.join(listed)} "
                f"rather than {wanted_exchange}. Check the exchange on the request."
            )
            raise ValidationError(
                message,
                context={"ticker": wanted, "requested": wanted_exchange, "listed": listed},
            )
        matches = narrowed

    if len(matches) > 1:
        candidates = sorted(f"{r.name} (CIK {r.cik}, {r.exchange or 'unknown'})" for r in matches)
        message = (
            f"{wanted} matches {len(matches)} companies: {'; '.join(candidates)}. Naming "
            "the exchange would separate them. Guessing would attach every figure in the "
            "report to whichever company happened to be first."
        )
        raise ValidationError(
            message, context={"ticker": wanted, "candidates": [r.cik for r in matches]}
        )

    return matches[0]
