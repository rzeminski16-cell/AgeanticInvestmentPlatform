"""A request to stop a run, recorded as its own row.

**Why this is not a column on ``jobs``.** The worker sets ``jobs.status = RUNNING`` and
holds that row's lock until the whole run commits — twenty to sixty minutes. Any attempt to
update the same row from the web process blocks for exactly as long as cancelling remains
useful. Verified rather than assumed: a second session's ``UPDATE`` waits until the first
commits.

So cancelling writes here, to a row nothing else holds, and the engine reads it before each
step. That also makes the two facts separately visible, which is honest — an in-flight
model call or HTTP fetch cannot be interrupted, so "you asked at 14:02" and "the run stopped
before the acquire step at 14:03" are different events and the audit trail should say both.

One row per job. Asking twice is not an error and does not create a second request: the
operator wants the run stopped, and they have said so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job

__all__ = ["JobCancellation"]


class JobCancellation(Base):
    __tablename__ = "job_cancellations"

    id: Mapped[UuidPk]

    # Unique: one standing request per job. A second click is idempotent rather than a
    # second row nobody can interpret.
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Who asked. Nullable and SET NULL rather than CASCADE: deleting a user must not erase
    # the record that a run was stopped, only who stopped it.
    requested_by: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    reason: Mapped[str | None] = mapped_column(Text)

    # When the operator asked. Distinct from when the run actually stopped, which is the
    # job's own `finished_at` -- a step already in flight runs to completion.
    requested_at: Mapped[Timestamp] = created_at_column()

    job: Mapped[Job] = relationship()

    def __repr__(self) -> str:
        return f"<JobCancellation job={self.job_id} at={self.requested_at.isoformat()}>"
