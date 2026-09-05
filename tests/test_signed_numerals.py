"""A numeral carries its sign, and the two questions asked of it differ in exactly that.

The first live run of the confirmation runbook lost `cash_flow_analysis` and
`capital_allocation` to figures that were right in every digit: "-139,500" in a table of
millions, "-52,546", "-9,307", each a stored cash-flow line the section's claims named. The
scanner read the digits without the sign and the comparison, correctly signed, found no
positive figure to match. The same defect made the agreement metric report "51.8" over a
stored -51.79 and "0.065" over -0.0649 as disagreements it could not tell from a wrong number.

These tests hold the scanner's reading of a sign, the one place the two rules differ — the
numeral rule asks for lineage and reads an unsigned numeral as a magnitude; the agreement
metric asks for the right number and reads the sign — and the ranges and parentheses the
scanner must *not* read as a sign.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.core.figures import numeral_tokens, reads_as
from aer.core.section_output import numeral_context, numerals_in, unsourced_numerals
from aer.eval.observations import CitedFigureObservation
from aer.eval.runtime import cited_figure_agreement


class TestTheScannerReadsASign:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("-139,500", ("-139500",)),
            ("\u221251.8 days", ("-51.8",)),
            ("\u20134", ("-4",)),
            ("negative 51.8 days", ("-51.8",)),
            ("a Negative 3.2% change", ("-3.2",)),
            ("minus 9,307", ("-9307",)),
            ("free cash flow of -139,500 and capex of -52,546", ("-139500", "-52546")),
        ],
    )
    def test_a_minus_glued_to_the_digits_or_said_in_words_is_read(
        self, text: str, expected: tuple[str, ...]
    ) -> None:
        assert numeral_tokens(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # A hyphen between two figures is a range's dash, whatever follows it.
            ("2020-2026", ("2020", "2026")),
            ("12%-14%", ("12", "14")),
            ("between 12 - 14 per cent", ("12", "14")),
            ("2024\u20132026", ("2024", "2026")),
            # Parentheses are a parenthesis in prose, not an accounting negative.
            ("gross margin (67.9 percent)", ("67.9",)),
            # A word that is not a sign leaves the figure unsigned.
            ("negative working capital of $5.2 billion", ("5.2",)),
            # Document references still shed no fragments.
            ("Form 10-K", ("10",)),
        ],
    )
    def test_a_dash_between_figures_and_a_parenthesis_are_not_signs(
        self, text: str, expected: tuple[str, ...]
    ) -> None:
        assert numeral_tokens(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # A unit glued to the digits is how a note writes a multiple or a sum.
            ("Debt to equity of 0.09x is nominal.", ("0.09",)),
            ("Interest cover of roughly 50.9x.", ("50.9",)),
            ("trading at 3.5\u00d7 sales", ("3.5",)),
            ("Revenue of $331,839m for the year.", ("331839",)),
            ("a $12bn programme", ("12",)),
            ("capex of -1.5bn", ("-1.5",)),
            # A fiscal marker is not a unit, so the guard still holds.
            ("FY22Q4", ()),
            ("in FY2026", ()),
        ],
    )
    def test_a_unit_glued_to_the_digits_is_read_and_a_word_is_not(
        self, text: str, expected: tuple[str, ...]
    ) -> None:
        assert numeral_tokens(text) == expected

    def test_the_numeral_rule_and_the_metric_read_the_same_tokens(self) -> None:
        text = "Capital expenditure was -52,546, against -139,500 a year earlier."
        assert numerals_in(text) == {"-52546", "-139500"}
        assert numeral_tokens(text) == ("-52546", "-139500")

    def test_a_negative_json_number_is_read_with_its_sign(self) -> None:
        # `repr(-139500)` is what the content scan reads for a JSON number.
        assert numerals_in(repr(-139500)) == {"-139500"}

    def test_the_refusal_quotes_the_signed_span(self) -> None:
        scanned = "Free cash flow fell to -139,500 in the year."
        assert "-139,500" in numeral_context(scanned, "-139500")


class TestTheLineageQuestion:
    """The numeral rule: does this numeral have lineage in a figure the claims name?"""

    STORED_CASH_FLOW = Decimal("-139500000000")
    STORED_CYCLE = Decimal("-51.790049338678")

    def test_a_signed_figure_row_in_millions_reads_as_the_stored_line(self) -> None:
        content = {"figures": [{"label": "Purchases of investments", "value": "-139,500"}]}
        assert unsourced_numerals(content, [], [self.STORED_CASH_FLOW]) == []

    def test_the_full_signed_value_reads_as_the_stored_line(self) -> None:
        content = {"figures": [{"label": "Purchases", "value": "-139500000000"}]}
        assert unsourced_numerals(content, [], [self.STORED_CASH_FLOW]) == []

    def test_an_unsigned_numeral_reads_as_the_magnitude_of_a_negative_figure(self) -> None:
        """ "A negative cycle of 51.8 days" is the stored -51.79, said as people say it;
        the lineage is exactly the cited calculation."""
        content = {"commentary": "The cash conversion cycle is negative at 51.8 days."}
        assert unsourced_numerals(content, [], [self.STORED_CYCLE]) == []

    def test_a_signed_numeral_over_the_opposite_sign_has_no_lineage(self) -> None:
        content = {"commentary": "Purchases of investments rose to -139,500."}
        assert unsourced_numerals(content, [], [Decimal("139500000000")]) != []

    def test_a_sign_the_claim_carries_covers_the_content(self) -> None:
        statement = "Purchases of investments were -139,500 million."
        content = {"figures": [{"label": "Purchases", "value": "-139,500"}]}
        assert unsourced_numerals(content, [statement], []) == []


class TestTheAgreementQuestion:
    """The metric: does the sentence quote the right number, sign included?"""

    @staticmethod
    def _row(text: str, value: str) -> CitedFigureObservation:
        return CitedFigureObservation(
            name="section/cash_conversion_cycle#1",
            calculation="cash_conversion_cycle",
            value=Decimal(value),
            unit="day",
            text=text,
        )

    @pytest.mark.parametrize(
        "text",
        [
            "The cash conversion cycle was -51.8 days in fiscal 2026.",
            "The cash conversion cycle was \u221251.8 days in fiscal 2026.",
            "The cycle stood at negative 51.8 days in fiscal 2026.",
        ],
    )
    def test_a_signed_quotation_of_a_negative_figure_agrees(self, text: str) -> None:
        result = cited_figure_agreement([self._row(text, "-51.790049338678")])
        assert result.failures == ()

    def test_a_dropped_sign_is_a_disagreement(self) -> None:
        """ "0.065" over a stored -0.0649 is the wrong number: the sign of an accruals
        ratio is the finding."""
        result = cited_figure_agreement(
            [self._row("The accruals ratio was 0.065 in fiscal 2026.", "-0.064857010243")]
        )
        assert len(result.failures) == 1

    def test_a_year_beside_the_figure_does_not_break_agreement(self) -> None:
        result = cited_figure_agreement(
            [self._row("In fiscal 2026 the cycle was -51.8 days.", "-51.790049338678")]
        )
        assert result.failures == ()


class TestReadsAsCarriesTheSwitch:
    def test_signed_by_default(self) -> None:
        assert not reads_as(Decimal("51.8"), Decimal("-51.79"))
        assert reads_as(Decimal("-51.8"), Decimal("-51.79"))

    def test_an_unsigned_numeral_may_read_as_the_magnitude_when_lineage_is_asked(self) -> None:
        assert reads_as(Decimal("51.8"), Decimal("-51.79"), sign_matters=False)

    def test_a_signed_numeral_never_reads_as_the_opposite_sign(self) -> None:
        assert not reads_as(Decimal("-51.8"), Decimal("51.79"), sign_matters=False)
