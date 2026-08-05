"""A grid over two assumptions, with a recorded calculation behind every cell.

A sensitivity table is the most persuasive object in a valuation deck and the easiest to
fake. Eighty-one numbers in a nine-by-nine grid look like eighty-one pieces of analysis, and
nothing about the presentation distinguishes a grid computed by re-running the model at each
point from one interpolated between the corners — or from one a language model wrote out.

So **every cell carries the id of the calculation that produced it**. Not a copy of the
figure with a note saying it was computed: the calculation row itself, which resolves through
its own inputs to the assumptions and facts the cell rests on. A cell with no calculation is
a number somebody typed, and the column is ``NOT NULL``.

The two axes are named by assumption, not by index, because "row 3, column 7" answers nothing
and ``discount_rate = 0.09, terminal_growth = 0.02`` answers everything.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.request import ResearchRequest

__all__ = ["Sensitivity", "SensitivityCell"]


class Sensitivity(Base):
    __tablename__ = "sensitivities"

    id: Mapped[UuidPk]

    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # Which scenario the grid was run against, if any. A sensitivity around the bear case and
    # one around the base case are different objects, and a table that could not tell them
    # apart would put them on the same page under the same heading.
    scenario_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL")
    )

    label: Mapped[str] = mapped_column(Text, nullable=False)

    # The two assumptions varied, by name.
    x_assumption: Mapped[str] = mapped_column(Text, nullable=False)
    y_assumption: Mapped[str] = mapped_column(Text, nullable=False)

    # What the grid reports — "equity value per share", "enterprise value" — and its unit.
    # The unit is on the grid rather than the cell because a grid whose cells disagreed about
    # units would be unreadable, and enforcing it here makes that unrepresentable.
    output_name: Mapped[str] = mapped_column(Text, nullable=False)
    output_unit: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pure")

    created_at: Mapped[Timestamp] = created_at_column()

    request: Mapped[ResearchRequest] = relationship()
    cells: Mapped[list[SensitivityCell]] = relationship(
        back_populates="sensitivity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(x_assumption)) > 0 AND char_length(btrim(y_assumption)) > 0",
            name="sensitivity_axes_are_named",
        ),
        # A grid of one assumption against itself is a line drawn twice. Every cell on the
        # diagonal would be the only meaningful one and the rest would be contradictions.
        CheckConstraint("x_assumption <> y_assumption", name="sensitivity_axes_differ"),
        CheckConstraint("char_length(btrim(output_name)) > 0", name="sensitivity_output_is_named"),
        Index("ix_sensitivities_request_id", "request_id"),
        Index("ix_sensitivities_job_id", "job_id"),
    )

    def __repr__(self) -> str:
        return f"<Sensitivity {self.x_assumption} x {self.y_assumption} -> {self.output_name}>"


class SensitivityCell(Base):
    """One point on the grid, and the calculation that produced it."""

    __tablename__ = "sensitivity_cells"

    id: Mapped[UuidPk]

    sensitivity_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("sensitivities.id", ondelete="CASCADE"), nullable=False
    )

    x_value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    y_value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    output_value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

    # **NOT NULL, and the point of the table.** A cell that cannot name the calculation
    # behind it is a number somebody typed, and eighty of those in a grid are indistinguishable
    # from eighty pieces of analysis.
    calculation_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("calculations.id", ondelete="RESTRICT"), nullable=False
    )

    created_at: Mapped[Timestamp] = created_at_column()

    sensitivity: Mapped[Sensitivity] = relationship(back_populates="cells")

    __table_args__ = (
        # One cell per point. A grid with two rows for the same coordinates is one whose
        # rendering depends on read order.
        UniqueConstraint("sensitivity_id", "x_value", "y_value", name="uq_sensitivity_cells_point"),
        Index("ix_sensitivity_cells_sensitivity_id", "sensitivity_id"),
        Index("ix_sensitivity_cells_calculation_id", "calculation_id"),
    )

    def __repr__(self) -> str:
        return f"<SensitivityCell ({self.x_value}, {self.y_value}) -> {self.output_value}>"
