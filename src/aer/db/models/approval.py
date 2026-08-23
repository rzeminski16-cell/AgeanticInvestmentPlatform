"""A human decision at an approval gate.

``payload_hash`` is the load-bearing column. It is a digest of exactly what was rendered
to the operator when they decided — the plan, the costs, the sources, the draft. Storing
only the decision would leave "approved" meaning nothing in particular six months later,
because the underlying rows may have been superseded. Storing the hash makes the claim
"this is what you saw" verifiable rather than asserted.

Rows here are never updated or deleted. Changing your mind creates a new decision.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SaEnum
from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import Decision, GateKind
from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.request import ResearchRequest
    from aer.db.models.user import User
    from aer.db.models.work_order import WorkOrder

__all__ = ["Approval"]


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[UuidPk]

    # A gate hangs off the run root, not off the mandate. Until ADR 0072 this column was
    # `request_id NOT NULL`, so every approval row asserted that an equity mandate existed
    # — which made the one monitor outcome ADR 0078 preserves as a genuine human judgement
    # the one outcome the schema forbade recording.
    work_order_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False
    )

    # Kept for the transition and dropped by the follow-up revision, once nothing reads it.
    request_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE")
    )

    # Nullable and deliberately not a foreign key: the plan gate is decided before any job
    # exists. A FK would force either a placeholder job row or a nullable-with-FK that
    # implies an ordering the schema does not actually have.
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    gate: Mapped[GateKind] = mapped_column(
        SaEnum(GateKind, name="gate_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    decision: Mapped[Decision] = mapped_column(
        SaEnum(Decision, name="decision", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    actor_user_id: Mapped[UuidFk] = mapped_column(ForeignKey("users.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    # SHA-256 of the exact payload rendered at the gate. See the module docstring.
    payload_hash: Mapped[Sha256] = mapped_column(nullable=False)

    decided_at: Mapped[Timestamp] = created_at_column()

    work_order: Mapped[WorkOrder] = relationship(back_populates="approvals")
    request: Mapped[ResearchRequest | None] = relationship(back_populates="approvals")
    actor: Mapped[User] = relationship(back_populates="approvals")

    __table_args__ = (
        Index("ix_approvals_work_order_id_gate", "work_order_id", "gate"),
        Index("ix_approvals_job_id", "job_id"),
    )
