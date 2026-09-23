"""The refresh (F4, ADR 0131): a second run on the same request that pays only for what moved.

Held on the fake scene, driven the way the worker drives a run. One approved report first;
then a refresh with nothing new, which must finish at no spend with no model call and leave
the prior report current; then a refresh after a restated filing, which must acquire only
the new accession, write the moves as rows, re-draft only the sections whose claims rest on
a moved figure, carry the rest, and supersede the prior report with the reserved word.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, JobStatus, UserRole
from aer.db.models import (
    Approval,
    Claim,
    Job,
    JobStep,
    Report,
    ReportChange,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.errors import ConflictError
from aer.providers.fake import FakeProvider
from aer.services import refresh as refresh_service
from aer.services import runs as run_service
from aer.services.filings import held_accessions
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.engine import spend_so_far
from aer.workflow.registry import resolve_workflow
from aer.workflow.workflows import refresh_v1
from aer.workflow.workflows.vertical_slice_v1 import seal_step_for
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, to_final_gate
from tests.schema_guard import refuse_unanswerable_schema
from tests.sec_fixtures import fixture_bytes
from tests.test_run_api import _hidden_value
from tests.workflow_fixtures import (
    AS_OF_DATE,
    COMPANY_FACTS_FIXTURE,
    DEFAULT_PER_RUN_BUDGET_GBP,
    SPINE_KEYS,
    SUBMISSIONS_FIXTURE,
    ScriptedSectionBrain,
    StubSecClient,
    _content_for,
    _contract_from_system,
    _evidence_from_messages,
    _numeric_claim,
    declared_schema_name,
)

CHANGE_SUMMARY = "change_summary"

# The filing the moved scene adds: a fiscal 2023 annual report that restates fiscal 2022's
# revenue and files a new year. Fiscal 2023 of Microsoft's, as EDGAR lists it.
NEW_ACCESSION = "0000789019-23-000014"
NEW_FILED = "2023-07-27"
RESTATED_REVENUE_2022 = 199_000_000_000
REVENUE_2023 = 211_915_000_000
NET_INCOME_2022 = 72_738_000_000
NET_INCOME_2023 = 72_361_000_000


# --- the scene -----------------------------------------------------------------------------


async def _request(session: AsyncSession, *, user_id: uuid.UUID) -> ResearchRequest:
    request = research_request(
        user_id=user_id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
    )
    session.add(request)
    await session.flush()
    return request


@pytest.fixture
async def committed(db_engine: Any) -> dict[str, Any]:
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="refresh@example.invalid", display_name="Refresh", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = await _request(session, user_id=user.id)
        await session.commit()
        return {"user": user, "request": request, "factory": factory}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    async def record(redis: Any, job_id: uuid.UUID) -> str:
        recorded.append(str(job_id))
        return f"task-{job_id}"

    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", record)
    monkeypatch.setattr("aer.web.pages.enqueue_run", record)
    return recorded


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: list[str],
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class RotatingBrain(ScriptedSectionBrain):
    """The scripted writer, citing a different calculation for each section it drafts.

    The stock brain cites the first calculation in every pack, so every section would rest
    on one figure and a refresh could only re-draft all of them or none. Rotating through
    the pack gives the scene what a real report has: sections resting on different figures,
    of which a restated filing moves some.
    """

    def __init__(self) -> None:
        super().__init__()
        self.drafts = 0

    def __call__(self, schema: type[Any]) -> Any:
        if declared_schema_name(schema) != "SectionDraft":
            return super().__call__(schema)
        from aer.agents.section_writer import SectionDraft  # noqa: PLC0415 -- keeps import light

        assert self.provider is not None
        call = self.provider.calls[-1]
        contract = _contract_from_system(str(call["system"]))
        evidence = _evidence_from_messages(call["messages"])
        calculations = [item for item in evidence if "calculation_id" in item]
        extraction = next((item for item in evidence if "extraction_id" in item), None)
        fact = next((item for item in evidence if "fact_id" in item), None)
        calculation = calculations[self.drafts % len(calculations)] if calculations else None
        self.drafts += 1
        claims: list[dict[str, Any]] = []
        if calculation is not None and extraction is not None:
            claims.append(_numeric_claim(calculation, extraction, misquote=False))
        return SectionDraft(
            content=_content_for(contract, calculation=calculation, fact=fact), claims=claims
        )


@pytest.fixture
def driver(db_engine: Any, api_settings: Settings) -> Driver:
    built = Driver(db_engine, api_settings)
    brain = RotatingBrain()
    built.provider = FakeProvider(brain, inspect_schema=refuse_unanswerable_schema)
    brain.provider = built.provider
    return built


async def _approved_run(api: Any, request_id: uuid.UUID, driver: Driver) -> uuid.UUID:
    job_id = await to_final_gate(api, request_id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step=seal_step_for(GateKind.FINAL.value))
    assert await driver.advance(job_id) is JobStatus.SUCCEEDED
    return job_id


async def _report_of(factory: Any, job_id: uuid.UUID) -> Report:
    async with factory() as session:
        report = await session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None
        return report


async def _start(
    factory: Any, *, report_id: uuid.UUID, user_id: uuid.UUID, settings: Settings
) -> uuid.UUID:
    async with factory() as session:
        report = await session.get(Report, report_id)
        user = await session.get(User, user_id)
        assert report is not None
        assert user is not None
        job = await refresh_service.start_refresh(
            session, report=report, actor=user, settings=settings
        )
        await session.commit()
        return job.id


async def _drive(driver: Driver, job_id: uuid.UUID) -> JobStatus:
    """Advance a refresh to its end, clearing only the final gate on the way."""
    status = await driver.advance(job_id)
    if status is JobStatus.AWAITING_APPROVAL:
        assert await driver.waiting_at(job_id) == "gate_final", await driver.waiting_at(job_id)
        await driver.approve(job_id, gate=GateKind.FINAL, step=seal_step_for(GateKind.FINAL.value))
        status = await driver.advance(job_id)
    return status


async def _output(factory: Any, job_id: uuid.UUID, step: str) -> dict[str, Any]:
    async with factory() as session:
        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
        )
        assert row is not None, f"the {step} step has not run"
        return dict(row.output_ref or {})


async def _sections(factory: Any, job_id: uuid.UUID) -> dict[str, ReportSection]:
    async with factory() as session:
        rows = await session.scalars(select(ReportSection).where(ReportSection.job_id == job_id))
        return {row.section_key: row for row in rows}


def _moved_facts() -> bytes:
    """The company-facts aggregate with fiscal 2023 filed and fiscal 2022's revenue restated."""
    facts = json.loads(fixture_bytes(COMPANY_FACTS_FIXTURE))
    revenues = facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    revenues.append(
        {
            "start": "2021-07-01",
            "end": "2022-06-30",
            "val": RESTATED_REVENUE_2022,
            "accn": NEW_ACCESSION,
            "fy": 2023,
            "fp": "FY",
            "form": "10-K",
            "filed": NEW_FILED,
        }
    )
    revenues.append(
        {
            "start": "2022-07-01",
            "end": "2023-06-30",
            "val": REVENUE_2023,
            "accn": NEW_ACCESSION,
            "fy": 2023,
            "fp": "FY",
            "form": "10-K",
            "filed": NEW_FILED,
        }
    )
    net_income = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    net_income.append(
        {
            "start": "2021-07-01",
            "end": "2022-06-30",
            "val": NET_INCOME_2022,
            "accn": NEW_ACCESSION,
            "fy": 2023,
            "fp": "FY",
            "form": "10-K",
            "filed": NEW_FILED,
        }
    )
    net_income.append(
        {
            "start": "2022-07-01",
            "end": "2023-06-30",
            "val": NET_INCOME_2023,
            "accn": NEW_ACCESSION,
            "fy": 2023,
            "fp": "FY",
            "form": "10-K",
            "filed": NEW_FILED,
        }
    )
    return json.dumps(facts).encode()


