"""The refresh's diff (F4, ADR 0131 §5): materiality is a table, and the table is code.

`aer.calc.changes` is pure — figures in, judged changes out — so it is held here the way the
rest of `calc/` is: the mechanism's rows one by one (`08-mechanisms.md` §1.4), then under
Hypothesis for the properties a reader relies on: a figure that did not move is never
material, a relative move at or past two per cent always is, and every change names the
row it came from so the summary's footnotes resolve.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aer.calc.changes import (
    ANCHORS,
    MATERIAL_RELATIVE_CHANGE,
    WATCHED_RELATIVE_CHANGE,
    Change,
    Figure,
    Movement,
    diff_figures,
    relative_change,
)

WATCHED = frozenset({("calculation", "net_margin")})

values = st.decimals(
    min_value=-1_000_000, max_value=1_000_000, places=4, allow_nan=False, allow_infinity=False
)


def _figure(
    name: str = "net_margin",
    value: str = "0.25",
    *,
    kind: str = "calculation",
    period: str = "FY 2022",
    case: str = "base",
    unit: str = "pure",
    reference: str = "row-1",
) -> Figure:
    return Figure(
        kind=kind,
        name=name,
        value=Decimal(value),
        unit=unit,
        period=period,
        case=case,
        reference=reference,
    )


def _one(prior: Figure | None, new: Figure | None, **kwargs: object) -> Change:
    changes = diff_figures(
        [prior] if prior is not None else [], [new] if new is not None else [], **kwargs
    )
    assert len(changes) == 1
    return changes[0]


class TestTheTable:
    def test_a_move_under_two_per_cent_is_not_material(self) -> None:
        change = _one(_figure(value="0.250"), _figure(value="0.254"))

        assert not change.material
        assert change.movement is Movement.UNCHANGED
        assert change.change_pct == Decimal("0.016")
        assert "Within the threshold" in change.narrative

    def test_a_move_of_two_per_cent_is_material(self) -> None:
        change = _one(_figure(value="0.250"), _figure(value="0.255"))

        assert change.material
        assert change.movement is Movement.RELATIVE
        assert change.change_pct == MATERIAL_RELATIVE_CHANGE
        assert change.narrative == "Net margin for FY 2022 moved from 0.25 to 0.255 (+2.0%)."

    def test_an_anchor_that_moved_at_all_is_material(self) -> None:
        change = _one(
            _figure("revenue", "198270000000", unit="USD"),
            _figure("revenue", "198270000001", unit="USD"),
        )

        assert "revenue" in ANCHORS
        assert change.material
        assert change.movement is Movement.ANCHOR
        assert change.narrative.endswith("It anchors the report.")

    def test_an_anchor_that_did_not_move_is_unchanged(self) -> None:
        change = _one(_figure("revenue", "1", unit="USD"), _figure("revenue", "1", unit="USD"))

        assert not change.material
        assert change.movement is Movement.UNCHANGED

    def test_a_sign_change_is_material_however_small(self) -> None:
        change = _one(_figure(value="0.001"), _figure(value="-0.001"))

        assert change.material
        assert change.movement is Movement.SIGN
        assert change.narrative.endswith("The sign changed.")

    def test_a_prior_of_nought_has_no_ratio_and_is_material(self) -> None:
        change = _one(_figure(value="0"), _figure(value="3"))

        assert change.material
        assert change.movement is Movement.FROM_ZERO
        assert change.change_pct is None
        assert change.magnitude == Decimal("Infinity")
        assert relative_change(Decimal(0), Decimal(3)) is None

    def test_a_figure_that_appeared_is_material_and_names_its_row(self) -> None:
        new = _figure(period="FY 2023", reference="row-new")
        change = _one(None, new)

        assert change.material
        assert change.movement is Movement.APPEARED
        assert change.prior is None
        assert change.new is new
        assert change.new.reference == "row-new"
        assert change.narrative == "Net margin for FY 2023 appears for the first time, at 0.25."

    def test_a_figure_that_disappeared_is_material(self) -> None:
        change = _one(_figure(), None)

        assert change.material
        assert change.movement is Movement.DISAPPEARED
        assert change.new is None
        assert "no longer computed" in change.narrative

    def test_a_premise_crossed_is_material_however_small_and_leads(self) -> None:
        def crossed(prior: Figure, new: Figure) -> str | None:
            return "the premise 'margins hold above a quarter'" if new.value < prior.value else None

        change = _one(_figure(value="0.2500"), _figure(value="0.2499"), crossed=crossed)

        assert change.material
        assert change.movement is Movement.PREMISE
        assert change.narrative.endswith("It crosses the premise 'margins hold above a quarter'.")

    def test_a_premise_that_did_not_cross_leaves_the_table_to_decide(self) -> None:
        change = _one(
            _figure(value="0.25"), _figure(value="0.26"), crossed=lambda _prior, _new: None
        )

        assert change.movement is Movement.RELATIVE


class TestTheKey:
    def test_the_same_name_in_two_periods_is_two_figures(self) -> None:
        prior = [_figure(period="FY 2021", value="0.2"), _figure(period="FY 2022", value="0.25")]
        new = [_figure(period="FY 2021", value="0.2"), _figure(period="FY 2022", value="0.30")]

        changes = diff_figures(prior, new)

        assert [(c.period, c.material) for c in changes] == [("FY 2021", False), ("FY 2022", True)]

    def test_cases_and_distinguishers_keep_rows_apart(self) -> None:
        prior = [
            _figure("value_per_share", "100", case="base", unit="USD"),
            _figure("value_per_share", "80", case="bear", unit="USD"),
        ]
        new = [
            _figure("value_per_share", "100", case="base", unit="USD"),
            _figure("value_per_share", "90", case="bear", unit="USD"),
        ]

        changes = diff_figures(prior, new)

        assert [(c.case, c.movement) for c in changes] == [
            ("base", Movement.UNCHANGED),
            ("bear", Movement.ANCHOR),
        ]
        assert "in the bear case" in changes[1].narrative

    def test_the_runs_own_order_is_kept_and_the_last_duplicate_wins(self) -> None:
        prior = [_figure("a", "1"), _figure("b", "2")]
        new = [_figure("b", "2"), _figure("c", "3"), _figure("a", "1"), _figure("a", "5")]

        changes = diff_figures(prior, new)

        assert [c.name for c in changes] == ["a", "b", "c"]
        assert changes[0].new is not None
        assert changes[0].new.value == Decimal(5)

    def test_units_are_said_and_pure_is_not(self) -> None:
        money = _one(_figure("cash", "10", unit="USD"), _figure("cash", "11", unit="USD"))
        ratio = _one(_figure("net_margin", "0.1"), _figure("net_margin", "0.2"))

        assert "10 USD to 11 USD" in money.narrative
        assert "0.1 to 0.2" in ratio.narrative
        assert "pure" not in ratio.narrative


class TestTheProperties:
    @settings(max_examples=100)
    @given(value=values)
    def test_a_figure_that_did_not_move_is_never_material(self, value: Decimal) -> None:
        change = _one(_figure("revenue", str(value)), _figure("revenue", str(value)))

        assert not change.material
        assert change.movement is Movement.UNCHANGED

    @settings(max_examples=100)
    @given(
        prior=values.filter(lambda v: v != 0),
        factor=st.decimals(
            min_value="1.02", max_value="5", places=3, allow_nan=False, allow_infinity=False
        ),
    )
    def test_a_relative_move_at_the_threshold_or_past_it_is_always_material(
        self, prior: Decimal, factor: Decimal
    ) -> None:
        change = _one(
            _figure("gross_margin", str(prior)), _figure("gross_margin", str(prior * factor))
        )

        assert change.material
        assert change.change_pct is not None
        assert abs(change.change_pct) >= MATERIAL_RELATIVE_CHANGE

    @settings(max_examples=100)
    @given(prior=values, new=values)
    def test_every_change_names_the_rows_it_came_from(self, prior: Decimal, new: Decimal) -> None:
        change = _one(
            _figure(value=str(prior), reference="before"),
            _figure(value=str(new), reference="after"),
        )

        assert change.prior is not None
        assert change.new is not None
        assert (change.prior.reference, change.new.reference) == ("before", "after")
        assert change.narrative.strip()

    @settings(max_examples=60)
    @given(prior=values.filter(lambda v: v != 0), new=values)
    def test_the_ratio_is_the_mechanisms_own(self, prior: Decimal, new: Decimal) -> None:
        change = _one(_figure(value=str(prior)), _figure(value=str(new)))

        assert change.change_pct == (new - prior) / abs(prior)


class TestAWatchedFigure:
    """ADR 0122 §2: a figure a premise the operator holds reads is material at half the
    threshold, whatever it feeds. The caller names the figures, as it names the crossing;
    the diff stays pure."""

    def test_a_one_per_cent_move_is_material_when_a_premise_reads_the_figure(self) -> None:
        change = _one(_figure(value="0.2500"), _figure(value="0.2525"), watched=WATCHED)

        assert change.material
        assert change.movement is Movement.WATCHED
        assert change.change_pct == Decimal("0.01")
        assert change.narrative == (
            "Net margin for FY 2022 moved from 0.25 to 0.2525 (+1.0%). It feeds a premise you "
            "hold, and is material at half the ordinary threshold."
        )

    def test_the_same_move_in_a_figure_nothing_reads_is_within_the_threshold(self) -> None:
        change = _one(
            _figure(value="0.2500"),
            _figure(value="0.2525"),
            watched=frozenset({("fact", "revenue")}),
        )

        assert not change.material
        assert change.movement is Movement.UNCHANGED

    def test_under_half_the_threshold_a_watched_figure_is_unchanged(self) -> None:
        change = _one(_figure(value="0.2500"), _figure(value="0.2520"), watched=WATCHED)

        assert change.change_pct == Decimal("0.008")
        assert not change.material

    def test_past_the_ordinary_threshold_a_watched_figure_still_says_what_it_feeds(
        self,
    ) -> None:
        change = _one(_figure(value="0.25"), _figure(value="0.30"), watched=WATCHED)

        assert change.movement is Movement.WATCHED
        assert "(+20.0%)" in change.narrative

    def test_a_crossing_and_an_anchor_outrank_the_watch(self) -> None:
        crossed = _one(
            _figure(value="0.25"),
            _figure(value="0.2525"),
            watched=WATCHED,
            crossed=lambda _prior, _new: "the premise 'Net margin stays above 25%'",
        )
        anchor = _one(
            _figure("revenue", "100", unit="USD"),
            _figure("revenue", "101", unit="USD"),
            watched=frozenset({("calculation", "revenue")}),
        )

        assert crossed.movement is Movement.PREMISE
        assert anchor.movement is Movement.ANCHOR

    def test_half_the_threshold_is_one_per_cent(self) -> None:
        assert Decimal("0.01") == WATCHED_RELATIVE_CHANGE
        assert WATCHED_RELATIVE_CHANGE == MATERIAL_RELATIVE_CHANGE / 2

    @settings(max_examples=100)
    @given(prior=values.filter(lambda v: v != 0), new=values)
    def test_a_watched_move_is_material_exactly_from_half_the_threshold(
        self, prior: Decimal, new: Decimal
    ) -> None:
        change = _one(_figure(value=str(prior)), _figure(value=str(new)), watched=WATCHED)

        ratio = (new - prior) / abs(prior)
        assert change.material == (abs(ratio) >= WATCHED_RELATIVE_CHANGE)


def test_the_threshold_is_the_audits_two_per_cent() -> None:
    assert Decimal("0.02") == MATERIAL_RELATIVE_CHANGE


@pytest.mark.parametrize("name", sorted(ANCHORS))
def test_every_anchor_is_a_name_the_ledger_or_the_fact_store_uses(name: str) -> None:
    assert name == name.lower()
    assert " " not in name
