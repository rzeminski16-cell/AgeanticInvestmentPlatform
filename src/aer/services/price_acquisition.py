"""Prices for the subject and its market, and the beta that falls out of them.

Gap B3. :mod:`aer.services.prices` knew how to store bars, adjust a series, regress a beta
and propose it; the EODHD client knew how to fetch. No workflow step called either, so no
run has ever held a price — which is why `beta` sat outstanding on the assumptions gate and
the valuation could not reach a discount rate.

**Everything here is optional, and its absence is a sentence rather than a failure.** The
subscription is the operator's; a machine without a key runs the whole workflow and says
that the price-derived figures are missing. That is the ADR 0030 shape — a licensed feed is
a capability the platform works without — and it is why this returns an outcome rather than
raising.

**The beta is proposed, never confirmed.** A regression is evidence for a beta, not a
decision about one: the proxy is a judgement, the window changes the answer, and a thinly
traded share's beta is biased low. So the operator meets it on the assumptions page like
any other proposal, with the proxy and the observation count in its justification.

**Nothing acquired here may leave the machine.** ADR 0030 route 2: the series is the
licensed information, and a chart of it is that information in repackaged form. The
operator's 2026-08-09 determination permits *derived* figures — a market capitalisation, a
multiple, a beta — and does not extend to the series itself. The provenance rows carry the
licence note; the containment is `aer.calc.comps.CompsTable.for_audience` and the exhibit
layer, not this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import prices as calc_prices
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import Provider, SourceTier
from aer.db.models import Company, ResearchRequest, Security
from aer.errors import AerError
from aer.services.acquisition import record_acquisition
from aer.services.prices import (
    adjusted_series_for,
    market_capitalisation_for,
    price_quantity,
    propose_computed_beta,
    record_actions,
    record_bars,
    upsert_security,
)
from aer.sources.eodhd.client import ActionsResponse, PriceResponse, SharesResponse
from aer.sources.eodhd.proxies import (
    MarketProxy,
    ProxyRefusedError,
    market_proxy_for,
    vendor_symbol,
)
from aer.storage.protocol import ArtefactStore

__all__ = [
    "BETA_WINDOW_YEARS",
    "PEER_WINDOW_DAYS",
    "PeerPrices",
    "PriceAcquisition",
    "PriceClient",
    "acquire_peer_prices",
    "acquire_prices",
]

_log = structlog.get_logger("aer.services.price_acquisition")

_SHARES: Final = Unit.base("shares")

# How far back the beta regression reaches. Five years of monthly returns is sixty
# observations — the convention, and enough that one unusual month does not carry the
# estimate. A longer window measures a company that may no longer exist in the same form.
BETA_WINDOW_YEARS: Final = 5

# The bar window fetched for a peer. A peer contributes one price — the last close at the
# as-of date — so five years of history would be spend against the daily allowance for
# nothing; a year is enough that a thinly traded listing still has a recent bar, with
# room for the split adjustments a close needs to be comparable.
PEER_WINDOW_DAYS: Final = 400


class PriceClient(Protocol):
    """The slice of the EODHD client this service uses.

    A protocol rather than the class, so the workflow tests can substitute a stub without
    the real client's fetcher, budget and store. The methods are the vendor's own.
    """

    async def fetch_bars(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> PriceResponse: ...

    async def fetch_actions(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> ActionsResponse: ...

    async def fetch_shares_outstanding(self, symbol: str, *, as_of: date) -> SharesResponse: ...

    @property
    def licence_note(self) -> str: ...


@dataclass(frozen=True, slots=True)
class PriceAcquisition:
    """What the run got, or the sentence explaining what it did not."""

    acquired: bool
    reason: str = ""
    security: Security | None = None
    bars: int = 0
    actions: int = 0
    market_capitalisation: Quantity | None = None
    beta_proposed: bool = False
    proxy: MarketProxy | None = None

    def as_dict(self) -> dict[str, Any]:
        if not self.acquired:
            return {"prices": False, "reason": self.reason}
        return {
            "prices": True,
            "symbol": self.security.provider_symbol if self.security is not None else "",
            "bars": self.bars,
            "actions": self.actions,
            # The figure, not the series. A market capitalisation is derived and publishable
            # under the 2026-08-09 determination; the bars behind it are not.
            "market_capitalisation": (
                str(self.market_capitalisation.value)
                if self.market_capitalisation is not None
                else None
            ),
            "beta_proposed": self.beta_proposed,
            "market_proxy": self.proxy.label if self.proxy is not None else "",
        }


async def acquire_prices(
    session: AsyncSession,
    client: PriceClient | None,
    store: ArtefactStore,
    *,
    request: ResearchRequest,
    company: Company,
    job_id: uuid.UUID,
    context: CalculationContext,
    shares_outstanding: Quantity | None = None,
) -> PriceAcquisition:
    """Fetch and store the subject's prices and its market's, and propose a beta.

    Args:
        client: ``None`` when no subscription is configured, which is an ordinary state and
            not an error — see the module docstring.
        shares_outstanding: The count from the filings, when the run has one. Supplied
            rather than fetched: the filed figure is a fact with a hashed source behind it,
            and the vendor's is a convenience. Falls back to the vendor's only when the
            filings carry none.

    Returns:
        A :class:`PriceAcquisition` describing what happened. Never raises for a missing
        key, an undocumented exchange or an empty series.
    """
    if client is None:
        # The full consequence, stated in the run record where an operator will read it
        # (polish P9): the live run reported this step's success in 96 milliseconds and
        # then asked for a typed beta, with nothing connecting the two.
        return PriceAcquisition(
            acquired=False,
            reason=(
                "No market-data subscription is configured, so this run holds no prices: "
                "no beta can be regressed, no market capitalisation computed, and no "
                "multiple priced — for the subject or any peer. The beta and the market "
                "capitalisation have to be entered by hand."
            ),
        )

    try:
        proxy = market_proxy_for(request.exchange)
    except ProxyRefusedError as refused:
        return PriceAcquisition(acquired=False, reason=str(refused))

    symbol = vendor_symbol(request.ticker, exchange=request.exchange)
    since = _window_start(request.as_of_date)

    subject = await _record_listing(
        session,
        client,
        store,
        request=request,
        company=company,
        job_id=job_id,
        symbol=symbol,
        exchange=request.exchange,
        since=since,
        name=company.name,
        ticker=request.ticker,
    )
    if subject.bars == 0:
        return PriceAcquisition(
            acquired=False,
            reason=(
                f"The market-data provider returned no prices for {symbol} on or before "
                f"{request.as_of_date.isoformat()}, so nothing price-derived could be "
                "computed."
            ),
        )

    market = await _record_listing(
        session,
        client,
        store,
        request=request,
        company=None,
        job_id=job_id,
        symbol=proxy.symbol,
        exchange=request.exchange,
        since=since,
        name=proxy.label,
        # An index has no corporate actions, and asking for them spends a call against the
        # daily allowance to be told so.
        with_actions=False,
    )

    capitalisation = await _market_capitalisation(
        session,
        client,
        context,
        security=subject.security,
        symbol=symbol,
        as_of=request.as_of_date,
        filed_shares=shares_outstanding,
    )

    beta_proposed = await _propose_beta(
        session,
        context,
        request=request,
        job_id=job_id,
        subject=subject.security,
        market=market.security,
        proxy=proxy,
        as_of=request.as_of_date,
        since=since,
    )

    _log.info(
        "prices.acquired",
        job_id=str(job_id),
        symbol=symbol,
        bars=subject.bars,
        actions=subject.actions,
        proxy=proxy.symbol,
        beta_proposed=beta_proposed,
    )
    return PriceAcquisition(
        acquired=True,
        security=subject.security,
        bars=subject.bars,
        actions=subject.actions,
        market_capitalisation=capitalisation,
        beta_proposed=beta_proposed,
        proxy=proxy,
    )


@dataclass(frozen=True, slots=True)
class PeerPrices:
    """A peer's price inputs for the comps table, or the sentence explaining their absence."""

    price_per_share: Quantity | None = None
    market_capitalisation: Quantity | None = None
    reason: str = ""