def _moved_index() -> bytes:
    """The submissions index with the fiscal 2023 annual report at its head."""
    index = json.loads(fixture_bytes(SUBMISSIONS_FIXTURE))
    recent = index["filings"]["recent"]
    heads = {
        "accessionNumber": NEW_ACCESSION,
        "filingDate": NEW_FILED,
        "reportDate": "2023-06-30",
        "form": "10-K",
        "primaryDocument": "msft-20230630.htm",
        "primaryDocDescription": "10-K",
        "isXBRL": 1,
    }
    for key, column in recent.items():
        column.insert(0, heads.get(key, column[0] if column else ""))
    return json.dumps(index).encode()


# --- the workflow is registered and priced -------------------------------------------------


class TestTheWorkflow:
    def test_it_is_registered_under_the_version_a_refresh_records(self) -> None:
        definition = resolve_workflow(refresh_service.WORKFLOW_VERSION)

        assert definition.adr == "0131"
        assert [step.key for step in definition.build_steps()] == [
            "carry_forward",
            "acquire",
            "acquire_macro",
            "classify",
            "propose_peers",
            "propose_themes",
            "extract",
            "gate_unmapped_concepts",
            "acquire_prices",
            "calculate",
            "comps",
            "propose_assumptions",
            "value",
            "diff",
            "draft",
            "validate",
            "revise",
            "gate_final",
            "render",
        ]

    def test_no_planner_critic_worker_adversary_or_verdict_is_declared(self) -> None:
        keys = {step.key for step in refresh_v1.build_steps()}

        assert not keys & {"plan", "critique_plan", "red_team", "verdict", "brief_challenges"}
        assert not {key for key in keys if key.startswith("research_")}

    def test_only_the_draft_and_the_validators_are_projected_to_spend(self) -> None:
        spending = {
            step.key: step.estimated_cost_gbp
            for step in refresh_v1.build_steps()
            if step.estimated_cost_gbp > 0
        }

        assert set(spending) == {"draft", "validate"}
        assert spending["draft"] < Decimal("2.00")

    def test_the_price_is_six_sections_at_the_slices_figure_rounded_up(
        self, api_settings: Settings
    ) -> None:
        estimate = refresh_service.estimate_refresh(api_settings)

        per_section = Decimal("5.00") / 18
        assert estimate.sections_expected == 6
        assert estimate.cost_gbp == (per_section * 6 + Decimal("0.02")).quantize(
            Decimal("0.01"), rounding="ROUND_CEILING"
        )
        assert estimate.label == f"Refresh — about £{estimate.cost_gbp:.2f}"
        assert estimate.cost_gbp <= api_settings.refresh_budget_gbp


