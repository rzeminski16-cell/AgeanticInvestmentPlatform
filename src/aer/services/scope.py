"""Building an :class:`~aer.core.scope.EvidenceScope` from what a caller is holding.

The scope itself is a pure value in `aer.core`. This is the one place that knows how to
fill it from a row, so the five fields are read from the same places every time rather than
assembled slightly differently by each of the three doors' callers.

During the transition ADR 0068 describes, `as_of_date`, `point_in_time` and the run id are
all available on a `ResearchRequest` without a second query — the columns are deliberately
duplicated for one revision, and `research_requests.id` *is* its work order's id. So the
common builder needs no database round trip. When the follow-up revision drops those
columns, only this module changes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

from aer.core.scope import COMPANY, EvidenceScope

if TYPE_CHECKING:
    from aer.db.models import ResearchRequest, WorkOrder

__all__ = ["scope_for_request", "scope_for_work_order", "with_subject"]


def scope_for_request(request: ResearchRequest) -> EvidenceScope:
    """The scope a research run reads evidence under.

    The subject is `request.company_id`, which is what `acquire` resolved against a registry
    and is authoritative. It is ``None`` before that has run, which is a real state and not
    a missing one: a scope with no subject sees no facts.
    """
    return EvidenceScope(
        # The detail row shares the run root's key, so this is the work order's id.
        work_order_id=request.id,
        as_of_date=request.as_of_date,
        point_in_time=request.point_in_time,
        subject_kind=COMPANY,
        subject_id=request.company_id,
    )


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
    are visibly the same five fields from two rows, which is the whole claim ADR 0068 makes
    about the supertype.
    """
    return EvidenceScope(
        work_order_id=work_order.id,
        as_of_date=work_order.as_of_date,
        point_in_time=work_order.point_in_time,
        subject_kind=work_order.subject_kind,
        subject_id=work_order.subject_id,
    )
