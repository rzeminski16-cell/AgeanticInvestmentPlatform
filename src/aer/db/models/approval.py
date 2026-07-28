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
from aer.db.types import Sha256, Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.request import ResearchRequest
    from aer.db.models.user import User

__all__ = ["Approval"]


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[UuidPk]
    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
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

    request: Mapped[ResearchRequest] = relationship(back_populates="approvals")
    actor: Mapped[User] = relationship(back_populates="approvals")

    __table_args__ = (
        Index("ix_approvals_request_id_gate", "request_id", "gate"),
        Index("ix_approvals_job_id", "job_id"),
    )
