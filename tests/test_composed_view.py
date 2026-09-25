"""ADR 0117's composed half, end to end: a valued run states a view.

`reports.rating` is assigned ``None`` in one place and written nowhere, so every report
this platform has produced printed *"no view reached"* — not because no view was reached
but because nothing was wired to reach one. Nine blind comparisons went against it and the
most-cited reason was that the console's answer said what it thought and this one did not.

What is under test is the whole chain, because every link of it existed separately and
none of them met: a valuation strikes per-share answers by method, the price step records a
close, the distance between them is a traced calculation, and the document walks all three
into a block whose markers resolve to the arithmetic. Each half passed its own tests while
the header said nothing.

The refusals matter as much: a run with no valuation has nothing for either method to give.

**Two answers, never a range (ADR 0132).** The masthead printed the two terminal methods'
figures as "$227.43 to $442.01 a share" above a valuation section saying they were not the
ends of a range, and every judge of the verdict round gave that as the first reason to
abandon. The header now says what each method gives, the block says why the two differ, and
the view line says the report takes no side: the operator decided on 25 September 2026 that
the view is theirs, in a thesis and a decision, and never the report's (ADR 0135).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from aer.calc.units import Quantity, SourceRef, Unit
from aer.config import HouseStyle
from aer.core.enums import UserRole
from aer.core.sectors import ValuationMandate, ValuationModel
from aer.db.models import Calculation, User
from aer.render import display
from aer.render.document import NO_VIEW, CalculationFootnote, assemble_document
from aer.render.markdown import serialise_markdown
from aer.render.summary import summary_document
from aer.render.view import VIEW_TITLE, view_content
from aer.services.assumption_gate import (
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import confirm, propose
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation_run import value_the_business
from tests.assumption_fixtures import a_year, analysed, seed_years

pytestmark = pytest.mark.integration

_SHARES = {
    "shares_outstanding": "100",
    "basic_shares_outstanding": "100",
    "diluted_shares_outstanding": "110",
    "interest_expense": "20",
    "short_term_debt": "0",
}

_YEARS = {
    date(2022, 12, 31): a_year(revenue="1000", operating_income="240", **_SHARES),
    date(2023, 12, 31): a_year(revenue="1150", operating_income="290", **_SHARES),
    date(2024, 12, 31): a_year(revenue="1300", operating_income="340", **_SHARES),
}

_CONFIRMED: dict[str, str] = {
    "revenue_growth": "0.05",
    "ebit_margin": "0.25",
    "capex_intensity": "0.06",
    "depreciation_intensity": "0.05",
    "working_capital_intensity": "0.20",
    "tax_rate": "0.21",
    "terminal_growth": "0.02",
    "exit_multiple": "10",
    RISK_FREE_ASSUMPTION: "0.042",
    BETA_ASSUMPTION: "1.1",
    EQUITY_RISK_PREMIUM_ASSUMPTION: "0.055",
}

MANDATE = ValuationMandate(
    model=ValuationModel.DCF_FCFF, subject="CTSO", sector_key="", confirmed_by=""
)


def _price(value: str, currency: str = "USD") -> Quantity:
    """One close, per share, as the price step records it — a stored fact."""
    return Quantity.of(
        Decimal(value),
        Unit.currency(currency) / Unit.base("shares"),
        source=SourceRef.security("a-listing", label="close"),
    )


async def _valued(scene: dict[str, Any], *, price: Quantity | None = None) -> None:
    """A run that reached a valuation, with every assumption a forecast needs confirmed."""
    await seed_years(scene, _YEARS)
    actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
    scene["session"].add(actor)
    await scene["session"].flush()

    for name, value in _CONFIRMED.items():
        assumption = await propose(
            scene["session"],
            request_id=scene["request"].id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=f"Scene value for {name}.",
            proposed_by="test",
        )
        await confirm(scene["session"], assumption=assumption, actor=actor)

    await value_the_business(
        scene["session"],
        request=scene["request"],
        job_id=scene["job"].id,
        analysis=await analysed(scene),
        mandate=MANDATE,
        years=5,
        price_per_share=price,
    )


async def _document(scene: dict[str, Any]) -> Any:
    return await assemble_document(scene["session"], job=scene["job"], request=scene["request"])


class TestAValuedRunStatesAView:
    async def test_the_block_carries_the_base_case_by_method(self, scene: dict[str, Any]) -> None:
        await _valued(scene)

        content = (await view_content(scene["session"], job=scene["job"])).content

        assert content is not None
        labels = [row["label"] for row in content["base"]]
        assert labels == ["Perpetuity growth", "Exit multiple"]

    async def test_the_grid_cells_are_not_mistaken_for_conclusions(
        self, scene: dict[str, Any]
    ) -> None:
        """180 of the 186 per-share rows on the stored runs are sensitivity cells.

        They are tagged `case: "sensitivity"` rather than left untagged, which the first
        draft of the composer assumed — reading them as cases would have printed ninety
        "scenarios" per method, which is a working paper rather than a view.
        """
        await _valued(scene)

        content = (await view_content(scene["session"], job=scene["job"])).content

        assert content is not None
        assert "scenarios" not in content
        assert len(content["base"]) == 2

    async def test_the_block_says_why_the_two_methods_differ(self, scene: dict[str, Any]) -> None:
        """ADR 0132 §2: each method's implied version of the other's assumption, as the
        base case struck it — the figures that say the gap is two assumptions that cannot
        both hold, rather than a width to split."""
        await _valued(scene)

        content = (await view_content(scene["session"], job=scene["job"])).content
        struck = {
            str(row.id): row
            for row in await scene["session"].scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        }

        assert content is not None
        rows = content["why_they_differ"]
        assert [row["label"] for row in rows] == [
            "Perpetual growth the exit multiple implies",
            "Exit multiple the perpetuity method implies",
        ]
        assert [struck[row["calculation_id"]].name for row in rows] == [
            "implied_terminal_growth",
            "implied_exit_multiple",
        ]
        assert all(struck[row["calculation_id"]].parameters["case"] == "base" for row in rows), (
            "the grid's cells strike their own implied figures, and they are a working paper"
        )

    async def test_the_implied_multiple_prints_as_a_multiple(self, scene: dict[str, Any]) -> None:
        """Both implied figures are dimensionless, and the display layer reads a pure
        number by its label's words, percentage words first: a label saying "growth"
        printed the multiple 6.4 as 640%."""
        await _valued(scene)

        rendered = serialise_markdown(await _document(scene))
        growth = next(line for line in rendered.splitlines() if "exit multiple implies" in line)
        multiple = next(line for line in rendered.splitlines() if "perpetuity method" in line)

        assert "%" in growth
        assert "\N{MULTIPLICATION SIGN}" in multiple
        assert "%" not in multiple

    async def test_the_distance_from_the_price_is_stated_where_a_price_exists(
        self, scene: dict[str, Any]
    ) -> None:
        await _valued(scene, price=_price("40"))

        content = (await view_content(scene["session"], job=scene["job"])).content

        assert content is not None
        # Labelled "upside" so the figure reads as the percentage it is. Without the word
        # the display layer took its reading from whichever *other* word the label
        # carried, and the two rows came out "0.7%" and "-0.01x" — the same kind of
        # figure in two notations, neither of them chosen.
        assert [row["label"] for row in content["against_the_price"]] == [
            "Upside on the perpetuity growth value",
            "Upside on the exit multiple value",
        ]

    async def test_no_price_costs_the_distance_and_nothing_else(
        self, scene: dict[str, Any]
    ) -> None:
        await _valued(scene)

        content = (await view_content(scene["session"], job=scene["job"])).content

        assert content is not None
        assert "against_the_price" not in content
        assert content["base"], "a valuation with no price is still a valuation"

    async def test_a_run_with_no_valuation_has_no_view(self, scene: dict[str, Any]) -> None:
        """The block is omitted rather than filled with apologies."""
        await seed_years(scene, _YEARS)

        assert (await view_content(scene["session"], job=scene["job"])).content is None


class TestTheViewReachesTheDocument:
    async def test_every_figure_carries_a_marker_that_resolves(self, scene: dict[str, Any]) -> None:
        """The property the whole phase turns on: a figure with a note leading to its
        arithmetic, rather than a figure asserted."""
        await _valued(scene, price=_price("40"))

        document = await _document(scene)

        assert document.view
        assert document.footnotes
        assert all(isinstance(note, CalculationFootnote) for note in document.footnotes), (
            "every marker in the view resolves to a calculation, never to a broken citation"
        )

    async def test_the_markers_are_the_documents_first(self, scene: dict[str, Any]) -> None:
        """A position a reader meets after eighteen sections is one they meet last."""
        await _valued(scene, price=_price("40"))

        rendered = serialise_markdown(await _document(scene))

        assert rendered.index(VIEW_TITLE) < rendered.index("## At a glance")
        assert "[^1]" in rendered.split("## At a glance")[0]

    async def test_the_header_says_what_each_method_gives_and_never_a_range(
        self, scene: dict[str, Any]
    ) -> None:
        """ADR 0132 §1: two figures, each named by its method, joined by "and" — never "to",
        which is a claim neither method makes."""
        await _valued(scene, price=_price("40"))

        document = await _document(scene)
        rendered = serialise_markdown(document)

        shown = document.header.method_values
        assert shown is not None
        assert "(perpetuity growth) and " in shown
        assert shown.endswith("(exit multiple) a share")
        assert " to " not in shown
        assert f"**What each method gives:** {shown}" in rendered

    async def test_the_view_line_says_the_report_takes_no_side(self, scene: dict[str, Any]) -> None:
        """The report states no view (ADR 0135), and the line says so as a decision rather
        than as a view nobody reached — the words the round read as the document failing
        to conclude."""
        await _valued(scene, price=_price("40"))

        rendered = serialise_markdown(await _document(scene))

        assert f"**Non-binding view:** {NO_VIEW}" in rendered
        assert "no view reached" not in rendered
        assert "none stated" not in rendered

    async def test_a_run_with_no_valuation_states_no_figures_and_no_view(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)

        document = await _document(scene)
        rendered = serialise_markdown(document)

        assert document.header.method_values is None
        assert "What each method gives" not in rendered
        assert f"**Non-binding view:** {NO_VIEW}" in rendered
        assert "no view reached" not in rendered

    async def test_the_masthead_and_the_block_cannot_disagree(self, scene: dict[str, Any]) -> None:
        """Composed from the same rows, so the masthead and the table under it agree by
        construction rather than by two readings happening to match — in the block's order,
        each beside its own method."""
        await _valued(scene)

        document = await _document(scene)
        content = (await view_content(scene["session"], job=scene["job"])).content

        assert content is not None
        shown = document.header.method_values
        assert shown is not None
        methods = ("perpetuity growth", "exit multiple")
        for row, method in zip(content["base"], methods, strict=True):
            money = display.money(Decimal(row["value"]), "USD", style=HouseStyle())
            assert f"{money} ({method})" in shown

    async def test_the_block_is_titled_for_what_it_holds(self, scene: dict[str, Any]) -> None:
        """Not "The view", and its first group not a base case "by method" a reader takes
        for a band: what the valuation gives, by terminal method, and why the two differ."""
        await _valued(scene, price=_price("40"))

        rendered = serialise_markdown(await _document(scene))
        block = rendered.split("## At a glance")[0]

        assert VIEW_TITLE == "What the valuation gives"
        assert VIEW_TITLE in block
        assert "By terminal method" in block
        assert "Why the two methods differ" in block

    async def test_the_one_page_summary_keeps_the_conclusion(self, scene: dict[str, Any]) -> None:
        """A summary carrying the evidence and not the position answers nothing."""
        await _valued(scene, price=_price("40"))

        summary = summary_document(await _document(scene))

        assert summary.view
        assert summary.footnotes, "and the footnotes its figures need survive the trim"

    async def test_no_adjective_reaches_the_block(self, scene: dict[str, Any]) -> None:
        """ADR 0117: a range, a method, a distance and the levers. What the figures mean
        is the operator's half, and it is not composed."""
        await _valued(scene, price=_price("40"))

        rendered = serialise_markdown(await _document(scene)).lower()
        view = rendered.split("## at a glance")[0]

        for adjective in ("attractive", "compelling", "cautious", "cheap", "expensive"):
            assert adjective not in view

    async def test_the_levers_are_named_in_words(self, scene: dict[str, Any]) -> None:
        """Phase 1.4's ratchet: `terminal_growth` is a code identifier, not a word."""
        await _valued(scene, price=_price("40"))

        rendered = serialise_markdown(await _document(scene))
        view = rendered.split("## At a glance")[0]

        assert "terminal_growth" not in view
        assert "wacc" not in view.lower() or "discount rate" in view

    async def test_the_figures_are_the_ledgers_own(self, scene: dict[str, Any]) -> None:
        """Composed means assembled. A renderer that computed would produce a figure no
        ledger row accounts for and no footnote could point at."""
        await _valued(scene, price=_price("40"))

        content = (await view_content(scene["session"], job=scene["job"])).content
        stored = {
            str(row.id)
            for row in await scene["session"].scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        }

        assert content is not None
        for block in content.values():
            for row in block:
                assert row["calculation_id"] in stored
