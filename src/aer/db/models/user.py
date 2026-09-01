"""The user model.

The MVP is single-user, and this table will hold exactly one row for a long time. It
exists now because every other table carries a ``user_id``, and adding an owner column to
a populated schema later means backfilling rows whose real owner is no longer knowable.
The cost of carrying it from the start is one join that always resolves; the cost of
adding it later is a migration nobody can perform correctly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SaEnum
from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import UserRole
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidPk

if TYPE_CHECKING:
    from aer.db.models.approval import Approval
    from aer.db.models.work_order import WorkOrder

__all__ = ["User"]


class User(Base):
    __tablename__ = "users"

    id: Mapped[UuidPk]

    # Stored case-insensitively via CITEXT, so "Jane@Example.com" and "jane@example.com"
    # cannot become two accounts. Enforcing that in application code alone fails the
    # moment a row is inserted by a script or a migration.
    email: Mapped[str] = mapped_column(CITEXT(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SaEnum(UserRole, name="user_role", values_callable=lambda enum: [m.value for m in enum]),
        nullable=False,
        default=UserRole.OWNER,
        server_default=UserRole.OWNER.value,
    )

    created_at: Mapped[Timestamp] = created_at_column()

    # **Work orders, not requests.** Who asked is a property of the run root since
    # ADR 0072: a monitor run belongs to somebody too, and a collection that could only
    # hold equity mandates would have been the wrong answer the moment one existed.
    work_orders: Mapped[list[WorkOrder]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    approvals: Mapped[list[Approval]] = relationship(back_populates="actor")

    __table_args__ = (Index("ix_users_created_at", "created_at"),)
