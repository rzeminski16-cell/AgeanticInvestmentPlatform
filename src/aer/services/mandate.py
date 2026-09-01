"""The equity mandate behind a run, when the run has one.

ADR 0072 made `work_orders` the run root and demoted `research_requests` to the detail row
describing what a run is *about* when that thing is one listed company. **A monitor run has
no mandate**, and it is the last thing standing between the schema and one existing: while
twenty-seven call sites reach for `job.request_id` and assume a row comes back, nothing
tool-agnostic can run.

So the read moves here, and it becomes two reads rather than one, because the callers
genuinely split in two:

* :func:`mandate_of` returns ``None`` for a run that has no mandate. Every surface that
  serves more than one kind of run uses it and says what it does with the absence.
* :func:`required_mandate` raises. A report page, an assumptions gate, a section writer —
  these are research machinery, and a research job whose detail row has gone missing is
  referential breakage, not a monitor. Refusing loudly is what `agents/base` already does
  one layer down, and for the same reason: *a guard that shrugged here would be a guard any
  orphaned step walks straight past.*

**Both read by the work order's id, not by ``jobs.request_id``.** The detail row shares the
run root's primary key — `research_requests.id` *is* its work order's id, which is what
migration 0054's backfill wrote and what `services/scope` already relies on — so there is
no join to find and no second column to keep in step. That is what lets the follow-up
revision drop `jobs.request_id` without any of these callers changing again.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from aer.db.models import ResearchRequest
from aer.errors import IntegrityError as BrokenRecordError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Job

__all__ = ["mandate_for", "mandate_of", "required_mandate", "required_mandate_for"]


async def mandate_of(session: AsyncSession, job: Job) -> ResearchRequest | None:
    """This run's equity mandate, or ``None`` when it is not about a company."""
    return await mandate_for(session, job.work_order_id)


async def mandate_for(session: AsyncSession, work_order_id: uuid.UUID) -> ResearchRequest | None:
    """The mandate of one work order, or ``None``.

    A primary-key lookup: the detail row shares the run root's key, so this is the same
    cost as reading the work order itself and needs no join to find.
    """
    return await session.get(ResearchRequest, work_order_id)


async def required_mandate(session: AsyncSession, job: Job) -> ResearchRequest:
    """This run's mandate, refusing when there is none.

    Raises:
        IntegrityError: When the run has no mandate. Use this only where the caller is
            research machinery that cannot mean anything without one — a report page, a
            gate over a ticker, a section writer. A tool-agnostic surface calls
            :func:`mandate_of` and decides what an absence means for itself.
    """
    return await required_mandate_for(session, job.work_order_id)


async def required_mandate_for(session: AsyncSession, work_order_id: uuid.UUID) -> ResearchRequest:
    """The mandate of one work order, refusing when there is none."""
    mandate = await mandate_for(session, work_order_id)
    if mandate is None:
        message = (
            f"Work order {work_order_id} has no research request, so there is no ticker, "
            "no as-of date and no horizon for this to be about. Either the run is not a "
            "research run — in which case this surface should be reading the work order — "
            "or its detail row has gone missing, which is referential breakage."
        )
        raise BrokenRecordError(message, context={"work_order_id": str(work_order_id)})
    return mandate
