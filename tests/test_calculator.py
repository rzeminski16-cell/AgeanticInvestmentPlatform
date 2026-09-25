"""ADR 0133: the calculator strikes the report's own model over the operator's numbers.

The operator asked for a page per model "where the user can manually change the values and
see how that affects the results". What is under test is what makes that safe to offer on a
platform whose founding rule is that every figure is recorded: the calculator is proved to be
the report's own model before it is used, it refuses what the assumptions gate would refuse,
and it records nothing at all.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import HouseStyle, Settings
from aer.core.assumption_scales import scale_complaint
from aer.db.models import Calculation
from aer.services.calculator import calculator_view, result_rows
from tests.api_fixtures import build_app, client_for
from tests.assumption_fixtures import a_year, seed_years
from tests.db_cleanup import delete_all
from tests.valued_run_fixtures import CONFIRMED, SHARES, valued_run

pytestmark = pytest.mark.integration


@pytest.fixture
async def valued(db_session: AsyncSession) -> dict[str, Any]:
    return await valued_run(db_session)


async def _calculations(session: AsyncSession) -> int:
    counted = await session.scalar(select(func.count()).select_from(Calculation))
    return int(counted or 0)


class TestItIsTheReportsOwnModel:
    async def test_the_confirmed_values_reproduce_the_report(self, valued: dict[str, Any]) -> None:
        """The proof the page shows before anything else: struck over the confirmed values,
        the calculator gives the report's recorded value per share by both methods."""
        view = await calculator_view(valued["session"], job=valued["job"], entries={})

        assert view is not None
        assert view.reproduces
        assert not view.any_changed
        assert view.yours is None
        assert view.recorded.gordon_upside is not None, "a priced run carries its distance"

    async def test_a_run_whose_filings_have_moved_says_so(self, valued: dict[str, Any]) -> None:
        """A later year acquired since the report moves the base year: the confirmed values
        struck today no longer give the report's figures, and the page must say which it
        is showing rather than compare the operator's numbers with the wrong thing."""
        await seed_years(
            valued,
            {date(2025, 12, 31): a_year(revenue="1600", operating_income="420", **SHARES)},
        )

        view = await calculator_view(valued["session"], job=valued["job"], entries={})

        assert view is not None
        assert not view.reproduces
        assert view.as_confirmed is not None
        assert view.as_confirmed.gordon_per_share != view.recorded.gordon_per_share

    async def test_a_run_with_no_valuation_has_no_calculator(
        self, db_session: AsyncSession
    ) -> None:
        scene = await valued_run(db_session, value=False)

        assert await calculator_view(db_session, job=scene["job"], entries={}) is None


class TestTheOperatorsNumbers:
    async def test_a_changed_growth_rate_moves_only_what_it_should(
        self, valued: dict[str, Any]
    ) -> None:
        """Terminal growth feeds the perpetuity and not the exit multiple — so the one value
        moves and the other does not, which is the model behaving as the report's does."""
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"terminal_growth": "0.03"}
        )

        assert view is not None
        assert view.any_changed
        assert view.yours is not None
        assert view.as_confirmed is not None
        assert view.yours.gordon_per_share != view.as_confirmed.gordon_per_share
        assert view.yours.exit_multiple_per_share == view.as_confirmed.exit_multiple_per_share

    async def test_nothing_is_recorded(self, valued: dict[str, Any]) -> None:
        session = valued["session"]
        before = await _calculations(session)

        await calculator_view(
            session, job=valued["job"], entries={"exit_multiple": "14", "terminal_growth": "0.03"}
        )
        await session.flush()

        assert await _calculations(session) == before

    async def test_the_confirmed_value_typed_again_is_not_a_change(
        self, valued: dict[str, Any]
    ) -> None:
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"exit_multiple": "10.00"}
        )

        assert view is not None
        assert not view.any_changed

    async def test_an_implausible_entry_is_refused_with_the_gates_own_sentence(
        self, valued: dict[str, Any]
    ) -> None:
        """3 for a growth rate is 300% — the factor-of-a-hundred slip the assumptions form
        refuses, refused here in the same words and not applied."""
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"terminal_growth": "3"}
        )

        assert view is not None
        growth = next(item for item in view.inputs if item.name == "terminal_growth")
        assert growth.problem == scale_complaint("terminal_growth", Decimal(3), remedy="")
        assert "box" not in growth.problem, "there is no box here to tick"
        assert not growth.changed
        assert view.yours is None

    async def test_something_that_is_not_a_number_is_refused_not_raised(
        self, valued: dict[str, Any]
    ) -> None:
        """`Decimal("NaN")` parses; the page must say so rather than fail on it."""
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"terminal_growth": "NaN"}
        )

        assert view is not None
        growth = next(item for item in view.inputs if item.name == "terminal_growth")
        assert "not a number" in growth.problem
        assert not view.any_changed

    async def test_a_word_is_not_a_number(self, valued: dict[str, Any]) -> None:
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"exit_multiple": "fourteen"}
        )

        assert view is not None
        multiple = next(item for item in view.inputs if item.name == "exit_multiple")
        assert "not a number" in multiple.problem
        assert not view.any_changed

    async def test_growth_past_the_discount_rate_is_refused_by_the_arithmetic(
        self, valued: dict[str, Any]
    ) -> None:
        """Plausible as a number, impossible as a perpetuity: the arithmetic's own reason,
        and no figures in the operator's column."""
        view = await calculator_view(
            valued["session"], job=valued["job"], entries={"terminal_growth": "0.4"}
        )

        assert view is not None
        assert view.any_changed
        assert view.yours is None
        assert view.refusal

    async def test_only_what_the_model_reads_is_offered(self, valued: dict[str, Any]) -> None:
        """No box for the discount rate itself — it is decomposed, never supplied — and none
        for the cost of debt where the filings carry the interest to derive it."""
        view = await calculator_view(valued["session"], job=valued["job"], entries={"wacc": "0.5"})

        assert view is not None
        names = {item.name for item in view.inputs}
        assert names == set(CONFIRMED)
        assert not view.any_changed


