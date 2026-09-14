"""The closed set of relations a front page's figures cannot hold together.

Gap A61: the MTB run published a 172.1% net margin — a stored fact and a recorded
calculation, every citation verified, every metric passing. Traceability is not sanity,
and this module is the sanity half, kept deliberately small (ADR 0066).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from aer.calc.plausibility import (
    LARGE_BALANCE_SHEET,
    TURNOVER_FLOOR,
    FigureScene,
    impossible_relations,
)
from aer.eval.runtime import presentation_integrity
from aer.render.glance import (
    _impossibility_refusal,
    _mixing_refusal,
)

# Money-shaped decimals, positive, two places — the shapes a statement actually carries.
_money = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("1000000000000"), places=2, allow_nan=False
)


class TestNothingFiresOnAPossibleWorld:
    @given(revenue=_money, share=st.decimals(min_value=0, max_value=1, places=4))
    def test_income_within_revenue_is_never_flagged(self, revenue: Decimal, share: Decimal) -> None:
        scene = FigureScene(period="FY2025", revenue=revenue, net_income=revenue * share)
        assert impossible_relations((scene,)) == ()

    @given(margin=st.decimals(min_value=Decimal("-5"), max_value=1, places=4))
    def test_a_margin_at_or_below_one_is_never_flagged(self, margin: Decimal) -> None:
        assert impossible_relations((FigureScene(period="FY2025", net_margin=margin),)) == ()

    @given(
        turnover=st.decimals(min_value=TURNOVER_FLOOR, max_value=10, places=4),
        assets=_money,
    )
    def test_turnover_at_or_above_the_floor_is_never_flagged(
        self, turnover: Decimal, assets: Decimal
    ) -> None:
        scene = FigureScene(period="FY2025", asset_turnover=turnover, total_assets=assets)
        assert impossible_relations((scene,)) == ()

    def test_absence_is_not_implausibility(self) -> None:
        """A scene with nothing to compare produces no findings — a thin run is not
        thereby a wrong one."""
        assert impossible_relations((FigureScene(period="FY2025"),)) == ()
        assert impossible_relations(()) == ()


class TestTheImpossibleIsNamedWithItsValues:
    def test_income_above_revenue_fires_and_states_both_figures(self) -> None:
        scene = FigureScene(
            period="Q2 FY2026", revenue=Decimal("442000000"), net_income=Decimal("818000000")
        )

        found = impossible_relations((scene,))

        assert len(found) == 1
        assert found[0].period == "Q2 FY2026"
        assert "442000000" in found[0].statement
        assert "818000000" in found[0].statement

    def test_a_margin_above_one_fires(self) -> None:
        found = impossible_relations((FigureScene(period="FY2025", net_margin=Decimal("1.7206")),))

        assert len(found) == 1
        assert "1.7206" in found[0].statement

    def test_low_turnover_fires_only_on_a_large_balance_sheet(self) -> None:
        """A small company's turnover can be legitimately strange; the floor is a claim
        about mislabelled numerators, and those need a balance sheet big enough that the
        alternative explanation is impossible."""
        turnover = Decimal("0.0076")
        large = FigureScene(
            period="FY2025", asset_turnover=turnover, total_assets=Decimal("219261000000")
        )
        small = FigureScene(
            period="FY2025",
            asset_turnover=turnover,
            total_assets=LARGE_BALANCE_SHEET - 1,
        )

        assert len(impossible_relations((large,))) == 1
        assert impossible_relations((small,)) == ()

    def test_the_mtb_front_page_is_caught_in_full(self) -> None:
        """The regression scene, verbatim from the live run's published figures."""
        scenes = (
            FigureScene(
                period="Q2 FY2026",
                revenue=Decimal("442000000"),
                net_income=Decimal("818000000"),
            ),
            FigureScene(period="FY2025", net_margin=Decimal("1.7206")),
            FigureScene(period="FY2023", net_margin=Decimal("1.847")),
            FigureScene(
                period="Q2 FY2026",
                asset_turnover=Decimal("0.0076"),
                total_assets=Decimal("219261000000"),
            ),
        )

        found = impossible_relations(scenes)

        assert len(found) == 4, "every one of the published impossibilities must be named"

    def test_negative_revenue_does_not_produce_a_nonsense_comparison(self) -> None:
        """Income above a negative revenue is a different pathology, and the comparison
        that assumes a positive base stays out of it rather than firing confusingly."""
        scene = FigureScene(period="FY2025", revenue=Decimal("-100"), net_income=Decimal("50"))
        assert impossible_relations((scene,)) == ()


# The rows M&T's live run offered the front page: fee income read as revenue, against the
# bank's real net income (F-24 of the readiness audit).
_MTB_FRONT_PAGE: Final = {
    "latest": [
        {"label": "Revenue", "period": "FY2025", "value": "1657000000", "unit": "USD"},
        {"label": "Net income", "period": "FY2025", "value": "2851000000", "unit": "USD"},
    ],
    "ratios": [],
}


class TestTheWithheldNoteSpeaksToAReader:
    """M&T's live run failed two checks at once, and the second was the platform's own
    doing: the front page's withheld note ends "(ADR 0066)", `presentation_integrity`
    refuses an architecture decision record in a document that should be about the company,
    and the note only renders on the runs that withhold a figure — so the check fired on
    text the platform wrote about itself. The reason belongs to the reader; the decision
    record belongs in the code.
    """

    def test_the_impossibility_note_names_no_decision_record(self) -> None:
        note = _impossibility_refusal(_MTB_FRONT_PAGE)

        assert note is not None
        assert "withheld" in note
        assert "ADR" not in note

    def test_the_mixing_note_names_no_decision_record(self) -> None:
        subject, other = uuid.uuid4(), uuid.uuid4()
        facts = [
            SimpleNamespace(company_id=subject),
            SimpleNamespace(company_id=other),
        ]

        note = _mixing_refusal(facts, subject)  # type: ignore[arg-type]

        assert note is not None
        assert "withheld" in note
        assert "ADR" not in note

    def test_the_presentation_check_passes_on_the_note(self) -> None:
        note = _impossibility_refusal(_MTB_FRONT_PAGE)
        document = f"# Note\n\n## At a glance\n\n{note}\n"

        # The figures in the note are formatted by the renderer, not here, so the
        # assertion is about the class of defect this note caused: process language.
        failures = presentation_integrity(document, "<main></main>", sections=1).failures
        assert not [item for item in failures if "process language" in item]
