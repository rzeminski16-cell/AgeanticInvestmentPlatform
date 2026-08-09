"""The research request: what the operator asked for.

This row is the root of everything downstream — plans, approvals, jobs, and eventually the
report — and it is the primary input to reproducing a run months later.

**It is freely editable until a run exists, and frozen from that moment.** Before a job,
this is a note to itself and correcting a mistyped ticker costs nothing. After one, it is
what a plan was approved against and what evidence was gathered under, so editing it would
not correct the record — it would falsify it. ``as_of_date`` and ``point_in_time``
especially: changing either retrospectively makes the stored evidence inconsistent with
the rules that admitted it, which is precisely the look-ahead bias the platform exists to
prevent.

The rule is enforced in :func:`aer.services.requests.immutable_reason`, not here. A
database constraint cannot see whether a job exists in the way this rule needs to explain
itself, and a trigger would put the rule somewhere nobody reading the service would find
it. See ADR 0014.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SaEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import AnalysisMode, RequestStatus
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.approval import Approval
    from aer.db.models.job import Job
    from aer.db.models.plan import ResearchPlan
    from aer.db.models.source_document import SourceDocument
    from aer.db.models.user import User

__all__ = ["ResearchRequest"]


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class ResearchRequest(Base):
    __tablename__ = "research_requests"

    id: Mapped[UuidPk]
    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # -- Subject -----------------------------------------------------------------------
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(String(12), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12))

    # -- Temporal and currency ---------------------------------------------------------
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reporting_currency: Mapped[str | None] = mapped_column(String(3))

    # -- Mandate -----------------------------------------------------------------------
    investment_horizon_months: Mapped[int] = mapped_column(nullable=False)
    horizon_label: Mapped[str | None] = mapped_column(Text)
    analysis_mode: Mapped[AnalysisMode] = mapped_column(
        _enum(AnalysisMode, "analysis_mode"),
        nullable=False,
        default=AnalysisMode.FULL,
        server_default=AnalysisMode.FULL.value,
    )
    point_in_time: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    # current_weight, maximum_weight, benchmark. JSONB rather than columns because the
    # shape is validated by Pydantic at the API boundary and is likely to grow; the
    # database does not need to understand it to store it faithfully.
    portfolio_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # -- Operator preferences ----------------------------------------------------------
    risk_tolerance: Mapped[str | None] = mapped_column(Text)
    liquidity_constraint_gbp: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    esg_sensitivity: Mapped[str | None] = mapped_column(Text)

    # What the operator specifically wants answered. Carried into the planner so a run
    # addresses the actual question rather than a generic template.
    focus_questions: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    excluded_sources: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # -- Control -----------------------------------------------------------------------
    max_cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("2.50")
    )
    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "request_status"),
        nullable=False,
        default=RequestStatus.DRAFT,
        server_default=RequestStatus.DRAFT.value,
    )

    # Whether the ticker and exchange have been confirmed against a real security by an
    # external lookup. Always false at creation: no outbound call is made while a request
    # is being written, so what is stored is exactly what the operator typed. Ticker
    # resolution arrives with the SEC EDGAR adapter.
    #
    # The column exists now rather than later because everything downstream needs to know
    # whether it is working from a confirmed identity or an unverified string, and a
    # nullable "we did not record it" state for older rows would make that unanswerable.
    resolved: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    # When the operator put this request out of the way, or NULL while it is on the list.
    #
    # **A timestamp rather than a boolean, and nullable rather than defaulted.** "Archived"
    # is an event somebody performed on a date, and the date is the useful half: a list of
    # things hidden six months ago reads differently from one hidden this morning. A
    # boolean would answer only the question the list page asks and none of the questions
    # asked of it afterwards.
    #
    # Archiving is deliberately *not* a `RequestStatus`. Status describes where a request
    # is in the research lifecycle, and being filed away is orthogonal to that — an
    # archived request keeps the status it earned, so restoring it does not have to guess
    # what it used to be.
    archived_at: Mapped[Timestamp | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    # -- Relationships -----------------------------------------------------------------
    user: Mapped[User] = relationship(back_populates="requests")
    plans: Mapped[list[ResearchPlan]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="request", cascade="all, delete-orphan")
    sources: Mapped[list[SourceDocument]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "investment_horizon_months BETWEEN 1 AND 240",
            name="horizon_months_in_range",
        ),
        CheckConstraint("max_cost_gbp > 0", name="max_cost_positive"),
        CheckConstraint("char_length(base_currency) = 3", name="base_currency_iso4217"),
        CheckConstraint(
            "reporting_currency IS NULL OR char_length(reporting_currency) = 3",
            name="reporting_currency_iso4217",
        ),
        # Portfolio weights are fractions in [0, 1] with current <= maximum. Enforced here
        # as well as in the API schema: a weight of 800% would silently poison every
        # portfolio-impact calculation downstream, and the database is the last place that
        # can refuse it.
        CheckConstraint(
            """
            portfolio_context = '{}'::jsonb
            OR (
              (portfolio_context->>'current_weight') IS NULL
              OR (
                (portfolio_context->>'current_weight')::numeric >= 0
                AND (portfolio_context->>'current_weight')::numeric <= 1
              )
            )
            """,
            name="current_weight_is_a_fraction",
        ),
        CheckConstraint(
            """
            portfolio_context = '{}'::jsonb
            OR (
              (portfolio_context->>'maximum_weight') IS NULL
              OR (
                (portfolio_context->>'maximum_weight')::numeric >= 0
                AND (portfolio_context->>'maximum_weight')::numeric <= 1
              )
            )
            """,
            name="maximum_weight_is_a_fraction",
        ),
        Index("ix_research_requests_user_id_created_at", "user_id", text("created_at DESC")),
        Index("ix_research_requests_ticker_as_of_date", "ticker", "as_of_date"),
        # Partial, because the list page's default question is "what is not archived?" and
        # a partial index answers it without carrying the rows it is there to exclude.
        Index(
            "ix_research_requests_live",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("archived_at IS NULL"),
        ),
    )
