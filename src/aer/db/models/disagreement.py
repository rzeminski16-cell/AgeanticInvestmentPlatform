"""A conflict between two sources, and what was done about it.

The table exists so that "which figure did we use, and what did we reject?" is a query
rather than an archaeology exercise. Both positions are stored on every row — including
the losing one, which is the whole point. A resolution record that kept only the winner
would document a choice while destroying the evidence that a choice was made.

**Agreement writes nothing.** Rung 1 of the ladder is the ordinary case, and a row per
agreeing pair would bury the rows that mean something under the rows that do not.

**``fingerprint`` makes recording idempotent.** The same two positions compared twice — a
re-run, a retried step — must produce one row, not two. It is a digest of the topic, the
kind and the two references in canonical order, so it is stable across runs of the same
comparison and different for any other. A unique index on ``(job_id, fingerprint)`` is what
enforces it; without it, re-running a step would inflate the disagreement appendix with
duplicates that look like independent conflicts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.disagreement import (
    DisagreementKind,
    ResolutionOutcome,
    ResolutionRule,
    ResolvedBy,
)
from aer.core.enums import GateKind
from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job

__all__ = ["Disagreement"]


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class Disagreement(Base):
    __tablename__ = "disagreements"

    id: Mapped[UuidPk]

    # CASCADE: a disagreement is a fact about one run's evidence. Another run over the same
    # company re-derives its own, from whatever it acquired.
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # What the two positions are about, in the words a reader would use: "Revenue FY2025".
    # Free text rather than a concept id, because a calculation conflict and a thesis
    # conflict are about things no concept vocabulary names.
    topic: Mapped[str] = mapped_column(Text, nullable=False)

    kind: Mapped[DisagreementKind] = mapped_column(
        _enum(DisagreementKind, "disagreement_kind"), nullable=False
    )

    # Both retained, whatever the outcome. Canonically ordered by the ladder, so "A" is a
    # property of the pair rather than of whichever argument the caller passed first.
    position_a: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    position_b: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    resolution: Mapped[ResolutionOutcome] = mapped_column(
        _enum(ResolutionOutcome, "resolution_outcome"), nullable=False
    )

    # Which rung fired, and who settled it. `docs/PLAN.md` puts both in one text column;
    # split here because "how often does the tier rule decide our numbers?" and "how often
    # does a human have to?" are different questions and both are worth asking.
    rule: Mapped[ResolutionRule] = mapped_column(_enum(ResolutionRule, "resolution_rule"))
    resolved_by: Mapped[ResolvedBy] = mapped_column(_enum(ResolvedBy, "resolved_by"))

    resolution_rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # Set when a person settled what the ladder would not. Nullable and not a foreign key
    # requirement on every row: most disagreements are decided by rule and no human ever
    # sees them.
    resolved_by_user_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[Timestamp | None] = mapped_column()

    # The gate this was raised at, when the ladder declined to decide. Non-null exactly
    # when the outcome is `escalated`, enforced below — an escalation nobody routed
    # anywhere is an escalation that quietly did nothing.
    escalated_to_gate: Mapped[GateKind | None] = mapped_column(_enum(GateKind, "gate_kind"))

    # A credible-source conflict per `docs/PLAN.md` section 2.4. Raises the banner at gate
    # 2; see `aer.core.disagreement.Resolution.material` for what it does and does not mean.
    material: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # NULL where the positions are not numerically comparable at all: a unit mismatch, or a
    # thesis conflict. Zero would be a lie in both cases.
    relative_difference: Mapped[Decimal | None] = mapped_column(Numeric(18, 9))

    # See the module docstring.
    fingerprint: Mapped[Sha256] = mapped_column(nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    job: Mapped[Job] = relationship()

    __table_args__ = (
        Index("uq_disagreements_job_fingerprint", "job_id", "fingerprint", unique=True),
        CheckConstraint(
            "char_length(topic) > 0",
            name="ck_disagreements_topic_is_present",
        ),
        # A resolution with no argument for it is a resolution nobody can review, and the
        # rationale is the only part of this row a reader of the report actually sees.
        CheckConstraint(
            "char_length(resolution_rationale) > 0",
            name="ck_disagreements_rationale_is_present",
        ),
        # An escalation goes to a gate or it goes nowhere. Both directions: a gate recorded
        # on a resolved row would put a decided conflict on the operator's banner.
        CheckConstraint(
            "(resolution = 'escalated') = (escalated_to_gate IS NOT NULL)",
            name="ck_disagreements_escalations_reach_a_gate",
        ),
        # A human resolution names the human. Half a record here would leave "who decided
        # this?" answerable only by guessing from the timestamp.
        CheckConstraint(
            "(resolved_by = 'human') = "
            "(resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_disagreements_human_resolutions_name_the_human",
        ),
        # The ordinary case writes no row at all; see the module docstring. A stored
        # `agreed` row would mean the recording rule had been bypassed.
        CheckConstraint(
            "resolution <> 'agreed'",
            name="ck_disagreements_agreement_is_not_recorded",
        ),
        Index("ix_disagreements_job_id", "job_id"),
        # The gate-2 query: this run's unresolved conflicts, most material first.
        Index("ix_disagreements_job_resolution", "job_id", "resolution"),
    )

    def __repr__(self) -> str:
        return f"<Disagreement {self.topic!r} {self.resolution.value} by {self.rule.value}>"
