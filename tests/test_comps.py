"""Multiples: what they mean, and the ways one can be a number that compares nothing.

Task 30's acceptance criterion is that **every multiple names its basis and its date**, and
`TestAMultipleIsNotJustANumber` is that criterion. The rest is the ways a table of correct
arithmetic can still be wrong.

A negative denominator produces a negative multiple, which sorted into a table reads as the
cheapest company in it. A per-share price over a whole-company earnings figure produces a
number roughly the share count too small. A peer whose year ends in March against one ending
in December compares three months of a different economy. A mean over a peer set containing
one company at 140x describes none of them. None of those is an arithmetic error and every one
of them is a wrong answer.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aer.calc import dcf
from aer.calc.comps import (
    IMPLAUSIBLE_MULTIPLE,
    MAX_PERIOD_DRIFT_DAYS,
    MULTIPLE_DEFINITIONS,
    Audience,
    CompsTable,
    MultipleBasis,
    MultipleResult,
    PeerRow,
    WithheldComps,
    align_peers,
    market_enterprise_value,
    median_multiple,
    multiple,
    multiples_for,
    percentile_rank,
)
from aer.calc.engine import CalculationContext
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
)

PERIOD_END = date(2024, 6, 30)
AS_OF = date(2024, 6, 28)
SOURCE = SourceRef.fact("test-fact")
USD = Unit.currency("USD")
PER_SHARE = USD / Unit.base("shares")


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def money(value: str, unit: Unit = USD) -> Quantity:
    return Quantity.of(Decimal(value), unit, source=SOURCE)


def pure(value: str | Decimal) -> Quantity:
    number = value if isinstance(value, Decimal) else Decimal(value)
    return Quantity.of(number, DIMENSIONLESS, source=SOURCE)


def healthy_inputs() -> dict[str, Quantity]:
    return {
        "enterprise_value": money("1200"),
        "ebitda": money("100"),
        "revenue": money("500"),
        "price_per_share": money("50", PER_SHARE),
        "earnings_per_share": money("2.5", PER_SHARE),
        "tangible_book_value_per_share": money("20", PER_SHARE),
        "ffo_per_share": money("4", PER_SHARE),
    }


# -- The basis --------------------------------------------------------------------------------


class TestAMultipleIsNotJustANumber:
    """Task 30's acceptance criterion: every multiple names its basis and its date."""

    def test_every_result_carries_a_basis_and_a_period(self, context):
        results = multiples_for(
            context,
            inputs=healthy_inputs(),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )

        assert len(results) == len(MULTIPLE_DEFINITIONS)
        for row in results:
            assert row.basis is MultipleBasis.TRAILING_TWELVE_MONTHS
            assert row.period_end == PERIOD_END

    def test_the_description_carries_both(self, context):
        results = multiples_for(
            context,
            inputs=healthy_inputs(),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )
        described = next(row for row in results if row.key == "ev_ebitda").describe()

        assert "12.0x" in described
        assert "ttm" in described
        assert "2024-06-30" in described

    def test_the_basis_reaches_the_calculation_record(self, context):
        """A trailing and a forward multiple are different numbers for the same company."""
        multiple(
            context,
            numerator=money("1200"),
            denominator=money("100"),
            basis=MultipleBasis.FORWARD,
        )

        record = next(r for r in context.records if r.name == "multiple")
        assert record.parameters["basis"] is MultipleBasis.FORWARD

    def test_there_is_no_way_to_compute_one_without_a_basis(self, context):
        """`basis` is keyword-only and has no default. That is the whole guarantee.

        A default would let a caller omit it and get a trailing multiple silently — and the
        record would then say "ttm" about a figure nobody chose the basis for.
        """
        with pytest.raises(TypeError):
            multiple(  # type: ignore[call-arg]
                context, numerator=money("1200"), denominator=money("100")
            )

    def test_the_table_cannot_be_built_without_one_either(self, context):
        with pytest.raises(TypeError):
            multiples_for(  # type: ignore[call-arg]
                context, inputs=healthy_inputs(), period_end=PERIOD_END
            )

    def test_a_free_text_basis_is_refused(self, context):
        """Typed callers cannot do this; untyped ones can, and the record would be a string."""
        with pytest.raises(CalculationError):
            multiple(
                context,
                numerator=money("1200"),
                denominator=money("100"),
                basis="trailing",  # type: ignore[arg-type]
            )


