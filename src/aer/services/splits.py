"""Derived split transactions — ADR 0094's writer.

A split recorded in ``corporate_actions`` implies one transaction per book that has ever
dealt in the security: kind ``SPLIT``, quantity the ratio, trade date the ex-date, and a
``corporate_action_id`` pointing at the action behind it. The row derives from the action
alone — holdings at the ex-date are never consulted — so it never goes stale, and asking
for it twice is not an error and does not create a second row: the partial unique index
``uq_transactions_split_per_action`` holds the rule, and this module simply avoids
tripping it.

Two callers, each a moment new information arrives. ``derive_for_action`` runs when the
vendor's feed records a new split; ``ensure_for`` runs when a transaction is recorded,
which is what makes a backfilled first-ever trade self-healing — recording it creates the
split rows the book had no reason to carry before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select

from aer.core.enums import AttestationKind, Grade, TransactionKind
from aer.db.models import Attestation, CorporateAction, Transaction
from aer.db.models import Security as SecurityModel
from aer.db.models.security import CorporateActionKind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Security

_log = structlog.get_logger("aer.services.splits")

# The attestor every derived row names. Not a person, because pretending otherwise would
# be an attestation nobody made (ADR 0094); the provenance that matters is structural —
# the row's corporate_action_id, and the action's own hashed vendor artefact.
DERIVED_BY = "derived: corporate action"


async def derive_for_action(session: AsyncSession, *, action: CorporateAction) -> int:
    """Write the split transactions one newly recorded action implies.

    Every portfolio with any transaction in the security gets a row — a book flat at the
    ex-date gets a multiplication of zero units, which is a harmless no-op and cheaper
    than a rule that consults holdings and thereby reintroduces the staleness the ratio
    shape exists to avoid.
    """
    if action.kind is not CorporateActionKind.SPLIT:
        return 0
    portfolio_ids = list(
        await session.scalars(
            select(Transaction.portfolio_id)
            .where(Transaction.security_id == action.security_id)
            .distinct()
        )
    )
    if not portfolio_ids:
        return 0
    security = await session.get(SecurityModel, action.security_id)
    assert security is not None, "the action's FK guarantees its security exists"
    return await _ensure(session, security=security, actions=[action], portfolio_ids=portfolio_ids)


async def ensure_for(session: AsyncSession, *, portfolio_id: UUID, security: Security) -> int:
    """Ensure every split on this security has its derived row in this book."""
    actions = list(
        await session.scalars(
            select(CorporateAction).where(
                CorporateAction.security_id == security.id,
                CorporateAction.kind == CorporateActionKind.SPLIT,
            )
        )
    )
    if not actions:
        return 0
    return await _ensure(session, security=security, actions=actions, portfolio_ids=[portfolio_id])


async def _ensure(
    session: AsyncSession,
    *,
    security: Security,
    actions: list[CorporateAction],
    portfolio_ids: list[UUID],
) -> int:
    written = 0
    held = {
        (row.portfolio_id, row.corporate_action_id)
        for row in await session.execute(
            select(Transaction.portfolio_id, Transaction.corporate_action_id).where(
                Transaction.corporate_action_id.in_([action.id for action in actions]),
                Transaction.portfolio_id.in_(portfolio_ids),
            )
        )
    }
    for action in actions:
        ratio = action.split_ratio
        if ratio is None or ratio <= 0 or ratio == 1:
            # A ratio of one multiplies nothing and the database refuses the row
            # (`transaction_split_multiplies`); a non-positive one is vendor noise the
            # action table's own check should have stopped. Either way, no row.
            _log.warning(
                "splits.action_not_derivable",
                action=str(action.id),
                ratio=str(ratio),
            )
            continue
        for portfolio_id in portfolio_ids:
            if (portfolio_id, action.id) in held:
                continue
            attestation = Attestation(
                kind=AttestationKind.TRANSACTION,
                grade=Grade.ATTESTED,
                effective_at=datetime.combine(action.ex_date, datetime.min.time(), tzinfo=UTC),
                recorded_by=DERIVED_BY,
                note=(
                    f"Share count multiplied by {format(ratio.normalize(), 'f')} on "
                    f"{action.ex_date} (ADR 0094)."
                ),
            )
            session.add(attestation)
            await session.flush()
            session.add(
                Transaction(
                    attestation_id=attestation.id,
                    portfolio_id=portfolio_id,
                    kind=TransactionKind.SPLIT,
                    security_id=security.id,
                    trade_date=action.ex_date,
                    quantity=ratio,
                    price=None,
                    fees=Decimal(0),
                    currency=security.quote_currency,
                    corporate_action_id=action.id,
                )
            )
            written += 1
    if written:
        await session.flush()
        _log.info(
            "splits.transactions_derived",
            security=str(security.id),
            written=written,
        )
    return written
