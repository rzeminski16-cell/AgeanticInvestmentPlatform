"""The portfolio's third door: a typed ticker verified once, at first sight.

Roadmap §3.1, under ADR 0093. A ticker the platform has never seen becomes dealable by
fetching a short window of its bars and recording them the way every acquisition is
recorded — the series hashed and stored, a source document rooted on a work order, the
security row keyed on the vendor's own symbol. Or it is refused, with the reason: no
subscription, an exchange the vendor mapping does not document, a symbol the vendor
returns nothing for.

**Every attempt leaves a record.** The act's work order is created first and marked
``COMPLETED`` or ``FAILED``, so "when did I try to add this, and what happened?" is a
query rather than a memory. The order carries a cap of zero: no step of this may call a
model, and the budget guard enforces exactly that (ADR 0093).

**A book acquisition is inherently not point-in-time.** The operator wants today's close —
that is the point of verifying at first sight — so the order says ``point_in_time=False``
and admissibility follows from that honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import structlog

from aer.core.enums import Provider, RequestStatus
from aer.db.models import Portfolio, Security, WorkOrder
from aer.errors import AerError
from aer.services.acquisition import record_acquisition
from aer.services.prices import record_bars, upsert_security
from aer.sources.eodhd.proxies import vendor_symbol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.services.price_acquisition import PriceClient
    from aer.storage.protocol import ArtefactStore

__all__ = ["AddedListing", "add_listing"]

_log = structlog.get_logger("aer.services.listings")

# Enough bars to prove the listing is real and to price the book at its latest close;
# deliberately not the beta window — a valuation's history is a research run's business.
_WINDOW_DAYS: Final = 30

# What each documented exchange quotes in. The vendor's bar rows carry no currency, so it
# comes from the venue — `GBX` for London (pence; `aer.calc.prices` owns the conversion,
# ADR 0032) — and an exchange missing here is refused rather than guessed, for the same
# reason `market_proxy_for` refuses one: a wrong currency prices the book a hundred times
# out and reads as a large holding rather than as a bug.
_QUOTE_CURRENCY: Final[dict[str, str]] = {
    "NASDAQ": "USD",
    "NYSE": "USD",
    "LSE": "GBX",
}

NO_SUBSCRIPTION: Final = (
    "No market-data subscription is configured, so an unknown ticker cannot be verified. "
    "Set the EODHD key in settings to enable this; listings a research run already priced "
    "stay dealable without it."
)


@dataclass(frozen=True, slots=True)
class AddedListing:
    """What one attempt produced: a dealable listing, or the reason there is none."""

    security: Security | None = None
    refusal: str = ""

    @property
    def is_dealable(self) -> bool:
        return self.security is not None


async def add_listing(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    portfolio: Portfolio,
    ticker: str,
    exchange: str,
    client: PriceClient | None,
) -> AddedListing:
    """Verify one never-seen ticker against the vendor and make it dealable, or say why not.

    No job, no workflow, no model call: the act is a fetch, three rows and an artefact,
    rooted on its own work order. The caller decides what "never seen" means — resolution
    against the listings already held is the form's first door and stays there.
    """
    if client is None:
        return AddedListing(refusal=NO_SUBSCRIPTION)

    today = datetime.now(UTC).date()
    order = WorkOrder(
        user_id=portfolio.user_id,
        tool="portfolio",
        subject_kind="portfolio",
        subject_id=portfolio.id,
        as_of_date=today,
        point_in_time=False,
        max_cost_gbp=Decimal(0),
        status=RequestStatus.RUNNING,
    )
    session.add(order)
    await session.flush()

    wanted = ticker.strip().upper()
    venue = exchange.strip().upper()
    try:
        symbol = vendor_symbol(wanted, exchange=venue)
        quote_currency = _QUOTE_CURRENCY[venue]
    except (AerError, KeyError):
        order.status = RequestStatus.FAILED
        await session.flush()
        known = ", ".join(sorted(_QUOTE_CURRENCY))
        return AddedListing(
            refusal=(
                f"{venue!r} is not an exchange this platform can verify a listing on "
                f"(it knows {known}). A symbol guessed for an undocumented venue either "
                "finds nothing or finds a different company, and the second is worse."
            )
        )

    since = today - timedelta(days=_WINDOW_DAYS)
    try:
        response = await client.fetch_bars(symbol, as_of=today, since=since)
    except AerError as refused:
        order.status = RequestStatus.FAILED
        await session.flush()
        return AddedListing(refusal=f"The market-data provider refused {symbol}: {refused.message}")

    if not response.bars:
        order.status = RequestStatus.FAILED
        await session.flush()
        return AddedListing(
            refusal=(
                f"The market-data provider returned no prices for {symbol} in the last "
                f"{_WINDOW_DAYS} days. Either the ticker does not trade on {venue}, or it "
                "is not one the subscription covers — nothing was made dealable."
            )
        )

    # The series is an externally derived fact: hashed, stored, and rooted on the act's
    # own order (invariant 1). `company_id` stays empty — a never-researched ticker has no
    # company row, and inventing one here would put an unverified identity in the registry
    # a research run trusts (ADR 0093).
    acquisition = await record_acquisition(
        session,
        store,
        work_order=order,
        result=response.fetch,
        provider=Provider.EODHD,
        source_tier=response.tier,
        title=f"{symbol} daily prices to {today.isoformat()}",
        publisher="EOD Historical Data",
        publication_date=response.bars[-1].on,
    )

    security = await upsert_security(
        session,
        ticker=wanted,
        exchange=venue,
        provider_symbol=symbol,
        quote_currency=quote_currency,
    )
    stored = await record_bars(
        session,
        security=security,
        response=response,
        source_document_id=acquisition.source_document.id,
    )

    order.status = RequestStatus.COMPLETED
    await session.flush()

    _log.info(
        "listing.added",
        portfolio=str(portfolio.id),
        work_order=str(order.id),
        symbol=symbol,
        bars=stored.inserted,
        sha256=acquisition.sha256,
    )
    return AddedListing(security=security)
