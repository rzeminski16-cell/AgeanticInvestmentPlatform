"""What an operator recorded about a catalyst whose window closed.

`docs/archive/knowledge-graph.md` K4. The calendar half has always been honest: "the stated
window has passed" is knowable from rows. Whether the event *happened* is not — no query
answers it, and a model asserting it would be making a factual claim with no citation. So
the answer is the operator's, recorded here: an outcome, a mandatory reason, who and
when.

**Identity is ``(company, label)``**, the same identity the catalyst node carries — two
runs naming the same expected event are refining one expectation, and its outcome is one
fact however many theses leaned on it. Re-recording **updates** the row rather than being
refused: this is operator bookkeeping, not a gate decision, and the vault regenerates
from whatever the row currently says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import CatalystOutcomeKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.company import Company

__all__ = ["CatalystResolution"]


class CatalystResolution(Base):
    __tablename__ = "catalyst_resolutions"

    id: Mapped[UuidPk]

    company_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The catalyst's own label, verbatim from the section content that proposed it. The
    # service refuses a label no approved run ever proposed, so junk cannot accumulate.
    label: Mapped[str] = mapped_column(Text, nullable=False)

    outcome: Mapped[CatalystOutcomeKind] = mapped_column(
        SaEnum(
            CatalystOutcomeKind,
            name="catalyst_outcome",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # Mandatory and non-blank, the assumptions table's discipline: an outcome with no
    # stated reason is a verdict nobody can argue with.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # Free text following `Assumption.approved_by`: who recorded it should survive the
    # user row it names.
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[Timestamp] = created_at_column()

    company: Mapped[Company] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "label", name="uq_catalyst_resolutions_per_catalyst"),
        CheckConstraint("char_length(btrim(reason)) > 0", name="reason_is_not_blank"),
        CheckConstraint("char_length(btrim(label)) > 0", name="label_is_not_blank"),
    )

    def __repr__(self) -> str:
        return f"<CatalystResolution {self.label!r} {self.outcome.value}>"
