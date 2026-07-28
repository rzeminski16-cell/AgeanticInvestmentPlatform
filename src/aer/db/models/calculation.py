"""One computation, recorded so it can be checked.

The row that makes "every number traces to a formula" true rather than aspirational. It
holds the formula a person can read, every input with its unit *and* its source, the exact
code version that ran, and the result.

**Inputs are JSONB, not a join table, and that is a deliberate trade.** A join table would
give referential integrity on each source id. It would also mean an input can point at
exactly one kind of thing, whereas an input points at a fact, an assumption, or another
calculation — three different tables. The alternatives are three nullable foreign keys with
a check constraint that exactly one is set, or a polymorphic association, and both make the
common operation — read a calculation and show what went into it — a multi-way join that
has to know about every source kind that will ever exist.

What is lost is a database-level guarantee that every source id resolves. What replaces it
is :func:`aer.services.calculations.lineage`, which resolves them explicitly and reports
any that do not — a check that runs when somebody asks, and can say *which* input dangles
rather than merely refusing a write.

**Nothing here is nullable except the parameters default.** A calculation missing its
formula, its code version or its inputs is not a partial record, it is an unusable one:
the entire reason the row exists is to make the number defensible, and a row that cannot do
that is worse than absent because it looks like it can.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job

__all__ = ["Calculation"]


class Calculation(Base):
    __tablename__ = "calculations"

    id: Mapped[UuidPk]

    # CASCADE: a job's calculations are meaningless without the job, and a deleted run
    # should not leave orphaned arithmetic behind.
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # -- What was computed -----------------------------------------------------------------

    name: Mapped[str] = mapped_column(Text, nullable=False)

    # The arithmetic in a form a person can check, e.g.
    # "cagr = (end / start) ^ (1 / years) - 1". Declared next to the function rather than
    # derived from its AST -- see aer.calc.engine.
    formula: Mapped[str] = mapped_column(Text, nullable=False)

    # "aer.calc.basic:cagr". Combined with code_version, this is what lets someone check
    # out the exact commit and re-run the exact function.
    function_ref: Mapped[str] = mapped_column(Text, nullable=False)

    code_version: Mapped[str] = mapped_column(Text, nullable=False)

    # -- What went in ----------------------------------------------------------------------

    # [{name, value, unit, source: {kind, id, label}}]. Values are stored as strings so a
    # JSON round-trip cannot turn an exact Decimal into a float -- the one way a provenance
    # record could disagree with the calculation it describes.
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    # Structural choices rather than measurements: how many periods, which basis. Recorded
    # because the calculation is not reproducible without them, kept separate from inputs
    # because they are not evidence and must not be presented as though they were.
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Free-text notes about what the formula takes for granted, e.g. that periods are of
    # equal length. Distinct from the `assumptions` table, which holds numbers a human
    # approved; these are properties of the arithmetic itself.
    assumptions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # -- What came out ---------------------------------------------------------------------

    output_value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    output_unit: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    job: Mapped[Job] = relationship()

    __table_args__ = (
        CheckConstraint("char_length(btrim(formula)) > 0", name="formula_is_not_blank"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_is_not_blank"),
        CheckConstraint("char_length(btrim(code_version)) > 0", name="code_version_is_recorded"),
        CheckConstraint("jsonb_typeof(inputs) = 'array'", name="inputs_are_an_array"),
        CheckConstraint("char_length(output_unit) > 0", name="output_unit_is_present"),
        # The query the provenance viewer makes: everything this run computed, by name.
        Index("ix_calculations_job_id_name", "job_id", "name"),
        Index("ix_calculations_created_at", "created_at"),
        # Finds every calculation that consumed a given source id, which is what answers
        # "what depends on this fact?" -- the question asked when a fact turns out to be
        # wrong. GIN over JSONB makes it an index scan rather than a full table read.
        Index("ix_calculations_inputs", "inputs", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Calculation {self.name}={self.output_value} {self.output_unit}>"
