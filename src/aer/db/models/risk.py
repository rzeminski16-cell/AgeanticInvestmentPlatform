"""A scenario the operator stated, and the shocks it is made of (ADR 0106 §3).

**Nothing here is a figure.** A scenario is a claim about what is worth worrying about, and
a shock is the fraction the operator chose; both are the operator's statements, kept so
the page can apply them to the book as at any date and so the risk analyst reads what was
stated rather than what a model chose. The profit and loss a scenario produces is a
recorded calculation over the book's values and these rows, never a column here.

A shock's target is matched against the exposure bands' own classification, so a scenario
about "United Kingdom" reaches exactly what the country band calls United Kingdom. The
``book`` kind reaches every holding and carries no target.

Withdrawn rather than deleted, like a decision: a scenario the operator stopped watching is
still what they once watched.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import ShockKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.portfolio import Portfolio

__all__ = ["RiskScenario", "RiskScenarioShock"]


class RiskScenario(Base):
    """One named scenario, belonging to one book."""

    __tablename__ = "risk_scenarios"

    id: Mapped[UuidPk]

    portfolio_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Who stated it. A scenario is a statement, and a statement has a speaker.
    stated_by: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()
    withdrawn_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))

    portfolio: Mapped[Portfolio] = relationship()
    shocks: Mapped[list[RiskScenarioShock]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="RiskScenarioShock.position",
    )

    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="risk_scenario_name_is_not_blank"),
        Index("ix_risk_scenarios_portfolio_id", "portfolio_id"),
    )

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    def __repr__(self) -> str:
        return f"<RiskScenario {self.name!r}>"


class RiskScenarioShock(Base):
    """One fraction applied to one target, in the order it was stated."""

    __tablename__ = "risk_scenario_shocks"

    id: Mapped[UuidPk]

    scenario_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("risk_scenarios.id", ondelete="CASCADE"), nullable=False
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[ShockKind] = mapped_column(
        SaEnum(ShockKind, name="shock_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    # The band label the shock reaches — a sector's name, a currency code, a country, a
    # ticker — or empty for the whole book.
    target: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # A fraction: -0.2 is a fall of a fifth. Never nil, and never a total loss or worse,
    # because an index that reaches nil has no level left to measure from.
    shock: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    scenario: Mapped[RiskScenario] = relationship(back_populates="shocks")

    __table_args__ = (
        UniqueConstraint("scenario_id", "position", name="uq_risk_scenario_shocks_position"),
        CheckConstraint(
            "shock > -1 AND shock <> 0", name="shock_moves_something_and_leaves_something"
        ),
        CheckConstraint(
            "kind = 'book' OR char_length(btrim(target)) > 0", name="shock_names_its_target"
        ),
    )

    def __repr__(self) -> str:
        return f"<RiskScenarioShock {self.kind.value} {self.target!r} {self.shock}>"