async def acquire_peer_prices(
    session: AsyncSession,
    client: PriceClient,
    store: ArtefactStore,
    *,
    request: ResearchRequest,
    company: Company,
    job_id: uuid.UUID,
    context: CalculationContext,
    filed_shares: Quantity | None,
) -> PeerPrices:
    """Fetch and store one confirmed peer's prices, and derive its comps inputs.

    The subject's own path (:func:`acquire_prices`) in miniature: the same provenance —
    bars archived and hashed, the licence note on the source row, the series stored
    against the peer's own security — with no beta and a short window, because a peer
    contributes a close and a market capitalisation, not a regression.

    Never raises for a peer that cannot be priced: an unlisted exchange, an unknown
    symbol or an empty series returns its reason, and the comps build records the peer
    as excluded rather than the table failing.
    """
    if not company.ticker or not company.exchange:
        return PeerPrices(
            reason=f"{company.name} has no stored listing to price, so it has no multiples."
        )

    try:
        symbol = vendor_symbol(company.ticker, exchange=company.exchange)
    except AerError as refused:
        return PeerPrices(reason=str(refused))

    since = request.as_of_date - timedelta(days=PEER_WINDOW_DAYS)
    try:
        listing = await _record_listing(
            session,
            client,
            store,
            request=request,
            company=company,
            job_id=job_id,
            symbol=symbol,
            exchange=company.exchange,
            since=since,
            name=company.name,
            ticker=company.ticker,
        )
    except AerError as refused:
        return PeerPrices(reason=f"The market-data provider refused {symbol}: {refused.message}")

    if listing.bars == 0:
        return PeerPrices(
            reason=(
                f"The market-data provider returned no prices for {symbol} on or before "
                f"{request.as_of_date.isoformat()}."
            )
        )

    series = await adjusted_series_for(session, listing.security, as_of=request.as_of_date)
    if not series.bars:
        return PeerPrices(reason=f"No usable bar for {symbol} inside the window.")

    price = price_quantity(
        series,
        source=SourceRef.fact(listing.security.id, label=f"{symbol} close"),
    )
    if series.currency in calc_prices.MINOR_UNITS:
        price = calc_prices.price_in_major_units(context, quoted=price)

    capitalisation = await _market_capitalisation(
        session,
        client,
        context,
        security=listing.security,
        symbol=symbol,
        as_of=request.as_of_date,
        filed_shares=filed_shares,
    )
    return PeerPrices(price_per_share=price, market_capitalisation=capitalisation)


