"""The document is checked against itself: one figure, one period, one value, no denials.

Gap C6, then ADR 0125. The live report's self-contradiction — a recorded ratio against the
very lines shown beside it — was caught by the red team, a model, hours after the
disagreeing rows were sitting in one database. These tests pin the deterministic
replacement over all three of its passes: the facts a report *publishes*, the calculations
it publishes, and the sentences in which it denies a figure it prints.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import DisagreementKind, ResolutionRule, position_figure
from aer.core.enums import ClaimKind, FactBasis
from aer.db.models import (
    Calculation,
    FinancialFact,
    ReportSection,
    SectionDefinition,
    SectionStatus,
)
from aer.services.citations import record_claim
from aer.services.consistency import check_report_consistency
from aer.services.disagreements import disagreements_for_job
from aer.storage.local import LocalArtefactStore
from tests.scene_fixtures import build_scene

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    return await build_scene(db_session, store)


@pytest.fixture
def store(tmp_path: Any) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=4_194_304)


async def _fact(
    session: AsyncSession,
    scene: dict[str, Any],
    *,
    value: str,
    period_end: date = date(2025, 9, 27),
    period_start: date | None = date(2024, 9, 29),
    fiscal_period: str = "FY",
    fiscal_year: int = 2025,
    concept: str = "revenue",
    unit: str = "USD",
    filed: date = date(2025, 10, 30),
) -> FinancialFact:
    company = scene.get("company")
    if company is None:
        from aer.db.models import Company  # noqa: PLC0415

        company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
        session.add(company)
        await session.flush()
        scene["company"] = company
    row = FinancialFact(
        company_id=company.id,
        source_document_id=scene["document"].id,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        fiscal_period=fiscal_period,
        fiscal_year=fiscal_year,
        basis=FactBasis.AS_REPORTED,
        filed_date=filed,
    )
    session.add(row)
    await session.flush()
    return row


async def _publish(session: AsyncSession, scene: dict[str, Any], fact: FinancialFact) -> None:
    await record_claim(
        session,
        section=scene["section"],
        kind=ClaimKind.NUMERIC,
        text=f"The recorded {fact.concept} is {fact.value} {fact.unit}.",
        financial_fact_id=fact.id,
    )


async def _calculation(
    session: AsyncSession,
    scene: dict[str, Any],
    *,
    name: str = "net_margin",
    value: str = "0.294",
    unit: str = "percent",
    period_label: str | None = "FY2025",
    period_start: date | None = date(2024, 9, 29),
    period_end: date | None = date(2025, 9, 27),
    case: str | None = None,
    sequence: int = 0,
    sources: tuple[str, ...] = (),
    parameters: dict[str, Any] | None = None,
) -> Calculation:
    settings: dict[str, Any] = dict(parameters or {})
    if case is not None:
        settings["case"] = case
    row = Calculation(
        job_id=scene["job"].id,
        name=name,
        formula=f"{name} = a / b",
        function_ref=f"aer.calc.ratios:{name}",
        code_version="test",
        inputs=[
            {"name": f"input_{index}", "value": "1", "unit": "USD", "source": {"id": identifier}}
            for index, identifier in enumerate(sources)
        ],
        parameters=settings,
        output_value=Decimal(value),
        output_unit=unit,
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        sequence=sequence,
    )
    session.add(row)
    await session.flush()
    return row


async def _publish_calculation(
    session: AsyncSession,
    scene: dict[str, Any],
    calculation: Calculation,
    *,
    section: ReportSection | None = None,
) -> None:
    await record_claim(
        session,
        section=section if section is not None else scene["section"],
        kind=ClaimKind.NUMERIC,
        text=f"The recorded {calculation.name} is {calculation.output_value}.",
        calculation_id=calculation.id,
    )


async def _second_section(
    session: AsyncSession, scene: dict[str, Any], *, content: dict[str, Any]
) -> ReportSection:
    """Another section of the same run, so a denial and a figure can sit in different ones."""
    definitions = list(
        await session.scalars(select(SectionDefinition).order_by(SectionDefinition.position))
    )
    other = next(row for row in definitions if row.key != scene["section"].section_key)
    section = ReportSection(
        job_id=scene["job"].id,
        section_definition_id=other.id,
        section_key=other.key,
        position=other.position,
        status=SectionStatus.GENERATED,
        content=content,
    )
    session.add(section)
    await session.flush()
    return section


class TestTheCheck:
    async def test_two_published_values_for_one_span_record_a_conflict(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )

        recorded = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert (recorded.facts, recorded.total) == (1, 1)
        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert row.kind is DisagreementKind.SOURCE_CONFLICT
        assert row.topic == "revenue, FY2025"

    async def test_agreeing_values_record_nothing(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="245122000000", filed=date(2025, 11, 15)),
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0
        assert await disagreements_for_job(db_session, scene["job"].id) == []

    async def test_different_periods_are_not_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The live failure's other half. An annual figure beside a quarterly one is a
        labelling problem — the period stamp (gap C1) — not a disagreement, and comparing
        them here would flag every well-labelled report."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="391035000000"))
        await _publish(
            db_session,
            scene,
            await _fact(
                db_session,
                scene,
                value="94930000000",
                period_start=date(2025, 3, 30),
                period_end=date(2025, 6, 28),
                fiscal_period="Q3",
            ),
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_fact_nobody_published_is_not_compared(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The check is over what the report shows a reader, never the whole store —
        two rows nobody printed cannot contradict anyone."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        # Stored, never cited.
        await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15))

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_figure_row_in_content_counts_as_published(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The second channel a value reaches the page by: a section figure row naming
        its fact — the same convention the numeral rule accepts as lineage."""
        cited = await _fact(db_session, scene, value="245122000000")
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )
        scene["section"].content = {
            "figures": [
                {
                    "label": "Revenue",
                    "value": str(cited.value),
                    "financial_fact_id": str(cited.id),
                }
            ]
        }
        await db_session.flush()

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).facts == 1

    async def test_running_twice_records_the_conflict_once(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The fingerprint's dedupe holds here too: a resumed run must not double its
        disagreements."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )

        first = await check_report_consistency(db_session, job_id=scene["job"].id)
        second = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert first.facts == 1
        # The second pass finds the same fingerprint already recorded and adds nothing.
        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1
        assert second.facts == 1  # the existing row is returned, not duplicated


class TestPublishedCalculations:
    """ADR 0125's first pass. The AstraZeneca run's adversary compared FY2025 draft figures
    against FY2021 calculations of the same name and published eight false challenges; the
    rule that forbids it for facts had never been extended to the arithmetic."""

    async def test_two_published_values_for_one_figure_record_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, value="1.721", sequence=1),
        )

        recorded = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert (recorded.calculations, recorded.total) == (1, 1)
        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert row.kind is DisagreementKind.SELF_CONTRADICTION
        assert row.rule is ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF
        assert row.topic == "net margin, FY2025"
        assert row.material is True

    async def test_the_rationale_claims_no_filing_and_no_tier(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The reason the ladder is not run. Both sides are this run's own arithmetic, and
        a rationale saying two regulators filed them on one day would be the appendix
        lying to make a code path shorter."""
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="1.721", sequence=1)
        )

        await check_report_consistency(db_session, job_id=scene["job"].id)

        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert "T1_REGULATORY" not in row.resolution_rationale
        assert "filed" not in row.resolution_rationale
        assert "this run's own arithmetic" in row.resolution_rationale
        assert position_figure(row.position_a).endswith("(this run's own arithmetic)")
        assert position_figure(row.position_b).endswith("(this run's own arithmetic)")

    async def test_the_same_figure_struck_twice_alike_is_one_figure(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A resumed step and a revised section both re-strike the ratio suite. Two
        identical results are one figure however many rows carry it."""
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, value="0.29400000", sequence=1),
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_two_cases_of_one_figure_are_two_answers(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A bear-case value per share beside a base-case one answers two questions."""
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(
                db_session, scene, name="value_per_share", value="485.29", case="base"
            ),
        )
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(
                db_session,
                scene,
                name="value_per_share",
                value="311.40",
                case="bear",
                sequence=1,
            ),
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_sensitivity_cell_is_not_an_answer(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Every cell of the grid shares a name, a case and an absent period, so admitting
        them would record one eighty-one-way disagreement about a grid that is working."""
        from aer.calc.dcf import SENSITIVITY_CASE  # noqa: PLC0415

        for index, value in enumerate(("410.00", "485.29", "560.10")):
            await _publish_calculation(
                db_session,
                scene,
                await _calculation(
                    db_session,
                    scene,
                    name="value_per_share",
                    value=value,
                    case=SENSITIVITY_CASE,
                    period_label=None,
                    period_start=None,
                    period_end=None,
                    sequence=index,
                ),
            )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_different_periods_are_not_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(
                db_session,
                scene,
                value="0.271",
                period_label="FY2024",
                period_start=date(2023, 10, 1),
                period_end=date(2024, 9, 28),
                sequence=1,
            ),
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_calculation_nobody_published_is_not_compared(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        # Recorded in the ledger, printed nowhere. A run strikes hundreds of these.
        await _calculation(db_session, scene, value="1.721", sequence=1)

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_one_figure_measured_two_ways_records_no_distance(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A margin published once as a fraction and once in currency is a defect in the
        run, and there is no percentage difference between the two to report."""
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, value="9690000000", unit="USD", sequence=1),
        )

        assert (
            await check_report_consistency(db_session, job_id=scene["job"].id)
        ).calculations == 1
        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert row.relative_difference is None

    async def test_a_figure_row_in_content_counts_as_published(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        printed = await _calculation(db_session, scene, value="0.294")
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="1.721", sequence=1)
        )
        scene["section"].content = {
            "figures": [{"label": "Net margin", "calculation_id": str(printed.id)}]
        }
        await db_session.flush()

        assert (
            await check_report_consistency(db_session, job_id=scene["job"].id)
        ).calculations == 1

    async def test_one_function_serving_three_ratios_is_three_figures(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The defect the dry run over the committed records found. ``days_outstanding``
        is one function struck three times a period — days sales, days inventory, days
        payable — and on the msft2 record those three read 115.2, 90.6 and 3.9 days for
        FY2025. Only the sources say which is which; a key of name and period would call a
        working ratio suite a contradiction three times a year."""
        for index, (value, pair) in enumerate(
            (
                ("115.212851954321", ("receivables", "revenue")),
                ("90.568517414207", ("inventory", "cost_of_revenue")),
                ("3.898054217759", ("payables", "cost_of_revenue")),
            )
        ):
            await _publish_calculation(
                db_session,
                scene,
                await _calculation(
                    db_session,
                    scene,
                    name="days_outstanding",
                    value=value,
                    unit="day",
                    sources=pair,
                    sequence=index,
                ),
            )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_forecast_series_is_not_a_disagreement(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The dry run's other finding. A discount factor takes the year as a *parameter*
        rather than an input, so ten forecast years are ten rows with one name, one absent
        period and identical inputs — and the parameters are what tell them apart."""
        for year, value in enumerate(("0.9174", "0.8417", "0.7722"), start=1):
            await _publish_calculation(
                db_session,
                scene,
                await _calculation(
                    db_session,
                    scene,
                    name="discount_factor",
                    value=value,
                    unit="pure",
                    period_label=None,
                    period_start=None,
                    period_end=None,
                    sources=("wacc",),
                    parameters={"year": year},
                    sequence=year,
                ),
            )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_the_same_question_answered_twice_still_fires(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The other side of both: identical function, period, inputs and parameters, two
        answers. That is a defect in the run, and it is all this pass is for."""
        for index, value in enumerate(("0.9174", "0.8417")):
            await _publish_calculation(
                db_session,
                scene,
                await _calculation(
                    db_session,
                    scene,
                    name="discount_factor",
                    value=value,
                    unit="pure",
                    period_label=None,
                    period_start=None,
                    period_end=None,
                    sources=("wacc",),
                    parameters={"year": 1},
                    sequence=index,
                ),
            )

        assert (
            await check_report_consistency(db_session, job_id=scene["job"].id)
        ).calculations == 1

    async def test_running_twice_records_the_contradiction_once(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="0.294")
        )
        await _publish_calculation(
            db_session, scene, await _calculation(db_session, scene, value="1.721", sequence=1)
        )

        first = await check_report_consistency(db_session, job_id=scene["job"].id)
        second = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert (first.calculations, second.calculations) == (1, 1)
        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1


class TestNegativeAssertions:
    """ADR 0125's second pass, and the defect every judge named. msft1's executive summary
    denied a value per share thirty lines above one; its key risks called operating cash
    flow unavailable while six sections cited $182.9bn."""

    async def test_a_section_denying_a_figure_another_prints(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="value_per_share", value="485.29"),
        )
        await _second_section(
            db_session,
            scene,
            content={"body": "No value per share sits on this record."},
        )

        recorded = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert recorded.denials == 1
        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert row.kind is DisagreementKind.SELF_CONTRADICTION
        assert row.topic == "value per share is denied and printed"
        assert row.relative_difference is None

    async def test_the_record_quotes_the_sentence_and_shows_no_figure_for_it(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="value_per_share", value="485.29"),
        )
        await _second_section(
            db_session, scene, content={"body": "No value per share sits on this record."}
        )

        await check_report_consistency(db_session, job_id=scene["job"].id)

        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert "No value per share sits on this record." in row.resolution_rationale
        assert position_figure(row.position_a) == "no figure"
        assert position_figure(row.position_b).startswith("485.29")

    async def test_a_section_denying_what_it_prints_itself(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The msft1 case, and the worse one: the denial and the table are the same page."""
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="value_per_share", value="485.29"),
        )
        scene["section"].content = {"body": "A value per share is not available here."}
        await db_session.flush()

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 1

    async def test_a_denial_of_a_figure_nobody_prints_is_honest(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A research report saying what it could not establish is doing its job."""
        scene["section"].content = {"body": "A value per share is not available here."}
        await db_session.flush()

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).total == 0

    async def test_a_sentence_that_is_not_a_denial_is_not_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="value_per_share", value="485.29"),
        )
        await _second_section(db_session, scene, content={"body": "The value per share is 485.29."})

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 0

    async def test_a_denial_naming_another_period_is_not_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The check's most obvious false positive, refused by construction: an FY2025 free
        cash flow does not answer a sentence about FY2021."""
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="free_cash_flow", value="74071000000"),
        )
        await _second_section(
            db_session,
            scene,
            content={"body": "Free cash flow for FY2021 is not disclosed."},
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 0

    async def test_a_denial_naming_the_period_that_is_printed(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="free_cash_flow", value="74071000000"),
        )
        await _second_section(
            db_session,
            scene,
            content={"body": "Free cash flow for FY2025 is not disclosed."},
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 1

    async def test_a_one_word_figure_is_never_the_subject_of_a_denial(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """ "No evidence of revenue growth by segment" is not a denial that revenue exists,
        and a check that read it as one would be switched off within a week."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _second_section(
            db_session,
            scene,
            content={"body": "There is no evidence of revenue growth by segment."},
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 0

    async def test_a_denied_fact_is_found_as_well_as_a_denied_calculation(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """msft1's key risks called operating cash flow unavailable while six sections
        cited it. A concept reaches the page the same two ways a calculation does."""
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="182901000000", concept="operating_cash_flow"),
        )
        await _second_section(
            db_session,
            scene,
            content={"body": "Operating cash flow is not among the figures available here."},
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 1

    async def test_a_clause_that_names_one_figure_and_denies_another(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The dry run's third finding, quoted from the msft2 record. Read whole, the
        sentence reports a contradiction that is not there: it names short-term investments
        affirmatively and denies a netted leverage figure, in two clauses."""
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="55900000000", concept="short_term_investments"),
        )
        await _second_section(
            db_session,
            scene,
            content={
                "body": (
                    "Cash of $20.9 billion and short-term investments of $55.9 billion sit "
                    "against those borrowings, and no netted leverage figure is recorded here."
                )
            },
        )

        assert (await check_report_consistency(db_session, job_id=scene["job"].id)).denials == 0

    async def test_running_twice_records_the_denial_once(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish_calculation(
            db_session,
            scene,
            await _calculation(db_session, scene, name="value_per_share", value="485.29"),
        )
        await _second_section(
            db_session, scene, content={"body": "No value per share sits on this record."}
        )

        first = await check_report_consistency(db_session, job_id=scene["job"].id)
        second = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert (first.denials, second.denials) == (1, 1)
        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1
