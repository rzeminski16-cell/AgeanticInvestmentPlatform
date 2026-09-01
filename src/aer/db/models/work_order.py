"""The run root: one unit of approved, budgeted, dated work, whatever it is about.

Until this table existed, every run in the platform hung off a `research_requests` row, and
so did every model call: `Agent._refuse_what_cannot_be_afforded` walks a step to its job to
its request to find a per-run cap, and refuses when it cannot. That is the right refusal —
invariant 6 says a cap that only warns is a cap that does not work — but it made an *equity
mandate* the precondition for spending a penny on anything. A thesis monitor running
overnight across a watchlist has no company to research and therefore, as the schema stood,
no way to be paid for.

So the cap moves to a row that is not about a company, and `research_requests` becomes the
detail row for what a run is *about* when that thing is one listed company. Nothing is
relaxed: `jobs.work_order_id` is `NOT NULL`, so every run still has a cap by construction.
ADR 0072 records why a nullable `jobs.request_id` was rejected instead — a cap that can be
NULL is worse than one that warns, because the guard then has to choose between refusing
every unattended run and inventing a limit nobody set.

**The subject is a kind and an id with no foreign key**, resolved through a resolver
registered per kind on the tool that owns it (ADR 0071). This follows `audit_events`, whose
`job_id` and `request_id` are unconstrained because "an audit record must survive the thing
it describes"; a work order for a watchlist entry the operator later deleted is still a run
that happened and cost money. The counter-evidence — that loose polymorphism already rotted
once in `_load_fact` — is answered by registration rather than by a constraint, because a
foreign key would not have caught that defect either: the id was valid and pointed at a real
row in the wrong table.

**A detail row shares the run root's primary key.** `research_requests.id` *is* the id of
its work order, which is the ordinary table-per-type shape for a 1:1 supertype and is what
migration 0051's backfill wrote. So `work_order_id=request.id` at a call site is not a
coincidence being exploited; it is the contract. The alternative — a separate id and a
lookup — buys nothing and adds a join to every path that has a request and wants its cap.

**This is now the only place those columns live.** `user_id`, `as_of_date`, `point_in_time`,
`max_cost_gbp`, `status` and `archived_at` were duplicated onto `research_requests` for
exactly one revision, kept in step by a single mirroring function, and migration `0064`
dropped the copies. The duplication was ugly on purpose: `tests/test_migrations.py` compares
the migrated schema against the models with `compare_type` on, so a column dropped in a
migration while still declared on a model is a red build in the same commit — and the only
way to drop a column *later* is for the model to keep it *now*.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy import (
    Enum as SaEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import RequestStatus
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.approval import Approval
    from aer.db.models.job import Job
    from aer.db.models.request import ResearchRequest
    from aer.db.models.source_document import SourceDocument
    from aer.db.models.user import User

__all__ = ["WorkOrder"]


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[UuidPk]

    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # -- Subject -----------------------------------------------------------------------

    # Which tool owns this run. Text rather than an enum: the vocabulary is the tool
    # registry's, and a Postgres type every tool has to migrate is a shared thing nobody
    # owns. ADR 0071 makes the registry the closed list and a test the thing that proves it.
    tool: Mapped[str] = mapped_column(String(32), nullable=False, server_default="research")

    # What the run is about. No foreign key, deliberately — see the module docstring.
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="company")

    # NULL until the subject is resolved, which for a company is when `acquire` turns a
    # typed ticker into a registry identifier. A work order with no subject sees no facts,
    # by construction and exactly as ADR 0061 arranged: the emptiness is the guard working.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # -- The clock the run reads --------------------------------------------------------

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Whether evidence published after `as_of_date` is admissible. Enforced at acquisition
    # in code, per invariant 4; this is what the enforcement reads.
    point_in_time: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    # -- Control -------------------------------------------------------------------------

    max_cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("2.50")
    )

    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "request_status"),
        nullable=False,
        default=RequestStatus.DRAFT,
        server_default=RequestStatus.DRAFT.value,
    )

    created_at: Mapped[Timestamp] = created_at_column()

    # Orthogonal to status, for the reason `research_requests` gives: an archived run keeps
    # the status it earned, so restoring it does not have to guess what it used to be.
    archived_at: Mapped[Timestamp | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    # -- Relationships -------------------------------------------------------------------

    user: Mapped[User] = relationship(back_populates="work_orders")
    jobs: Mapped[list[Job]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan"
    )
    sources: Mapped[list[SourceDocument]] = relationship(back_populates="work_order")

    # The equity mandate, when this run is about one listed company. ``None`` for every
    # other kind of run, which is the whole reason this table exists (ADR 0072) — and the
    # reason it is `uselist=False` rather than a list: a run has one subject, and the
    # detail row shares this row's key.
    request: Mapped[ResearchRequest | None] = relationship(
        back_populates="work_order", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__: Any = (
        # A cap of zero is not a cheap run; it is a run that cannot make a single call, and
        # the guard would refuse every step. `research_requests` carries the same check.
        # `>= 0` since ADR 0093: a portfolio data acquisition is budgeted at zero model
        # spend by design, and a zero cap is the enforcement — the budget guard refuses
        # every call under it. The name survives the widening so no rename ripples.
        CheckConstraint("max_cost_gbp >= 0", name="ck_work_orders_cost_is_positive"),
        Index("ix_work_orders_user_id", "user_id"),
        Index("ix_work_orders_subject", "subject_kind", "subject_id"),
        # The live-work list, which is what every landing page asks for.
        Index(
            "ix_work_orders_tool_created_at",
            "tool",
            "created_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )
