"""What a cost looks like before it reaches a template, and what it refuses to invent.

The rule ADR 0077 states for JavaScript holds for Jinja: a template may print a figure and may
not decide one. The run console decides one today — `£{{ spend_gbp }}` puts a currency symbol
in a template, and the next surface to show that number puts it there again, differently.

These tests are mostly about the awkward inputs, because the ordinary ones are obvious and the
awkward ones are where a cost display starts lying: a run past its ceiling, a ceiling nobody
configured, and a total too small to round to a penny.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.config import HouseStyle
from aer.web.figures import (
    NOT_AVAILABLE,
    RenderedFigure,
    cost_context,
    lineage_figure,
    pounds,
    tone_for,
)
from aer.web.overview.pages import _pounds
from aer.web.shell.provenance import Provenance, ProvenanceRef
from aer.web.vocabulary import Tone

_REF = ProvenanceRef(kind=Provenance.SOURCE_FACT, identifier="fact-1", href="/sources/fact-1")


class TestPounds:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            (Decimal("0"), "£0.00"),
            (Decimal("2.5"), "£2.50"),
            (Decimal("8"), "£8.00"),
            (Decimal("1234.5"), "£1,234.50"),
        ],
    )
    def test_an_ordinary_amount(self, amount: Decimal, expected: str) -> None:
        assert pounds(amount) == expected

    def test_a_total_too_small_to_round_says_so(self) -> None:
        """ "We have spent nothing" and "we have spent a third of a penny" are different
        answers, and `£0.00` is the wrong one on a screen about what a run cost."""
        assert pounds(Decimal("0.003")) == "under £0.01"

    def test_exactly_nothing_is_not_dressed_up_as_something(self) -> None:
        """The complement of the rule above: zero is zero and must not read as a rounding."""
        assert pounds(Decimal("0")) == "£0.00"

    def test_a_negative_that_rounds_to_nothing_is_not_reported_as_spend(self) -> None:
        """A refund smaller than a penny is still not "£0.00 spent"."""
        assert pounds(Decimal("-0.004")) == "under £0.01"

    def test_thousands_are_separated(self) -> None:
        """A four-figure total is read at a glance or it is misread."""
        assert "," in pounds(Decimal("10000"))


class TestACostAgainstItsCeiling:
    def test_the_ordinary_case(self) -> None:
        cost = cost_context(spent=Decimal("6.40"), ceiling=Decimal("8.00"))
        assert cost.spent_display == "£6.40"
        assert cost.ceiling_display == "£8.00"
        assert cost.remaining_display == "£1.60"
        assert cost.summary == "£6.40 of £8.00"

    def test_the_summary_is_the_line_every_surface_shows(self) -> None:
        """£6.40 answers nothing. £6.40 of £8.00 is the sentence that decides whether to
        continue, and it must read identically on the console and on all seven gates."""
        cost = cost_context(spent=Decimal("0.15"), ceiling=Decimal("2.50"))
        assert cost.summary == "£0.15 of £2.50"

    def test_nothing_spent_yet(self) -> None:
        cost = cost_context(spent=Decimal("0"), ceiling=Decimal("8.00"))
        assert cost.fraction == Decimal(0)
        assert not cost.is_near_ceiling
        assert cost.remaining_display == "£8.00"

    def test_near_the_ceiling_is_flagged(self) -> None:
        cost = cost_context(spent=Decimal("7.00"), ceiling=Decimal("8.00"))
        assert cost.is_near_ceiling

    def test_a_run_past_its_ceiling_has_nothing_left_rather_than_a_negative_allowance(
        self,
    ) -> None:
        """A remaining allowance of minus £1.20 is arithmetic nobody asked for.

        The overspend is not hidden by this: `spent_display` is the larger figure, plainly,
        and the fraction is capped at one so a progress bar cannot run off its own track.
        """
        cost = cost_context(spent=Decimal("9.20"), ceiling=Decimal("8.00"))
        assert cost.remaining_display == "£0.00"
        assert cost.spent_display == "£9.20"
        assert cost.fraction == Decimal(1)


class TestAMissingCeilingIsNotAZeroOne:
    """A denominator nobody configured must not become a percentage.

    The tempting failure is to divide by nothing, call it zero, and render "0% of budget
    used" — a measurement, presented with the confidence of one, on the strength of an absent
    setting.
    """

    @pytest.mark.parametrize("ceiling", [None, Decimal("0")])
    def test_the_ceiling_and_the_remainder_are_stated_as_unavailable(
        self, ceiling: Decimal | None
    ) -> None:
        cost = cost_context(spent=Decimal("6.40"), ceiling=ceiling)
        assert cost.ceiling_display == NOT_AVAILABLE
        assert cost.remaining_display == NOT_AVAILABLE

    def test_what_was_spent_is_still_reported(self) -> None:
        """The half that is known stays known. A missing ceiling hides the allowance, not the
        spend — an operator with no cap configured still needs to see what a run has cost."""
        cost = cost_context(spent=Decimal("6.40"), ceiling=None)
        assert cost.spent_display == "£6.40"

    def test_nothing_is_flagged_as_near_a_ceiling_that_does_not_exist(self) -> None:
        assert not cost_context(spent=Decimal("999"), ceiling=None).is_near_ceiling


class TestTheScopeIsCarried:
    """Roadmap: the two ceilings have different remedies and reporting one as the other sends
    the operator to change the wrong number."""

    def test_a_run_ceiling_is_the_default(self) -> None:
        assert cost_context(spent=Decimal("1"), ceiling=Decimal("2")).scope == "run"

    def test_a_monthly_ceiling_says_so(self) -> None:
        cost = cost_context(spent=Decimal("40"), ceiling=Decimal("50"), scope="month")
        assert cost.scope == "month"


class TestTheToneNeverCallsAGuardrailAFault:
    def test_an_ordinary_spend_is_neutral(self) -> None:
        assert tone_for(cost_context(spent=Decimal("1"), ceiling=Decimal("8"))) is Tone.INFO

    def test_approaching_the_ceiling_is_worth_noticing(self) -> None:
        assert tone_for(cost_context(spent=Decimal("7"), ceiling=Decimal("8"))) is Tone.WARNING

    def test_reaching_it_is_a_refusal_and_never_a_failure(self) -> None:
        """The same distinction the state vocabulary makes for `BUDGET_EXCEEDED`.

        A cost block in failure red beside a status that says "stopped before overspending"
        would put the two halves of one event in two different colours, and the louder half
        would be the wrong one.
        """
        cost = cost_context(spent=Decimal("8"), ceiling=Decimal("8"))
        assert tone_for(cost) is Tone.REFUSAL
        assert tone_for(cost) is not Tone.FAILURE


class TestTheOverviewUsesTheSameRenderer:
    def test_it_is_not_a_second_implementation(self) -> None:
        """`_pounds` was private to the overview, and a private renderer is how a second one
        gets written. It is now a name over the shared function."""
        assert _pounds(Decimal("0.003")) == pounds(Decimal("0.003")) == "under £0.01"
        assert _pounds(Decimal("12.5")) == pounds(Decimal("12.5")) == "£12.50"


class TestAFigureTravelsWithItsLineage:
    """ADR 0077 stopped a badge being rendered without a drill-down. This is the step before:
    a *figure* rendered with no badge at all, which no required argument can reach, because a
    bare string has nothing to require anything of."""

    def test_a_traced_figure_carries_where_it_came_from(self) -> None:
        figure = RenderedFigure.traced("£12.50", _REF, label="Closing price")
        assert figure.value_display == "£12.50"
        assert figure.provenance is _REF
        assert figure.is_available

    def test_a_figure_with_no_provenance_is_refused(self) -> None:
        """Invariant 3, at the moment a number reaches a screen."""
        with pytest.raises(ValueError, match="has no provenance"):
            RenderedFigure(value_display="£12.50")

    def test_a_figure_that_renders_as_nothing_is_refused(self) -> None:
        """An empty cell reads as nil, and nil is a claim about the record."""
        with pytest.raises(ValueError, match="renders as nothing"):
            RenderedFigure(value_display="  ", provenance=_REF)


class TestAnAbsentFigureSaysWhyItIsAbsent:
    def test_the_reason_is_required(self) -> None:
        """A bare dash is read as a zero or as a bug, and it is usually neither."""
        with pytest.raises(ValueError, match="does not say why"):
            RenderedFigure(value_display=NOT_AVAILABLE)

    def test_an_unavailable_figure_renders_the_dash_and_the_reason(self) -> None:
        figure = RenderedFigure.unavailable("not filed for this period", label="EBITDA")
        assert figure.value_display == NOT_AVAILABLE
        assert not figure.is_available
        assert figure.unavailable_because == "not filed for this period"

    def test_it_carries_no_lineage(self) -> None:
        """There is no chain under a number that does not exist, and a badge beside a dash
        claims one — a reader who clicks it is owed something the record does not have."""
        with pytest.raises(ValueError, match="no lineage"):
            RenderedFigure(
                value_display=NOT_AVAILABLE, provenance=_REF, unavailable_because="not filed"
            )


class TestALineageRowBecomesAFigure:
    """The tranche-7 closure of the gap tranche 1 left: the handler formats the node's
    value and attaches the drill-down, so no template touches a Decimal or composes an
    origin link."""

    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "kind": "calculation",
            "id": "calc-1",
            "label": "value_per_share",
            "value": Decimal("42.10"),
            "unit": "GBP",
            "detail": {},
            "depth": 1,
            "is_leaf": False,
            "is_resolved": True,
        }
        base.update(overrides)
        return base

    def _figure(self, row: dict[str, object]) -> RenderedFigure:
        return lineage_figure(row, request_id="req-1", job_id="job-1", style=HouseStyle())

    def test_a_sub_calculation_walks_to_its_own_page(self) -> None:
        figure = self._figure(self._row())
        assert figure.provenance is not None
        assert figure.provenance.kind is Provenance.CALCULATED
        assert figure.provenance.href == "/calculations/calc-1"

    def test_the_root_row_walks_to_the_formula_above_it(self) -> None:
        figure = self._figure(self._row(depth=0))
        assert figure.provenance is not None
        assert figure.provenance.href == "#formula"

    def test_an_assumption_walks_to_its_history(self) -> None:
        figure = self._figure(self._row(kind="assumption", id="asm-1"))
        assert figure.provenance is not None
        assert figure.provenance.kind is Provenance.ASSUMED
        assert figure.provenance.href == "/requests/req-1/assumptions/asm-1"

    def test_a_fact_walks_to_the_document_it_was_reported_in(self) -> None:
        figure = self._figure(self._row(kind="fact", detail={"source_document_id": "doc-9"}))
        assert figure.provenance is not None
        assert figure.provenance.kind is Provenance.SOURCE_FACT
        assert figure.provenance.href == "/runs/job-1/sources#source-doc-9"

    def test_a_fact_with_no_document_still_reaches_the_sources_table(self) -> None:
        figure = self._figure(self._row(kind="fact"))
        assert figure.provenance is not None
        assert figure.provenance.href == "/runs/job-1/sources"

    def test_a_dangling_reference_is_shown_as_unresolved_not_hidden(self) -> None:
        figure = self._figure(self._row(kind="missing", value=None, is_resolved=False))
        assert not figure.is_available
        assert figure.provenance is None
        assert "no longer here" in figure.unavailable_because

    def test_a_truncated_walk_says_the_tree_is_deeper(self) -> None:
        figure = self._figure(self._row(kind="truncated", value=None))
        assert not figure.is_available
        assert "deeper" in figure.unavailable_because

    def test_a_node_with_no_value_carries_no_badge(self) -> None:
        figure = self._figure(self._row(value=None))
        assert not figure.is_available
        assert figure.provenance is None
