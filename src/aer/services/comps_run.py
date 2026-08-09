"""The comps table for a run: the subject's multiples, and the peers there was data for.

Gap B3. :func:`aer.services.comps.build` has always known how to assemble a table from a
confirmed peer set, and nothing ever called it — so the comparables page has been empty
since the first live run. This module builds the subject's row from the analysis and the
prices, hands the peer set to that function, and stores the result.

**A peer with no data is excluded with a reason, not dropped.** `build` already insists on
this and it is the property that matters most here, because today it is the *usual* case:
peers are proposed from companies this database already holds, and a fresh database holds
one — the subject. A table that quietly showed a subject row and no peers would look like a
company with no comparables rather than like a platform that has not acquired any.

**Multiples that cannot be computed say which input was missing.**
:func:`aer.calc.comps.multiples_for` reports each one it could not produce rather than
omitting it, so "no P/E" is distinguishable from "P/E of nothing".

**The table knows who may read it.** ADR 0030 route 2 as amended on 2026-08-09: figures
*derived* from the licensed feed may be published, and the series itself may not.
:meth:`aer.calc.comps.CompsTable.for_audience` is the enforcement and it reads the
determination off the fetch policy; nothing here decides it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import comps as calc
from aer.calc.engine import CalculationContext
from aer.calc.ratios import net_debt as net_debt_of
from aer.calc.units import Quantity
from aer.db.models import Job
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis
from aer.services.comps import PeerSetNotConfirmedError, build

__all__ = ["CompsOutcome", "build_comps_table"]

_log = structlog.get_logger("aer.services.comps_run")

# The multiple inputs this build can source, and the concept each comes from. Anything not
# here is reported absent by `multiples_for` with the input named — which is the honest
# result for a figure the filings do not support.
_FROM_STATEMENTS: dict[str, str] = {
    "revenue": "revenue",
    "ebitda": "ebitda",
}


@dataclass(frozen=True, slots=True)
class CompsOutcome:
    """The table, or the sentence explaining why there is none."""

    built: bool
    reason: str = ""
    table: calc.CompsTable | None = None

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
        }


async def build_comps_table(
    session: AsyncSession,
    context: CalculationContext,
    *,
    job: Job,
    company_name: str,
    ticker: str,
    analysis: AnalysisOutcome,
    market_capitalisation: Quantity | None,
    as_of: date,
) -> CompsOutcome:
    """Assemble the run's comparables table.

    Args:
        market_capitalisation: From :mod:`aer.services.price_acquisition`, or ``None`` on a
            machine with no subscription. Without it the enterprise-value multiples cannot
            be computed and say so; the table is still built, because the peer set and the
            exclusions are worth showing either way.

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

    subject = calc.PeerRow(
        identifier=ticker,
        name=company_name,
        period_end=latest.period_end,
        multiples=calc.multiples_for(
            context,
            inputs=_inputs_for(context, latest, market_capitalisation=market_capitalisation),
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            period_end=latest.period_end,
        ),
        rationale="The company this report is about.",
    )

    try:
        table = await build(
            session,
            job,
            subject=subject,
            # Empty, and that is the honest state rather than a stub. A peer's multiple
            # needs that peer's filings and prices, and this workflow acquires neither —
            # every confirmed peer is therefore excluded by name with the reason `build`
            # writes, which is what an operator needs in order to know the table is thin
            # because the data is missing and not because the companies are incomparable.
            peer_multiples={},
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            as_of=as_of,
        )
    except PeerSetNotConfirmedError as refused:
        return CompsOutcome(built=False, reason=str(refused))

    _log.info(
        "comps.table_built",
        job_id=str(job.id),
        peers=len(table.peers),
        excluded=len(table.excluded),
        computed=sum(1 for row in subject.multiples if row.quantity is not None),
    )
    return CompsOutcome(built=True, table=table)


def _inputs_for(
    context: CalculationContext,
    period: PeriodAnalysis,
    *,
    market_capitalisation: Quantity | None,
) -> dict[str, Quantity]:
    """What the subject's multiples are computed from.

    Only what the run genuinely holds. A name absent here is reported by `multiples_for` as
    an input the filing does not support, which is a better answer than a figure assembled
    from a substitute nobody asked for.
    """
    inputs: dict[str, Quantity] = {}

    for name, concept in _FROM_STATEMENTS.items():
        found = period.statements.get(concept)
        if found is not None:
            inputs[name] = found

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
    inputs["enterprise_value"] = calc.market_enterprise_value(
        context,
        market_capitalisation=market_capitalisation,
        net_debt=net_debt_of(context, total_debt=settled_debt, cash=cash),
    )
    return inputs


def subject_identifier(ticker: str) -> str:
    """How the subject is keyed in the table. Its ticker, which the peer set also uses."""
    return ticker.strip().upper()
