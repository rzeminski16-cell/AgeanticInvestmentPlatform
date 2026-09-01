"""Plan endpoints: what gate 1 shows.

The plan, the sources it intends to use with their tiers, the token-counted cost estimate,
a runtime estimate, and the risks the planner named. Plus the hash an approval must carry.

**The hash is computed from the same object the page renders.** Not from the row, not from
a re-serialisation — from the exact structure handed to the template. Anything else leaves
room for the displayed plan and the approved plan to differ, which is the one thing the
hash exists to prevent.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession
from aer.db.models import Job, PlanSkillPin, ResearchPlan, WorkOrder
from aer.errors import AerError
from aer.services.approvals import payload_hash_for
from aer.skills.resolution import pinned_skills_for_work_order
from aer.workflow.workflows.vertical_slice_v1 import plan_gate_payload

__all__ = ["PlanRead", "router"]

router = APIRouter(prefix="/api/plans", tags=["plans"])


class PlanNotFoundError(AerError):
    """No such plan, or it belongs to someone else."""

    code = "plan_not_found"
    http_status = HTTP_404_NOT_FOUND


class PlanRead(BaseModel):
    """A plan as gate 1 shows it."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    request_id: uuid.UUID
    workflow_version: str
    summary: str
    sections: list[dict[str, Any]]
    section_listing: list[dict[str, Any]]
    planned_sources: list[dict[str, Any]]
    known_risks: list[Any]
    estimated_cost_gbp: str
    estimated_runtime_seconds: int
    skills: list[dict[str, Any]]

    # What an approval must echo back. The operator approves this hash, and the workflow
    # refuses to continue past the gate unless the approval carries it.
    payload_hash: str


@router.get("/{plan_id}", response_model=PlanRead, summary="Retrieve a plan")
async def read_plan(plan_id: uuid.UUID, session: DbSession, user: CurrentUser) -> PlanRead:
    plan = await session.scalar(
        select(ResearchPlan)
        .join(WorkOrder, WorkOrder.id == ResearchPlan.request_id)
        .where(ResearchPlan.id == plan_id, WorkOrder.user_id == user.id)
    )
    if plan is None:
        message = f"No plan {plan_id}."
        raise PlanNotFoundError(message, context={"plan_id": str(plan_id)})

    return _read(plan, await pinned_skills_for_work_order(session, work_order_id=plan.request_id))


@router.get("/for-run/{job_id}", response_model=PlanRead, summary="The plan a run is waiting on")
async def read_plan_for_run(job_id: uuid.UUID, session: DbSession, user: CurrentUser) -> PlanRead:
    """The most recent plan for a run's request.

    What the console links to when a run pauses at gate 1: the operator has a run id, not
    a plan id.
    """
    plan = await session.scalar(
        select(ResearchPlan)
        .join(Job, Job.work_order_id == ResearchPlan.request_id)
        .join(WorkOrder, WorkOrder.id == ResearchPlan.request_id)
        .where(Job.id == job_id, WorkOrder.user_id == user.id)
        .order_by(ResearchPlan.created_at.desc())
    )
    if plan is None:
        message = f"No plan for run {job_id}."
        raise PlanNotFoundError(message, context={"job_id": str(job_id)})

    return _read(plan, await pinned_skills_for_work_order(session, work_order_id=plan.request_id))


def _read(plan: ResearchPlan, pins: list[PlanSkillPin]) -> PlanRead:
    payload = plan_gate_payload(plan, pins)
    return PlanRead(
        id=plan.id,
        request_id=plan.request_id,
        workflow_version=plan.workflow_version,
        summary=payload["summary"],
        sections=payload["sections"],
        section_listing=payload["section_listing"],
        planned_sources=payload["planned_sources"],
        known_risks=payload["known_risks"],
        skills=payload["skills"],
        estimated_cost_gbp=payload["estimated_cost_gbp"],
        estimated_runtime_seconds=payload["estimated_runtime_seconds"],
        payload_hash=payload_hash_for(payload),
    )


def estimated_total_gbp(plan: ResearchPlan) -> Decimal:
    """What the plan says the run will cost."""
    return Decimal(str(plan.estimated_cost_gbp))
