"""Every stated validation rule, with a case that passes and a case that fails.

A rule tested only in its failing direction is a rule that might reject everything; one
tested only in its passing direction is a rule that might accept everything. Both
directions or it is not tested.

No database and no clock: :func:`check_limits` takes "today" as an argument precisely so
that "reject a future date" can be asserted at any date without freezing time.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from aer.core.enums import AnalysisMode
from aer.core.schemas.request import (
    MAX_HORIZON_MONTHS,
    EsgSensitivity,
    PortfolioContext,
    RequestLimits,
    ResearchRequestCreate,
    RiskTolerance,
    check_limits,
)

TODAY = date(2026, 7, 28)

VALID: dict[str, object] = {
    "company_name": "Microsoft Corporation",
    "ticker": "MSFT",
    "exchange": "NASDAQ",
    "as_of_date": date(2026, 7, 1),
    "base_currency": "USD",
    "investment_horizon_months": 36,
    "max_cost_gbp": Decimal("2.00"),
}


def build(**overrides: object) -> ResearchRequestCreate:
    return ResearchRequestCreate(**{**VALID, **overrides})


def limits(*, budget: str = "2.50", today: date = TODAY) -> RequestLimits:
    return RequestLimits(today=today, per_run_budget_gbp=Decimal(budget))


def messages(exc: PydanticValidationError) -> str:
    return " ".join(error["msg"] for error in exc.errors())


class TestTicker:
    @pytest.mark.parametrize("value", ["MSFT", "BRK.B", "RIO.L", "BT-A.L", "A", "0P0000XYZ12"])
    def test_accepted(self, value):
        assert build(ticker=value).ticker == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "TOOLONGATICKER",  # 14 characters
            "MS FT",  # space
            "MS_FT",  # underscore
            "MS/FT",
            "<script>",
            "MSFT;DROP",
        ],
    )
    def test_rejected(self, value):
        with pytest.raises(PydanticValidationError):
            build(ticker=value)

    def test_is_uppercased(self):
        # Normalised on the way in, so "msft" and "MSFT" cannot become two different
        # requests for the same company.
        assert build(ticker="  msft  ").ticker == "MSFT"


class TestExchange:
    def test_is_normalised(self):
        # Which exchanges are supported is a universe rule, checked in the service. The
        # schema's job is only to make the value comparable.
        assert build(exchange="nyse american").exchange == "NYSE_AMERICAN"
        assert build(exchange="nyse-american").exchange == "NYSE_AMERICAN"


class TestAsOfDate:
    def test_a_past_date_passes(self):
        assert check_limits(build(as_of_date=date(2020, 1, 1)), limits()) == []

    def test_today_passes(self):
        # The boundary is inclusive: research as at today is ordinary, not an error.
        assert check_limits(build(as_of_date=TODAY), limits()) == []

    def test_a_future_date_is_rejected(self):
        problems = check_limits(build(as_of_date=date(2026, 7, 29)), limits())

        assert [p.field for p in problems] == ["as_of_date"]
        assert "in the future" in problems[0].message

    def test_the_rejection_states_the_date_it_compared_against(self):
        # "Today" is the UTC date, which shortly after local midnight can differ from the
        # operator's. Naming the date turns a baffling rejection into an obvious one.
        problems = check_limits(build(as_of_date=date(2026, 7, 29)), limits())
        assert TODAY.isoformat() in problems[0].message

    def test_the_rule_does_not_read_the_clock(self):
        # The whole reason the rule takes `today` as an argument. If it ever reached for
        # date.today(), this would fail.
        far_future = build(as_of_date=date(2099, 1, 1))
        assert check_limits(far_future, limits(today=date(2100, 1, 1))) == []


class TestCurrency:
    @pytest.mark.parametrize("value", ["GBP", "USD", "EUR", "gbp"])
    def test_supported_codes_pass(self, value):
        assert build(base_currency=value).base_currency == value.upper()

    @pytest.mark.parametrize("value", ["ZZZ", "XYZ", "BTC"])
    def test_an_unsupported_code_is_rejected_with_the_reason(self, value):
        # Rejected because no exchange-rate series is wired up, not because the code is
        # malformed — and the message has to say so or the operator will assume a typo.
        with pytest.raises(PydanticValidationError) as excinfo:
            build(base_currency=value)
        assert "not supported" in messages(excinfo.value)

    @pytest.mark.parametrize("value", ["GB", "GBPP", "12X", "$$$"])
    def test_a_malformed_code_is_rejected(self, value):
        with pytest.raises(PydanticValidationError):
            build(base_currency=value)

    def test_reporting_currency_is_optional(self):
        assert build(reporting_currency=None).reporting_currency is None
        assert build(reporting_currency="").reporting_currency is None


class TestPortfolioWeights:
    def test_current_below_maximum_passes(self):
        context = PortfolioContext(current_weight=Decimal("0.02"), maximum_weight=Decimal("0.05"))
        assert context.current_weight == Decimal("0.02")

    def test_equal_weights_pass(self):
        # At the limit, not over it.
        PortfolioContext(current_weight=Decimal("0.05"), maximum_weight=Decimal("0.05"))

    def test_current_above_maximum_is_rejected(self):
        with pytest.raises(PydanticValidationError) as excinfo:
            PortfolioContext(current_weight=Decimal("0.06"), maximum_weight=Decimal("0.05"))
        assert "exceeds maximum_weight" in messages(excinfo.value)

    @pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("1.01"), Decimal("8")])
    def test_a_weight_outside_zero_to_one_is_rejected(self, value):
        # A weight of 800% would silently poison every portfolio-impact figure downstream.
        with pytest.raises(PydanticValidationError):
            PortfolioContext(current_weight=value)

    def test_both_may_be_absent(self):
        # Someone researching a company they do not own has no weights to give, and
        # demanding a zero would be demanding a fiction.
        assert PortfolioContext().is_empty()

    def test_a_blank_benchmark_is_treated_as_absent(self):
        assert PortfolioContext(benchmark="   ").benchmark is None


class TestHorizon:
    @pytest.mark.parametrize("value", [1, 36, MAX_HORIZON_MONTHS])
    def test_in_range_passes(self, value):
        assert build(investment_horizon_months=value).investment_horizon_months == value

    @pytest.mark.parametrize("value", [0, -1, MAX_HORIZON_MONTHS + 1, 1000])
    def test_out_of_range_is_rejected(self, value):
        with pytest.raises(PydanticValidationError):
            build(investment_horizon_months=value)


class TestMaxCost:
    def test_within_budget_passes(self):
        assert check_limits(build(max_cost_gbp=Decimal("2.50")), limits(budget="2.50")) == []

    def test_zero_is_rejected_by_the_schema(self):
        with pytest.raises(PydanticValidationError):
            build(max_cost_gbp=Decimal("0"))

    def test_negative_is_rejected_by_the_schema(self):
        with pytest.raises(PydanticValidationError):
            build(max_cost_gbp=Decimal("-1"))

    def test_above_the_per_run_budget_is_rejected(self):
        problems = check_limits(build(max_cost_gbp=Decimal("10")), limits(budget="2.50"))

        assert [p.field for p in problems] == ["max_cost_gbp"]
        assert "AER_PER_RUN_BUDGET_GBP" in problems[0].message

    def test_the_budget_comes_from_the_argument_not_the_environment(self):
        payload = build(max_cost_gbp=Decimal("10"))
        assert check_limits(payload, limits(budget="50.00")) == []


class TestIsin:
    @pytest.mark.parametrize(
        "value",
        [
            "US5949181045",  # Microsoft
            "US0378331005",  # Apple
            "GB0002875804",  # British American Tobacco
        ],
    )
    def test_a_real_isin_passes(self, value):
        assert build(isin=value).isin == value

    def test_it_is_optional(self):
        assert build(isin=None).isin is None
        assert build(isin="").isin is None

    def test_a_wrong_check_digit_is_rejected(self):
        # A transposition is the usual way an ISIN is mistyped, and structure alone would
        # not catch it — every character is still in a legal position.
        with pytest.raises(PydanticValidationError) as excinfo:
            build(isin="US5949181046")
        assert "check digit" in messages(excinfo.value)

    @pytest.mark.parametrize("value", ["US594918104", "5949181045US", "US59491810450"])
    def test_a_malformed_isin_is_rejected(self, value):
        with pytest.raises(PydanticValidationError):
            build(isin=value)


class TestExcludedSources:
    @pytest.mark.parametrize(
        ("given", "stored"),
        [
            ("seekingalpha.com", "seekingalpha.com"),
            ("https://seekingalpha.com", "seekingalpha.com"),
            ("https://www.seekingalpha.com/article/123", "seekingalpha.com"),
            ("HTTP://Example.CO.UK:8080/x", "example.co.uk"),
        ],
    )
    def test_a_pasted_url_is_reduced_to_its_host(self, given, stored):
        # Operators paste what they see in the address bar. Storing that verbatim would
        # mean the exclusion never matches anything.
        assert build(excluded_sources=[given]).excluded_sources == [stored]

    def test_duplicates_collapse(self):
        sources = ["seekingalpha.com", "https://www.seekingalpha.com/x"]
        assert build(excluded_sources=sources).excluded_sources == ["seekingalpha.com"]

    @pytest.mark.parametrize("value", ["not a domain", "localhost", "..", "http://"])
    def test_something_that_is_not_a_domain_is_rejected(self, value):
        with pytest.raises(PydanticValidationError):
            build(excluded_sources=[value])


class TestOptionalPreferences:
    def test_enumerated_preferences_parse(self):
        payload = build(
            risk_tolerance="conservative",
            esg_sensitivity="material",
            analysis_mode="quick",
        )
        assert payload.risk_tolerance is RiskTolerance.CONSERVATIVE
        assert payload.esg_sensitivity is EsgSensitivity.MATERIAL
        assert payload.analysis_mode is AnalysisMode.QUICK

    def test_they_are_all_optional(self):
        payload = build()
        assert payload.risk_tolerance is None
        assert payload.esg_sensitivity is None
        assert payload.analysis_mode is AnalysisMode.FULL
        assert payload.point_in_time is True

    def test_an_unknown_value_is_rejected(self):
        with pytest.raises(PydanticValidationError):
            build(risk_tolerance="reckless")

    def test_blank_focus_questions_are_dropped(self):
        assert build(focus_questions=["  ", "Real question?", ""]).focus_questions == [
            "Real question?"
        ]


class TestUnknownFields:
    def test_an_unexpected_field_is_rejected(self):
        # extra="forbid". A client sending `rating: "BUY"` must fail loudly rather than
        # have it silently ignored and assume it took effect.
        with pytest.raises(PydanticValidationError):
            build(rating="BUY")


class TestAllProblemsTogether:
    def test_both_contextual_rules_report_at_once(self):
        # Told one problem at a time, an operator makes one round trip per rule. The
        # configuration loader has the same property for the same reason.
        problems = check_limits(
            build(as_of_date=date(2030, 1, 1), max_cost_gbp=Decimal("99")),
            limits(budget="2.50"),
        )
        assert sorted(p.field for p in problems) == ["as_of_date", "max_cost_gbp"]