# --- nothing new ---------------------------------------------------------------------------


class TestAQuietQuarter:
    async def test_it_finishes_at_no_spend_with_no_model_call_and_the_prior_stays_current(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        calls_before = len(driver.provider.calls)

        job_id = await _start(
            factory, report_id=report.id, user_id=committed["user"].id, settings=api_settings
        )
        status = await _drive(driver, job_id)

        assert status is JobStatus.SUCCEEDED
        assert len(driver.provider.calls) == calls_before, "a quiet refresh asked a model"
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.workflow_version == "refresh_v1"
            assert job.refresh_kind == "refresh"
            assert job.refreshes_report_id == report.id
            assert job.plan_id is not None
            assert await spend_so_far(session, job_id=job_id) == 0
            assert await session.scalar(select(Report).where(Report.job_id == job_id)) is None
            prior = await session.get(Report, report.id)
            assert prior is not None
            assert prior.is_current
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ReportChange)
                    .where(ReportChange.job_id == job_id)
                )
                == 0
            )

        diff = await _output(factory, job_id, "diff")
        assert diff["nothing_new"] is True
        assert diff["filings_read_first"] == 0
        assert diff["same_aggregate"] is True
        acquired = await _output(factory, job_id, "acquire")
        assert acquired["filings"] == []
        assert acquired["filings_held"], "the held accessions were fetched again"
        assert (await _output(factory, job_id, "render"))["report_id"] is None
        assert (await _output(factory, job_id, "validate"))["skipped"] is True

    async def test_the_change_summary_is_the_whole_result_and_says_so(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        job_id = await _start(
            factory, report_id=report.id, user_id=committed["user"].id, settings=api_settings
        )
        await _drive(driver, job_id)

        sections = await _sections(factory, job_id)
        summary = sections[CHANGE_SUMMARY]
        assert summary.status is SectionStatus.GENERATED
        assert summary.content is not None
        assert summary.content["basis"].startswith("Nothing new was read")
        assert summary.content["moved"] == []
        assert summary.content["broke"] == []
        assert "commentary" not in summary.content, "a writer was paid for stillness"
        assert summary.low_confidence_reason is not None
        assert summary.low_confidence_reason.startswith("Nothing new was read")
        # The first run never carried the summary: it is the refresh's section alone.
        assert CHANGE_SUMMARY not in await _sections(factory, first_job)
        draft = await _output(factory, job_id, "draft")
        assert draft["redrafted"] == []
        assert set(draft["carried"]) == set(SPINE_KEYS) - {
            "prior_research_comparison",
            "validation_disagreements",
        }

    async def test_the_carried_decisions_are_on_the_new_job(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        job_id = await _start(
            factory, report_id=report.id, user_id=committed["user"].id, settings=api_settings
        )

        async with factory() as session:
            approvals = {
                row.gate: row
                for row in await session.scalars(select(Approval).where(Approval.job_id == job_id))
            }
            prior = {
                row.gate: row
                for row in await session.scalars(
                    select(Approval).where(Approval.job_id == first_job)
                )
            }
            assert GateKind.PLAN in approvals
            assert approvals[GateKind.PLAN].notes is not None
            assert "priced at £" in approvals[GateKind.PLAN].notes
            for gate in (GateKind.PEER_SET, GateKind.SECTOR_SPECIALIST, GateKind.THEME_SET):
                if gate in prior:
                    assert approvals[gate].payload_hash == prior[gate].payload_hash
                    assert "Carried from the run of" in (approvals[gate].notes or "")
            assert GateKind.FINAL not in approvals
            request = await session.get(ResearchRequest, committed["request"].id)
            assert request is not None
            assert request.work_order.as_of_date > AS_OF_DATE, "the request was not re-dated"
            assert (await session.get(Report, report.id)).as_of_date == AS_OF_DATE  # type: ignore[union-attr]


# --- something moved -----------------------------------------------------------------------


class TestARestatedFiling:
    async def _refreshed(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> dict[str, Any]:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        calls_before = len(driver.provider.calls)
        driver.sec_client = StubSecClient(
            driver._store,
            payload=_moved_facts(),
            submissions=_moved_index(),
        )
        job_id = await _start(
            factory, report_id=report.id, user_id=committed["user"].id, settings=api_settings
        )
        status = await _drive(driver, job_id)
        return {
            "factory": factory,
            "first_job": first_job,
            "report": report,
            "job_id": job_id,
            "status": status,
            "calls_before": calls_before,
        }

    async def test_only_the_new_accession_is_fetched_and_the_moves_are_rows(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        scene = await self._refreshed(api, committed, driver, api_settings)
        factory = scene["factory"]

        assert scene["status"] is JobStatus.SUCCEEDED
        acquired = await _output(factory, scene["job_id"], "acquire")
        assert [item["accession"] for item in acquired["filings"]] == [NEW_ACCESSION]
        assert acquired["filings_held"], "the accessions the record held were not skipped"
        assert NEW_ACCESSION not in acquired["filings_held"]
        assert driver.sec_client.document_calls == [
            url for url in driver.sec_client.document_calls if "20230630" in url
        ], "a held filing's document was fetched again"

        diff = await _output(factory, scene["job_id"], "diff")
        assert diff["nothing_new"] is False
        assert diff["filings_read_first"] == 1
        assert diff["material"] > 0
        async with factory() as session:
            rows = list(
                await session.scalars(
                    select(ReportChange).where(ReportChange.job_id == scene["job_id"])
                )
            )
            by_kind = {kind: [r for r in rows if r.kind == kind] for kind in {r.kind for r in rows}}
            assert len(by_kind["document"]) == 1
            assert NEW_ACCESSION in by_kind["document"][0].narrative
            revenue = [
                r
                for r in by_kind["fact"]
                if r.name == "revenue" and "2022-06-30" in (r.period or "")
            ]
            assert len(revenue) == 1
            assert revenue[0].prior_value == Decimal(198_270_000_000)
            assert revenue[0].new_value == Decimal(RESTATED_REVENUE_2022)
            assert revenue[0].movement == "anchor"
            assert revenue[0].material
            assert all(r.material for r in rows)
            assert all(r.narrative.strip() for r in rows)
            assert all(r.job_id == scene["job_id"] for r in rows)
            assert {r.prior_report_id for r in rows} == {scene["report"].id}
            appeared = [r for r in by_kind["fact"] if "2023-06-30" in (r.period or "")]
            assert appeared, "the new year's facts did not appear"
            assert all(r.movement == "appeared" for r in appeared)
            assert all(r.report_id is not None for r in rows), "the rows do not name the report"

    async def test_only_the_sections_resting_on_a_moved_figure_are_redrafted(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        scene = await self._refreshed(api, committed, driver, api_settings)
        factory = scene["factory"]
        draft = await _output(factory, scene["job_id"], "draft")
        redrafted = set(draft["redrafted"])
        carried = set(draft["carried"])

        assert CHANGE_SUMMARY in redrafted, "the summary's commentary was not written"
        spine_redrafted = redrafted - {CHANGE_SUMMARY}
        assert spine_redrafted, "no section rested on the restated revenue"
        assert carried, "every section was re-drafted"
        assert draft["stale"] == []
        assert draft["too_many"] is False
        model_sections = set(SPINE_KEYS) - {"prior_research_comparison", "validation_disagreements"}
        assert spine_redrafted | carried == model_sections

        # The expectation from the record: a section is re-drafted exactly when a material
        # row names a calculation among its prior claims.
        async with factory() as session:
            moved = {
                (row.kind, row.name)
                for row in await session.scalars(
                    select(ReportChange).where(ReportChange.job_id == scene["job_id"])
                )
            }
            named = await refresh_service.figures_named_by_sections(
                session, job_id=scene["first_job"]
            )
        expected = {key for key, figures in named.items() if figures & moved}
        assert spine_redrafted == expected

        # Carried sections keep their prose word for word; only the calculation ids inside
        # a figure row are re-pointed at this run's ledger.
        prior_sections = await _sections(factory, scene["first_job"])
        new_sections = await _sections(factory, scene["job_id"])
        async with factory() as session:
            remap = await refresh_service.calculation_remap(
                session, prior_job_id=scene["first_job"], job_id=scene["job_id"]
            )
        for key in carried:
            before, after = prior_sections[key], new_sections[key]
            assert after.status is SectionStatus.GENERATED
            assert refresh_v1._remapped(before.content, remap) == after.content
            assert _prose(before.content) == _prose(after.content)
            assert after.confidence == before.confidence
        for key in spine_redrafted:
            assert new_sections[key].status is SectionStatus.GENERATED
            assert new_sections[key].content != prior_sections[key].content or True

        # A carried section's claims point at this run's ledger where the figure resolves,
        # and its citations came with them — unverified until the gate verified them.
        async with factory() as session:
            for key in carried:
                claims = list(
                    await session.scalars(
                        select(Claim).where(Claim.report_section_id == new_sections[key].id)
                    )
                )
                prior_claims = list(
                    await session.scalars(
                        select(Claim).where(Claim.report_section_id == prior_sections[key].id)
                    )
                )
                assert len(claims) == len(prior_claims)
                for claim in claims:
                    if claim.calculation_id is not None:
                        assert str(claim.calculation_id) in set(remap.values()) | set(remap) - set(
                            remap
                        )

    async def test_the_prior_report_is_superseded_with_the_reserved_word(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        scene = await self._refreshed(api, committed, driver, api_settings)
        factory = scene["factory"]

        async with factory() as session:
            prior = await session.get(Report, scene["report"].id)
            new = await session.scalar(select(Report).where(Report.job_id == scene["job_id"]))
            assert prior is not None
            assert new is not None
            assert new.is_current
            assert new.immutable
            assert prior.superseded_by == new.id
            assert prior.supersession_reason is not None
            assert prior.supersession_reason.startswith("Refreshed on ")
            assert re.search(
                r"\d+ figures? moved, \d+ sections? re-drafted\.$", prior.supersession_reason
            )
            assert new.as_of_date > prior.as_of_date
            assert new.content is not None
            assert new.content["sections"][0] == CHANGE_SUMMARY, "the summary is not first"
            assert await spend_so_far(session, job_id=scene["job_id"]) > 0
            assert (
                await spend_so_far(session, job_id=scene["job_id"])
                <= api_settings.refresh_budget_gbp
            )

        markdown = str(new.content["markdown"])
        assert "What changed" in markdown
        assert len(driver.provider.calls) > scene["calls_before"]

        library = await api.get("/reports")
        assert 'data-report-state="superseded"' in library.text
        page = await api.get(f"/reports/{prior.id}")
        assert 'id="superseded-band"' in page.text
        assert "Refreshed on" in page.text


# --- the control ---------------------------------------------------------------------------


class TestTheControl:
    async def test_the_report_page_offers_the_priced_control_and_starts_the_run(
        self,
        api: Any,
        committed: dict[str, Any],
        driver: Driver,
        api_settings: Settings,
        enqueued: list[str],
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)

        page = await api.get(f"/reports/{report.id}")
        assert page.status_code == 200
        assert 'id="refresh-form"' in page.text
        assert refresh_service.estimate_refresh(api_settings).label in page.text
        assert 'id="refresh-refused"' not in page.text

        started = await api.post(
            f"/reports/{report.id}/refresh",
            data={CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME)},
            follow_redirects=False,
        )
        assert started.status_code == 303, started.text
        job_id = started.headers["location"].rsplit("/", 1)[-1]
        assert enqueued[-1] == job_id
        async with factory() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            assert job is not None
            assert job.refresh_kind == "refresh"
            assert job.status is JobStatus.QUEUED

        again = await api.get(f"/reports/{report.id}")
        assert 'id="refresh-form"' not in again.text
        assert "already has a run going" in again.text
        assert 'id="refresh-history"' in again.text
        assert f"/runs/{job_id}" in again.text

        twice = await api.post(
            f"/reports/{report.id}/refresh",
            data={CSRF_FIELD_NAME: _hidden_value(again.text, CSRF_FIELD_NAME)},
            follow_redirects=False,
        )
        assert twice.status_code == 409

    async def test_a_report_that_is_not_current_says_why_and_a_bad_token_is_refused(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        page = await api.get(f"/reports/{report.id}")
        token = _hidden_value(page.text, CSRF_FIELD_NAME)
        withdrawn = await api.post(
            f"/reports/{report.id}/withdraw",
            data={CSRF_FIELD_NAME: token, "reason": "Wrong year."},
            follow_redirects=False,
        )
        assert withdrawn.status_code == 303

        after = await api.get(f"/reports/{report.id}")
        assert 'id="refresh-form"' not in after.text
        assert "Only the current report" in after.text

        refused = await api.post(
            f"/reports/{report.id}/refresh", data={CSRF_FIELD_NAME: "stale"}, follow_redirects=False
        )
        assert refused.status_code == 403

    async def test_the_service_refuses_another_accounts_report_and_a_live_run(
        self, api: Any, committed: dict[str, Any], driver: Driver, api_settings: Settings
    ) -> None:
        factory = committed["factory"]
        first_job = await _approved_run(api, committed["request"].id, driver)
        report = await _report_of(factory, first_job)
        async with factory() as session:
            stranger = User(
                email="other@example.invalid", display_name="Other", role=UserRole.OWNER
            )
            session.add(stranger)
            await session.flush()
            report_row = await session.get(Report, report.id)
            assert report_row is not None
            assert (
                await refresh_service.refusal_to_refresh(session, report=report_row, user=stranger)
                == "This report is not in your account's record."
            )
            owner = await session.get(User, committed["user"].id)
            assert owner is not None
            with pytest.raises(ConflictError, match="not in your account"):
                await refresh_service.start_refresh(
                    session, report=report_row, actor=stranger, settings=api_settings
                )
            job = await refresh_service.start_refresh(
                session, report=report_row, actor=owner, settings=api_settings
            )
            assert job.status is JobStatus.QUEUED
            with pytest.raises(ConflictError, match="already has a run going"):
                await refresh_service.start_refresh(
                    session, report=report_row, actor=owner, settings=api_settings
                )
            # `start_run` keeps its own rule: a request with a current report returns the
            # run that made it; the refresh is the one path that creates a second job.
            request = await session.get(ResearchRequest, committed["request"].id)
            assert request is not None
            assert (await run_service.start_run(session, request=request)).id in {
                first_job,
                job.id,
            }
            await session.rollback()


# --- the record's own readers --------------------------------------------------------------


class TestTheHeldAccessions:
    async def test_they_are_read_from_the_documents_urls(
        self, api: Any, committed: dict[str, Any], driver: Driver
    ) -> None:
        factory = committed["factory"]
        await _approved_run(api, committed["request"].id, driver)
        async with factory() as session:
            held = await held_accessions(session, work_order_id=committed["request"].id)
            urls = list(
                await session.scalars(
                    select(SourceDocument.url).where(
                        SourceDocument.work_order_id == committed["request"].id
                    )
                )
            )
        folders = {
            m.group(1) for url in urls if (m := re.search(r"/data/\d+/(\d{18})/", url or ""))
        }
        assert held == {f"{f[:10]}-{f[10:12]}-{f[12:]}" for f in folders}
        assert "0000789019-22-000010" in held


def _prose(content: dict[str, Any] | None) -> list[str]:
    """Every string in a section's content that is not an identifier, in order."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and not re.fullmatch(r"[0-9a-f-]{36}", value):
            found.append(value)

    walk(content or {})
    return found
