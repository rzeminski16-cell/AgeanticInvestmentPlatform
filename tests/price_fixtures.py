"""Builders for a vendor price series, shared by every test that stores bars.

Here rather than in one test module because three of them want the same three helpers, and
a second copy of `fetch_result` is how a stand-in drifts from the thing it stands in for.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from aer.errors import ExternalServiceError
from aer.fetch.client import FetchResult
from aer.sources.eodhd import api
from aer.sources.eodhd.client import PriceResponse

__all__ = ["AS_OF", "StubPrices", "bars_response", "fetch_result", "row"]

AS_OF = date(2024, 6, 28)


def fetch_result() -> FetchResult:
    """A `FetchResult` stand-in. These tests store rows; they do not fetch."""
    return FetchResult(
        url="https://eodhd.com/api/eod/MSFT.US?api_token=REDACTED",
        final_url="https://eodhd.com/api/eod/MSFT.US?api_token=REDACTED",
        status_code=200,
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/json",
        declared_media_type="application/json",
        headers={},
        redirect_chain=(),
        elapsed_ms=1.0,
        attempts=1,
    )


def bars_response(
    rows: list[api.BarRow], *, as_of: date = AS_OF, symbol: str = "MSFT.US"
) -> PriceResponse:
    return PriceResponse(
        symbol=symbol,
        as_of=as_of,
        bars=tuple(rows),
        discarded_after_as_of=0,
        fetch=fetch_result(),
    )


class StubPrices:
    """The slice of the vendor client the daily pass uses, answering from a dict.

    A symbol mapped to ``None`` raises, which is the delisted-ticker case: the pass must
    record it and carry on to the next listing rather than losing the night's other reads.
    Shared by the pass's own tests and the price alert's, which needs a pass to run.
    """

    def __init__(self, bars: dict[str, list[tuple[date, str]]] | None = None) -> None:
        self.bars = bars or {}
        self.asked: list[str] = []

    async def fetch_bars(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> PriceResponse:
        self.asked.append(symbol)
        rows = self.bars.get(symbol)
        if rows is None:
            message = f"No series for {symbol}."
            raise ExternalServiceError(message, provider="stub", context={"symbol": symbol})
        return bars_response([row(on, close) for on, close in rows], as_of=as_of, symbol=symbol)

    async def fetch_actions(self, symbol: str, *, as_of: date, since: date | None = None) -> Any:
        raise NotImplementedError

    async def fetch_shares_outstanding(self, symbol: str, *, as_of: date) -> Any:
        raise NotImplementedError

    @property
    def licence_note(self) -> str:
        return "stub"


def row(on: date, close: str, *, adjusted: str | None = None) -> api.BarRow:
    value = Decimal(close)
    return api.BarRow(
        on=on,
        open=value,
        high=value,
        low=value,
        close=value,
        adjusted_close=Decimal(adjusted) if adjusted else None,
        volume=1_000,
    )
