"""Building an :class:`~aer.core.scope.EvidenceScope` from what a caller is holding.

The scope itself is a pure value in `aer.core`. This is the one place that knows how to
fill it from a row, so the fields are read from the same places every time rather than
assembled slightly differently by each of the two doors' callers.

The run identity and the subject kind come from the work order, in both builders, and the
reason is not tidiness: a run without a mandate has nowhere else to carry them, and a scope
that read a mandate's copy would be a second answer to "which run is this", kept in step by
one function remembering to write both. That is the shape of every defect this expansion
has turned up so far: two readers, one of them quietly wrong.

The cost is one primary-key lookup per scope. The alternative was a divergence nobody would
see until one door answered differently from the other.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

from aer.core.scope import EvidenceScope
from aer.db.models import WorkOrder
from aer.errors import IntegrityError as BrokenRecordError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import ResearchRequest

__all__ = ["scope_for_request", "scope_for_work_order", "with_subject"]


async def scope_for_request(session: AsyncSession, request: ResearchRequest) -> EvidenceScope:
    """The scope a research run reads evidence under.

    The run identity comes from the work order; the subject comes from the mandate, because
    `acquire` writes the resolved company there and that is the authoritative answer. It is
    ``None`` before `acquire` has run, which is a real state and not a missing one: a scope
    with no subject sees no facts.
    """
    # The detail row shares the run root's key, so this needs no join to find.
    work_order = await session.get(WorkOrder, request.id)
    if work_order is None:  # pragma: no cover -- every request is created with one
        message = (
            f"Research request {request.id} has no work order, so there is no run to scope "
            "evidence to."
        )
        raise BrokenRecordError(message, context={"request_id": str(request.id)})
    return replace(scope_for_work_order(work_order), subject_id=request.company_id)


def with_subject(scope: EvidenceScope, company_id: uuid.UUID | None) -> EvidenceScope:
    """The same scope with the subject supplied from somewhere other than the column.

    One caller needs this. `research._company_id_for` falls back to a ticker-and-exchange
    lookup for a request `acquire` has not reached yet, and that is a weaker key — a re-used
    or re-listed ticker defeats it — so it is expressed as a deliberate substitution rather
    than as an argument the ordinary builder invites everyone to pass.
    """
    return replace(scope, subject_id=company_id)


def scope_for_work_order(work_order: WorkOrder) -> EvidenceScope:
    """The scope any run reads evidence under, mandate or no mandate.

    What a tool with no research request uses. Nothing calls it yet — the second tool is
    what will — and it exists here rather than being written later so that the two builders
    are visibly the same three fields from two rows, which is the whole claim ADR 0072 makes
    about the supertype.
    """
    return EvidenceScope(
        work_order_id=work_order.id,
        subject_kind=work_order.subject_kind,
        subject_id=work_order.subject_id,
    )
