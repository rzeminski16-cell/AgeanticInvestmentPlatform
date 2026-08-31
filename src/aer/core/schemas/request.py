"""The research request schema.

One definition, used by the JSON API, the HTML form and every test. A second copy of
these rules for the GUI would drift, and the direction it drifts is always the same: the
form gets more permissive than the API, and something invalid reaches the database
through the door nobody was testing.

**Where the clock is, and is not.** ``aer.core`` is required to be pure — no I/O, no
globals, no clock reads — because it is the part of the codebase that has to be trivially
testable. Two of the required rules need outside knowledge: ``as_of_date`` must not be in
the future, and ``max_cost_gbp`` must not exceed the configured per-run budget. Rather
than reach for ``date.today()`` and ``get_settings()`` here, those live in
:func:`check_limits`, which takes both as arguments in a :class:`RequestLimits`.

That is not ceremony. It means "is this date in the future?" is a function you can test
at any date you like without freezing a clock, and it keeps the impurity where it
belongs: at the edge, in the service layer, where it is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aer.core.enums import AnalysisMode, RequestStatus

__all__ = [
    "MAX_FOCUS_QUESTIONS",
    "MAX_HORIZON_MONTHS",
    "SUPPORTED_CURRENCIES",
    "TICKER_PATTERN",
    "EsgSensitivity",
    "FieldProblem",
    "PortfolioContext",
    "RequestLimits",
    "ResearchRequestCreate",
    "ResearchRequestRead",
    "ResearchRequestSummary",
    "RiskTolerance",
    "check_limits",
    "cost_above_ceiling",
]

# Uppercase alphanumerics plus dot and hyphen: covers "MSFT", "BRK.B", "RIO.L" and
# "BT-A.L". Anything else is a typo or an attempt to put something else in the field.
TICKER_PATTERN: Final = re.compile(r"\A[A-Z0-9.\-]{1,12}\Z")

MAX_HORIZON_MONTHS: Final = 240
MAX_FOCUS_QUESTIONS: Final = 20
MAX_EXCLUDED_SOURCES: Final = 50

# The value at which a doubled Luhn digit wraps.
_LUHN_WRAP: Final = 10

# ISO 4217 codes the platform can actually work in. Kept to the currencies the supported
# exchanges report in, plus the majors an operator might want a valuation expressed in.
# A code outside this set is not rejected because it is invalid but because no exchange
# rate series is wired up for it, and a valuation in a currency we cannot convert is
# worse than a refusal.
SUPPORTED_CURRENCIES: Final[frozenset[str]] = frozenset(
    {"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "USD"}
)

# A domain, optionally with a leading protocol or path, which is stripped. Operators type
# what they see in a browser; the platform stores the host.
_DOMAIN_PATTERN: Final = re.compile(r"\A[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z]{2,63})+\Z")


class RiskTolerance(StrEnum):
    """How much downside the operator is willing to underwrite.

    Shapes the emphasis of the analysis and the scenario weightings the planner proposes.
    It never sets a rating: that is a conclusion, and a conclusion that follows from a
    preference is not a conclusion.
    """

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class EsgSensitivity(StrEnum):
    """How much weight environmental, social and governance factors should carry."""

    NONE = "none"
    CONSIDERED = "considered"
    MATERIAL = "material"


class PortfolioContext(BaseModel):
    """Where this holding sits, or would sit, in the operator's portfolio.

    Optional in full: someone researching a company they do not own has no weights to
    give, and demanding a zero would be demanding a fiction.
    """

    model_config = ConfigDict(extra="forbid")

    current_weight: Annotated[Decimal | None, Field(ge=0, le=1)] = None
    maximum_weight: Annotated[Decimal | None, Field(ge=0, le=1)] = None
    benchmark: Annotated[str | None, Field(max_length=120)] = None

    @field_validator("benchmark", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _current_within_maximum(self) -> PortfolioContext:
        current = self.current_weight
        maximum = self.maximum_weight
        if current is not None and maximum is not None and current > maximum:
            message = (
                f"current_weight ({current}) exceeds maximum_weight ({maximum}). The "
                "maximum is a limit, so a current holding above it is either a typo or a "
                "breach worth knowing about before commissioning research."
            )
            raise ValueError(message)
        return self

    def is_empty(self) -> bool:
        return (
            self.current_weight is None and self.maximum_weight is None and self.benchmark is None
        )


class ResearchRequestCreate(BaseModel):
    """What the operator is asking for.

    Every rule expressible without knowing the time or the configuration is expressed
    here, so the API and the form cannot disagree about it. The remaining two are in
    :func:`check_limits`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # -- Subject -------------------------------------------------------------------------
    company_name: Annotated[str, Field(min_length=1, max_length=200)]
    ticker: Annotated[str, Field(min_length=1, max_length=12)]
    exchange: Annotated[str, Field(min_length=1, max_length=32)]
    isin: Annotated[str | None, Field(min_length=12, max_length=12)] = None

    # -- Temporal and currency -----------------------------------------------------------
    as_of_date: date
    base_currency: Annotated[str, Field(min_length=3, max_length=3)] = "GBP"
    reporting_currency: Annotated[str | None, Field(min_length=3, max_length=3)] = None

    # -- Mandate -------------------------------------------------------------------------
    investment_horizon_months: Annotated[int, Field(ge=1, le=MAX_HORIZON_MONTHS)]
    horizon_label: Annotated[str | None, Field(max_length=120)] = None
    analysis_mode: AnalysisMode = AnalysisMode.FULL
    point_in_time: bool = True
    portfolio_context: PortfolioContext = Field(default_factory=PortfolioContext)

    # -- Operator preferences ------------------------------------------------------------
    risk_tolerance: RiskTolerance | None = None
    liquidity_constraint_gbp: Annotated[Decimal | None, Field(ge=0)] = None
    esg_sensitivity: EsgSensitivity | None = None
    focus_questions: Annotated[list[str], Field(max_length=MAX_FOCUS_QUESTIONS)] = Field(
        default_factory=list
    )
    excluded_sources: Annotated[list[str], Field(max_length=MAX_EXCLUDED_SOURCES)] = Field(
        default_factory=list
    )

    # -- Control -------------------------------------------------------------------------
    max_cost_gbp: Annotated[Decimal, Field(gt=0)]

    # -- Normalisation and validation ----------------------------------------------------

    @field_validator(
        "isin",
        "horizon_label",
        "reporting_currency",
        "risk_tolerance",
        "esg_sensitivity",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        """Treat an empty form field as unset.

        An HTML form submits every field it renders, so an untouched optional input
        arrives as ``""``. Without this, "not specified" becomes an empty string that
        fails a length rule and produces an error about a field the operator never
        touched.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("ticker", mode="after")
    @classmethod
    def _ticker_is_well_formed(cls, value: str) -> str:
        upper = value.strip().upper()
        if not TICKER_PATTERN.match(upper):
            message = (
                "must be 1-12 characters using only letters, digits, '.' and '-' "
                "(for example MSFT, BRK.B or RIO.L)"
            )
            raise ValueError(message)
        return upper

    @field_validator("exchange", mode="after")
    @classmethod
    def _normalise_exchange(cls, value: str) -> str:
        # Normalised but not restricted here: which exchanges are in scope is a universe
        # rule, and it belongs with the other universe rules so that its refusal can
        # explain itself. See aer.core.universe.
        return value.strip().upper().replace(" ", "_").replace("-", "_")

    @field_validator("base_currency", "reporting_currency", mode="after")
    @classmethod
    def _currency_is_supported(cls, value: str | None) -> str | None:
        if value is None:
            return None
        upper = value.strip().upper()
        if not upper.isalpha():
            message = "must be a three-letter ISO 4217 code, for example GBP or USD"
            raise ValueError(message)
        if upper not in SUPPORTED_CURRENCIES:
            supported = ", ".join(sorted(SUPPORTED_CURRENCIES))
            message = (
                f"{upper} is not supported. No exchange-rate series is configured for it, "
                f"and a valuation in a currency that cannot be converted is worse than a "
                f"refusal. Supported: {supported}"
            )
            raise ValueError(message)
        return upper

    @field_validator("isin", mode="after")
    @classmethod
    def _isin_is_well_formed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        upper = value.strip().upper()
        # Structure only: two-letter country code, nine alphanumerics, one check digit.
        # The check digit itself is verified below, because a transposed character is the
        # most common way an ISIN is mistyped and structure alone would not catch it.
        if not re.match(r"\A[A-Z]{2}[A-Z0-9]{9}[0-9]\Z", upper):
            message = (
                "must be 12 characters: a two-letter country code, nine alphanumerics and "
                "a check digit (for example US5949181045)"
            )
            raise ValueError(message)
        if not _isin_check_digit_is_valid(upper):
            message = f"{upper} has an invalid check digit, which usually means a typo"
            raise ValueError(message)
        return upper

    @field_validator("focus_questions", mode="before")
    @classmethod
    def _clean_questions(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @field_validator("excluded_sources", mode="after")
    @classmethod
    def _clean_domains(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            domain = _domain_of(raw)
            if domain is None:
                message = (
                    f"{raw!r} is not a domain. Give the host only, for example 'seekingalpha.com'."
                )
                raise ValueError(message)
            if domain not in cleaned:
                cleaned.append(domain)
        return cleaned


class ResearchRequestSummary(BaseModel):
    """The list view: enough to choose one, not enough to re-read the whole request."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    ticker: str
    exchange: str
    as_of_date: date
    analysis_mode: AnalysisMode
    status: RequestStatus
    created_at: datetime

    # When the operator filed this away, or null while it is on the list. On the summary
    # rather than only the detail because the listing is where the distinction is acted on,
    # and a client filtering by it should not have to fetch each row to find out.
    archived_at: datetime | None = None


class ResearchRequestRead(ResearchRequestSummary):
    """A persisted request, as returned by the API."""

    isin: str | None
    base_currency: str
    reporting_currency: str | None
    investment_horizon_months: int
    horizon_label: str | None
    point_in_time: bool
    portfolio_context: PortfolioContext
    risk_tolerance: str | None
    liquidity_constraint_gbp: Decimal | None
    esg_sensitivity: str | None
    focus_questions: list[str]
    excluded_sources: list[str]
    max_cost_gbp: Decimal

    # False until an external lookup confirms the ticker maps to a real security. No such
    # lookup happens at request time, by design, so this is always False on creation.
    resolved: bool

    @field_validator("focus_questions", "excluded_sources", mode="before")
    @classmethod
    def _null_array_is_empty(cls, value: Any) -> Any:
        # The columns are nullable arrays; an absent list and an empty one mean the same
        # thing to a reader, and a client should not have to handle both.
        return [] if value is None else value


@dataclass(frozen=True, slots=True)
class RequestLimits:
    """The outside facts the context-dependent rules need.

    Passed in rather than read, so that :func:`check_limits` stays a pure function of its
    arguments and "reject a future as-of date" can be tested at any date without freezing
    a clock.
    """

    today: date
    per_run_budget_gbp: Decimal


@dataclass(frozen=True, slots=True)
class FieldProblem:
    """One rejected field, named so a form can put the message beside the right input.

    ``code`` carries a stable machine-readable identifier where one exists — a universe
    exclusion rule, say — so an API client can branch on the reason rather than matching
    on prose that is free to change.
    """

    field: str
    message: str
    code: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"field": self.field, "message": self.message, "code": self.code}