# -- Internals ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Listing:
    security: Security
    bars: int
    actions: int


def _quote_currency(request: ResearchRequest, *, exchange: str) -> str:
    """What the listing is quoted in.

    The vendor's bar rows carry no currency, so it comes from the exchange. London quotes
    in pence and `aer.calc.prices` converts to pounds as its own recorded calculation —
    skipping that gives a market capitalisation a hundred times too large, which reads as a
    large company rather than as a bug.
    """
    return "GBX" if exchange.strip().upper() == "LSE" else request.base_currency


def _window_start(as_of: date) -> date:
    """The earliest bar the beta window needs.

    A whole extra year beyond the regression window, because a monthly return needs the
    month before it and a five-year window that starts exactly five years back yields
    fifty-nine returns rather than sixty.
    """
    return date(as_of.year - BETA_WINDOW_YEARS - 1, as_of.month, 1)


async def _record_listing(
    session: AsyncSession,
    client: PriceClient,
    store: ArtefactStore,
    *,
    request: ResearchRequest,
    company: Company | None,
    job_id: uuid.UUID,
    symbol: str,
    exchange: str,
    since: date,
    name: str,
    ticker: str | None = None,
    with_actions: bool = True,
) -> _Listing:
    """Fetch one listing's bars, store the artefact, and record the provenance.

    ``ticker`` names the security row explicitly. It used to be taken from the request
    whenever a company was supplied, which was correct while the subject was the only
    company ever listed — a peer's bars recorded that way would sit under the subject's
    ticker, which is a provenance error nobody would see until two series disagreed.
    """
    response = await client.fetch_bars(symbol, as_of=request.as_of_date, since=since)

    acquisition = await record_acquisition(
        session,
        store,
        request=request,
        job_id=job_id,
        # Whose series this is (ADR 0061). The subject's listing passes its company; the
        # market proxy passes ``None``, which is the honest answer — an index is not an
        # issuer — and leaves it visible to every section, which is what a beta regression
        # needs it to be.
        company_id=company.id if company is not None else None,
        result=response.fetch,
        provider=Provider.EODHD,
        source_tier=SourceTier.T4_LICENSED_MARKET,
        title=f"{name} daily prices to {request.as_of_date.isoformat()}",
        publisher="EOD Historical Data",
        # The last bar's date. A price series has no publication date of its own, and the
        # newest observation is the day it could first have existed — the same reasoning
        # ADR 0044 applies to the XBRL aggregate.
        publication_date=response.bars[-1].on if response.bars else None,
    )

    security = await upsert_security(
        session,
        ticker=ticker if ticker is not None else symbol.split(".", maxsplit=1)[0],
        exchange=exchange,
        provider_symbol=symbol,
        quote_currency=_quote_currency(request, exchange=exchange),
        name=name,
        company_id=company.id if company is not None else None,
    )

    stored = await record_bars(
        session,
        security=security,
        response=response,
        source_document_id=acquisition.source_document.id,
        job_id=job_id,
    )

    recorded_actions = 0
    if with_actions:
        actions = await client.fetch_actions(symbol, as_of=request.as_of_date, since=since)
        stored_actions = await record_actions(
            session,
            security=security,
            response=actions,
            source_document_id=acquisition.source_document.id,
        )
        recorded_actions = stored_actions.splits_inserted + stored_actions.dividends_inserted

    return _Listing(security=security, bars=stored.inserted, actions=recorded_actions)