class TestWhatThePagePrints:
    async def test_the_rows_read_as_the_report_reads(self, valued: dict[str, Any]) -> None:
        view = await calculator_view(valued["session"], job=valued["job"], entries={})
        assert view is not None

        rows = {row["label"]: row for row in result_rows(view, style=HouseStyle())}

        assert rows["Value per share, perpetuity growth"]["recorded"].startswith("$")
        assert rows["Exit multiple the perpetuity method implies"]["recorded"].endswith(
            "\N{MULTIPLICATION SIGN}"
        )
        assert rows["Discount rate"]["recorded"].endswith("%")
        assert "Distance from the price, exit multiple" in rows
        # Nothing changed, so the operator's column is the confirmed values struck today —
        # which reproduce the report, figure for figure as printed.
        for row in rows.values():
            assert row["yours"] == row["recorded"], row["label"]


# -- The page, over a committed run -----------------------------------------------------------


@pytest.fixture
async def served(api_settings: Settings, db_engine: Any, fake_redis: Any) -> Any:
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        scene = await valued_run(session)
        await session.commit()
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client, scene


def _visible(html: str) -> str:
    """The page's text as a reader sees it: no tags, so no attribute values."""
    return re.sub(r"<[^>]+>", " ", html)


class TestThePage:
    async def test_it_opens_on_the_proof_and_the_report_s_figures(self, served: Any) -> None:
        client, scene = served

        page = await client.get(f"/runs/{scene['job'].id}/calculator")

        assert page.status_code == 200
        assert "The calculator reproduces the report" in page.text
        assert 'id="calculator-results"' in page.text
        assert 'id="nothing-changed"' in page.text
        assert "A WHAT-IF, NEVER A RECORD" in page.text

    async def test_a_changed_box_is_struck_and_marked(self, served: Any) -> None:
        client, scene = served

        page = await client.get(
            f"/runs/{scene['job'].id}/calculator", params={"terminal_growth": "0.03"}
        )

        assert page.status_code == 200
        assert 'id="nothing-changed"' not in page.text
        assert 'value="0.03"' in page.text
        assert "changed" in _visible(page.text)

    async def test_a_refused_box_says_why_beside_it(self, served: Any) -> None:
        client, scene = served

        page = await client.get(
            f"/runs/{scene['job'].id}/calculator", params={"terminal_growth": "3"}
        )

        assert page.status_code == 200
        assert 'aria-invalid="true"' in page.text
        text = _visible(page.text)
        assert "outside the plausible range for the terminal growth" in text
        assert "terminal_growth" not in text

    async def test_no_code_identifier_reaches_the_reader(self, served: Any) -> None:
        """Phase 1.4's ratchet: the boxes are named by the assumption's key for the form,
        and a reader meets words."""
        client, scene = served

        text = _visible((await client.get(f"/runs/{scene['job'].id}/calculator")).text)

        for identifier in ("terminal_growth", "exit_multiple", "risk_free_rate", "ebit_margin"):
            assert identifier not in text
        assert "Terminal growth" in text
        # An acronym keeps its capitals: `capitalize` printed "Ebit margin".
        assert "EBIT margin" in text
        # And a coefficient reads without the column's twelve stored places.
        assert "1.100000000000" not in text

    async def test_the_valuation_page_links_to_it(self, served: Any) -> None:
        client, scene = served

        page = await client.get(f"/runs/{scene['job'].id}/valuation")

        assert f'href="/runs/{scene["job"].id}/calculator"' in page.text
