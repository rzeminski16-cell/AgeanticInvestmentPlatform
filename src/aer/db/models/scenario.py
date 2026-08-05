"""A scenario is a diff over the base case, never a copy of it.

A bear case that copied every assumption and changed two of them would be wrong the moment
somebody corrected the base case's tax rate: the base case would improve and the bear case
would go on using the old figure, silently, for as long as anybody kept reading it. Every
comparison between them would then be measuring the correction as well as the scenario.

So a scenario stores **only what it overrides**. Resolving one reads the confirmed
assumptions and applies its own rows on top, which means a base-case change propagates to
every scenario that did not explicitly disagree with it — and a scenario that *did* disagree
says so in a row with its own justification.

**An override carries a reason of its own.** "Bear case: revenue growth 2%" is not an
argument. "Bear case: revenue growth 2%, because the two largest contracts expire in the
period and neither renewal is signed" is one, and it is what a reviewer needs in order to
disagree with the bear case specifically rather than with pessimism generally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
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
    from aer.db.models.request import ResearchRequest

__all__ = ["Scenario", "ScenarioOverride"]


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[UuidPk]

    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )

    # Nullable and SET NULL, as on `assumptions`: a scenario can be drafted while planning,
    # and deleting a failed run must not erase the reasoning behind its cases.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # `bear`, `base`, `bull`, or whatever this analysis needs. Free text rather than an enum
    # because a sector-specific case — "no regulatory approval" — is a legitimate fourth
    # scenario and should not require a migration.
    key: Mapped[str] = mapped_column(String(64), nullable=False)

    label: Mapped[str] = mapped_column(Text, nullable=False)

    # What this case supposes, in words. Not optional: a scenario is an argument, and one
    # with no stated premise is a column of numbers.
    description: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    request: Mapped[ResearchRequest] = relationship()
    overrides: Mapped[list[ScenarioOverride]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("request_id", "key", name="uq_scenarios_key_per_request"),
        CheckConstraint("char_length(btrim(key)) > 0", name="scenario_key_is_not_blank"),
        CheckConstraint("char_length(btrim(label)) > 0", name="scenario_label_is_not_blank"),
        CheckConstraint(
            "char_length(btrim(description)) > 0", name="scenario_description_is_not_blank"
        ),
        Index("ix_scenarios_request_id", "request_id"),
    )

    def __repr__(self) -> str:
        return f"<Scenario {self.key} ({len(self.overrides)} overrides)>"


class ScenarioOverride(Base):
    """One assumption this scenario disagrees with the base case about."""

    __tablename__ = "scenario_overrides"

    id: Mapped[UuidPk]

    scenario_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )

    # By name rather than by assumption id. An override is a statement about *the discount
    # rate*, and it should survive the base-case assumption row being replaced — which is
    # what an amendment does. A foreign key would either break or silently follow the
    # replacement, and neither is what "the bear case sets the discount rate to 11%" means.
    assumption_name: Mapped[str] = mapped_column(Text, nullable=False)

    value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pure")

    # Why *this case* differs, not why the assumption exists. See the module docstring.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    scenario: Mapped[Scenario] = relationship(back_populates="overrides")

    __table_args__ = (
        # One override per assumption per scenario. Two would be a scenario that disagrees
        # with itself, and whichever row happened to be read last would win.
        UniqueConstraint(
            "scenario_id", "assumption_name", name="uq_scenario_overrides_name_per_scenario"
        ),
        CheckConstraint(
            "char_length(btrim(assumption_name)) > 0", name="override_name_is_not_blank"
        ),
        CheckConstraint(
            "char_length(btrim(justification)) > 0", name="override_justification_is_not_blank"
        ),
        Index("ix_scenario_overrides_scenario_id", "scenario_id"),
    )

    def __repr__(self) -> str:
        return f"<ScenarioOverride {self.assumption_name}={self.value} {self.unit}>"
