"""Acquiring a run's risk-free rate, and saying plainly when it cannot be.

Phase 1.6. The macro stack — the series registry, the archive client, the vintage store —
was complete and had no caller, so every run reached the assumptions gate asking the
operator to type a government yield the platform could have fetched (readiness audit
2026-09). This is the caller: one series, at the run's own as-of vintage, recorded and
converted through the one sanctioned conversion, :func:`aer.services.macro.as_rate`, and
handed to the assumptions step as exactly the observation this run acquired.

**Exactly this run's observation, never the newest row.** ``macro_observations`` outlives
the request that fetched it (ADR 0084), so a run standing on a later as-of date finds an
earlier run's reading in the table — a period a year old, at a vintage the later date
admits. :func:`~aer.services.macro.observation_as_at` cannot tell that reading from a fresh
one; this module can, because it fetched at this run's vintage and refuses a reading older
than :data:`STALE_AFTER_DAYS`.

**Never a failure of the run.** No client, no key, a currency with no documented series, an
archive with nothing at the vintage, a fetch that failed: each becomes a sentence in the
step's record, and the gate names the rate outstanding with that sentence, which is what an
operator can act on. A hand-typed rate stays admissible, as ADR 0082 says, and stays what
that record calls it — the operator's own attestation rather than a row in the rate store.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.errors import AerError
from aer.services.macro import as_rate, observation_as_at, record_series
from aer.sources.macro.series import SeriesRefusedError, risk_free_series_for

__all__ = ["PROPOSED_BY", "STALE_AFTER_DAYS", "RiskFreeAcquisition", "acquire_risk_free"]

_log = structlog.get_logger("aer.services.macro_acquisition")

PROPOSED_BY: Final = "aer.services.macro"
"""How the gate records who put the rate forward: a published series, through this module."""

# A daily yield series answers a vintage with readings up to the day before, or a few days
# before over a holiday. A reading further back than this is the archive answering an older
# question — an earlier run's vintage, a series that stopped publishing — and is not the
# rate on the as-of date.
STALE_AFTER_DAYS: Final = 14


@dataclass(frozen=True, slots=True)
class RiskFreeAcquisition:
    """What this run put behind its risk-free rate, or the sentence saying why nothing.

    Carried in the macro step's output and read back by the assumptions step, so the rate
    the gate proposes is the one this run fetched. Every field is a string or a date in the
    record; ``as_dict`` and ``from_dict`` are the two ends of that.
    """

    currency: str
    series_key: str = ""
    series_label: str = ""
    originator: str = ""
    identifier: str = ""
    observed_on: date | None = None
    vintage: date | None = None
    # As published, in per cent, and as the discount rate takes it — a fraction, through
    # the one sanctioned conversion (ADR 0027). Both kept: the justification quotes the
    # first and the assumption row carries the second.
    quoted: Decimal | None = None
    rate: Decimal | None = None
    observation_id: uuid.UUID | None = None
    # Why there is no rate, in a sentence the gate shows. Empty when there is one.
    reason: str = ""

    @property
    def acquired(self) -> bool:
        return self.rate is not None and self.observed_on is not None and self.vintage is not None

    @property
    def justification(self) -> str:
        """The sentence the assumption row carries: instrument, date, vintage, publisher."""
        if not self.acquired or self.observed_on is None or self.vintage is None:
            return ""
        # The store holds twelve places; the sentence says 4.36, not 4.360000000000.
        quoted = format(self.quoted.normalize(), "f") if self.quoted is not None else ""
        rate = format(self.rate.normalize(), "f") if self.rate is not None else ""
        return (
            f"The {self.series_label} was {quoted}% on {self.observed_on.isoformat()}, "
            f"as published by {self.vintage.isoformat()} ({self.originator}, series "
            f"{self.identifier}). Taken as the {self.currency} risk-free rate for this run's "
            f"as-of date and converted to the fraction {rate}."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "series_key": self.series_key,
            "series_label": self.series_label,
            "originator": self.originator,
            "identifier": self.identifier,
            "observed_on": self.observed_on.isoformat() if self.observed_on else "",
            "vintage": self.vintage.isoformat() if self.vintage else "",
            "quoted": str(self.quoted) if self.quoted is not None else "",
            "rate": str(self.rate) if self.rate is not None else "",
            "observation_id": str(self.observation_id) if self.observation_id else "",
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RiskFreeAcquisition:
        """The record read back from a step output, tolerant of the fields being blank."""

        def day(key: str) -> date | None:
            value = str(raw.get(key) or "")
            return date.fromisoformat(value) if value else None

        def number(key: str) -> Decimal | None:
            value = str(raw.get(key) or "")
            return Decimal(value) if value else None

        observation = str(raw.get("observation_id") or "")
        return cls(
            currency=str(raw.get("currency") or ""),
            series_key=str(raw.get("series_key") or ""),
            series_label=str(raw.get("series_label") or ""),
            originator=str(raw.get("originator") or ""),
            identifier=str(raw.get("identifier") or ""),
            observed_on=day("observed_on"),
            vintage=day("vintage"),
            quoted=number("quoted"),
            rate=number("rate"),
            observation_id=uuid.UUID(observation) if observation else None,
            reason=str(raw.get("reason") or ""),
        )


async def acquire_risk_free(
    session: AsyncSession,
    client: Any,
    *,
    currency: str,
    as_of: date,
    context: CalculationContext,
) -> RiskFreeAcquisition:
    """Fetch the currency's risk-free series at the as-of vintage and read the rate from it.

    Args:
        client: A :class:`~aer.sources.macro.client.MacroClient`, or ``None`` on a machine
            the bundle built none for. Typed loosely for the reason the price step's is.
        currency: The currency the valuation's cash flows are in — the filings' reporting
            currency, because a discount rate must match what it discounts.
        context: The ledger the percentage-to-fraction conversion is recorded in. The
            caller persists it, so the conversion is a calculation the report can walk.

    Every way of having no rate returns a record with a ``reason`` rather than raising:
    the step must finish, and the gate must be able to say why it is asking.
    """
    code = currency.upper()
    try:
        series = risk_free_series_for(code)
    except SeriesRefusedError as refused:
        return RiskFreeAcquisition(currency=code, reason=refused.message)

    found = RiskFreeAcquisition(
        currency=code,
        series_key=series.key,
        series_label=series.label,
        originator=series.originator,
        identifier=series.identifier,
    )
    if client is None:
        return replace(
            found,
            reason=(
                f"No macro client was available to this run, so the {series.label} was not "
                "fetched. Enter the rate you are using and say which instrument and date it "
                "is from."
            ),
        )

    try:
        response = await client.fetch_series(series.key, as_of=as_of)
    except AerError as failure:
        _log.warning(
            "macro.risk_free_unavailable",
            series=series.key,
            as_of=as_of.isoformat(),
            code=failure.code,
            reason=failure.message,
        )
        return replace(
            found,
            reason=(
                f"The {series.label} could not be fetched as at {as_of.isoformat()}: "
                f"{failure.message}"
            ),
        )

    await record_series(session, response)
    observation = await observation_as_at(session, key=series.key, as_of=as_of)
    if observation is None:
        return replace(
            found,
            reason=(
                f"The archive holds no {series.label} reading published on or before "
                f"{as_of.isoformat()}, so there is no rate to propose for this as-of date."
            ),
        )

    age = (as_of - observation.observed_on).days
    if age > STALE_AFTER_DAYS:
        return replace(
            found,
            reason=(
                f"The newest {series.label} reading published by {as_of.isoformat()} is for "
                f"{observation.observed_on.isoformat()}, {age} days earlier — too old to "
                "stand for the rate on the as-of date. Enter the rate you are using and say "
                "which instrument and date it is from."
            ),
        )

    rate = as_rate(observation, series=series, context=context)
    _log.info(
        "macro.risk_free_acquired",
        series=series.key,
        observed_on=observation.observed_on.isoformat(),
        vintage=observation.vintage.isoformat(),
        quoted=str(observation.value),
    )
    return replace(
        found,
        observed_on=observation.observed_on,
        vintage=observation.vintage,
        quoted=observation.value,
        rate=rate.value,
        observation_id=observation.id,
    )
