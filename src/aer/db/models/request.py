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

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
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

from aer.core.enums import AnalysisMode
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.plan import ResearchPlan
    from aer.db.models.work_order import WorkOrder

__all__ = ["ResearchRequest"]


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class ResearchRequest(Base):
    __tablename__ = "research_requests"

    # **The work order's id, shared.** A detail row takes its supertype's key (ADR 0072),
    # so this is not merely equal to `work_orders.id` — it is that id, which is what lets
    # every caller reach the mandate from a run without a join and lets a run reach nothing
    # at all when it is not about a company.
    id: Mapped[UuidPk] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), primary_key=True
    )

    # -- Subject -----------------------------------------------------------------------
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(String(12), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12))

    # -- Currency ------------------------------------------------------------------------
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

    # Which company this request is about, once `acquire` has resolved the ticker against a
    # registry and upserted the row. NULL until then, and that is a real state: a request is
    # written from a string somebody typed, and no company exists for it yet.
    #
    # **The reason this is a column and not a lookup.** Every consumer of a fact needs to
    # know whose fact it is, and the alternative — matching `Company.ticker` and
    # `Company.exchange` back to the request's strings — is a weaker key that a re-used or
    # re-listed ticker defeats silently. One authoritative id, written once by the step that
    # resolved it, is what makes "scoped to the subject" checkable in SQL (ADR 0061).
    #
    # SET NULL rather than CASCADE: deleting a company must not delete the record that
    # somebody asked about it.
    company_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    # -- Relationships -----------------------------------------------------------------
    #
    # **The run root, reached by the shared key.** Who asked, what the run may spend, what
    # date its evidence is judged against and whether it is archived are properties of a
    # *run* rather than of an equity report (ADR 0072), and they live on exactly one table
    # now. `request.work_order.as_of_date` is a join the reader can see; the alternative was
    # a second copy kept in step by one function remembering to write both, which is the
    # shape of every defect this expansion turned up.
    #
    # ``lazy="joined"``, which is unusual here and deliberate. Reading a mandate's run-root
    # fields is what forty call sites do, this session is async, and an async lazy load is
    # not a slow path — it raises. One join on a primary key, always, beats a class of
    # failure that only appears where somebody forgot a `selectinload`.
    work_order: Mapped[WorkOrder] = relationship(back_populates="request", lazy="joined")
    plans: Mapped[list[ResearchPlan]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "investment_horizon_months BETWEEN 1 AND 240",
            name="horizon_months_in_range",
        ),
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
        # The list page's questions — whose, and not archived — are asked of the *work
        # order* now, and its own indexes answer them. What is left here is the one lookup
        # that is genuinely about the mandate: which requests were about this ticker.
        Index("ix_research_requests_ticker", "ticker"),
    )
