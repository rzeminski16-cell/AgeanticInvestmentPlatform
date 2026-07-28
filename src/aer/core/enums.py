"""Domain enumerations.

These live in ``aer.core`` because they are pure vocabulary — no I/O, no dependencies —
and both the database models and the API schemas need them. Defining them here rather
than on the ORM models keeps the correctness core independent of SQLAlchemy.

Each is rendered as a **native PostgreSQL enum**, so an invalid value is rejected by the
database rather than merely by the application. That matters for a system whose whole
premise is that the stored record can be trusted: a bad status written by a script, a
migration or a future service still cannot land.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AnalysisMode",
    "Decision",
    "GateKind",
    "JobStatus",
    "RequestStatus",
    "UserRole",
]


class UserRole(StrEnum):
    """Access level. Designed now, enforced when authentication arrives."""

    OWNER = "owner"
    ANALYST = "analyst"
    VIEWER = "viewer"


class AnalysisMode(StrEnum):
    """How much work a research request asks for."""

    QUICK = "quick"
    STANDARD = "standard"
    FULL = "full"


class RequestStatus(StrEnum):
    """Lifecycle of a research request.

    Legal transitions are enforced in the service layer, not by the database. A CHECK
    constraint cannot see the previous value, and a trigger would put business rules
    somewhere they are easy to miss when reading the code. The database's job here is to
    reject values that are not statuses at all.
    """

    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobStatus(StrEnum):
    """Lifecycle of a job or an individual job step.

    ``PAUSED`` and ``BUDGET_EXCEEDED`` are deliberately distinct from ``FAILED``: neither
    is an error, and both are resumable after a human decision. Collapsing them into
    failure would lose the distinction between "this went wrong" and "this is waiting for
    you", which is the difference between a run you must debug and one you must approve.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class GateKind(StrEnum):
    """Which human approval gate a decision belongs to.

    ``PLAN`` and ``FINAL`` are the two gates every run passes through. The rest fire
    conditionally: UK filings need their extracted financials confirmed, comparable-company
    analysis needs its peer set confirmed, specialist sectors need an acknowledgement that
    the standard model does not apply, and a run projected over budget needs a decision.
    """

    PLAN = "PLAN"
    UK_FINANCIALS = "UK_FINANCIALS"
    PEER_SET = "PEER_SET"
    SECTOR_SPECIALIST = "SECTOR_SPECIALIST"
    BUDGET = "BUDGET"
    FINAL = "FINAL"


class Decision(StrEnum):
    """The outcome recorded at an approval gate."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AMENDED = "AMENDED"
