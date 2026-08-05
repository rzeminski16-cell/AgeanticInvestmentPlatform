"""Statements assembled from facts, and the identities reported rather than raised.

The expected figures below are stated as literals worked out by hand. Where a test needs a
filer's tags rather than canonical concepts it goes through
:func:`~aer.core.concepts.canonical_concept`, because the point of several of these tests is
that the mapping is what makes a US filing and a UK filing comparable — asserting on
canonical names the test itself chose would prove nothing about the map.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

import pytest

from aer.calc.engine import CalculationContext
from aer.calc.statements import _IDENTITIES as IDENTITIES
from aer.calc.statements import (
    BALANCE_SHEET_LINES,
    CASH_FLOW_LINES,
    INCOME_STATEMENT_LINES,
    SUPPLEMENTARY_LINES,
    TOLERANCE,
    Statements,
    assemble,
    money_unit_of,
)
from aer.calc.units import (
    Quantity,
    SourceRef,
    UnitMismatchError,
    UnsourcedValueError,
    money,
    shares,
)
from aer.core.concepts import CANONICAL_CONCEPTS, MAGNITUDE_CONCEPTS, canonical_concept

SOURCE = SourceRef.fact("fact-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SOURCE)


def gbp(value: str) -> Quantity:
    return money(value, "GBP", source=SOURCE)


def from_tags(taxonomy: str, tagged: dict[str, str], currency: str) -> dict[str, Quantity]:
    """A filer's tagged figures, mapped through the concept map exactly as a run would.

    A tag the map does not know is dropped here, which is what makes the "unmapped tags reach
    a person" behaviour visible: the statement simply has no line for it.
    """
    facts: dict[str, Quantity] = {}
    for tag, value in tagged.items():
        concept = canonical_concept(taxonomy, tag)
        if concept is not None:
            facts[concept] = money(value, currency, source=SourceRef.fact(f"{taxonomy}:{tag}"))
    return facts


# A balance sheet that closes exactly, in canonical concepts. Assets 1000 = liabilities 600 +
# equity 400; current 300 + non-current 700; current 250 + non-current 350.
BALANCED = {
    "assets": "1000",
    "current_assets": "300",
    "noncurrent_assets": "700",
    "liabilities": "600",
    "current_liabilities": "250",
    "noncurrent_liabilities": "350",
    "equity": "400",
}


def balanced_facts(currency: str = "USD") -> dict[str, Quantity]:
    return {concept: money(value, currency, source=SOURCE) for concept, value in BALANCED.items()}


class TestTheLineLayout:
    def test_every_line_is_a_canonical_concept(self):
        for concept in (
            *INCOME_STATEMENT_LINES,
            *BALANCE_SHEET_LINES,
            *CASH_FLOW_LINES,
            *SUPPLEMENTARY_LINES,
        ):
            assert concept in CANONICAL_CONCEPTS, concept

    def test_every_canonical_concept_has_a_line(self):
        """What makes `unplaced` mean "not a canonical concept" rather than "we forgot"."""
        placed = {
            *INCOME_STATEMENT_LINES,
            *BALANCE_SHEET_LINES,
            *CASH_FLOW_LINES,
            *SUPPLEMENTARY_LINES,
        }
        assert CANONICAL_CONCEPTS - placed == set()

    def test_no_concept_appears_on_two_statements(self):
        lines = [
            *INCOME_STATEMENT_LINES,
            *BALANCE_SHEET_LINES,
            *CASH_FLOW_LINES,
            *SUPPLEMENTARY_LINES,
        ]
        assert len(lines) == len(set(lines))

    def test_no_identity_mixes_in_a_magnitude_concept(self):
        """The identity sums add signed figures. A magnitude in one would need a decision.

        `_total` adds without consulting the sign convention, which is correct only because
        every side of every identity is a stated total or balance. If an identity ever needs
        a concept reported as a positive magnitude — capital expenditure, dividends paid —
        this fails, and the author has to decide the sign rather than inherit a wrong one.
        """
        for identity in IDENTITIES:
            overlap = set(identity.concepts) & MAGNITUDE_CONCEPTS
            assert overlap == set(), f"{identity.name} uses magnitude concepts {overlap}"

    def test_every_identity_names_only_canonical_concepts(self):
        for identity in IDENTITIES:
            for concept in identity.concepts:
                assert concept in CANONICAL_CONCEPTS, f"{identity.name}: {concept}"


class TestAUsFilerAndAUkFilerProduceTheSameLines:
    """The whole reason for a concept map: two taxonomies, one comparable statement."""

    US_TAGS: ClassVar[dict[str, str]] = {
        "Revenues": "1000",
        "CostOfRevenue": "600",
        "OperatingIncomeLoss": "250",
        "IncomeTaxExpenseBenefit": "50",
        "NetIncomeLoss": "200",
        "Assets": "5000",
        "AssetsCurrent": "1500",
        "AssetsNoncurrent": "3500",
        "Liabilities": "3000",
        "LiabilitiesCurrent": "1000",
        "LiabilitiesNoncurrent": "2000",
        "StockholdersEquity": "2000",
        "InventoryNet": "300",
    }

    # The same company, filed under IFRS. Not one tag is spelled the same way.
    IFRS_TAGS: ClassVar[dict[str, str]] = {
        "Revenue": "1000",
        "CostOfSales": "600",
        "ProfitLossFromOperatingActivities": "250",
        "IncomeTaxExpenseContinuingOperations": "50",
        "ProfitLoss": "200",
        "Assets": "5000",
        "CurrentAssets": "1500",
        "NoncurrentAssets": "3500",
        "Liabilities": "3000",
        "CurrentLiabilities": "1000",
        "NoncurrentLiabilities": "2000",
        "Equity": "2000",
        "Inventories": "300",
    }

    def test_the_same_lines_are_present(self, context):
        american = assemble(context, from_tags("us-gaap", self.US_TAGS, "USD"))
        british = assemble(context, from_tags("ifrs-full", self.IFRS_TAGS, "GBP"))

        for left, right in zip(american.statements, british.statements, strict=True):
            assert left.present_concepts == right.present_concepts

    def test_the_same_figures_appear_under_the_same_names(self, context):
        american = assemble(context, from_tags("us-gaap", self.US_TAGS, "USD"))
        british = assemble(context, from_tags("ifrs-full", self.IFRS_TAGS, "GBP"))

        for concept in american.income.present_concepts + american.balance_sheet.present_concepts:
            us_value = american.get(concept)
            uk_value = british.get(concept)
            assert us_value is not None
            assert uk_value is not None
            assert us_value.value == uk_value.value, concept

    def test_the_currency_is_not_lost_in_the_process(self, context):
        """Comparable lines, different money. Nothing here converts, and nothing may."""
        american = assemble(context, from_tags("us-gaap", self.US_TAGS, "USD"))
        british = assemble(context, from_tags("ifrs-full", self.IFRS_TAGS, "GBP"))

        assert str(money_unit_of(american.balance_sheet)) == "USD"
        assert str(money_unit_of(british.balance_sheet)) == "GBP"

    def test_both_filings_close_their_identities(self, context):
        for taxonomy, tags, currency in (
            ("us-gaap", self.US_TAGS, "USD"),
            ("ifrs-full", self.IFRS_TAGS, "GBP"),
        ):
            statements = assemble(context, from_tags(taxonomy, tags, currency))
            assert statements.failed_identities == ()


class TestAMissingLineIsAbsentNotZero:
    def test_an_unreported_line_has_no_quantity(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        inventory = next(
            line for line in statements.balance_sheet.lines if line.concept == "inventory"
        )
        assert inventory.present is False
        assert inventory.quantity is None
        assert inventory.value is None

    def test_an_absent_line_says_why(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        inventory = next(
            line for line in statements.balance_sheet.lines if line.concept == "inventory"
        )
        assert "not reported" in inventory.absent_because
        assert "concept map" in inventory.absent_because

    def test_a_present_line_gives_no_reason(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        revenue = next(line for line in statements.income.lines if line.concept == "revenue")
        assert revenue.absent_because == ""

    def test_zero_is_a_reported_zero_and_stays_distinguishable(self, context):
        """A filer stating zero and a filer stating nothing must not look the same."""
        statements = assemble(context, {"inventory": usd("0")})
        inventory = next(
            line for line in statements.balance_sheet.lines if line.concept == "inventory"
        )
        assert inventory.present is True
        assert inventory.value == Decimal(0)

    def test_a_line_the_filer_did_not_report_does_not_enter_an_identity(self, context):
        """The check reports what it lacked rather than treating the gap as zero."""
        statements = assemble(context, {"assets": usd("1000"), "liabilities": usd("600")})
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.ran is False
        assert check.holds is False
        assert check.missing == ("equity",)

    def test_the_order_of_lines_is_presentation_order_not_the_order_facts_arrived(self, context):
        facts = {"net_income": usd("200"), "revenue": usd("1000"), "gross_profit": usd("400")}
        statements = assemble(context, facts)
        concepts = [line.concept for line in statements.income.lines]
        assert concepts == list(INCOME_STATEMENT_LINES)


class TestDerivedSubtotals:
    def test_gross_profit_is_derived_when_the_filer_did_not_state_it(self, context):
        statements = assemble(context, {"revenue": usd("1000"), "cost_of_revenue": usd("600")})
        line = next(line for line in statements.income.lines if line.concept == "gross_profit")
        assert line.value == Decimal(400)
        assert line.derived is True

    def test_a_stated_subtotal_is_never_overwritten(self, context):
        """The filer's own figure wins. A disagreement is for the identity check to report."""
        facts = {
            "revenue": usd("1000"),
            "cost_of_revenue": usd("600"),
            "gross_profit": usd("390"),
        }
        statements = assemble(context, facts)
        line = next(line for line in statements.income.lines if line.concept == "gross_profit")
        assert line.value == Decimal(390)
        assert line.derived is False

        check = next(c for c in statements.identities if c.name == "gross profit")
        assert check.ran is True
        assert check.holds is False
        assert check.difference == Decimal(-10)

    def test_pre_tax_income_adds_the_tax_charge_back(self, context):
        """Tax is a positive charge under the sign convention, so the pre-tax figure is a sum."""
        statements = assemble(context, {"net_income": usd("200"), "income_tax_expense": usd("50")})
        line = next(line for line in statements.income.lines if line.concept == "pre_tax_income")
        assert line.value == Decimal(250)
        assert line.derived is True

    def test_total_debt_is_the_sum_of_its_two_maturities(self, context):
        statements = assemble(
            context, {"short_term_debt": usd("100"), "long_term_debt": usd("900")}
        )
        line = next(line for line in statements.balance_sheet.lines if line.concept == "total_debt")
        assert line.value == Decimal(1000)
        assert line.derived is True

    def test_a_derivation_needs_both_components(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        line = next(line for line in statements.income.lines if line.concept == "gross_profit")
        assert line.present is False

    def test_a_derivation_is_recorded_in_the_calculation_ledger(self, context):
        assemble(context, {"revenue": usd("1000"), "cost_of_revenue": usd("600")})
        names = [record.name for record in context.records]
        assert "subtotal_difference" in names

    def test_a_derivation_carries_the_provenance_of_its_inputs(self, context):
        statements = assemble(context, {"revenue": usd("1000"), "cost_of_revenue": usd("600")})
        gross = statements.get("gross_profit")
        assert gross is not None
        assert gross.source is not None
        assert gross.source.kind == "calculation"

    def test_an_unsourced_fact_is_refused_by_the_derivation(self, context):
        """Invariant 3 at the point it would be broken, not at the point it would be noticed."""
        facts = {"revenue": money("1000", "USD"), "cost_of_revenue": usd("600")}
        with pytest.raises(UnsourcedValueError):
            assemble(context, facts)

    def test_a_derivation_across_two_currencies_raises(self, context):
        """Invariant 5. Adding dollars to pounds is not a diagnostic, it is a bug."""
        facts = {"revenue": usd("1000"), "cost_of_revenue": gbp("600")}
        with pytest.raises(UnitMismatchError):
            assemble(context, facts)


class TestTheIdentitiesAreOutputNotAssertions:
    def test_a_balance_sheet_that_closes_says_so(self, context):
        statements = assemble(context, balanced_facts())
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.ran is True
        assert check.holds is True
        assert check.difference == Decimal(0)
        assert statements.failed_identities == ()

    def test_a_balance_sheet_that_does_not_close_is_reported_not_raised(self, context):
        broken = {**BALANCED, "equity": "300"}
        statements = assemble(
            context, {c: money(v, "USD", source=SOURCE) for c, v in broken.items()}
        )

        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.ran is True
        assert check.holds is False
        assert check.difference == Decimal(100)
        assert check in statements.failed_identities
        assert "DOES NOT HOLD" in check.describe()

    def test_rounding_within_tolerance_still_closes(self, context):
        """A filing rounded to the nearest million must not be reported as broken."""
        rounded = {**BALANCED, "equity": "400.4"}
        statements = assemble(
            context, {c: money(v, "USD", source=SOURCE) for c, v in rounded.items()}
        )
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.relative < TOLERANCE
        assert check.holds is True

    def test_a_miss_just_beyond_tolerance_does_not_close(self, context):
        # 1000 against 1002: two parts in a thousand, twice the tolerance.
        beyond = {**BALANCED, "equity": "402"}
        statements = assemble(
            context, {c: money(v, "USD", source=SOURCE) for c, v in beyond.items()}
        )
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.holds is False

    def test_the_size_of_the_miss_is_kept_not_just_the_verdict(self, context):
        broken = {**BALANCED, "equity": "300"}
        statements = assemble(
            context, {c: money(v, "USD", source=SOURCE) for c, v in broken.items()}
        )
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.difference == Decimal(100)
        assert check.relative == Decimal(100) / Decimal(1000)

    def test_an_identity_it_could_not_run_is_not_an_identity_that_passed(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        for check in statements.identities:
            assert check.ran is False
            assert check.holds is False
        assert statements.failed_identities == ()
        assert len(statements.unchecked_identities) == len(statements.identities)

    def test_an_unrunnable_check_says_what_it_lacked(self, context):
        statements = assemble(context, {"assets": usd("1000")})
        check = next(c for c in statements.identities if c.name == "assets split")
        assert set(check.missing) == {"current_assets", "noncurrent_assets"}
        assert "not checked" in check.describe()

    def test_mixed_units_make_a_check_unrunnable_rather_than_wrong(self, context):
        """Two lines of one balance sheet in different currencies is a mapping error.

        Reported as a check that could not run, because the operator needs to see which line
        is in the wrong currency. Nothing coerces — invariant 5 holds either way.
        """
        facts = {"assets": usd("1000"), "liabilities": gbp("600"), "equity": usd("400")}
        statements = assemble(context, facts)
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.ran is False
        assert any("GBP" in reason for reason in check.missing)
        assert any("USD" in reason for reason in check.missing)

    def test_both_sides_zero_closes_exactly(self, context):
        """The scale is zero, so a relative miss is undefined. Zero equals zero regardless."""
        facts = {"assets": usd("0"), "liabilities": usd("0"), "equity": usd("0")}
        statements = assemble(context, facts)
        check = next(c for c in statements.identities if c.name == "balance sheet balances")
        assert check.ran is True
        assert check.relative is None
        assert check.holds is True

    def test_every_identity_is_checked_when_every_input_is_present(self, context):
        facts = {
            **balanced_facts(),
            "short_term_debt": usd("100"),
            "long_term_debt": usd("500"),
            "total_debt": usd("600"),
            "revenue": usd("2000"),
            "cost_of_revenue": usd("1200"),
            "gross_profit": usd("800"),
            "pre_tax_income": usd("250"),
            "income_tax_expense": usd("50"),
            "net_income": usd("200"),
            "net_change_in_cash": usd("70"),
            "operating_cash_flow": usd("300"),
            "investing_cash_flow": usd("-150"),
            "financing_cash_flow": usd("-80"),
        }
        statements = assemble(context, facts)
        assert statements.unchecked_identities == ()
        assert statements.failed_identities == ()


class TestUnplacedFacts:
    def test_a_key_no_line_carries_is_surfaced_rather_than_dropped(self, context):
        statements = assemble(context, {"revenue": usd("1000"), "AssetsCurrent": usd("300")})
        assert statements.unplaced == ("AssetsCurrent",)

    def test_a_canonical_concept_is_never_unplaced(self, context):
        facts = {concept: usd("1") for concept in sorted(CANONICAL_CONCEPTS)}
        statements = assemble(context, facts)
        assert statements.unplaced == ()

    def test_share_counts_land_in_the_supplementary_block(self, context):
        facts = {"shares_outstanding": shares("1000", source=SOURCE)}
        statements = assemble(context, facts)
        assert statements.supplementary.get("shares_outstanding") is not None
        assert statements.unplaced == ()


class TestCoverageAndCurrency:
    def test_coverage_is_the_share_of_lines_that_arrived(self, context):
        statements = assemble(context, balanced_facts())
        assert statements.balance_sheet.coverage == Decimal(7) / Decimal(len(BALANCE_SHEET_LINES))

    def test_coverage_counts_a_derived_line_as_present(self, context):
        statements = assemble(context, {"revenue": usd("1000"), "cost_of_revenue": usd("600")})
        assert "gross_profit" in statements.income.present_concepts

    def test_absent_and_present_concepts_together_are_every_line(self, context):
        statements = assemble(context, balanced_facts())
        sheet = statements.balance_sheet
        assert set(sheet.present_concepts) | set(sheet.absent_concepts) == set(BALANCE_SHEET_LINES)

    def test_per_share_lines_do_not_decide_the_statement_currency(self, context):
        """Earnings per share is USD/shares. It is not what the statement is stated in."""
        facts = {
            "revenue": usd("1000"),
            "earnings_per_share_basic": usd("2") / shares("1", source=SOURCE),
        }
        statements = assemble(context, facts)
        assert str(money_unit_of(statements.income)) == "USD"

    def test_disagreeing_currencies_give_no_answer_rather_than_a_guess(self, context):
        facts = {"revenue": usd("1000"), "impairment": gbp("10")}
        statements = assemble(context, facts)
        assert money_unit_of(statements.income) is None

    def test_a_statement_with_nothing_monetary_has_no_currency(self, context):
        statements = assemble(context, {"shares_outstanding": shares("1000", source=SOURCE)})
        assert money_unit_of(statements.supplementary) is None


class TestTheUkFrcSubtotalIsNotMapped:
    """`TotalAssetsLessCurrentLiabilities` is not non-current assets, and must not become it.

    The Companies Act format's subtotal is fixed assets plus current assets less creditors
    falling due within one year. Mapped to the nearest-looking concept, it would put a figure
    wrong by (current assets less current liabilities) onto a balance sheet that still appeared
    to balance, because the identity it would break is the assets split — which most filings
    using this format do not report enough tags to run.
    """

    def test_the_tag_is_unmapped(self):
        assert canonical_concept("uk-core", "TotalAssetsLessCurrentLiabilities") is None

    def test_so_it_reaches_an_operator_rather_than_a_statement(self, context):
        tagged = {
            "TurnoverRevenue": "1000",
            "ProfitLossForPeriod": "200",
            "TotalAssetsLessCurrentLiabilities": "3000",
        }
        statements = assemble(context, from_tags("uk-core", tagged, "GBP"))
        assert statements.balance_sheet.get("noncurrent_assets") is None
        assert statements.get("revenue") is not None


class TestTheStatementSetReadsBackWhatWentIn:
    def test_get_finds_a_line_on_any_statement(self, context):
        facts = {
            "revenue": usd("1000"),
            "assets": usd("5000"),
            "operating_cash_flow": usd("300"),
        }
        statements = assemble(context, facts)
        assert statements.get("revenue").value == Decimal(1000)
        assert statements.get("assets").value == Decimal(5000)
        assert statements.get("operating_cash_flow").value == Decimal(300)

    def test_get_returns_none_for_a_line_that_is_not_there(self, context):
        statements = assemble(context, {"revenue": usd("1000")})
        assert statements.get("goodwill") is None
        assert statements.get("not_a_concept_at_all") is None

    def test_each_statement_knows_which_one_it_is(self, context):
        statements = assemble(context, {})
        assert statements.income.kind is Statements.INCOME
        assert statements.balance_sheet.kind is Statements.BALANCE_SHEET
        assert statements.cash_flow.kind is Statements.CASH_FLOW
        assert statements.supplementary.kind is Statements.SUPPLEMENTARY

    def test_assembling_nothing_gives_empty_statements_rather_than_failing(self, context):
        statements = assemble(context, {})
        for statement in statements.statements:
            assert statement.present_concepts == ()
        assert statements.failed_identities == ()
        assert len(statements.unchecked_identities) == len(IDENTITIES)

    def test_assembling_does_not_mutate_the_facts_it_was_given(self, context):
        facts = {"revenue": usd("1000"), "cost_of_revenue": usd("600")}
        assemble(context, facts)
        assert set(facts) == {"revenue", "cost_of_revenue"}