class TestEveryMultipleIsDimensionless:
    def test_a_currency_over_a_currency_is_a_pure_number(self, context):
        result = multiple(
            context,
            numerator=money("1200"),
            denominator=money("100"),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
        )

        assert result.value == Decimal(12)
        assert result.unit == DIMENSIONLESS

    def test_a_per_share_price_over_per_share_earnings_is_too(self, context):
        result = multiple(
            context,
            numerator=money("50", PER_SHARE),
            denominator=money("2.5", PER_SHARE),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
        )

        assert result.value == Decimal(20)
        assert result.unit == DIMENSIONLESS

    def test_a_total_over_a_per_share_figure_is_refused(self, context):
        """The figure would be wrong by the share count and would look entirely ordinary."""
        with pytest.raises(UnitMismatchError) as excinfo:
            multiple(
                context,
                numerator=money("1200"),
                denominator=money("2.5", PER_SHARE),
                basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            )
        assert "pure number" in str(excinfo.value)

    def test_two_currencies_are_refused(self, context):
        with pytest.raises(UnitMismatchError):
            multiple(
                context,
                numerator=money("1200", Unit.currency("GBP")),
                denominator=money("100"),
                basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            )


# -- Not meaningful ---------------------------------------------------------------------------


class TestANonPositiveDenominatorHasNoMultiple:
    """The most important guard here: a negative multiple sorts as the cheapest peer."""

    @pytest.mark.parametrize("denominator", ["-100", "0"])
    def test_it_is_refused_by_the_calculation(self, context, denominator):
        with pytest.raises(CalculationError) as excinfo:
            multiple(
                context,
                numerator=money("1200"),
                denominator=money(denominator),
                basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            )
        assert "not a multiple" in str(excinfo.value)

    def test_the_table_reports_it_rather_than_raising(self, context):
        inputs = healthy_inputs() | {"ebitda": money("-40")}

        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )
        ev_ebitda = next(row for row in results if row.key == "ev_ebitda")

        assert ev_ebitda.present is False
        assert ev_ebitda.value is None
        assert ev_ebitda.absent_because

    def test_the_reason_reaches_the_reader(self, context):
        inputs = healthy_inputs() | {"ebitda": money("-40")}
        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )

        described = next(row for row in results if row.key == "ev_ebitda").describe()
        assert "not meaningful" in described

    def test_the_other_multiples_still_compute(self, context):
        """One loss-making line does not empty the table."""
        inputs = healthy_inputs() | {"ebitda": money("-40")}
        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )

        assert next(row for row in results if row.key == "ev_sales").present
        assert next(row for row in results if row.key == "pe").present


class TestAMissingInputIsADifferentStateFromANegativeOne:
    def test_it_names_the_concepts_the_filing_lacks(self, context):
        inputs = healthy_inputs()
        del inputs["ffo_per_share"]

        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )
        p_ffo = next(row for row in results if row.key == "p_ffo")

        assert p_ffo.missing == ("ffo_per_share",)
        assert "does not report" in p_ffo.absent_because

    def test_a_negative_denominator_names_no_missing_concept(self, context):
        """The filing reported it. It is the figure that is unusable, not the tagging."""
        inputs = healthy_inputs() | {"ebitda": money("-40")}
        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )

        assert next(row for row in results if row.key == "ev_ebitda").missing == ()

    def test_a_unit_mismatch_still_raises_from_the_table(self, context):
        """A currency mix is a mapping error, and swallowing it would hide it in exactly the
        place somebody is looking for problems."""
        inputs = healthy_inputs() | {"ebitda": money("100", Unit.currency("GBP"))}

        with pytest.raises(UnitMismatchError):
            multiples_for(
                context,
                inputs=inputs,
                basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
                period_end=PERIOD_END,
            )


class TestAnImplausibleMultipleIsFlaggedNotDropped:
    def test_a_denominator_near_zero_produces_an_enormous_figure(self, context):
        inputs = healthy_inputs() | {"ebitda": money("0.5")}
        results = multiples_for(
            context,
            inputs=inputs,
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )
        ev_ebitda = next(row for row in results if row.key == "ev_ebitda")

        assert ev_ebitda.present
        assert ev_ebitda.value > IMPLAUSIBLE_MULTIPLE
        assert ev_ebitda.is_implausible

    def test_an_ordinary_multiple_is_not_flagged(self, context):
        results = multiples_for(
            context,
            inputs=healthy_inputs(),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
        )

        assert not next(row for row in results if row.key == "ev_ebitda").is_implausible


