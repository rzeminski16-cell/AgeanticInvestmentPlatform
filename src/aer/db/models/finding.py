"""A finding: the monitor noticed this, and a person has not yet said what they did about it.

The record ADR 0078 wanted kept apart from an approval, and kept apart structurally rather
than by a nullable column. An ``approvals`` row says *a named person was shown this and
agreed*; a row here says *the platform noticed this* — and a `weakened` reading nobody has
read is exactly that, recorded honestly, rather than a rubber-stamped approval asserting
that somebody read it.

**The tier is a column, pinned by a check.** ``opens_gate`` is written by code when the
status is, and the constraint below holds it equal to ``status = 'contradicted'`` — so which
findings ask for a decision is a fact of the row, decided once at write time, and never a
question a template answers differently from the feed (ADR 0078: "assigned by code at the
point the status is written").

**A resolution is an appended row, never a flag.** ``finding_resolutions`` is the history
of what people did: dismissed with a reason, withdrew the premise, reopened it. Nothing on
``findings`` changes when it is resolved. ADR 0078 argues this at length; the short form is
that "I saw this and chose to do nothing" is decision data, and a queue that empties itself
teaches the operator that ignoring it works.

**Nothing here is a figure.** ``observed`` carries the measurement code made — the metric,
the value, the threshold, the verdict — as JSON for a reader, pointing at the calculation or
fact row it came from. No calculation reads it, and a monitor finding is never a source
reference: this table has no ``SourceRef`` constructor and no claim can cite it.

**The check constraints are documentation; migration 0066 is the enforcement.** The test
schema is built by the real Alembic path, and autogenerate does not compare CHECKs.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import FindingAction, FindingKind, PremiseStatus
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.approval import Approval
    from aer.db.models.judgement import Premise, Thesis

__all__ = ["Finding", "FindingResolution"]


def _enum(kind: type, name: str) -> SaEnum:
    return SaEnum(kind, name=name, values_callable=lambda e: [m.value for m in e])


class Finding(Base):
    """One thing the monitor noticed about one thesis."""

    __tablename__ = "findings"

    id: Mapped[UuidPk]

    thesis_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("theses.id", ondelete="CASCADE"), nullable=False
    )

    # The premise a reading is about. NULL for a stopped pass, which read nothing. CASCADE
    # rather than SET NULL: the check below says a reading names its premise, and a
    # finding about a premise that is gone names nothing — it goes with the premise, which
    # is never deleted by design in any case.
    judgement_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("premises.judgement_id", ondelete="CASCADE")
    )

    # The monitor pass that wrote it. SET NULL so a finding survives `aer reset-research`
    # taking the run root with it — what was noticed is the operator's record, not the run's.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    kind: Mapped[FindingKind] = mapped_column(_enum(FindingKind, "finding_kind"), nullable=False)

    # ADR 0079's closed enum. NULL exactly when the finding is not a reading.
    status: Mapped[PremiseStatus | None] = mapped_column(_enum(PremiseStatus, "premise_status"))

    justification: Mapped[str] = mapped_column(Text, nullable=False)

    # The source documents the justification names — ids, as strings, validated by code
    # against the window before they were stored (ADR 0079: "source_document ids only").
    source_document_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # What code measured, for a reader: metric, value, unit, period, threshold, comparator,
    # whether the premise holds, and the row the value came from. NULL where nothing was
    # measured — an unobservable metric, a stopped pass.
    observed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # The filing dates the reading covered — the earliest and latest `filed_date` among
    # the facts read — so the next reading knows what is news. NULL on a stopped pass.
    window_from: Mapped[date | None] = mapped_column(Date)
    window_to: Mapped[date | None] = mapped_column(Date)

    # The tier, as data. See the module docstring.
    opens_gate: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    thesis: Mapped[Thesis] = relationship()
    premise: Mapped[Premise | None] = relationship()
    resolutions: Mapped[list[FindingResolution]] = relationship(
        back_populates="finding",
        cascade="all, delete-orphan",
        order_by="FindingResolution.resolved_at",
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(justification)) > 0", name="finding_justification_is_not_blank"
        ),
        CheckConstraint(
            "(kind = 'reading') = (status IS NOT NULL)", name="finding_reading_carries_a_status"
        ),
        CheckConstraint(
            "kind <> 'reading' OR judgement_id IS NOT NULL",
            name="finding_reading_names_its_premise",
        ),
        CheckConstraint(
            "opens_gate = (status = 'contradicted')", name="finding_gate_follows_the_status"
        ),
        Index("ix_findings_thesis_id_created_at", "thesis_id", text("created_at DESC")),
        Index("ix_findings_judgement_id", "judgement_id"),
        Index("ix_findings_job_id", "job_id"),
    )

    @property
    def is_open(self) -> bool:
        """Open until a person acts, and open again if they reopen it."""
        if not self.resolutions:
            return True
        return self.resolutions[-1].action is FindingAction.REOPENED

    @property
    def gate_is_decidable(self) -> bool:
        """Whether the gate this finding opened can still be decided as a gate.

        An approval hangs off a run root (ADR 0072), and the root is the pass that raised
        the finding. `aer reset-research` takes passes with it and leaves findings behind,
        so a contradicted finding can outlive the only row its approval could point at. It
        is then closed the ordinary way — an act with a reason — rather than left with a gate
        nothing can decide and a dismissal the gate refuses.
        """
        return self.opens_gate and self.is_open and self.job_id is not None

    def __repr__(self) -> str:
        what = self.status.value if self.status is not None else self.kind.value
        return f"<Finding {what} on thesis {self.thesis_id}>"


class FindingResolution(Base):
    """One act a person took on a finding, with the reason. Append-only."""

    __tablename__ = "finding_resolutions"

    id: Mapped[UuidPk]

    finding_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )

    action: Mapped[FindingAction] = mapped_column(
        _enum(FindingAction, "finding_action"), nullable=False
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)

    # The gate decision this act was, where the finding opened one. The approval carries
    # the actor, the payload hash and the chained event; this row carries what was done.
    approval_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL")
    )

    resolved_at: Mapped[Timestamp] = created_at_column()

    finding: Mapped[Finding] = relationship(back_populates="resolutions")
    approval: Mapped[Approval | None] = relationship()

    __table_args__ = (
        CheckConstraint("char_length(btrim(reason)) > 0", name="resolution_reason_is_not_blank"),
        CheckConstraint("char_length(btrim(actor)) > 0", name="resolution_actor_is_not_blank"),
        Index("ix_finding_resolutions_finding_id_resolved_at", "finding_id", "resolved_at"),
    )

    def __repr__(self) -> str:
        return f"<FindingResolution {self.action.value} of {self.finding_id} by {self.actor}>"
