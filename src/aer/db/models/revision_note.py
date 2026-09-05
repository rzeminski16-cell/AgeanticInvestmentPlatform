"""What the critique loop did about a challenge, one row per decision (ADR 0091).

The memory §3.13 asks for, at its substrate: which class of challenge provoked a revision,
in which run, and what the loop did. The table remembers; it never teaches. Nothing reads
these rows into a prompt — a recurring class reaches a future run only when the operator
authors a methodology skill through the proved §3.11 boundary, and `aer lessons` is the
surface that shows them what recurs.

``dimension`` is the challenge's class — a red-team scored dimension for the draft, a
critic aspect for the plan — and it is the grouping key recurrence is counted over, which
is why it is a column rather than a key inside a JSON blob.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

__all__ = [
    "DISPOSITION_REVISED",
    "DISPOSITION_REVISION_REFUSED",
    "DISPOSITION_SKIPPED_CUSTOM",
    "DISPOSITION_STOOD",
    "SCOPE_DRAFT",
    "SCOPE_PLAN",
    "RevisionNote",
]

# The two steps the loop runs on (roadmap §3.13). A closed set, checked by the database:
# the loop does not belong on the deterministic steps, and a row claiming one would be
# describing a revision the architecture forbids.
SCOPE_PLAN: Final = "plan"
SCOPE_DRAFT: Final = "draft"

# What the loop did. `revised`: a redraft happened and stood up. `revision_refused`: a
# redraft was attempted and did not pass, so the approved draft was kept (ADR 0098) — the
# spend is real and the challenge is unanswered, and both are facts a reader of gate 2 is
# owed. `stood`: the challenge was recorded and shown but fell below the revision
# threshold. `skipped_custom`: the challenge named a user-authored section, which the loop
# never redrafts (ADR 0037 — a platform-initiated redraft would execute under a pinned
# policy content gate 1 never displayed).
DISPOSITION_REVISED: Final = "revised"
DISPOSITION_REVISION_REFUSED: Final = "revision_refused"
DISPOSITION_STOOD: Final = "stood"
DISPOSITION_SKIPPED_CUSTOM: Final = "skipped_custom"


class RevisionNote(Base):
    __tablename__ = "revision_notes"

    id: Mapped[UuidPk]

    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    scope: Mapped[str] = mapped_column(Text, nullable=False)

    # Empty for the plan scope: a plan has no sections to name, and NULL-versus-'' games
    # in a grouping column cost more than they buy.
    section_key: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )

    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[int] = mapped_column(nullable=False)

    # The challenge as the critic stated it, so a reader of `aer lessons` sees what the
    # class means by example rather than by its name alone.
    statement: Mapped[str] = mapped_column(Text, nullable=False)

    disposition: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        CheckConstraint(f"scope IN ('{SCOPE_PLAN}', '{SCOPE_DRAFT}')", name="scope_is_known"),
        CheckConstraint(
            "disposition IN "
            f"('{DISPOSITION_REVISED}', '{DISPOSITION_REVISION_REFUSED}', "
            f"'{DISPOSITION_STOOD}', '{DISPOSITION_SKIPPED_CUSTOM}')",
            name="disposition_is_known",
        ),
        CheckConstraint("severity BETWEEN 1 AND 5", name="severity_is_scored"),
        CheckConstraint("char_length(btrim(dimension)) > 0", name="dimension_is_recorded"),
        Index("ix_revision_notes_job_id", "job_id"),
        # The lessons query: recurrence counted by class, across runs.
        Index("ix_revision_notes_scope_dimension", "scope", "dimension"),
    )

    def __repr__(self) -> str:
        return f"<RevisionNote {self.scope}:{self.dimension} {self.disposition}>"