# -- Enterprise value -------------------------------------------------------------------------


class TestMarketEnterpriseValue:
    def test_it_is_market_capitalisation_plus_net_debt(self, context):
        result = market_enterprise_value(
            context, market_capitalisation=money("1000"), net_debt=money("200")
        )
        assert result.value == Decimal(1200)

    def test_net_cash_reduces_it(self, context):
        result = market_enterprise_value(
            context, market_capitalisation=money("1000"), net_debt=money("-300")
        )
        assert result.value == Decimal(700)

    def test_two_currencies_are_refused(self, context):
        with pytest.raises(UnitMismatchError):
            market_enterprise_value(
                context,
                market_capitalisation=money("1000"),
                net_debt=money("200", Unit.currency("GBP")),
            )

    def test_it_is_a_different_calculation_from_the_dcf_one(self, context):
        """One is observed and one is derived. Comparing them is most of the point."""
        market_enterprise_value(context, market_capitalisation=money("1000"), net_debt=money("200"))

        assert "market_enterprise_value" in {r.name for r in context.records}
        assert hasattr(dcf, "enterprise_value")


# -- The median -------------------------------------------------------------------------------


class TestTheMedianNotTheMean:
    def test_an_odd_count_takes_the_middle(self, context):
        result = median_multiple(context, observations=[pure("9"), pure("11"), pure("13")])
        assert result.value == Decimal(11)

    def test_an_even_count_averages_the_middle_pair(self, context):
        result = median_multiple(
            context, observations=[pure("9"), pure("11"), pure("13"), pure("15")]
        )
        assert result.value == Decimal(12)

    def test_one_outlier_does_not_move_it(self, context):
        """A peer set of eight at 9-13x plus one at 140x has a mean of 27x, describing none."""
        ordinary = [pure(str(v)) for v in (9, 10, 11, 11, 12, 12, 13, 13)]
        with_outlier = [*ordinary, pure("140")]

        assert median_multiple(context, observations=ordinary).value == Decimal("11.5")
        assert median_multiple(context, observations=with_outlier).value == Decimal(12)

    def test_the_order_the_peers_arrive_in_does_not_change_it(self, context):
        """Every other test in this class hands the median an already-sorted list.

        That is the one input for which sorting is not needed, so removing the sort passed
        all of them — and passed all fifty-six test files that can reach this module. Peers
        arrive in whatever order the peer set was assembled, which is a person's ordering,
        not an ascending one.
        """
        unsorted = [pure(v) for v in ("13", "9", "15", "11")]

        assert median_multiple(context, observations=unsorted).value == Decimal(12)

    @given(
        values=st.lists(
            st.integers(min_value=1, max_value=100_000).map(lambda n: Decimal(n) / Decimal(100)),
            min_size=1,
            max_size=15,
        ),
        rotation=st.integers(min_value=0, max_value=14),
    )
    def test_it_is_the_same_median_however_the_peers_are_ordered(self, values, rotation):
        """The property the concrete case above is one instance of.

        Stated over any peer set rather than one: a median that depended on arrival order
        would produce a different peer benchmark for the same companies listed differently,
        and nothing downstream could tell.
        """
        context = CalculationContext(code_version="testsha")
        turned = values[rotation:] + values[:rotation]

        ordered = median_multiple(context, observations=[pure(str(v)) for v in sorted(values)])
        rotated = median_multiple(context, observations=[pure(str(v)) for v in turned])
        reversed_ = median_multiple(context, observations=[pure(str(v)) for v in reversed(values)])

        assert ordered.value == rotated.value == reversed_.value

    def test_no_observations_is_refused(self, context):
        with pytest.raises(CalculationError):
            median_multiple(context, observations=[])

    def test_a_dimensioned_observation_is_refused(self, context):
        with pytest.raises(UnitMismatchError):
            median_multiple(context, observations=[money("12")])


