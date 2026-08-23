"""The exhibits service and the chart pack's place in the document.

The scene seeds exactly what a real run records — revenue facts behind a source document,
margin calculations whose inputs cite those facts, case-tagged scenario valuations, a
sensitivity grid — and the tests read it back through ``exportable_charts_for`` into an
assembled document, holding the properties ADR 0043 names: geometry from rows, markers in
the global sequence, licensed charts refused at the assembler.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.charts import Chart
from aer.core.enums import JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    Scenario,
    SectionDefinition,
    SectionStatus,
    Sensitivity,
    SensitivityCell,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.render.document import assemble_document
from aer.render.html import render_html
from aer.render.markdown import serialise_markdown
from aer.services.exhibits import (
    _segment_label,
    exportable_charts_for,
    internal_charts_for,
)
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

GENERATED_AT = datetime(2022, 7, 2, 9, 30, tzinfo=UTC)
RETRIEVED_AT = datetime(2022, 7, 1, 12, 0, tzinfo=UTC)


def _calc(
    job_id: uuid.UUID,
    *,
    name: str,
    value: str,
    unit: str = "USD/share",
    parameters: dict[str, Any] | None = None,
    inputs: list[dict[str, Any]] | None = None,
    sequence: int = 0,
) -> Calculation:
    return Calculation(
        job_id=job_id,
        name=name,
        formula=f"{name} = recorded",
        function_ref=f"aer.calc.test:{name}",
        code_version="exhibitcode1234",
        inputs=inputs or [],
        parameters=parameters or {},
        output_value=Decimal(value),
        output_unit=unit,
        sequence=sequence,
    )


def _fact_input(fact_id: uuid.UUID, *, name: str) -> dict[str, Any]:
    """One recorded input in the shape the calculation engine persists."""
    return {
        "name": name,
        "value": "1",
        "unit": "USD",
        "source": {"kind": "fact", "id": str(fact_id), "label": name},
    }


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="exhibits@example.invalid", display_name="Exhibits", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        work_order_id=request.id,
        request_id=request.id,
        workflow_version="exhibit_scene_v1",
        code_version="exhibitcode1234",
        status=JobStatus.RUNNING,
        started_at=GENERATED_AT,
    )
    db_session.add(job)
    await db_session.flush()

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    artefact = Artefact(
        sha256="a" * 64, media_type="text/html", size_bytes=10, storage_key="exhibit/a"
    )
    db_session.add(artefact)
    await db_session.flush()

    source = SourceDocument(
        work_order_id=request.id,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        title="Form 10-K, fiscal 2022",
        publisher="US Securities and Exchange Commission",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=RETRIEVED_AT,
        publication_date=date(2022, 6, 15),
        quarantined=False,
    )
    db_session.add(source)
    await db_session.flush()

    facts: dict[int, FinancialFact] = {}
    for year, value in ((2021, "168088000000"), (2022, "198270000000")):
        fact = FinancialFact(
            company_id=company.id,
            source_document_id=source.id,
            concept="revenue",
            unit="USD",
            value=Decimal(value),
            period_end=date(year, 6, 30),
            fiscal_year=year,
            fiscal_period="FY",
            filed_date=date(year, 7, 28),
        )
        db_session.add(fact)
        facts[year] = fact
    await db_session.flush()

    margin = _calc(
        job.id,
        name="net_margin",
        value="0.34",
        unit="ratio",
        inputs=[
            _fact_input(facts[2022].id, name="net_income"),
            _fact_input(facts[2022].id, name="revenue"),
        ],
    )
    db_session.add(margin)
    # A margin whose inputs cite two different periods cannot be placed on a year, and a
    # quarterly revenue fact is not history at the chart's grain. Both stay off the chart.
    db_session.add(
        _calc(
            job.id,
            name="gross_margin",
            value="0.68",
            unit="ratio",
            inputs=[
                _fact_input(facts[2021].id, name="gross_profit"),
                _fact_input(facts[2022].id, name="revenue"),
            ],
        )
    )
    db_session.add(
        FinancialFact(
            company_id=company.id,
            source_document_id=source.id,
            concept="revenue",
            unit="USD",
            value=Decimal("50120000000"),
            period_end=date(2022, 9, 30),
            fiscal_year=2023,
            fiscal_period="Q1",
            filed_date=date(2022, 10, 25),
        )
    )

    for index, (key, label, per_share) in enumerate(
        (
            ("bear", "Bear case", "210.50"),
            ("base", "Base case", "280.00"),
            ("bull", "Bull case", "341.25"),
        )
    ):
        db_session.add(
            Scenario(
                request_id=request.id,
                job_id=job.id,
                key=key,
                label=label,
                description=f"The {label.lower()}, as stated.",
            )
        )
        db_session.add(
            _calc(
                job.id,
                name="value_per_share",
                value=per_share,
                parameters={"method": "gordon_growth", "case": key},
                sequence=index + 1,
            )
        )
    # The base case's second terminal method, so the football field has a real spread.
    db_session.add(
        _calc(
            job.id,
            name="value_per_share",
            value="262.40",
            parameters={"method": "exit_multiple", "case": "base"},
            sequence=10,
        )
    )
    await db_session.flush()

    cell_calcs = [
        _calc(job.id, name="value_per_share", value=str(240 + 10 * i), sequence=20 + i)
        for i in range(4)
    ]
    db_session.add_all(cell_calcs)
    await db_session.flush()

    grid = Sensitivity(
        request_id=request.id,
        job_id=job.id,
        label="Value per share against WACC and terminal growth",
        x_assumption="wacc",
        y_assumption="terminal_growth",
        output_name="value_per_share",
        output_unit="USD/share",
    )
    db_session.add(grid)
    await db_session.flush()
    for index, (x, y) in enumerate(((7, 1), (7, 2), (8, 1), (8, 2))):
        db_session.add(
            SensitivityCell(
                sensitivity_id=grid.id,
                x_value=Decimal(f"0.0{x}"),
                y_value=Decimal(f"0.0{y}"),
                output_value=Decimal(240 + 10 * index),
                calculation_id=cell_calcs[index].id,
            )
        )
    await db_session.flush()

    return {
        "session": db_session,
        "job": job,
        "request": request,
        "company": company,
        "source": source,
    }


class TestTheExportablePack:
    async def test_the_pack_reads_the_run_back(self, scene: dict[str, Any]) -> None:
        charts = await exportable_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )

        by_key = {chart.key: chart for chart in charts}
        # Four exhibits with data; the fifth is omitted below, not apologised for.
        assert set(by_key) == {
            "revenue_margin_history",
            "scenario_bridge",
            "sensitivity_heatmap",
            "football_field",
        }
        assert all(chart.exportable for chart in charts)

        revenue = by_key["revenue_margin_history"]
        assert not revenue.placeholder
        assert "FY2021" in revenue.svg
        assert "FY2022" in revenue.svg
        assert "Net margin" in revenue.svg  # the margin's period came back through its inputs
        kinds = {ref.kind for ref in revenue.citations}
        assert kinds == {"source_document", "calculation"}
        # Two full-year revenue points and one margin point: the quarterly fact stays off
        # a history drawn at fiscal-year grain, and the cross-period margin cannot be
        # placed on a year, so neither is drawn.
        assert len(revenue.citations) == 3
        assert "Gross margin" not in revenue.svg
        assert "FY2023" not in revenue.svg  # the quarterly fact's year never appears

        bridge = by_key["scenario_bridge"]
        assert not bridge.placeholder
        # In the scenario service's canonical order (by key), not seeding order.
        assert [ref.label for ref in bridge.citations] == [
            "Value per share — Base case",
            "Value per share — Bear case",
            "Value per share — Bull case",
        ]

        heatmap = by_key["sensitivity_heatmap"]
        assert not heatmap.placeholder
        assert len(heatmap.citations) == 4

        field = by_key["football_field"]
        assert not field.placeholder
        assert "DCF, terminal methods" in field.svg
        assert "Scenario range" in field.svg

        # The exhibit with no data disappears from the pack rather than printing an
        # apology into the document (gap R11): this scene records no dimensioned facts,
        # and the segment mix never estimates a breakdown from prose. The coverage
        # notice, not a picture of absence, is where a thin run says so.
        assert "segment_mix" not in by_key

    async def test_a_run_that_recorded_nothing_gets_no_exhibits(
        self, scene: dict[str, Any]
    ) -> None:
        bare_job = Job(
            work_order_id=scene["request"].id,
            request_id=scene["request"].id,
            workflow_version="exhibit_scene_v1",
            code_version="exhibitcode1234",
            status=JobStatus.RUNNING,
            started_at=GENERATED_AT,
        )
        scene["session"].add(bare_job)
        await scene["session"].flush()

        charts = await exportable_charts_for(
            scene["session"], job=bare_job, request=scene["request"]
        )
        assert charts == ()

    async def test_the_licence_note_reaches_the_field_caption(self, scene: dict[str, Any]) -> None:
        note = "Comparable multiples are withheld from exportable surfaces under licence."
        charts = await exportable_charts_for(
            scene["session"], job=scene["job"], request=scene["request"], licence_note=note
        )
        field = next(chart for chart in charts if chart.key == "football_field")
        assert note in field.caption

    async def test_an_untagged_valuation_cannot_reach_the_bridge(
        self, scene: dict[str, Any]
    ) -> None:
        """Rows recorded before the ``case`` parameter existed stay off the chart —
        positional guessing is exactly what the parameter replaced."""
        session: AsyncSession = scene["session"]
        session.add(
            Scenario(
                request_id=scene["request"].id,
                job_id=scene["job"].id,
                key="untagged",
                label="Untagged case",
                description="A case whose valuation predates attribution.",
            )
        )
        session.add(_calc(scene["job"].id, name="value_per_share", value="999", sequence=50))
        await session.flush()

        charts = await exportable_charts_for(session, job=scene["job"], request=scene["request"])
        bridge = next(chart for chart in charts if chart.key == "scenario_bridge")
        assert "Untagged case" not in bridge.svg
        assert len(bridge.citations) == 3


def _segment_fact(
    scene: dict[str, Any],
    *,
    member: str,
    value: str,
    axis: str = "us-gaap:StatementBusinessSegmentsAxis",
    year: int = 2022,
) -> FinancialFact:
    return FinancialFact(
        company_id=scene["company"].id,
        source_document_id=scene["source"].id,
        concept="revenue",
        unit="USD",
        value=Decimal(value),
        period_start=date(year - 1, 7, 1),
        period_end=date(year, 6, 30),
        fiscal_year=year,
        fiscal_period="FY",
        dimension_axis=axis,
        dimension_member=member,
        filed_date=date(year, 7, 28),
    )


class TestTheSegmentExhibit:
    """The chart that rendered its placeholder on the live run, now fed from rows."""

    async def _charts(self, scene: dict[str, Any]) -> dict[str, Chart]:
        charts = await exportable_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )
        return {chart.key: chart for chart in charts}

    async def test_dimensioned_revenue_draws_the_chart(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(_segment_fact(scene, member="msft:CloudSegmentMember", value="91200000000"))
        session.add(_segment_fact(scene, member="msft:DevicesSegmentMember", value="60300000000"))
        await session.flush()

        chart = (await self._charts(scene))["segment_mix"]

        assert not chart.placeholder
        assert "Cloud" in chart.svg
        assert "Devices" in chart.svg
        assert "FY2022" in chart.caption
        assert {ref.kind for ref in chart.citations} == {"source_document"}

    async def test_the_member_qname_becomes_a_readable_label(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(
            _segment_fact(scene, member="msft:GreaterChinaSegmentMember", value="70000000000")
        )
        session.add(_segment_fact(scene, member="msft:IPhoneMember", value="20000000000"))
        await session.flush()

        chart = (await self._charts(scene))["segment_mix"]

        assert "Greater China" in chart.svg
        assert "IPhone" in chart.svg, "an initialism must not be split into letters"

    async def test_an_elimination_row_is_not_a_segment(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(_segment_fact(scene, member="msft:CloudSegmentMember", value="91200000000"))
        session.add(
            _segment_fact(
                scene, member="us-gaap:IntersegmentEliminationMember", value="-1200000000"
            )
        )
        await session.flush()

        chart = (await self._charts(scene))["segment_mix"]

        assert "Elimination" not in chart.svg

    async def test_one_axis_is_drawn_when_the_filing_tags_two(self, scene: dict[str, Any]) -> None:
        """The reportable segments the company itself defines win over the product split,
        deterministically — the exhibit must not change subject when a filer adds a row."""
        session: AsyncSession = scene["session"]
        session.add(_segment_fact(scene, member="msft:CloudSegmentMember", value="91200000000"))
        session.add(
            _segment_fact(
                scene,
                member="msft:AzureProductMember",
                value="45000000000",
                axis="srt:ProductOrServiceAxis",
            )
        )
        await session.flush()

        chart = (await self._charts(scene))["segment_mix"]

        assert "Cloud" in chart.svg
        assert "Azure Product" not in chart.svg

    async def test_the_latest_year_is_the_one_drawn(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(_segment_fact(scene, member="msft:CloudSegmentMember", value="91200000000"))
        session.add(
            _segment_fact(scene, member="msft:CloudSegmentMember", value="80000000000", year=2021)
        )
        await session.flush()

        chart = (await self._charts(scene))["segment_mix"]

        assert "FY2022" in chart.caption
        assert "FY2021" not in chart.caption

    async def test_the_revenue_history_never_reads_a_segment_row(
        self, scene: dict[str, Any]
    ) -> None:
        """The exclusion that keeps the aggregate honest: a segment's slice must not win
        a year from the consolidated line on any chart drawn at company grain."""
        session: AsyncSession = scene["session"]
        # Filed later than the consolidated FY2022 fact, so it would win `by_period`
        # if the query let it compete.
        session.add(
            FinancialFact(
                company_id=scene["company"].id,
                source_document_id=scene["source"].id,
                concept="revenue",
                unit="USD",
                value=Decimal("91200000000"),
                period_end=date(2022, 6, 30),
                fiscal_year=2022,
                fiscal_period="FY",
                dimension_axis="us-gaap:StatementBusinessSegmentsAxis",
                dimension_member="msft:CloudSegmentMember",
                filed_date=date(2022, 9, 30),
            )
        )
        await session.flush()

        chart = (await self._charts(scene))["revenue_margin_history"]

        # The consolidated bars survive: both years drawn, and the segment's citation
        # count does not inflate the chart's.
        assert "FY2021" in chart.svg
        assert "FY2022" in chart.svg
        assert len(chart.citations) == 3


class TestTheDocumentIntegration:
    async def _document_with_charts(self, scene: dict[str, Any]) -> Any:
        session: AsyncSession = scene["session"]
        definition = SectionDefinition(
            key="exhibit_probe",
            version=1,
            origin="builtin",
            title="Exhibit Probe",
            position=Decimal(100),
            required=False,
            output_contract={
                "type": "object",
                "properties": {
                    "figures": {
                        "type": "array",
                        "title": "Figures",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "calculation_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
            evidence_policy={"min_sources": 0, "requires_primary": False},
            token_budget=1000,
            allowed_tools=[],
            applicability={},
        )
        session.add(definition)
        await session.flush()
        margin_row = await session.get(Calculation, (await self._margin_id(scene)))
        assert margin_row is not None
        session.add(
            ReportSection(
                job_id=scene["job"].id,
                section_definition_id=definition.id,
                section_key=definition.key,
                position=definition.position,
                status=SectionStatus.GENERATED,
                content={
                    "figures": [
                        {
                            "label": "Net margin",
                            "value": "0.34",
                            "unit": "ratio",
                            "calculation_id": str(margin_row.id),
                        }
                    ],
                },
            )
        )
        await session.flush()

        charts = await exportable_charts_for(session, job=scene["job"], request=scene["request"])
        return await assemble_document(
            session,
            job=scene["job"],
            request=scene["request"],
            company=scene["company"],
            charts=charts,
            generated_at=GENERATED_AT,
        )

    async def _margin_id(self, scene: dict[str, Any]) -> uuid.UUID:
        session: AsyncSession = scene["session"]
        found = await session.scalar(
            select(Calculation.id).where(
                Calculation.job_id == scene["job"].id, Calculation.name == "net_margin"
            )
        )
        assert found is not None
        return found

    async def test_exhibit_markers_continue_the_global_numbering(
        self, scene: dict[str, Any]
    ) -> None:
        document = await self._document_with_charts(scene)

        assert document.charts
        # Reading order owns the numbers: the back-of-document pack's markers are the
        # document's last, after the glance block (gap R10) and every section.
        pack_markers = sum(len(chart.markers) for chart in document.charts)
        first_chart_marker = min(marker for chart in document.charts for marker in chart.markers)
        assert first_chart_marker == document.footnote_count - pack_markers + 1
        assert document.footnote_count == len(document.citations)
        assert len(document.footnotes) == document.footnote_count

    async def test_both_notations_carry_the_exhibits_and_agree_on_markers(
        self, scene: dict[str, Any]
    ) -> None:
        document = await self._document_with_charts(scene)
        markdown = serialise_markdown(document)
        html = render_html(document)

        assert "## Exhibits" in markdown
        assert "Rendered in the HTML and PDF editions" in markdown
        assert 'id="section-exhibits"' in html
        assert html.count("data:image/svg+xml;base64,") == len(document.charts)

        md_markers = re.findall(r"\[\^(\d+)\](?!:)", markdown)
        html_markers = re.findall(r'<sup class="fn-ref"[^>]*><a[^>]*href="#fn-(\d+)">', html)
        assert md_markers == html_markers

    async def test_a_claimed_exhibit_renders_beside_its_section(
        self, scene: dict[str, Any]
    ) -> None:
        """Gap N1: a section's definition claims its chart through data, the chart
        renders after that section in both notations, and the unclaimed rest keep the
        pack at the back. Marker numbering follows reading order."""
        session: AsyncSession = scene["session"]
        definition = SectionDefinition(
            key="claiming_probe",
            version=1,
            origin="builtin",
            title="Claiming Probe",
            position=Decimal(100),
            required=False,
            output_contract={
                "type": "object",
                "properties": {"commentary": {"type": "string", "title": "Commentary"}},
            },
            evidence_policy={
                "min_sources": 0,
                "requires_primary": False,
                "exhibits": ["revenue_margin_history"],
            },
            token_budget=1000,
            allowed_tools=[],
            applicability={},
        )
        session.add(definition)
        await session.flush()
        session.add(
            ReportSection(
                job_id=scene["job"].id,
                section_definition_id=definition.id,
                section_key=definition.key,
                position=definition.position,
                status=SectionStatus.GENERATED,
                content={"commentary": "The revenue trajectory under discussion."},
            )
        )
        await session.flush()

        charts = await exportable_charts_for(session, job=scene["job"], request=scene["request"])
        document = await assemble_document(
            session,
            job=scene["job"],
            request=scene["request"],
            company=scene["company"],
            charts=charts,
            generated_at=GENERATED_AT,
        )

        claiming = next(view for view in document.sections if view.key == "claiming_probe")
        assert [chart.key for chart in claiming.charts] == ["revenue_margin_history"]
        assert "revenue_margin_history" not in {chart.key for chart in document.charts}
        assert document.charts  # the unclaimed rest keep the pack at the back

        # Reading order owns the numbers: the claimed chart's markers directly follow
        # the section's own — offset by the glance block, whose markers come first —
        # and everything at the back is numbered after them.
        glance_markers = document.footnote_count - (
            sum(len(view.citations) for view in document.sections)
            + sum(len(chart.markers) for chart in document.charts)
            + sum(len(chart.markers) for view in document.sections for chart in view.charts)
        )
        claimed_markers = [m for chart in claiming.charts for m in chart.markers]
        assert claimed_markers[0] == glance_markers + len(claiming.citations) + 1
        assert min(m for chart in document.charts for m in chart.markers) > claimed_markers[-1]

        markdown = serialise_markdown(document)
        html = render_html(document)
        in_section = markdown.split("## Claiming Probe", 1)[1].split("\n## ", 1)[0]
        assert "### Revenue and margin history" in in_section
        section_html = html.split('id="section-claiming_probe"', 1)[1].split("</section>", 1)[0]
        assert 'id="exhibit-revenue_margin_history"' in section_html
        assert (
            'id="exhibit-revenue_margin_history"' not in html.split('id="section-exhibits"', 1)[1]
        )

        md_markers = re.findall(r"\[\^(\d+)\](?!:)", markdown)
        html_markers = re.findall(r'<sup class="fn-ref"[^>]*><a[^>]*href="#fn-(\d+)">', html)
        assert md_markers == html_markers

    async def test_the_assembler_refuses_licensed_geometry(self, scene: dict[str, Any]) -> None:
        smuggled = Chart(
            key="price_relative",
            title="Price (internal)",
            svg="<svg/>",
            caption="Licensed.",
            exportable=False,
        )
        with pytest.raises(ValidationError, match="Internal-only charts"):
            await assemble_document(
                session=scene["session"],
                job=scene["job"],
                request=scene["request"],
                charts=(smuggled,),
            )


class TestTheInternalSet:
    async def test_it_is_licensed_geometry_even_when_empty(self, scene: dict[str, Any]) -> None:
        charts = await internal_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )
        assert charts
        assert all(not chart.exportable for chart in charts)

    async def test_no_recorded_implied_values_means_no_comps_field(
        self, scene: dict[str, Any]
    ) -> None:
        charts = await internal_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )
        assert "football_field_internal" not in {chart.key for chart in charts}

    async def test_recorded_implied_values_draw_the_comps_band(self, scene: dict[str, Any]) -> None:
        """The band's ends are the run's own implied-value calculations, cited row by
        row — the comps range reaches the field only as recorded figures."""
        session: AsyncSession = scene["session"]
        for sequence, value in enumerate(("241.10", "297.40"), start=60):
            session.add(
                _calc(
                    scene["job"].id,
                    name="implied_value_per_share_from_ev_multiple",
                    value=value,
                    sequence=sequence,
                )
            )
        await session.flush()

        charts = await internal_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )

        field = next(chart for chart in charts if chart.key == "football_field_internal")
        assert not field.exportable
        assert "Comps (enterprise multiple)" in field.svg
        assert "Internal use only" in field.caption
        cited = [ref for ref in field.citations if "Implied value per share" in ref.label]
        assert len(cited) == 2


class TestASubtotalIsNotASegment:
    """Gap A55. The live chart drew Apple's "Product" total beside the very product
    lines it sums — the axis carries both — so every real segment was flattened by a
    bar that double-counts them. A subtotal is recognised by arithmetic, never by name:
    a member whose value equals, exactly, the sum of two or more of the others.
    """

    _AXIS = "srt:ProductOrServiceAxis"

    async def _chart(self, scene: dict[str, Any]) -> Chart:
        charts = await exportable_charts_for(
            scene["session"], job=scene["job"], request=scene["request"]
        )
        return {chart.key: chart for chart in charts}["segment_mix"]

    async def test_a_member_summing_the_others_is_suppressed(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(
            _segment_fact(
                scene, member="us-gaap:ProductMember", value="30000000000", axis=self._AXIS
            )
        )
        session.add(
            _segment_fact(scene, member="msft:IPhoneMember", value="20000000000", axis=self._AXIS)
        )
        session.add(
            _segment_fact(scene, member="msft:MacMember", value="10000000000", axis=self._AXIS)
        )
        session.add(
            _segment_fact(
                scene, member="us-gaap:ServiceMember", value="5000000000", axis=self._AXIS
            )
        )
        await session.flush()

        chart = await self._chart(scene)

        assert "IPhone" in chart.svg
        assert "Mac" in chart.svg
        assert "Service" in chart.svg
        assert "Product" not in chart.svg, "the subtotal double-counts its own components"

    async def test_a_filer_segmented_by_exactly_product_and_service_keeps_both(
        self, scene: dict[str, Any]
    ) -> None:
        """The reason the rule is arithmetic and not a name list: on an axis carrying
        only these two, they are the segmentation."""
        session: AsyncSession = scene["session"]
        session.add(
            _segment_fact(
                scene, member="us-gaap:ProductMember", value="30000000000", axis=self._AXIS
            )
        )
        session.add(
            _segment_fact(
                scene, member="us-gaap:ServiceMember", value="5000000000", axis=self._AXIS
            )
        )
        await session.flush()

        chart = await self._chart(scene)

        assert "Product" in chart.svg
        assert "Service" in chart.svg

    async def test_the_glued_conjunction_is_respaced_on_the_chart(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        session.add(
            _segment_fact(
                scene,
                member="aapl:WearablesHomeandAccessoriesMember",
                value="40000000000",
                axis=self._AXIS,
            )
        )
        session.add(
            _segment_fact(scene, member="msft:IPhoneMember", value="20000000000", axis=self._AXIS)
        )
        await session.flush()

        chart = await self._chart(scene)

        assert "Wearables Home and Accessories" in chart.svg
        assert "Homeand" not in chart.svg


class TestTheSegmentLabelSurgery:
    """The label repair beside the boundary rule: glued conjunctions are a table, not a
    pattern, because a pattern would mangle every geography ending in "land"."""

    def test_the_glued_conjunction_is_respaced(self) -> None:
        label = _segment_label("aapl:WearablesHomeandAccessoriesMember")

        assert label == "Wearables Home and Accessories"

    def test_a_geography_ending_in_land_is_untouched(self) -> None:
        assert _segment_label("msft:IrelandSegmentMember") == "Ireland"
        assert _segment_label("msft:EnglandMember") == "England"
