"""What the operator added to a slate they were asked to confirm.

Both conditional slates arrive as somebody else's work: the peer set is a model's
proposal resolved against EDGAR, or the deterministic floor underneath it; the theme set
is a model's bounded slate. A person could approve either or refuse it, and could not say
"and this one too" — which is the first thing anybody who knows the company wants to say.

**These rows are additions to a proposal, not confirmations of one.** Adding a theme does
not file the company under it and adding a peer does not put it in the comps table; both
join the slate the gate is about to hash, and the gate's approval is still what makes them
real. That is the whole reason the addition is a row rather than a rewrite of the step's
recorded output: a step's output is what that step produced, and the run's record stops
being a record the moment something else writes into it.

**Neither is trusted as typed.** A theme's key is slugged by the same function the
proposal path uses, so an operator founding a theme and a model founding one cannot
produce two spellings of one identity. A peer names a company row rather than a ticker:
the platform must already hold its filings, which is the rule the deterministic floor
already obeys and the only kind of peer a comps table can use.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

__all__ = ["OperatorPeer", "OperatorTheme"]


class OperatorTheme(Base):
    """One theme the operator put on this run's slate."""

    __tablename__ = "operator_themes"

    id: Mapped[UuidPk]

    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Slugged before it is stored, by `services.themes.slugged` — the same identity rule a
    # model's proposal passes through, so "AI Capex" typed here and "ai-capex" proposed
    # there are one theme rather than two.
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)

    # Required, and for the reason the gate shows every rationale at full length: a theme
    # shapes how every later reader of the library weighs the company, invisibly.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    added_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        UniqueConstraint("job_id", "key", name="uq_operator_themes_job_key"),
        CheckConstraint("char_length(key) > 0", name="operator_theme_has_a_key"),
        CheckConstraint("char_length(label) > 0", name="operator_theme_has_a_label"),
        CheckConstraint("char_length(rationale) > 0", name="operator_theme_has_a_rationale"),
    )


class OperatorPeer(Base):
    """One company the operator put on this run's peer set."""

    __tablename__ = "operator_peers"

    id: Mapped[UuidPk]

    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # **A company this platform already holds, not a ticker to go and resolve.** The web
    # process has no source client and should not have one: only `aer.fetch` reaches the
    # network and acquisition is the worker's. The deterministic floor draws from exactly
    # this pool and skips a candidate with no stored financial facts, because a peer with
    # no period end cannot be aligned against the subject and would be excluded a step
    # later anyway. The operator's addition obeys the rule the floor obeys.
    company_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    added_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        UniqueConstraint("job_id", "company_id", name="uq_operator_peers_job_company"),
        CheckConstraint("char_length(rationale) > 0", name="operator_peer_has_a_rationale"),
    )