class TestPercentileRank:
    def test_the_lowest_figure_ranks_low(self, context):
        history = [pure(str(v)) for v in (10, 11, 12, 13, 14)]
        result = percentile_rank(context, value=pure("10"), observations=history)
        assert result.value == Decimal("0.2")

    def test_the_highest_figure_ranks_at_one(self, context):
        history = [pure(str(v)) for v in (10, 11, 12, 13, 14)]
        result = percentile_rank(context, value=pure("14"), observations=history)
        assert result.value == Decimal(1)

    def test_a_company_at_its_own_long_run_level_ranks_in_the_middle(self, context):
        """The honest form of "is this expensive?"."""
        history = [pure(str(v)) for v in (12, 13, 14, 15, 16)]
        result = percentile_rank(context, value=pure("14"), observations=history)
        assert result.value == Decimal("0.6")

    def test_no_history_is_refused(self, context):
        with pytest.raises(CalculationError):
            percentile_rank(context, value=pure("14"), observations=[])


# -- Peer alignment ---------------------------------------------------------------------------


class TestAPeerWithADifferentYearEnd:
    def test_a_matching_period_is_kept(self):
        kept, excluded = align_peers(
            [("PEER", "Peer plc", PERIOD_END)], subject_period_end=PERIOD_END
        )

        assert len(kept) == 1
        assert excluded == ()

    def test_a_fiscal_calendar_quirk_is_kept(self):
        """A 52/53-week year or a 30 June against a 3 July is the same twelve months."""
        close = PERIOD_END - timedelta(days=MAX_PERIOD_DRIFT_DAYS)
        kept, excluded = align_peers([("PEER", "Peer plc", close)], subject_period_end=PERIOD_END)

        assert len(kept) == 1
        assert excluded == ()

    def test_a_quarter_of_drift_is_not(self):
        """The constant and its own rationale disagreed about this case, and this is the test
        that found it: at 92 days the March-against-December comparison was permitted by the
        code that says it excludes it."""
        drifted = PERIOD_END - timedelta(days=91)
        kept, excluded = align_peers([("PEER", "Peer plc", drifted)], subject_period_end=PERIOD_END)

        assert kept == ()
        assert len(excluded) == 1

    def test_a_march_year_end_against_a_december_one_is_excluded(self):
        kept, excluded = align_peers(
            [("PEER", "Peer plc", date(2024, 3, 31))], subject_period_end=date(2023, 12, 31)
        )

        assert kept == ()
        assert len(excluded) == 1

    def test_the_exclusion_carries_a_reason(self):
        """A comparison whose exclusions are invisible is one a reader cannot check."""
        _, excluded = align_peers(
            [("PEER", "Peer plc", date(2025, 3, 31))], subject_period_end=PERIOD_END
        )

        assert "days from the subject" in excluded[0].reason
        assert excluded[0].period_end == date(2025, 3, 31)
        assert excluded[0].name == "Peer plc"

    def test_a_mixed_set_is_split_rather_than_rejected(self):
        kept, excluded = align_peers(
            [
                ("A", "Aligned", PERIOD_END),
                ("B", "Drifted", date(2025, 3, 31)),
                ("C", "Also aligned", PERIOD_END - timedelta(days=30)),
            ],
            subject_period_end=PERIOD_END,
        )

        assert [row[0] for row in kept] == ["A", "C"]
        assert [row.identifier for row in excluded] == ["B"]


# -- The licence restriction ------------------------------------------------------------------


def table_of(*, peers: int = 3) -> CompsTable:
    subject = PeerRow(
        identifier="SUBJ",
        name="Subject plc",
        period_end=PERIOD_END,
        multiples=(
            MultipleResult(
                key="ev_ebitda",
                label="EV/EBITDA",
                quantity=pure("12"),
                basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
                period_end=PERIOD_END,
            ),
        ),
    )
    rows = tuple(
        PeerRow(
            identifier=f"P{index}",
            name=f"Peer {index}",
            period_end=PERIOD_END,
            multiples=(
                MultipleResult(
                    key="ev_ebitda",
                    label="EV/EBITDA",
                    quantity=pure(str(9 + index)),
                    basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
                    period_end=PERIOD_END,
                ),
            ),
            rationale="Same industry, similar size",
        )
        for index in range(peers)
    )
    return CompsTable(
        subject=subject,
        peers=rows,
        excluded=(),
        basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
        as_of=AS_OF,
        peer_set_confirmed=True,
        licence_note="Licensed market data. Redistribution prohibited.",
    )


