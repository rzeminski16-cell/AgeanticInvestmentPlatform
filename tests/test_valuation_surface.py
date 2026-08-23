"""The valuation surface: what a reader is told, and how far they have to click to check it.

The phase's user-visible outcome, so the tests that matter are about what reaches a reader
rather than about what the code computed.

* **Every figure links to its calculation, and every calculation to its inputs.** Two clicks
  from a per-share figure to the assumption underneath it — the standard `docs/archive/PLAN.md` set
  for evidence in Phase 2, applied to arithmetic. Asserted by walking the hrefs, so a broken
  link fails it.
* **Nothing is recomputed.** The page reads the run's ledger. A page that re-ran the valuation
  would show today's answer beside yesterday's report, and both would look authoritative — so
  the test amends an assumption after the run and asserts the page does not move.
* **A blocked sector shows the banner and no valuation.** Not a valuation with a banner above
  it: a number a reader has seen is a number they remember.
* **It works with JavaScript off**, asserted by parsing the served HTML rather than by driving
  a browser — a page whose content arrives by fetch renders empty here.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.calc.comps import (
    Audience,
    CompsTable,
    MultipleBasis,
    MultipleResult,
    PeerExclusion,
    PeerRow,
)
from aer.calc.dcf import DcfInputs, DriverPath, GridAxis, GridMeasure, TerminalMethod
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef, money
from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import ValuationModel, profile_for, unclassified_mandate
from aer.db.models import Calculation, Job, JobStep, ResearchRequest, User
from aer.services import approvals as approval_service
from aer.services import valuation as valuation_service
from aer.services.sectors import CLASSIFY_STEP, classification_payload
from aer.services.valuation_view import valuation_view
from aer.web.templating import DISCLAIMER
from tests.api_fixtures import build_app, client_for
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

EMAIL = "valuation@example.invalid"
ASSUMPTION = SourceRef.assumption("assumption-1")
FACT = SourceRef.financial_fact("fact-1")
MANDATE = unclassified_mandate(ValuationModel.DCF_FCFF, subject="TESTCO")
PERIOD_END = date(2024, 6, 30)


def rate(value: str) -> Quantity:
    return Quantity.of(Decimal(value), source=ASSUMPTION)


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def shares(value: str) -> Quantity:
    return Quantity.of(Decimal(value), "shares", source=FACT)


def flat(name: str, value: str, *, years: int = 3) -> DriverPath:
    return DriverPath.flat(name, rate(value), years=years)


def base_inputs(**overrides: Any) -> DcfInputs:
    inputs = DcfInputs(
        base_revenue=usd("1000"),
        revenue_growth=DriverPath("revenue_growth", (rate("0.10"), rate("0.08"), rate("0.06"))),
        ebit_margin=flat("ebit_margin", "0.20"),
        capex_intensity=flat("capex_intensity", "0.08"),
        depreciation_intensity=flat("depreciation_intensity", "0.05"),
        working_capital_intensity=flat("working_capital_intensity", "0.10"),
        opening_working_capital=usd("100"),
        tax_rate=rate("0.25"),
        wacc=rate("0.10"),
        terminal_growth=rate("0.02"),
        exit_multiple=rate("4.5"),
        net_debt=usd("500"),
        shares_outstanding=shares("100"),
        non_operating=(),
    )
    return replace(inputs, **overrides) if overrides else inputs


async def seed_scene(session: AsyncSession, *, email: str = EMAIL) -> dict[str, Any]:
    """A user, a request, a job, and a valuation the run recorded."""
    analyst = User(email=email, display_name="Analyst", role=UserRole.ANALYST)
    session.add(analyst)
    await session.flush()

    research_request = ResearchRequest(
        user_id=analyst.id,
        company_name="Testco plc",
        ticker="TEST",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(research_request)
    await session.flush()

    job = await seed_job(session, request=research_request)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    result = await valuation_service.run_valuation(
        session, job_id=job.id, inputs=base_inputs(), mandate=MANDATE
    )

    return {
        "analyst": analyst,
        "request": research_request,
        "job": job,
        "result": result,
    }


async def add_grid(session: AsyncSession, scene: dict[str, Any]) -> None:
    await valuation_service.run_sensitivity(
        session,
        request_id=scene["request"].id,
        job_id=scene["job"].id,
        inputs=base_inputs(),
        rows=GridAxis(field="wacc", values=(rate("0.09"), rate("0.10"), rate("0.11"))),
        columns=GridAxis(
            field="terminal_growth", values=(rate("0.01"), rate("0.02"), rate("0.03"))
        ),
        method=TerminalMethod.GORDON_GROWTH,
        measure=GridMeasure.VALUE_PER_SHARE,
        mandate=MANDATE,
        label="WACC against terminal growth",
    )


async def classify_as(session: AsyncSession, scene: dict[str, Any], sector_key: str) -> None:
    """Record and confirm a specialist classification, the way the workflow would."""
    profile = profile_for(sector_key)
    assert profile is not None
    output = {
        "sector_key": sector_key,
        "sector_label": profile.label,
        "rationale": f"Classified as {sector_key}.",
        "proposed_by": "sic_lookup",
        "allowed_models": [m.value for m in profile.allowed_models],
        "blocked_models": [m.value for m in profile.blocked_models],
        "warnings": list(profile.warnings),
    }
    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key=CLASSIFY_STEP,
            sequence=3,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{scene['job'].id}:{CLASSIFY_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()

    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash="1" * 64,
    )
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.SECTOR_SPECIALIST,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash=sha256_hex(canonical_json(classification_payload(output))),
    )


def comps_table() -> CompsTable:
    def multiple(value: str | None) -> MultipleResult:
        return MultipleResult(
            key="ev_ebitda",
            label="EV/EBITDA",
            quantity=(
                Quantity.of(Decimal(value), DIMENSIONLESS, source=FACT)
                if value is not None
                else None
            ),
            basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
            period_end=PERIOD_END,
            absent_because="" if value is not None else "EBITDA was negative",
        )

    return CompsTable(
        subject=PeerRow(
            identifier="SUBJ",
            name="Testco plc",
            period_end=PERIOD_END,
            multiples=(multiple("12"),),
        ),
        peers=(
            PeerRow(
                identifier="P1",
                name="Peer One plc",
                period_end=PERIOD_END,
                multiples=(multiple("10"),),
                rationale="Same industry",
            ),
            PeerRow(
                identifier="P2",
                name="Loss-making plc",
                period_end=PERIOD_END,
                multiples=(multiple(None),),
                rationale="Same end market",
            ),
        ),
        excluded=(
            PeerExclusion(
                identifier="P3",
                name="March year-end plc",
                reason="reports to 2024-03-31, 91 days from the subject's",
                period_end=date(2024, 3, 31),
            ),
        ),
        basis=MultipleBasis.TRAILING_TWELVE_MONTHS,
        as_of=PERIOD_END,
        peer_set_confirmed=True,
        licence_note="Licensed for internal use; no derived-data exemption.",
    )


# -- The read model ---------------------------------------------------------------------------


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    await db_session.execute(
        text("TRUNCATE research_requests, audit_events, users, artefacts RESTART IDENTITY CASCADE")
    )
    return await seed_scene(db_session)


class TestItReadsTheLedgerBack:
    async def test_both_terminal_methods_are_complete(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])

        assert view.gordon.is_complete
        assert view.exit_multiple.is_complete
        assert view.has_valuation

    async def test_the_two_methods_are_told_apart(self, db_session, scene):
        """They share every calculation name; the recorded method is what distinguishes them."""
        view = await valuation_view(db_session, scene["job"])

        assert view.gordon.value_per_share is not None
        assert view.exit_multiple.value_per_share is not None
        assert (
            view.gordon.value_per_share.calculation_id
            != view.exit_multiple.value_per_share.calculation_id
        )

    async def test_each_figure_matches_what_the_run_computed(self, db_session, scene):
        """To the precision the column holds.

        `calculations.output_value` is `NUMERIC(38,12)`, and the kernel computes at 34
        significant digits. The page shows the *stored* figure, which is the run's record —
        so the comparison is against the in-memory value quantized to twelve places rather
        than against the full one.
        """
        view = await valuation_view(db_session, scene["job"])
        result = scene["result"]

        places = Decimal("0.000000000001")
        assert view.gordon.value_per_share.value == result.gordon.value_per_share.value.quantize(
            places
        )
        assert (
            view.exit_multiple.value_per_share.value
            == result.exit_multiple.value_per_share.value.quantize(places)
        )

    async def test_every_figure_names_a_calculation(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])

        for outcome in (view.gordon, view.exit_multiple):
            for figure in (
                outcome.enterprise_value,
                outcome.equity_value,
                outcome.terminal_share,
                outcome.value_per_share,
            ):
                assert figure is not None
                assert uuid.UUID(figure.calculation_id)

    async def test_a_run_with_no_valuation_produces_no_figures(self, db_session):
        """It says so rather than computing one. A figure this page produced would be a
        figure the report does not contain."""
        analyst = User(
            email="valuation-empty@example.invalid", display_name="A", role=UserRole.ANALYST
        )
        db_session.add(analyst)
        await db_session.flush()
        research_request = ResearchRequest(
            user_id=analyst.id,
            company_name="Empty plc",
            ticker="EMPT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
        )
        db_session.add(research_request)
        await db_session.flush()
        job = await seed_job(db_session, request=research_request)

        view = await valuation_view(db_session, job)

        assert not view.has_valuation
        assert view.gordon.value_per_share is None

    async def test_it_does_not_recompute_when_an_assumption_changes(self, db_session, scene):
        """The whole reason this reads the ledger.

        Amending an assumption after a run must not move the page: it describes what the run
        recorded, and the report was written from that.
        """
        before = await valuation_view(db_session, scene["job"])

        # A different valuation entirely, run against a *different* job, so this job's ledger
        # is untouched. The page for the original job must not notice.
        other = await seed_scene(db_session, email="valuation-other@example.invalid")
        await valuation_service.run_valuation(
            db_session,
            job_id=other["job"].id,
            inputs=base_inputs(wacc=rate("0.20")),
            mandate=MANDATE,
        )

        after = await valuation_view(db_session, scene["job"])
        assert after.gordon.value_per_share.value == before.gordon.value_per_share.value


class TestTheGrid:
    async def test_a_stored_grid_is_pivoted_into_rows(self, db_session, scene):
        await add_grid(db_session, scene)

        view = await valuation_view(db_session, scene["job"])

        assert len(view.grids) == 1
        grid = view.grids[0]
        assert len(grid.x_values) == 3
        assert len(grid.rows) == 3
        assert grid.cell_count == 9

    async def test_every_cell_names_its_calculation(self, db_session, scene):
        await add_grid(db_session, scene)
        view = await valuation_view(db_session, scene["job"])

        for _, cells in view.grids[0].rows:
            for _, _, calculation_id in cells:
                assert uuid.UUID(calculation_id)

    async def test_a_run_with_no_grid_has_none(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])
        assert view.grids == ()


class TestTheSectorBanner:
    async def test_an_ordinary_company_gets_none(self, db_session, scene):
        """A report announcing "this is not a bank" on every run trains a reader to skip it."""
        view = await valuation_view(db_session, scene["job"])
        assert view.sector is None

    async def test_a_bank_gets_one_naming_the_blocked_model(self, db_session, scene):
        await classify_as(db_session, scene, "banks")

        view = await valuation_view(db_session, scene["job"])

        assert view.sector is not None
        assert view.sector.label
        assert ValuationModel.DCF_FCFF.value in view.sector.blocked_models
        assert view.sector.blocks_the_dcf

    async def test_a_sector_that_blocks_nothing_does_not_block_the_page(self, db_session, scene):
        await classify_as(db_session, scene, "utilities")

        view = await valuation_view(db_session, scene["job"])

        assert view.sector is not None
        assert not view.sector.blocks_the_dcf


class TestTheComps:
    async def test_an_internal_audience_gets_the_table(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"], comps=comps_table())

        assert isinstance(view.comps, CompsTable)
        assert view.comps.median_of("ev_ebitda") == Decimal(10)

    async def test_a_shareable_audience_gets_no_figure(self, db_session, scene):
        view = await valuation_view(
            db_session, scene["job"], comps=comps_table(), audience=Audience.SHAREABLE
        )

        assert not isinstance(view.comps, CompsTable)
        assert not hasattr(view.comps, "peers")

    async def test_a_run_with_no_comps_shows_none(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])
        assert view.comps is None


# -- The page ----------------------------------------------------------------------------------


@pytest.fixture
def settings(api_settings: Settings) -> Settings:
    return api_settings


@pytest.fixture
async def clean_slate(db_engine: Any) -> None:
    """Empty everything these tests write, before each one.

    The application commits for real, so its writes outlive the test that made them.
    Truncated at setup rather than teardown, for the reason `test_provenance_surfaces.py`
    gives: it is what the *next* test needs, and doing it here cannot contend with a
    transaction a finished test still holds open.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE sensitivity_cells, sensitivities, calculations, assumptions, "
                "report_sections, jobs, research_requests, audit_events, users "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def served(clean_slate: None, db_engine: Any, fake_redis: Any, settings: Settings) -> Any:
    """A client over an application that can see a committed valuation."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        built = await seed_scene(session)
        await add_grid(session, built)
        await session.commit()

    async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
        yield client, built


class TestTheValuationPage:
    async def test_it_renders_without_javascript(self, served):
        client, built = served

        response = await client.get(f"/runs/{built['job'].id}/valuation")
        html = response.text

        assert response.status_code == 200
        assert 'id="terminal-methods"' in html
        assert _body_scripts(html) == 0

    async def test_both_terminal_methods_are_shown(self, served):
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        assert "Gordon growth" in html
        assert "Exit multiple" in html

    async def test_the_terminal_share_sits_beside_the_per_share_figure(self, served):
        """Not in a footnote. A DCF whose terminal value is most of the answer is a statement
        about one assumption, and a reader has to meet that where they meet the number."""
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        assert "Terminal value share" in html
        assert 'id="figure-gordon_growth-terminal_share"' in html
        assert 'id="figure-gordon_growth-value_per_share"' in html

    async def test_every_figure_is_a_link_to_its_calculation(self, served):
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        for method in ("gordon_growth", "exit_multiple"):
            for key in (
                "enterprise_value",
                "equity_value",
                "terminal_share",
                "value_per_share",
            ):
                href = _href(html, f"figure-{method}-{key}")
                assert href.startswith("/calculations/"), f"{method}/{key}"

    async def test_the_grid_cells_link_to_their_calculations(self, served):
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        assert 'id="grid-0"' in html
        # Anchored on the attribute boundary: `data-href="..."` contains `href="..."`, so an
        # unanchored pattern would count a link that had been turned into a span. A sabotage
        # pass found exactly that.
        cells = re.findall(r'<a\s[^>]*\bhref="(/calculations/[0-9a-f-]+)"', html)
        assert len(cells) >= 9

    async def test_a_run_that_is_not_yours_is_not_found(self, served):
        client, _ = served
        assert (await client.get(f"/runs/{uuid.uuid4()}/valuation")).status_code == 404

    async def test_it_carries_the_disclaimer(self, served):
        client, built = served
        assert DISCLAIMER in (await client.get(f"/runs/{built['job'].id}/valuation")).text


class TestTwoClicksToAnOrigin:
    """Task 31's acceptance criterion: any input's origin in two clicks, links only."""

    async def test_the_first_click_reaches_the_calculation(self, served):
        client, built = served

        page = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        href = _href(page, "figure-gordon_growth-value_per_share")

        response = await client.get(href)

        assert response.status_code == 200
        assert 'id="output-value"' in response.text
        assert 'id="formula"' in response.text

    async def test_the_second_click_reaches_an_input(self, served):
        client, built = served

        page = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        calculation = (await client.get(_href(page, "figure-gordon_growth-value_per_share"))).text

        # Every input of `value_per_share` is itself a calculation, so the second click is a
        # link to one of them. Following it must land on a rendered page rather than a 404.
        onward = re.findall(r'<a\s[^>]*\bhref="(/calculations/[0-9a-f-]+)"', calculation)
        assert onward, "the calculation page offers nothing to click through to"

        response = await client.get(onward[0])
        assert response.status_code == 200
        assert 'id="lineage"' in response.text

    async def test_the_calculation_page_lists_what_it_rests_on(self, served):
        client, built = served

        page = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        html = (await client.get(_href(page, "figure-gordon_growth-value_per_share"))).text

        assert 'id="lineage"' in html
        assert "What it rests on" in html

    async def test_the_calculation_page_shows_the_recorded_method(self, served):
        """The parameter that tells the two valuations apart, shown rather than inferred."""
        client, built = served

        page = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        html = (await client.get(_href(page, "figure-gordon_growth-value_per_share"))).text

        assert 'id="parameters"' in html
        assert "gordon_growth" in html

    async def test_the_calculation_page_works_without_javascript(self, served):
        client, built = served

        page = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        html = (await client.get(_href(page, "figure-gordon_growth-value_per_share"))).text

        assert _body_scripts(html) == 0

    async def test_a_calculation_from_another_run_is_not_found(self, served, db_engine):
        """A calculation id is a UUID somebody could guess at."""
        client, _ = served
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            other = await seed_scene(session, email="valuation-stranger@example.invalid")
            await session.commit()
            calculation = await session.scalar(
                select(Calculation).where(Calculation.job_id == other["job"].id)
            )

        assert (await client.get(f"/calculations/{calculation.id}")).status_code == 404

    async def test_an_unknown_calculation_is_not_found(self, served):
        client, _ = served
        assert (await client.get(f"/calculations/{uuid.uuid4()}")).status_code == 404


class TestABlockedSectorShowsNoValuation:
    @pytest.fixture
    async def blocked(self, clean_slate, db_engine, fake_redis, settings):
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            built = await seed_scene(session)
            await classify_as(session, built, "banks")
            await session.commit()

        async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
            yield client, built

    async def test_the_banner_is_shown(self, blocked):
        client, built = blocked

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        assert 'id="sector-banner"' in html
        # Whitespace-normalised, because the template wraps the sentence across lines and a
        # contiguous substring search would be asserting about the template's line breaks.
        assert "blocked rather than discouraged" in _flat(html)

    async def test_no_valuation_is_shown_beside_it(self, blocked):
        """Not a valuation with a banner above it. A number a reader has seen is remembered."""
        client, built = blocked

        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text

        assert 'id="no-dcf"' in html
        assert 'id="terminal-methods"' not in html

    async def test_the_figures_exist_but_are_not_rendered(self, blocked, db_engine):
        """The run computed them before it was classified. The page still refuses to show
        them, which is the point: the block is about what a reader meets."""
        client, built = blocked
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, built["job"].id)
            view = await valuation_view(session, job)

        assert view.has_valuation
        html = (await client.get(f"/runs/{built['job'].id}/valuation")).text
        assert 'id="no-dcf"' in html


def _flat(html: str) -> str:
    """The page with runs of whitespace collapsed, for asserting on sentences."""
    return re.sub(r"\s+", " ", html)


def _body_scripts(html: str) -> int:
    """Scripts the page itself adds, ignoring the ones the shell loads in ``<head>``."""
    _, _, body = html.partition("</head>")
    return len(re.findall(r"<script", body))


def _href(html: str, element_id: str) -> str:
    match = re.search(rf'<a\s[^>]*href="([^"]+)"[^>]*id="{element_id}"', html)
    assert match is not None, f"no link with id {element_id!r}"
    return match.group(1)


class TestTheFiguresTheReaderIsWarnedAbout:
    """Two things the page states rather than leaving a reader to work out."""

    async def test_a_high_terminal_share_is_flagged(self, db_session, scene):
        """These inputs put four-fifths of the answer beyond the forecast. That is a
        statement about one assumption, not about the years anybody can check."""
        view = await valuation_view(db_session, scene["job"])

        assert view.gordon.terminal_share.value > Decimal("0.75")
        assert view.gordon.terminal_share_is_high

    async def test_a_modest_terminal_share_is_not_flagged(self, db_session, scene):
        """So the flag means something when it appears."""
        view = await valuation_view(db_session, scene["job"])
        modest = replace(
            view.gordon,
            terminal_share=replace(view.gordon.terminal_share, value=Decimal("0.4")),
        )

        assert not modest.terminal_share_is_high

    async def test_the_two_methods_disagreeing_is_quantified(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])

        assert view.methods_disagree is not None
        assert view.methods_disagree > Decimal("0.25")

    async def test_it_is_computed_from_the_two_recorded_figures(self, db_session, scene):
        view = await valuation_view(db_session, scene["job"])

        gordon = view.gordon.value_per_share.value
        exit_multiple = view.exit_multiple.value_per_share.value
        low, high = min(gordon, exit_multiple), max(gordon, exit_multiple)

        assert view.methods_disagree == (high - low) / low

    async def test_a_run_with_one_method_missing_reports_no_disagreement(self, db_session, scene):
        """`None`, not zero. "We could not compare" and "they agree" are different claims."""
        view = await valuation_view(db_session, scene["job"])
        crippled = replace(view, exit_multiple=replace(view.exit_multiple, value_per_share=None))

        assert crippled.methods_disagree is None


class TestAResumedRunShowsItsLastState:
    """A resumed run is a *later transaction*, and that is what makes the ordering work.

    Two valuations persisted inside one transaction share a `created_at` — Postgres `now()`
    is transaction-start time — and their sequences overlap, because sequence restarts at zero
    for each context. Nothing recorded says which came second, and the page says so by not
    pretending. These tests therefore commit between the two runs, which is what a resumed run
    actually does.
    """

    async def test_the_later_valuation_wins(self, db_session, scene):
        before = await valuation_view(db_session, scene["job"])
        await db_session.commit()

        await valuation_service.run_valuation(
            db_session,
            job_id=scene["job"].id,
            inputs=base_inputs(wacc=rate("0.15")),
            mandate=MANDATE,
        )

        after = await valuation_view(db_session, scene["job"])

        assert after.gordon.value_per_share.value != before.gordon.value_per_share.value
        assert (
            after.gordon.value_per_share.calculation_id
            != before.gordon.value_per_share.calculation_id
        )

    async def test_a_higher_discount_rate_lowers_the_answer(self, db_session, scene):
        """Which is what proves the *second* run is the one being shown, not merely a
        different row."""
        before = await valuation_view(db_session, scene["job"])
        await db_session.commit()

        await valuation_service.run_valuation(
            db_session,
            job_id=scene["job"].id,
            inputs=base_inputs(wacc=rate("0.15")),
            mandate=MANDATE,
        )

        after = await valuation_view(db_session, scene["job"])
        assert after.gordon.value_per_share.value < before.gordon.value_per_share.value


class TestAnotherOperatorSeesNothing:
    """A job id and a calculation id are both UUIDs somebody could hold."""

    @pytest.fixture
    async def stranger(self, clean_slate, db_engine, fake_redis, settings):
        """A server whose signed-in user is *not* the one who owns the run.

        `get_current_user` returns the earliest-created user, so the owner is seeded second.
        """
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                User(
                    email="valuation-first@example.invalid",
                    display_name="First",
                    role=UserRole.ANALYST,
                )
            )
            await session.flush()
            built = await seed_scene(session, email="valuation-owner@example.invalid")
            await session.commit()
            calculation = await session.scalar(
                select(Calculation).where(Calculation.job_id == built["job"].id)
            )
            built["calculation_id"] = calculation.id

        async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
            yield client, built

    async def test_the_valuation_page_is_not_found(self, stranger):
        client, built = stranger
        assert (await client.get(f"/runs/{built['job'].id}/valuation")).status_code == 404

    async def test_the_calculation_page_is_not_found(self, stranger):
        client, built = stranger
        assert (await client.get(f"/calculations/{built['calculation_id']}")).status_code == 404


class TestTheLedgerKeepsItsOrder:
    """Migration 0019. The engine says order is significant; the database used to lose it."""

    async def test_the_stored_sequence_matches_the_context(self, db_session, scene):
        rows = list(
            await db_session.scalars(
                select(Calculation)
                .where(Calculation.job_id == scene["job"].id)
                .order_by(Calculation.sequence)
            )
        )

        assert [row.sequence for row in rows] == list(range(len(rows)))

    async def test_every_row_of_one_context_shares_a_timestamp(self, db_session, scene):
        """Which is why `sequence` had to exist. Postgres `now()` is transaction-start time,
        so the timestamp cannot order a ledger and never could."""
        rows = list(
            await db_session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        )

        assert len({row.created_at for row in rows}) == 1
        assert len({row.sequence for row in rows}) == len(rows)

    async def test_an_input_is_never_written_before_the_row_it_cites(self, db_session, scene):
        """The property the engine's own docstring claims, now checkable from the database."""
        rows = list(
            await db_session.scalars(
                select(Calculation)
                .where(Calculation.job_id == scene["job"].id)
                .order_by(Calculation.sequence)
            )
        )
        position = {str(row.id): row.sequence for row in rows}

        for row in rows:
            for item in row.inputs:
                cited = str(item.get("source_id", ""))
                if cited in position:
                    assert position[cited] < row.sequence, row.name
