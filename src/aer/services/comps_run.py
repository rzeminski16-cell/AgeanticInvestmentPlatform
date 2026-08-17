"""The comps table for a run: the subject's full multiple set, and the peers' beside it.

Gap B3 built the frame; gap S3 filled it. The table used to be the subject's row plus a
list of exclusions, because a peer's multiple needs that peer's price and nothing ever
fetched one. Now each confirmed peer is priced through the same archived, licensed path
as the subject — a peer is only ever proposed because its filed facts are already stored,
so its statements come from rows the platform can already defend — and the peers' range
is read back onto the subject as a pair of recorded implied-value calculations, which is
what the internal football field draws as the comps band.

**A peer with no data is excluded with a reason, not dropped.** `build` already insists
on this, and it remains the degraded mode: a machine with no market-data subscription
excludes every peer by name rather than shrinking the table silently.

**Multiples that cannot be computed say which input was missing.**
:func:`aer.calc.comps.multiples_for` reports each one it could not produce rather than
omitting it, so "no P/E" is distinguishable from "P/E of nothing".

**The table knows who may read it.** ADR 0030 route 2 as amended on 2026-08-09: figures
*derived* from the licensed feed may be published, and the series itself may not.
:meth:`aer.calc.comps.CompsTable.for_audience` is the enforcement and it reads the
determination off the fetch policy; nothing here decides it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import comps as calc
from aer.calc import prices as calc_prices
from aer.calc.engine import CalculationContext
from aer.calc.ratios import ebitda as ebitda_of
from aer.calc.ratios import net_debt as net_debt_of
from aer.calc.units import Quantity, SourceRef
from aer.db.models import Company, Job, ResearchRequest, Security
from aer.errors import AerError
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis, analyse_company
from aer.services.calculations import new_context
from aer.services.comps import PeerSetNotConfirmedError, build, confirmed_peer_set
from aer.services.price_acquisition import PriceClient, acquire_peer_prices
from aer.services.prices import adjusted_series_for, price_quantity
from aer.storage.protocol import ArtefactStore

__all__ = ["CompsOutcome", "build_comps_table"]

_log = structlog.get_logger("aer.services.comps_run")

# The multiple inputs read straight off a statement line, and the concept each comes from.
# Anything not here is derived below or reported absent by `multiples_for` with the input
# named — which is the honest result for a figure the filings do not support.
_FROM_STATEMENTS: dict[str, str] = {
    "revenue": "revenue",
}

# The per-share earnings figure, diluted where the filing reports it — diluted is what a
# published P/E means, and falling back to basic is stated in the input's own source.
_EPS_CONCEPTS: tuple[str, ...] = ("earnings_per_share_diluted", "earnings_per_share_basic")


@dataclass(frozen=True, slots=True)
class CompsOutcome:
    """The table, or the sentence explaining why there is none."""

    built: bool
    reason: str = ""
    table: calc.CompsTable | None = None

    # The peers' range read back onto the subject as a per-share value, when at least one
    # peer had the multiple. Serialised for the step output; the internal football field
    # cites the recorded implied-value calculations behind it rather than these strings.
    band: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        if not self.built or self.table is None:
            return {"comps": False, "reason": self.reason}
        return {
            "comps": True,
            "peers": len(self.table.peers),
            "excluded": [
                {"name": item.name, "reason": item.reason} for item in self.table.excluded
            ],
            "basis": self.table.basis.value,
            "as_of": self.table.as_of.isoformat(),
            "subject_multiples": [
                {
                    "key": row.key,
                    "label": row.label,
                    "value": str(row.quantity.value) if row.quantity is not None else None,
                    "absent_because": row.absent_because,
                }
                for row in self.table.subject.multiples
            ],
            "peer_multiples": [
                {
                    "identifier": peer.identifier,
                    "name": peer.name,
                    "period_end": peer.period_end.isoformat(),
                    "multiples": [
                        {
                            "key": row.key,
                            "label": row.label,
                            "value": str(row.quantity.value) if row.quantity is not None else None,
                            "absent_because": row.absent_because,
                        }
                        for row in peer.multiples
                    ],
                }
                for peer in self.table.peers
            ],
            "comps_band": self.band,
        }


async def build_comps_table(
    session: AsyncSession,
    context: CalculationContext,
    *,
    job: Job,
    request: ResearchRequest,
    company_name: str,
    ticker: str,
    analysis: AnalysisOutcome,
    market_capitalisation: Quantity | None,
    as_of: date,
    client: PriceClient | None = None,
    store: ArtefactStore | None = None,
) -> CompsOutcome:
    """Assemble the run's comparables table: the subject's row, and the peers' beside it.

    Args:
        market_capitalisation: From :mod:`aer.services.price_acquisition`, or ``None`` on a
            machine with no subscription. Without it the enterprise-value multiples cannot
            be computed and say so; the table is still built, because the peer set and the
            exclusions are worth showing either way.
        client: The market-data client, or ``None`` without a subscription. Every peer
            multiple needs a peer price, so without it every confirmed peer is excluded
            with the reason `build` writes — the pre-S3 behaviour, kept as the honest
            degraded mode.

    Returns:
        A :class:`CompsOutcome`. Never raises for an unconfirmed peer set — a run whose
        peer gate was skipped for want of candidates has no table, which is a fact about
        the database rather than an error.
    """
    latest = analysis.periods[0] if analysis.periods else None
    if latest is None:
        return CompsOutcome(
            built=False,
            reason=(
                "No annual period could be assembled, so the subject has no denominator to "
                "compute a multiple against."
            ),
        )

    price = await _subject_price(
        session, context, ticker=ticker, exchange=request.exchange, as_of=as_of
    )
    inputs = _inputs_for(
        context, latest, market_capitalisation=market_capitalisation, price_per_share=price
    )
    subject = calc.PeerRow(
        identifier=ticker,
        name=company_name,
        period_end=latest.period_end,
        multiples=calc.multiples_for(
            context,
            inputs=inputs,
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            period_end=latest.period_end,
        ),
        rationale="The company this report is about.",
    )

    try:
        peer_multiples = await _peer_multiples(
            session, context, request=request, job=job, client=client, store=store
        )
        table = await build(
            session,
            job,
            subject=subject,
            peer_multiples=peer_multiples,
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            as_of=as_of,
        )
    except PeerSetNotConfirmedError as refused:
        return CompsOutcome(built=False, reason=str(refused))

    band = _implied_band(context, table=table, inputs=inputs)

    _log.info(
        "comps.table_built",
        job_id=str(job.id),
        peers=len(table.peers),
        excluded=len(table.excluded),
        computed=sum(1 for row in subject.multiples if row.quantity is not None),
        band=band["label"] if band else "",
    )
    return CompsOutcome(built=True, table=table, band=band)


def _inputs_for(
    context: CalculationContext,
    period: PeriodAnalysis,
    *,
    market_capitalisation: Quantity | None,
    price_per_share: Quantity | None = None,
) -> dict[str, Quantity]:
    """What a company's multiples are computed from — the subject's or a peer's.

    Only what the run genuinely holds. A name absent here is reported by `multiples_for` as
    an input the filing does not support, which is a better answer than a figure assembled
    from a substitute nobody asked for.
    """
    inputs: dict[str, Quantity] = {}

    for name, concept in _FROM_STATEMENTS.items():
        found = period.statements.get(concept)
        if found is not None:
            inputs[name] = found

    # EBITDA is not a statement line — it is a derivation from two of them. The original
    # lookup asked the statements for a concept no filing carries, which could never
    # answer, so EV/EBITDA sat absent on every table ever built. Derived here, into the
    # same ledger as the multiples it feeds, so the figure's lineage is the run's own.
    operating = period.statements.get("operating_income")
    depreciation = period.statements.get("depreciation_and_amortisation")
    if operating is not None and depreciation is not None:
        inputs["ebitda"] = ebitda_of(context, operating_income=operating, depreciation=depreciation)

    for concept in _EPS_CONCEPTS:
        eps = period.statements.get(concept)
        if eps is not None:
            inputs["earnings_per_share"] = eps
            break

    equity = period.statements.get("equity")
    shares = period.statements.get("shares_outstanding")
    if shares is not None and shares.value > 0:
        # Kept under its own name as well as inside the per-share figures: the implied
        # comps band needs the count to spread an enterprise value over.
        inputs["shares_outstanding"] = shares
        if equity is not None:
            inputs["book_value_per_share"] = calc.book_value_per_share(
                context, equity=equity, shares=shares
            )

    if price_per_share is not None:
        inputs["price_per_share"] = price_per_share

    if market_capitalisation is None:
        return inputs

    inputs["market_capitalisation"] = market_capitalisation

    cash = period.statements.get("cash_and_equivalents")
    debt = period.statements.get("total_debt")
    if cash is None:
        # Enterprise value without a cash line would be market capitalisation plus gross
        # debt, which is a different and consistently larger figure. Better to report the
        # EV multiples as uncomputable than to quote the wrong one.
        return inputs

    settled_debt = (
        debt if debt is not None else Quantity.of(cash.value * 0, cash.unit, source=cash.source)
    )
    inputs["net_debt"] = net_debt_of(context, total_debt=settled_debt, cash=cash)
    inputs["enterprise_value"] = calc.market_enterprise_value(
        context,
        market_capitalisation=market_capitalisation,
        net_debt=inputs["net_debt"],
    )
    return inputs


async def _subject_price(
    session: AsyncSession,
    context: CalculationContext,
    *,
    ticker: str,
    exchange: str,
    as_of: date,
) -> Quantity | None:
    """The subject's last close at the as-of date, from the bars the price step stored.

    ``None`` on a machine that never acquired prices, which reports the per-share
    multiples as uncomputable with the missing input named — the same honest shape as a
    missing market capitalisation.
    """
    security = await session.scalar(
        select(Security)
        .where(
            Security.ticker == ticker,
            Security.exchange == exchange,
            Security.company_id.is_not(None),
        )
        .order_by(Security.created_at)
        .limit(1)
    )
    if security is None:
        return None
    series = await adjusted_series_for(session, security, as_of=as_of)
    if not series.bars:
        return None

    price = price_quantity(
        series, source=SourceRef.fact(security.id, label=f"{security.provider_symbol} close")
    )
    if series.currency in calc_prices.MINOR_UNITS:
        price = calc_prices.price_in_major_units(context, quoted=price)
    return price


async def _peer_multiples(
    session: AsyncSession,
    context: CalculationContext,
    *,
    request: ResearchRequest,
    job: Job,
    client: PriceClient | None,
    store: ArtefactStore | None,
) -> dict[str, tuple[calc.MultipleResult, ...]]:
    """Each confirmed peer's multiples, computed from its own filings and its own price.

    Raises:
        PeerSetNotConfirmedError: Propagated from :func:`confirmed_peer_set`; the caller
            already turns it into the no-table outcome.
    """
    confirmed = await confirmed_peer_set(session, job)
    if not confirmed or client is None or store is None:
        return {}

    computed: dict[str, tuple[calc.MultipleResult, ...]] = {}
    for peer in confirmed:
        multiples = await _one_peer(
            session, context, request=request, job=job, client=client, store=store, peer=peer
        )
        if multiples is not None:
            computed[peer.identifier] = multiples
    return computed


async def _one_peer(
    session: AsyncSession,
    context: CalculationContext,
    *,
    request: ResearchRequest,
    job: Job,
    client: PriceClient,
    store: ArtefactStore,
    peer: Any,
) -> tuple[calc.MultipleResult, ...] | None:
    """One peer's multiples, or ``None`` for a peer the run cannot price or read.

    ``None`` is deliberate: `build` turns an absent entry into a named exclusion, which
    is where "we could not compute a multiple for this one" belongs — on the table, not
    swallowed here.

    The peer's statements are assembled in a throwaway ledger, the same shape the
    assumptions step uses for its recomputation: the run's persisted calculations are the
    subject's, and eight peers' full ratio suites would bury them. The multiples
    themselves land in the run's ledger, where the table's figures must be.
    """
    try:
        peer_id = uuid.UUID(str(peer.identifier))
    except ValueError:
        return None
    company = await session.get(Company, peer_id)
    if company is None:
        return None

    peer_analysis = await analyse_company(
        session, new_context(), company_id=company.id, request=request
    )
    latest = peer_analysis.periods[0] if peer_analysis.periods else None
    if latest is None:
        return None

    prices = await acquire_peer_prices(
        session,
        client,
        store,
        request=request,
        company=company,
        job_id=job.id,
        context=context,
        filed_shares=latest.statements.get("shares_outstanding"),
    )
    if prices.price_per_share is None and prices.market_capitalisation is None:
        # Every multiple's numerator is price-derived, so a peer with no price has no
        # row — only the exclusion `build` writes, with `reason` logged for the operator.
        _log.info("comps.peer_not_priced", peer=company.name, reason=prices.reason)
        return None

    try:
        inputs = _inputs_for(
            context,
            latest,
            market_capitalisation=prices.market_capitalisation,
            price_per_share=prices.price_per_share,
        )
        return calc.multiples_for(
            context,
            inputs=inputs,
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            period_end=latest.period_end,
        )
    except AerError as refused:
        # A peer reporting in another currency raises a unit mismatch, which for the
        # subject would be a mapping error and for a peer is a fact about the peer.
        _log.info("comps.peer_not_computed", peer=company.name, reason=str(refused))
        return None


# Which multiple the football-field band is read from, in preference order: the enterprise
# multiple compares whole businesses and survives capital-structure differences; the P/E
# is the fallback when the subject's EBITDA or bridge inputs are missing.
_BAND_PREFERENCE: tuple[str, ...] = ("ev_ebitda", "pe")


def _implied_band(
    context: CalculationContext,
    *,
    table: calc.CompsTable,
    inputs: dict[str, Quantity],
) -> dict[str, Any] | None:
    """The peers' multiple range read onto the subject as a per-share value range.

    Two traced calculations — the lowest peer multiple applied to the subject's own
    denominator, and the highest — so the band's ends resolve to recorded rows exactly
    as a text figure must. ``None`` when no peer has the multiple or the subject lacks
    the inputs to apply one, which leaves the football field without a comps band rather
    than with an invented one.
    """
    for key in _BAND_PREFERENCE:
        observed = [
            row.quantity
            for peer in table.peers
            for row in peer.multiples
            if row.key == key and row.quantity is not None
        ]
        if not observed:
            continue
        low, high = min(observed, key=lambda q: q.value), max(observed, key=lambda q: q.value)

        try:
            if key == "ev_ebitda":
                if not {"ebitda", "net_debt", "shares_outstanding"} <= inputs.keys():
                    continue
                ends = [
                    calc.implied_value_per_share_from_ev_multiple(
                        context,
                        multiple_observed=end,
                        denominator=inputs["ebitda"],
                        net_debt=inputs["net_debt"],
                        shares=inputs["shares_outstanding"],
                    )
                    for end in (low, high)
                ]
            else:
                if "earnings_per_share" not in inputs:
                    continue
                ends = [
                    calc.implied_value_per_share_from_price_multiple(
                        context,
                        multiple_observed=end,
                        denominator_per_share=inputs["earnings_per_share"],
                    )
                    for end in (low, high)
                ]
        except AerError as refused:
            _log.info("comps.band_not_computed", key=key, reason=str(refused))
            continue

        values = sorted(end.value for end in ends)
        label = next(d.label for d in calc.MULTIPLE_DEFINITIONS if d.key == key)
        return {
            "key": key,
            "label": f"Comps ({label})",
            "low": str(values[0]),
            "high": str(values[-1]),
            "unit": ends[0].unit.symbol,
            "peers": len(observed),
        }
    return None


def subject_identifier(ticker: str) -> str:
    """How the subject is keyed in the table. Its ticker, which the peer set also uses."""
    return ticker.strip().upper()
