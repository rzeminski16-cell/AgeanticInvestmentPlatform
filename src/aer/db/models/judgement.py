"""A judgement: a named person held this view at this time on this stated basis.

The fifth record class (ADR 0074), and the weakest guarantee of the five — deliberately.
A fact says somebody published these bytes on a date. A calculation says re-running this
code reproduces this number. An assumption says a named person agreed to this *value* and
said why. An attestation says what the book says, at a grade of evidence. A judgement says
only that a view existed, at a knowable time, on a basis somebody wrote down: not that the
view was right, and not that anything supports it. That weakness is the record, not a
defect in it.

**A judgement is never a source reference.** Nothing here carries a value a calculation
could consume; ``SourceKind`` has no fifth member and ``SourceRef`` no fifth constructor;
``claims`` has no column for a ``judgement_id``. The schema cannot express the thing this
record forbids, so there is nothing for a later change to talk its way past. A test walks
the metadata to prove that no table but ``premises`` references this one.

**The shape follows ``attestations``.** ``judgements`` is the supertype: who held the view,
when, on what basis, and a supersession link. ``premises`` is the one subtype today, keyed
on the judgement's own id rather than carrying its own — a premise *is* a judgement seen
from its thesis, and a separate key would allow a premise with no holder, no time and no
basis. ``JudgementKind`` has one value for the same reason ``AttestationKind`` does: a
subtype here is a value *and* a detail table, so adding one is visibly a schema change.

**A thesis is the container, not the judgement.** It names a subject, a title and the
report it was written against, and it belongs to a person; what it *asserts* is its
premises, each a judgement of its own. ADR 0079's model: a premise is a free-text
statement plus an *optional* predicate, and an item with no predicate is not second-class
— it gets a scheduled human review instead, which is why ``review_by`` is required exactly
when the predicate is absent. A premise that can be tested by nothing and is reviewed by
nobody is a view the platform would silently stop asking about.

**Corrections are new rows, and nothing is deleted.** A view held at a time is a fact
about that time. Changing one's mind is a *later* fact, recorded as a withdrawal with a
reason, on the row that was withdrawn; the row itself is untouched. ADR 0078 argues this
for attention items — "I saw this and chose to do nothing" is decision data — and it holds
here with more force, because a premise quietly rewritten after it failed is precisely the
row the post-trade reviewer of ADR 0081 exists to read.

**The check constraints below are documentation; the migration is the enforcement.** The
test schema is built by running the real Alembic path, and autogenerate does not compare
CHECK constraints — so ``migrations/versions/0065`` decides what the database refuses.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import JudgementKind, PremiseComparator
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.user import User

__all__ = ["Judgement", "Premise", "Thesis"]


def _enum(kind: type, name: str) -> SaEnum:
    return SaEnum(kind, name=name, values_callable=lambda e: [m.value for m in e])


class Judgement(Base):
    """One view a named person held, at a time, on a stated basis."""

    __tablename__ = "judgements"

    id: Mapped[UuidPk]

    # Which subtype this is, and therefore which detail table holds what the view is *of*.
    kind: Mapped[JudgementKind] = mapped_column(
        _enum(JudgementKind, "judgement_kind"), nullable=False
    )

    # Whose view. Their name on the view, which is the difference between a judgement and
    # an anonymous opinion — and, as on `attestations.recorded_by`, it records origin
    # rather than granting authority.
    held_by: Mapped[str] = mapped_column(Text, nullable=False)

    # When the view was held, as the holder states it, and when the platform was told. Two
    # clocks for the reason `attestations` keeps two: a thesis written on Monday and
    # entered on Thursday is one row with two dates, and "what did you believe before the
    # results came out" is answered by the first.
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[Timestamp] = created_at_column()

    # On what grounds. NOT NULL, and the line ADR 0074 draws beside `assumptions`: that
    # table's `justification` is required because an assumption without a reason is a
    # guess wearing a label, and a view without a stated basis is the same thing one class
    # down. There is no `note` here that could stand in for it.
    basis: Mapped[str] = mapped_column(Text, nullable=False)

    # Which row this one corrects. Unique, so a view is superseded at most once and the
    # history cannot fork.
    supersedes_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("judgements.id", ondelete="RESTRICT")
    )

    # The holder no longer holds it, and said why. Both or neither: a withdrawal with no
    # reason is the least reviewable row this table could hold, and a reason with no
    # withdrawal is a note in the wrong column.
    withdrawn_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)

    premise: Mapped[Premise | None] = relationship(
        back_populates="judgement", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint("char_length(btrim(held_by)) > 0", name="judgement_holder_is_not_blank"),
        CheckConstraint("char_length(btrim(basis)) > 0", name="judgement_basis_is_not_blank"),
        CheckConstraint("id <> supersedes_id", name="judgement_does_not_supersede_itself"),
        CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_reason IS NULL)",
            name="judgement_withdrawal_carries_a_reason",
        ),
        UniqueConstraint("supersedes_id", name="uq_judgements_supersedes_once"),
        Index("ix_judgements_held_at", "held_at"),
    )

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    def __repr__(self) -> str:
        return f"<Judgement {self.kind.value} by {self.held_by} at {self.held_at}>"


class Thesis(Base):
    """A set of premises one person holds about one subject, written against one report.

    **The subject is a kind and an id with no foreign key**, in the shape `work_orders`
    settled (ADR 0072): a thesis outlives the company row it was about, exactly as an audit
    record outlives the thing it describes. Deleting a company from the registry must not
    delete what somebody thought of it.

    **Not a research request and not a position.** The join between what you hold and what
    you think is a query over the subject, never a foreign key, which is the only shape
    consistent with ADR 0064 — prior research may shape the questions and never the
    answers. The one link this row does carry is to the *report* it was written against,
    nullable and severed if that report goes, because "the evidence it rests on" is the
    reader's first question and a report id is an honest answer to it.
    """

    __tablename__ = "theses"

    id: Mapped[UuidPk]

    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="company")
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)

    # The report this view was formed against, where there is one. SET NULL rather than
    # CASCADE: the thesis is the operator's, and it survives the platform tidying its own
    # output.
    report_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))

    created_at: Mapped[Timestamp] = created_at_column()

    # Put away with a stated reason, never deleted. A retired thesis is exactly what the
    # post-trade reviewer reads (ADR 0081), and the reason is the first thing it wants.
    retired_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="theses")
    premises: Mapped[list[Premise]] = relationship(
        back_populates="thesis",
        cascade="all, delete-orphan",
        order_by="Premise.position",
    )

    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) > 0", name="thesis_title_is_not_blank"),
        CheckConstraint(
            "(retired_at IS NULL) = (retirement_reason IS NULL)",
            name="thesis_retirement_carries_a_reason",
        ),
        Index("ix_theses_user_id_created_at", "user_id", text("created_at DESC")),
        Index("ix_theses_subject", "subject_kind", "subject_id"),
    )

    @property
    def is_retired(self) -> bool:
        return self.retired_at is not None

    def __repr__(self) -> str:
        return f"<Thesis {self.title!r} on {self.subject_kind}:{self.subject_id}>"


class Premise(Base):
    """One thing a thesis asserts: a statement, and what would defeat it.

    **The predicate is optional, and the tidier design is wrong** (ADR 0079). "Azure
    revenue growth >= 25%" is a threshold code can test against a stored fact; "management
    allocates capital well" is not, and a number invented so that it could be tested would
    be a measurement nobody made wearing a `Quantity`'s clothes. So a premise either names
    a metric, a comparator, a threshold and its unit — all four, or none — or names the date
    by which a person will look at it again. Exactly one of the two is what makes it
    monitorable rather than forgotten.

    What the metric *names* is free text here and the monitor's business to resolve
    (ADR 0079's `unobservable` status exists for a metric no filing answers). Closing the
    vocabulary in this table would put the platform's current reach in the operator's
    mouth: a premise about a line it cannot yet read is still a premise.
    """

    __tablename__ = "premises"

    # Shared primary key: a premise *is* a judgement, seen from its thesis. `UuidFk` rather
    # than `UuidPk` even though this is the primary key, for the reason `transactions`
    # gives — the primary-key alias carries a server default, and on a foreign key that
    # means an INSERT omitting the parent silently invents one instead of failing.
    judgement_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("judgements.id", ondelete="CASCADE"), primary_key=True
    )

    thesis_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("theses.id", ondelete="CASCADE"), nullable=False
    )

    # Where it sits in the thesis. Unique per thesis, so the document reads in one order.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    statement: Mapped[str] = mapped_column(Text, nullable=False)

    # -- The predicate: all four or none ------------------------------------------------
    metric: Mapped[str | None] = mapped_column(Text)
    comparator: Mapped[PremiseComparator | None] = mapped_column(
        _enum(PremiseComparator, "premise_comparator")
    )
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    unit: Mapped[str | None] = mapped_column(String(32))

    # When a person looks at it again, for a premise nothing can test.
    review_by: Mapped[date | None] = mapped_column(Date)

    # `lazy="joined"` for the reason `ResearchRequest.work_order` gives: the session is
    # async, an unloaded relationship raises rather than reads slowly, and every surface
    # that shows a premise shows who held it and when.
    judgement: Mapped[Judgement] = relationship(back_populates="premise", lazy="joined")
    thesis: Mapped[Thesis] = relationship(back_populates="premises")

    __table_args__ = (
        UniqueConstraint("thesis_id", "position", name="uq_premises_position_per_thesis"),
        CheckConstraint("char_length(btrim(statement)) > 0", name="premise_statement_is_not_blank"),
        CheckConstraint(
            "(metric IS NULL) = (comparator IS NULL) "
            "AND (metric IS NULL) = (threshold IS NULL) "
            "AND (metric IS NULL) = (unit IS NULL)",
            name="premise_predicate_is_whole_or_absent",
        ),
        CheckConstraint(
            "metric IS NOT NULL OR review_by IS NOT NULL",
            name="premise_without_a_predicate_is_reviewed",
        ),
        Index("ix_premises_thesis_id", "thesis_id"),
        Index("ix_premises_review_by", "review_by"),
    )

    @property
    def has_predicate(self) -> bool:
        return self.metric is not None

    def __repr__(self) -> str:
        return f"<Premise {self.position} of {self.thesis_id}: {self.statement[:40]!r}>"
