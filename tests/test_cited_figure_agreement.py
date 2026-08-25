"""A claim that names a calculation must quote that calculation's figure.

Gap R19, and invariant 3's missing half. Every other check asks whether a figure is
*recorded* correctly — replayed consistently, cited admissibly, dated honestly — and none
reads the sentence. So the 2026-08-24 MSFT note could cite `quick_ratio` and then write a
different number, with `numerical_consistency`, `citation_accuracy` and
`figure_plausibility` all green. The adversarial reviewer caught it; nothing deterministic
did.

The corpus below is that run: its real drafted sentences against its real recorded values,
plus the renderings the platform legitimately produces, because a check that fires on
"46.8%" over a stored 0.4676 would be switched off within a week and would deserve to be.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.eval.metrics import EmptyCorpusError, Metric
from aer.eval.observations import CitedFigureObservation
from aer.eval.runtime import cited_figure_agreement


def observed(
    text: str, value: str, *, unit: str = "pure", name: str = "quick_ratio"
) -> CitedFigureObservation:
    return CitedFigureObservation(
        name=f"balance_sheet_liquidity/{name}#1",
        text=text,
        calculation=name,
        value=Decimal(value),
        unit=unit,
    )


class TestTheFailureItWasBuiltFor:
    """The four figures the red team found, as the run actually drafted them."""

    @pytest.mark.parametrize(
        ("text", "recorded"),
        [
            ("The company reports a quick ratio of 0.93.", "1.5670"),
            ("A current ratio of 1.23 leaves little slack.", "1.7850"),
            ("Debt to equity of 0.09x is nominal.", "0.2990"),
            ("Interest cover of roughly 50.9x.", "40.4000"),
        ],
    )
    def test_a_drafted_figure_the_calculation_does_not_hold_is_a_violation(
        self, text: str, recorded: str
    ) -> None:
        result = cited_figure_agreement((observed(text, recorded),))

        assert result.value == Decimal(1)
        assert not result.passed
        assert recorded in result.failures[0]

    def test_the_failure_names_the_calculation_and_both_figures(self) -> None:
        """An operator reading the coverage notice must be able to act without the ledger."""
        result = cited_figure_agreement(
            (observed("The company reports a quick ratio of 0.93.", "1.5670"),)
        )

        (failure,) = result.failures
        assert "quick_ratio" in failure
        assert "1.5670" in failure
        assert "0.93" in failure


class TestTheRenderingsThePlatformActuallyProduces:
    """None of these is the figure being wrong, and a check that said so would be useless."""

    def test_a_percentage_is_the_stored_fraction_times_a_hundred(self) -> None:
        # `display._pure_reading` scales a percent-word label by 100 and keeps one decimal.
        rows = (observed("An operating margin of 46.8%.", "0.4676966842354274"),)

        assert cited_figure_agreement(rows).passed

    def test_a_multiple_rounds_to_the_two_places_it_is_printed_at(self) -> None:
        # 0.0857 prints as "0.09x". A relative tolerance loose enough to accept that would
        # accept half the errors worth catching; the draft's own precision is the rule.
        rows = (observed("Debt to equity of 0.09x.", "0.0857"),)

        assert cited_figure_agreement(rows).passed

    def test_money_written_longhand_in_billions_agrees_with_the_stored_units(self) -> None:
        rows = (
            observed(
                "Free cash flow of $157.7 billion.",
                "157690000000",
                unit="USD",
                name="free_cash_flow",
            ),
        )

        assert cited_figure_agreement(rows).passed

    def test_the_same_figure_in_millions_agrees_too(self) -> None:
        """The house style renders money in millions; a drafter may follow it or not."""
        rows = (
            observed(
                "Revenue of $331,839m for the year.",
                "331839000000",
                unit="USD",
                name="revenue_total",
            ),
        )

        assert cited_figure_agreement(rows).passed

    def test_a_figure_stated_at_full_precision_agrees_with_itself(self) -> None:
        rows = (observed("A quick ratio of 1.567.", "1.5670"),)

        assert cited_figure_agreement(rows).passed


class TestWhatIsDeliberatelyNotAViolation:
    def test_a_claim_resting_on_a_calculation_without_printing_it(self) -> None:
        """Plenty of sentences cite a figure they do not quote.

        Failing those would make the metric fire on good prose until somebody switched it
        off, which is the way a blocking check dies.
        """
        rows = (observed("Liquidity has tightened over the period.", "1.5670"),)

        assert cited_figure_agreement(rows).passed
        assert cited_figure_agreement(rows).value == Decimal(0)

    def test_a_sentence_carrying_other_figures_as_well_as_its_own(self) -> None:
        """Any numeral matching is enough. A sentence naming one calculation and quoting
        three figures is ordinary prose, not three claims."""
        rows = (
            observed(
                "A quick ratio of 1.57 against a current ratio of 1.79 and 2 years of data.",
                "1.5670",
            ),
        )

        assert cited_figure_agreement(rows).passed

    def test_a_year_in_the_sentence_does_not_rescue_a_wrong_ratio(self) -> None:
        """The other direction of the same rule: an unrelated numeral must not pass it."""
        rows = (observed("In FY2026 the quick ratio was 0.93.", "1.5670"),)

        assert not cited_figure_agreement(rows).passed


class TestTheMetricItself:
    def test_a_run_with_no_cited_figures_is_not_exercised_rather_than_passed(self) -> None:
        """A gate that passes when its population disappears has stopped testing anything."""
        with pytest.raises(EmptyCorpusError):
            cited_figure_agreement(())

    def test_the_threshold_is_zero(self) -> None:
        """This is invariant 3, not a quality score. One is one too many."""
        result = cited_figure_agreement((observed("A quick ratio of 1.57.", "1.5670"),))

        assert result.metric is Metric.CITED_FIGURE_AGREEMENT
        assert result.threshold == Decimal(0)

    def test_every_violation_is_reported_rather_than_the_first(self) -> None:
        rows = (
            observed("A quick ratio of 0.93.", "1.5670"),
            observed("A current ratio of 1.23.", "1.7850", name="current_ratio"),
        )

        result = cited_figure_agreement(rows)

        assert result.value == Decimal(2)
        assert len(result.failures) == 2
