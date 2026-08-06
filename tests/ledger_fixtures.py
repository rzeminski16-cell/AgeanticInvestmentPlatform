"""A persisted calculation chain resting on a confirmed assumption.

Shared by ``test_eval_replay`` and ``test_evaluation_gate`` so the two exercise the same
ledger rather than drifting apart: an assumption proposed and confirmed through the real
service, fed into a real traced chain via :func:`~aer.services.assumptions.as_quantity`, and
persisted through :func:`~aer.services.calculations.persist_context`. Nothing here writes a
row by hand — the point of both consumers is that what the *services* stored can be replayed
and resolved.

The figures are chosen to be exact in decimal arithmetic: 100 * 1.02 / (0.10 - 0.02) = 1275
and 1275 * 0.64 = 816, so a replay that disagrees by anything at all is a code change.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import gordon_terminal_value, present_value
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef
from aer.db.models import Job, ResearchRequest, User
from aer.services.assumptions import as_quantity, confirm, propose
from aer.services.calculations import persist_context

__all__ = ["record_valuation_ledger"]

TERMINAL_GROWTH = Decimal("0.02")
FINAL_CASH_FLOW = Decimal(100)
WACC = Decimal("0.10")
DISCOUNT = Decimal("0.64")

TERMINAL_VALUE = Decimal(1275)
DISCOUNTED_TERMINAL = Decimal(816)


async def record_valuation_ledger(
    db_session: AsyncSession,
    *,
    request: ResearchRequest,
    job: Job,
    actor: User,
) -> dict[str, Any]:
    """Confirm one assumption, run a two-step chain on it, persist the chain.

    Returns the assumption and the persisted rows, in ledger order:
    ``gordon_terminal_value`` at sequence 0, ``present_value`` at sequence 1 — the second
    citing the first, so the lineage as well as the arithmetic is exercised on replay.
    """
    assumption = await propose(
        db_session,
        request_id=request.id,
        name="terminal_growth",
        value=TERMINAL_GROWTH,
        unit="pure",
        justification="Long-run nominal growth, held below the discount rate.",
        proposed_by="planner",
        job_id=job.id,
    )
    await confirm(db_session, assumption=assumption, actor=actor)

    context = CalculationContext(code_version="eval-test")
    terminal = gordon_terminal_value(
        context,
        final_cash_flow=Quantity.of(
            FINAL_CASH_FLOW, "USD", source=SourceRef.fact(str(uuid.uuid4()), label="final FCF")
        ),
        wacc=Quantity.of(
            WACC, "pure", source=SourceRef.fact(str(uuid.uuid4()), label="cost of capital")
        ),
        terminal_growth=as_quantity(assumption),
    )
    present_value(
        context,
        amount=terminal,
        factor=Quantity.of(
            DISCOUNT, "pure", source=SourceRef.fact(str(uuid.uuid4()), label="discount factor")
        ),
    )

    rows = await persist_context(db_session, context, job_id=job.id)
    return {"assumption": assumption, "rows": rows}