class TestNothingPriceDerivedLeavesTheMachine:
    """ADR 0030 route 2, enforced by what the object contains rather than by a flag."""

    def test_an_internal_audience_gets_the_table(self):
        table = table_of()
        assert table.for_audience(Audience.INTERNAL) is table

    def test_a_shareable_audience_gets_something_with_no_rows(self):
        withheld = table_of().for_audience(Audience.SHAREABLE)

        assert isinstance(withheld, WithheldComps)
        assert not hasattr(withheld, "peers")
        assert not hasattr(withheld, "subject")

    def test_no_multiple_survives_into_the_shareable_form(self):
        """A renderer handed one cannot print a figure because there is no figure in it."""
        withheld = table_of().for_audience(Audience.SHAREABLE)

        rendered = repr(withheld) + withheld.as_paragraph()
        for forbidden in ("12", "9", "10", "11", "EV/EBITDA", "Peer 0"):
            assert forbidden not in rendered.replace("2024", "").replace("0030", "")

    def test_it_discloses_that_something_was_withheld(self):
        """Silence would read as "no comparison was done", which is a different claim."""
        withheld = table_of(peers=4).for_audience(Audience.SHAREABLE)

        paragraph = withheld.as_paragraph()
        assert "4 peer(s)" in paragraph
        assert "withheld" in paragraph
        assert "internal use only" in paragraph

    def test_the_counts_are_of_companies_not_of_licensed_figures(self):
        """A peer set is chosen by a person; it is not the vendor's data."""
        withheld = table_of(peers=4).for_audience(Audience.SHAREABLE)

        assert withheld.peer_count == 4
        assert withheld.excluded_count == 0


class TestTheTableItself:
    def test_the_peer_median_ignores_the_subject(self):
        table = table_of()
        # Peers at 9, 10, 11; the subject at 12 is not one of them.
        assert table.median_of("ev_ebitda") == Decimal(10)

    def test_a_multiple_nobody_computed_has_no_median(self):
        assert table_of().median_of("p_ffo") is None

    def test_a_peer_absent_for_a_multiple_entirely_is_left_out(self):
        """A peer row with no entry for the key at all, as opposed to one that is not
        meaningful. Both must be skipped, and they reach `median_of` by different routes."""
        table = table_of()
        partial = CompsTable(
            subject=table.subject,
            peers=(
                *table.peers,
                PeerRow(
                    identifier="OTHER",
                    name="Different multiples plc",
                    period_end=PERIOD_END,
                    multiples=(
                        MultipleResult(
                            key="pe",
                            label="P/E",
                            quantity=pure("40"),
                            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
                            period_end=PERIOD_END,
                        ),
                    ),
                ),
            ),
            excluded=table.excluded,
            basis=table.basis,
            as_of=table.as_of,
            peer_set_confirmed=True,
        )

        assert partial.median_of("ev_ebitda") == Decimal(10)

    def test_a_not_meaningful_peer_is_left_out_of_the_median(self):
        table = table_of()
        crippled = CompsTable(
            subject=table.subject,
            peers=(
                *table.peers,
                PeerRow(
                    identifier="LOSS",
                    name="Loss-making plc",
                    period_end=PERIOD_END,
                    multiples=(
                        MultipleResult(
                            key="ev_ebitda",
                            label="EV/EBITDA",
                            quantity=None,
                            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
                            period_end=PERIOD_END,
                            absent_because="negative EBITDA",
                        ),
                    ),
                ),
            ),
            excluded=table.excluded,
            basis=table.basis,
            as_of=table.as_of,
            peer_set_confirmed=True,
        )

        assert crippled.median_of("ev_ebitda") == Decimal(10)


class TestTheSpecialistMultiples:
    def test_a_reit_is_valued_on_p_ffo(self):
        p_ffo = next(d for d in MULTIPLE_DEFINITIONS if d.key == "p_ffo")
        assert "reits" in p_ffo.specialist_for

    def test_a_bank_is_valued_on_p_tbv(self):
        p_tbv = next(d for d in MULTIPLE_DEFINITIONS if d.key == "p_tbv")
        assert "banks" in p_tbv.specialist_for

    def test_every_definition_states_what_it_commits_to(self):
        """ "EV/EBITDA" without a definition of enterprise value is not comparable."""
        for definition in MULTIPLE_DEFINITIONS:
            assert definition.note, definition.key

    def test_the_enterprise_value_note_names_what_it_does_not_adjust_for(self):
        ev_ebitda = next(d for d in MULTIPLE_DEFINITIONS if d.key == "ev_ebitda")
        assert "Minority interests" in ev_ebitda.note