async def _market_capitalisation(
    session: AsyncSession,
    client: PriceClient,
    context: CalculationContext,
    *,
    security: Security,
    symbol: str,
    as_of: date,
    filed_shares: Quantity | None,
) -> Quantity | None:
    """The subject's market capitalisation, or nothing if the share count is unknown.

    **The filed share count is preferred to the vendor's**, because it is a fact with a
    hashed filing behind it and the vendor's is a number in a JSON document. The vendor's is
    used only when the filings carry none, which happens for a company whose taxonomy this
    build does not map.
    """
    shares = filed_shares
    if shares is None:
        try:
            response = await client.fetch_shares_outstanding(symbol, as_of=as_of)
        except AerError as refused:
            _log.info("prices.no_share_count", symbol=symbol, reason=str(refused))
            return None
        shares = Quantity.of(
            response.shares.shares,
            _SHARES,
            source=SourceRef.fact(
                security.id,
                label=f"{symbol} shares outstanding as reported on "
                f"{response.shares.as_reported_on.isoformat()}",
            ),
        )

    series = await adjusted_series_for(session, security, as_of=as_of)
    if not series.bars:
        return None

    return market_capitalisation_for(
        context,
        series=series,
        shares=shares,
        price_source=SourceRef.fact(security.id, label=f"{symbol} close"),
    )


async def _propose_beta(
    session: AsyncSession,
    context: CalculationContext,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    subject: Security,
    market: Security,
    proxy: MarketProxy,
    as_of: date,
    since: date,
) -> bool:
    """Regress the beta and put it forward, or say nothing.

    Returns ``False`` rather than raising when the series do not overlap enough to regress:
    a newly listed company has no five-year beta, which is a fact about the company and not
    a failure of the run. The operator enters one by hand, as they would have had to anyway.
    """
    subject_series = await adjusted_series_for(session, subject, as_of=as_of, since=since)
    market_series = await adjusted_series_for(session, market, as_of=as_of, since=since)

    try:
        await propose_computed_beta(
            session,
            context,
            request_id=request.id,
            subject=subject_series,
            market=market_series,
            subject_source=SourceRef.fact(subject.id, label=subject.provider_symbol),
            market_source=SourceRef.fact(market.id, label=market.provider_symbol),
            market_label=proxy.label,
            job_id=job_id,
        )
    except AerError as refused:
        _log.info(
            "prices.beta_not_regressed",
            symbol=subject.provider_symbol,
            proxy=proxy.symbol,
            reason=str(refused),
        )
        return False
    return True
