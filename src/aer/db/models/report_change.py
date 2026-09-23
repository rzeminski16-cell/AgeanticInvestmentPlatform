"""What moved between a refresh and the report it refreshed (F4, ADR 0131 §5).

One row per material change, written by the refresh's diff step from the two runs' own
figures — never by a model. The change summary is composed *from* these rows (data model
Gap 2): what broke, what moved, what is new, and how much did not move. ``report_id`` is
null until the refresh renders a report, and stays null on a refresh that found nothing
new; ``job_id`` is what a summary is read by either way.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

__all__ = ["ReportChange"]


class ReportChange(Base):
    """One figure's movement between the prior report's run and the refresh."""

    __tablename__ = "report_changes"

    id: Mapped[UuidPk]

    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    prior_report_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))

    # ``fact`` | ``calculation`` | ``document`` | ``premise`` — what kind of thing moved.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str | None] = mapped_column(String(32))
    case: Mapped[str | None] = mapped_column(String(32))

    prior_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    prior_unit: Mapped[str | None] = mapped_column(String(32))
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    new_unit: Mapped[str | None] = mapped_column(String(32))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))

    material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Why it is material, in the mechanism's own words: relative, anchor, sign, appeared,
    # disappeared, from_zero, premise.
    movement: Mapped[str] = mapped_column(String(16), nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    # The rows behind the two figures, so the summary's footnotes resolve.
    prior_reference: Mapped[str | None] = mapped_column(Text)
    new_reference: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "kind IN ('fact', 'calculation', 'document', 'premise')",
            name="report_change_kind_is_one_of_four",
        ),
        CheckConstraint("char_length(btrim(narrative)) > 0", name="report_change_says_something"),
        Index("ix_report_changes_job_id", "job_id"),
        Index("ix_report_changes_prior_report_id", "prior_report_id"),
    )