def check_limits(payload: ResearchRequestCreate, limits: RequestLimits) -> list[FieldProblem]:
    """Apply the rules that depend on the time and the configuration.

    Returns every problem rather than raising on the first, so a form can show all of them
    at once. An empty list means the request passes.
    """
    problems: list[FieldProblem] = []

    if payload.as_of_date > limits.today:
        problems.append(
            FieldProblem(
                field="as_of_date",
                message=(
                    f"{payload.as_of_date.isoformat()} is in the future. Research is "
                    "performed as at a date that has already happened; a future as-of date "
                    f"has no evidence behind it. Today is {limits.today.isoformat()}."
                ),
            )
        )

    above_ceiling = cost_above_ceiling(payload.max_cost_gbp, limits.per_run_budget_gbp)
    if above_ceiling is not None:
        problems.append(above_ceiling)

    return problems


def cost_above_ceiling(cap: Decimal, ceiling: Decimal) -> FieldProblem | None:
    """The one rule that bounds what a report may cost, or ``None`` if ``cap`` is inside it.

    Named and shared because it is asked twice from different directions: when a request
    is written, and when an operator raises the ceiling on a run that is already going
    (:func:`aer.services.requests.raise_cap`). Two copies of a bound are two bounds, and
    the one that drifts is always the one nobody was looking at.
    """
    if cap <= ceiling:
        return None
    return FieldProblem(
        field="max_cost_gbp",
        message=(
            f"£{cap} exceeds the per-run budget of £{ceiling}. Raise "
            "AER_PER_RUN_BUDGET_GBP if you genuinely intend to spend more on a single "
            "report."
        ),
    )


def _domain_of(raw: str) -> str | None:
    """Extract a bare host from whatever the operator pasted, or ``None`` if it is not one."""
    candidate = raw.strip().lower()
    if not candidate:
        return None
    candidate = re.sub(r"\A[a-z][a-z0-9+.\-]*://", "", candidate)
    candidate = candidate.split("/", 1)[0]
    candidate = candidate.split("@")[-1]
    candidate = candidate.split(":", 1)[0]
    candidate = candidate.removeprefix("www.")
    if not _DOMAIN_PATTERN.match(candidate):
        return None
    return candidate


def _isin_check_digit_is_valid(isin: str) -> bool:
    """Verify an ISIN's Luhn check digit over its letter-expanded digits."""
    # A -> 10, B -> 11, ... Z -> 35, then each expanded digit is treated individually.
    digits = "".join(str(ord(char) - 55) if char.isalpha() else char for char in isin)
    total = 0
    # Luhn, applied right to left: every second digit is doubled, and a doubled value
    # of ten or more has its own digits summed, which for a single digit is the same as
    # subtracting nine.
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value >= _LUHN_WRAP:
                value -= _LUHN_WRAP - 1
        total += value
    return total % 10 == 0
