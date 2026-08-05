"""Every value anybody ever put forward for an assumption, including the ones nobody took.

The ``assumptions`` table holds the *current* state of a number somebody chose. This holds
the history of how it got there, and the two are separate for the same reason a citation and
an extraction are: **a proposal is a claim, and a claim survives being rejected.**

An operator who reads a model's proposed discount rate of 9%, disagrees, and enters 11% has
made a judgement. Overwriting the 9% would leave a report resting on 11% with no record that
anything was ever different, and the single most useful question about a valuation — "who
chose this, and what did they choose it over?" — would have no answer.

**Rows are immutable.** An amendment is a new row, not an edit. The current value is the
newest row's, and every earlier one stays exactly as it was written.

**A proposal names its origin, not its authority.** ``proposed_by`` is the agent role or the
person's email. It says where the number came from; it does not say the number may be used.
Only :attr:`~aer.db.models.assumption.Assumption.approved` says that, and only a person can
set it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Float,
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
    from aer.db.models.assumption import Assumption

__all__ = ["AssumptionProposal"]


class AssumptionProposal(Base):
    __tablename__ = "assumption_proposals"

    id: Mapped[UuidPk]

    assumption_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("assumptions.id", ondelete="CASCADE"), nullable=False
    )

    # The value put forward, with the unit it was put forward in. Both copied rather than
    # referenced: an assumption whose unit was later corrected must not silently rewrite what
    # an earlier proposal said, which is exactly what a foreign key to the current row would
    # do.
    value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pure")

    # Mandatory and non-blank, as on `assumptions`. A proposal without a reason is a guess
    # wearing a label, and an amendment without one is worse: it overrides a reasoned figure
    # with an unreasoned one and leaves no way to tell.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float | None] = mapped_column(Float)

    # The agent role, or the person's email. See the module docstring on why this is not
    # authority.
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)

    # Whether a human put this forward. Derivable from `proposed_by` only by knowing every
    # agent role that has ever existed, which is precisely the knowledge a stored column
    # exists to avoid needing.
    by_human: Mapped[bool] = mapped_column(nullable=False)

    # Which proposal this one replaced, if any.
    supersedes_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("assumption_proposals.id", ondelete="SET NULL")
    )

    # Position in this assumption's history, from 1. **Not derivable from `created_at`**:
    # Postgres `now()` is transaction-start time, so a propose-then-amend in one transaction
    # writes two rows with identical timestamps and "the latest" becomes whichever the
    # planner returned first. The history a reviewer reads has to be in the order it
    # happened, and a random UUID tiebreak is not that order.
    sequence: Mapped[int] = mapped_column(nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    assumption: Mapped[Assumption] = relationship(back_populates="proposals")

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(justification)) > 0", name="proposal_justification_is_not_blank"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="proposal_confidence_is_a_probability",
        ),
        CheckConstraint("char_length(btrim(proposed_by)) > 0", name="proposer_is_not_blank"),
        CheckConstraint("id <> supersedes_id", name="proposal_does_not_supersede_itself"),
        CheckConstraint("sequence >= 1", name="proposal_sequence_starts_at_one"),
        # Two rows claiming the same position is a history that cannot be ordered. Under
        # concurrent amendment this makes one of them fail rather than both succeed and one
        # silently disappear from the reading.
        UniqueConstraint("assumption_id", "sequence", name="uq_assumption_proposals_sequence"),
        Index("ix_assumption_proposals_assumption_id", "assumption_id"),
        Index("ix_assumption_proposals_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AssumptionProposal #{self.sequence} {self.value} {self.unit} by {self.proposed_by}>"
        )
