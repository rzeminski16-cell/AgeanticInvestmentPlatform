"""Themes: the cross-company connective tissue of the knowledge graph.

`docs/knowledge-graph.md` K1, ADR 0065. The comparable relation can say "a run of A named
B"; it cannot say *AI capital expenditure* links a hyperscaler, a fab and a utility. A
theme is that link — proposed by a model, confirmed by a person at the ``THEME_SET`` gate,
and only then an edge.

Two tables because a theme and its memberships live different lives. The ``themes`` row is
the shared identity — one key, however many runs name it — which is what stops the graph
filling with near-duplicates nobody could merge: a proposal matching an existing key joins
that theme rather than founding a rival spelling. The membership is one run's confirmed
claim that its subject belongs, carrying the rationale a person read at the gate.

**A membership row is inert until its report is approved.** Rows are written when the
run's report is created, which is before the FINAL gate; the graph and the vault read
memberships only through reports that are ``immutable``. The same shape as peer edges: what
exists in the database and what counts as knowledge are joined by the approval, not by the
insert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.company import Company
    from aer.db.models.report import Report

__all__ = ["Theme", "ThemeMembership"]


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[UuidPk]

    # The identity a proposal matches against, exactly. Slugged lowercase in code before it
    # ever reaches a row, so "AI Capex" and "ai-capex" cannot become two themes.
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # The display name, from whichever confirmed proposal founded the theme. Editing it
    # later renames every note that projects it; the key never changes.
    label: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    memberships: Mapped[list[ThemeMembership]] = relationship(
        back_populates="theme", cascade="all, delete-orphan", passive_deletes=True
    )


class ThemeMembership(Base):
    __tablename__ = "theme_memberships"

    id: Mapped[UuidPk]

    theme_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # CASCADE: destroying a report (the retention flow) takes its theme claims with it.
    # A membership that outlived the run that made it would be an edge with no provenance.
    report_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What the reviewer read at the gate — why this company belongs to this theme,
    # according to the run that proposed it. Kept verbatim; the theme note projects it.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    theme: Mapped[Theme] = relationship(back_populates="memberships")
    company: Mapped[Company] = relationship()
    report: Mapped[Report] = relationship()

    __table_args__ = (
        # One claim per (theme, company, run). A retried report step re-records the same
        # confirmed set; the constraint is what makes that a no-op instead of a duplicate.
        UniqueConstraint("theme_id", "company_id", "report_id", name="uq_theme_memberships_triple"),
    )
