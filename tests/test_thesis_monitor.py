"""The thesis monitor: code measures the crossing, the model reads the rest, and a finding
is closed by an act with a reason.

Four layers. The pure part proves the resolver, the per-cent convention and the comparison
without a database. The pass proves what one reading writes: a finding whose status is
bounded by the crossing, no call at all when nothing new was filed or nothing can be
measured, and a stop with a finding rather than a pause when a cap binds. The acts prove
ADR 0078's rules — a reason every time, an appended row every time, the gate for the one
status with a consequence and nowhere else. And the surfaces prove a person can see and do
all of that from the work list and the pages.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.thesis_monitor import PremiseReading
from aer.calc.units import UnitMismatchError
from aer.config import Settings
from aer.core.enums import (
    Decision,
    FactBasis,
    FindingAction,
    FindingKind,
    GateKind,
    JobStatus,
    PremiseComparator,
    PremiseStatus,
    Provider,
    RequestStatus,
    SourceTier,
    UserRole,
)
from aer.db.models import (
    Approval,
    Artefact,
    AuditEvent,
    Calculation,
    Company,
    FinancialFact,
    Finding,
    Job,
    JobStep,
    SourceDocument,
    Thesis,
    User,
    WorkOrder,
)
from aer.errors import ConflictError, ExternalServiceError, ValidationError
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import approvals as approval_service
from aer.services import theses as thesis_service
from aer.services import thesis_monitor as monitor
from aer.services.approvals import payload_hash_for
from aer.services.theses import Predicate
from aer.storage.local import LocalArtefactStore
from aer.web.overview import monitor as monitor_feed
from aer.web.overview.attention import Severity
from tests.api_fixtures import build_app, client_for
from tests.assumption_fixtures import ANNUAL, a_year, seed_years, unit_for
from tests.request_fixtures import research_request
from tests.schema_guard import refuse_unanswerable_schema

pytestmark = pytest.mark.integration

FY2023 = date(2023, 12, 31)
FY2024 = date(2024, 12, 31)
# `seed_years` files each year on 1 February of the next: FY2023 on 2024-02-01, FY2024 on
# 2025-02-01. A premise held between the two sees exactly one new filing.
HELD_BETWEEN = datetime(2024, 6, 1, tzinfo=UTC)
HELD_AFTER_BOTH = datetime(2025, 3, 1, tzinfo=UTC)


# -- The pure part -----------------------------------------------------------------------------


class TestResolvingWhatAPremiseNames:
    def test_the_operators_words_become_a_growth_a_ratio_or_a_level(self) -> None:
        assert monitor.resolve_metric("Revenue growth") == monitor.ResolvedMetric(
            "growth", "revenue", "revenue growth"
        )
        assert monitor.resolve_metric("operating margin").kind == "ratio"
        assert monitor.resolve_metric("net income").kind == "level"

    def test_a_name_the_platform_cannot_measure_resolves_to_nothing(self) -> None:
        """ADR 0102 kept the metric free text on purpose; this is where the freedom lands."""
        assert monitor.resolve_metric("capital allocation score") is None
        assert monitor.resolve_metric("Azure revenue growth") is None

    def test_every_name_it_advertises_resolves(self) -> None:
        for name in monitor.measurable_metrics():
            assert monitor.resolve_metric(name) is not None, name


class TestPerCentIsAConventionHere:
    def test_a_percentage_threshold_becomes_a_fraction_once(self) -> None:
        """ADR 0027: divided by a hundred here, and nowhere else, so 25 per cent meets a
        margin of 0.25 on equal terms."""
        for unit in ("percent", "per cent", "%", "PCT"):
            assert monitor.threshold_quantity(Decimal(25), unit).value == Decimal("0.25"), unit

    def test_a_dimensionless_threshold_is_taken_as_written(self) -> None:
        assert monitor.threshold_quantity(Decimal("1.5"), "x").value == Decimal("1.5")
        assert monitor.threshold_quantity(Decimal("0.3"), "ratio").unit.is_dimensionless

    def test_a_currency_threshold_keeps_its_currency(self) -> None:
        assert monitor.threshold_quantity(Decimal(1200), "USD").unit.symbol == "USD"

    def test_a_unit_the_platform_does_not_know_raises_rather_than_guessing(self) -> None:
        with pytest.raises(UnitMismatchError):
            monitor.threshold_quantity(Decimal(3), "bananas")


class TestTheCrossingIsCodes:
    def test_each_comparator_reads_as_its_word(self) -> None:
        observed = monitor.threshold_quantity(Decimal("0.30"), "ratio")
        floor = monitor.threshold_quantity(Decimal(25), "percent")
        assert monitor.predicate_holds(observed, PremiseComparator.AT_LEAST, floor)
        assert monitor.predicate_holds(observed, PremiseComparator.ABOVE, floor)
        assert not monitor.predicate_holds(observed, PremiseComparator.AT_MOST, floor)
        assert not monitor.predicate_holds(observed, PremiseComparator.BELOW, floor)
        assert monitor.predicate_holds(floor, PremiseComparator.AT_LEAST, floor)
        assert not monitor.predicate_holds(floor, PremiseComparator.ABOVE, floor)

    def test_a_threshold_in_dollars_never_meets_a_ratio(self) -> None:
        """ADR 0079's promise: `Quantity.__ge__` refuses, and nothing coerces."""
        observed = monitor.threshold_quantity(Decimal("0.30"), "ratio")
        dollars = monitor.threshold_quantity(Decimal(30), "USD")
        with pytest.raises(UnitMismatchError):
            monitor.predicate_holds(observed, PremiseComparator.AT_LEAST, dollars)


# -- The pass ----------------------------------------------------------------------------------


def _reading(
    status: PremiseStatus = PremiseStatus.UNCHANGED, sources: list[str] | None = None
) -> PremiseReading:
    return PremiseReading(
        status=status,
        justification="Revenue for the year to 31 December 2024 was 1300 against 1000 a year "
        "earlier, which is what the premise expects.",
        source_document_ids=sources or [],
    )


def _provider(reading: PremiseReading | None = None) -> FakeProvider:
    return FakeProvider(
        {"PremiseReading": reading or _reading()}, inspect_schema=refuse_unanswerable_schema
    )


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        http_user_agent="Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        **overrides,
    )


async def _user_of(scene: dict[str, Any]) -> User:
    user = await scene["session"].get(User, scene["request"].work_order.user_id)
    assert user is not None
    return user


async def _thesis_with(
    scene: dict[str, Any],
    *,
    metric: str = "revenue growth",
    comparator: PremiseComparator = PremiseComparator.AT_LEAST,
    threshold: Decimal = Decimal(25),
    unit: str = "percent",
    held_at: datetime = HELD_BETWEEN,
    predicate: bool = True,
) -> Thesis:
    session: AsyncSession = scene["session"]
    user = await _user_of(scene)
    thesis = await thesis_service.write_thesis(
        session, user=user, company=scene["company"], title="Contoso keeps compounding"
    )
    await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Revenue keeps growing above 25% a year.",
        basis="The segment disclosure.",
        predicate=(
            Predicate(metric=metric, comparator=comparator, threshold=threshold, unit=unit)
            if predicate
            else None
        ),
        review_by=None if predicate else date(2027, 3, 31),
        held_at=held_at,
    )
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    return loaded


async def _run(
    scene: dict[str, Any], thesis: Thesis, tmp_path: Path, *, provider: FakeProvider, **overrides
) -> monitor.MonitorOutcome:
    settings = _settings(tmp_path, **overrides)
    return await monitor.run_monitor(
        scene["session"],
        settings=settings,
        provider=provider,
        router=Router(settings),
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        user=await _user_of(scene),
        thesis=thesis,
    )


async def _two_years(scene: dict[str, Any], *, latest_revenue: str = "1300") -> None:
    await seed_years(
        scene,
        {
            FY2023: a_year(),
            FY2024: a_year(revenue=latest_revenue, gross_profit="900"),
        },
    )


class TestOneReading:
    async def test_a_premise_the_filing_confirms_takes_the_models_status(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        provider = _provider(
            _reading(PremiseStatus.WEAKENED, [str(scene["document"].id), "not-in-the-window"])
        )

        outcome = await _run(scene, thesis, tmp_path, provider=provider)

        [finding] = outcome.findings
        assert finding.kind is FindingKind.READING
        assert finding.status is PremiseStatus.WEAKENED
        assert not finding.opens_gate
        assert outcome.read == 1
        assert provider.call_count == 1
        # 1000 -> 1300 is 30%, above the 25% floor: the crossing is code's and it holds.
        assert finding.observed is not None
        assert finding.observed["holds"] is True
        assert finding.observed["value"] == "0.3"
        assert finding.observed["threshold"] == "0.25"
        # The window is the one filing newer than the premise.
        assert finding.window_from == finding.window_to == date(2025, 2, 1)
        # An id the window does not hold is dropped, not trusted.
        assert finding.source_document_ids == [str(scene["document"].id)]

    async def test_the_growth_is_a_recorded_calculation(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """Invariant 3: the figure the finding shows is a row with a formula behind it."""
        await _two_years(scene)
        thesis = await _thesis_with(scene)

        outcome = await _run(scene, thesis, tmp_path, provider=_provider())

        [finding] = outcome.findings
        assert finding.observed is not None
        recorded = await scene["session"].get(
            Calculation, uuid.UUID(finding.observed["calculation_id"])
        )
        assert recorded is not None
        assert recorded.name == "growth_rate"
        assert recorded.job_id == outcome.job.id

    async def test_a_defeated_predicate_is_contradicted_whatever_the_model_says(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ADR 0103 §1: the tier is code's. A model that shrugs at a crossing is corrected."""
        await _two_years(scene)
        thesis = await _thesis_with(scene, threshold=Decimal(40))

        outcome = await _run(scene, thesis, tmp_path, provider=_provider(_reading()))

        [finding] = outcome.findings
        assert finding.status is PremiseStatus.CONTRADICTED
        assert finding.opens_gate
        assert finding.observed is not None
        assert finding.observed["holds"] is False

    async def test_a_confirmed_predicate_is_never_contradicted_by_the_model(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene)

        outcome = await _run(
            scene, thesis, tmp_path, provider=_provider(_reading(PremiseStatus.CONTRADICTED))
        )

        [finding] = outcome.findings
        assert finding.status is PremiseStatus.UNCHANGED
        assert not finding.opens_gate

    async def test_a_ratio_and_a_level_are_measured_too(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        # Operating income stays at 250 while revenue rises to 1300: a 19.2% margin, below
        # a 20% floor. And revenue itself, in dollars, against a dollar threshold.
        await _two_years(scene)
        margin = await _thesis_with(scene, metric="Operating margin", threshold=Decimal(20))
        level = await _thesis_with(scene, metric="revenue", threshold=Decimal(1200), unit="USD")

        first = await _run(scene, margin, tmp_path, provider=_provider())
        second = await _run(scene, level, tmp_path, provider=_provider())

        assert first.findings[0].status is PremiseStatus.CONTRADICTED
        assert first.findings[0].observed["metric"] == "Operating margin"
        assert second.findings[0].status is PremiseStatus.UNCHANGED
        assert second.findings[0].observed["unit"] == "USD"
        assert second.findings[0].observed["fact_id"] is not None

    async def test_nothing_new_makes_no_call_and_writes_nothing(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ "No news" is not a finding; a queue that filled with it would be the alert feed
        ADR 0079 refuses."""
        await _two_years(scene)
        thesis = await _thesis_with(scene, held_at=HELD_AFTER_BOTH)
        provider = _provider()

        outcome = await _run(scene, thesis, tmp_path, provider=provider)

        assert outcome.findings == []
        assert outcome.nothing_new == 1
        assert provider.call_count == 0
        assert outcome.job.status is JobStatus.SUCCEEDED

    async def test_a_second_pass_reads_nothing_it_already_read(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        provider = _provider()

        first = await _run(scene, thesis, tmp_path, provider=provider)
        second = await _run(scene, thesis, tmp_path, provider=provider)

        assert len(first.findings) == 1
        assert second.findings == []
        assert second.nothing_new == 1
        assert provider.call_count == 1

    async def test_a_metric_nothing_measures_is_unobservable_with_no_call(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene, metric="capital allocation score", unit="ratio")
        provider = _provider()

        outcome = await _run(scene, thesis, tmp_path, provider=provider)

        [finding] = outcome.findings
        assert finding.status is PremiseStatus.UNOBSERVABLE
        assert "capital allocation score" in finding.justification
        assert "operating_margin" in finding.justification
        assert finding.observed is None
        assert provider.call_count == 0

    async def test_a_threshold_in_the_wrong_unit_is_unobservable_and_says_so(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """Revenue is in dollars; "25 percent" of it is a comparison nothing can make."""
        await _two_years(scene)
        thesis = await _thesis_with(scene, metric="revenue", threshold=Decimal(25))
        provider = _provider()

        outcome = await _run(scene, thesis, tmp_path, provider=provider)

        [finding] = outcome.findings
        assert finding.status is PremiseStatus.UNOBSERVABLE
        assert "cannot be compared" in finding.justification
        assert provider.call_count == 0

    async def test_a_premise_a_person_reviews_is_not_read(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene, predicate=False)
        provider = _provider()

        outcome = await _run(scene, thesis, tmp_path, provider=provider)

        assert outcome.findings == []
        assert outcome.reviewed_by_a_person == 1
        assert provider.call_count == 0

    async def test_the_pass_is_a_work_order_of_its_own_and_every_write_is_on_the_chain(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene)

        outcome = await _run(scene, thesis, tmp_path, provider=_provider())

        job = await scene["session"].get(Job, outcome.job.id)
        assert job is not None
        assert job.workflow_version == monitor.WORKFLOW_VERSION
        assert job.status is JobStatus.SUCCEEDED
        order = await scene["session"].get(WorkOrder, job.work_order_id)
        assert order is not None
        assert order.tool == monitor.TOOL
        assert order.subject_kind == monitor.SUBJECT_THESIS
        assert order.subject_id == thesis.id
        assert order.point_in_time is False
        assert order.status is RequestStatus.COMPLETED
        assert job.total_cost_gbp > 0
        events = list(
            await scene["session"].scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "monitor.finding", AuditEvent.subject_id == thesis.id
                )
            )
        )
        assert len(events) == 1
        assert events[0].subject_kind == "thesis"
        assert events[0].job_id == job.id

    async def test_a_retired_thesis_is_not_monitored(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        thesis = await _thesis_with(scene)
        await thesis_service.retire_thesis(
            scene["session"], thesis=thesis, actor=await _user_of(scene), reason="Replaced."
        )

        with pytest.raises(ConflictError, match="retired"):
            await _run(scene, thesis, tmp_path, provider=_provider())


class TestAPassThatHitsItsCeiling:
    async def test_it_stops_with_a_finding_and_never_pauses(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ADR 0078: an unattended run that breaches its cap stops and leaves a finding. It
        does not sit in BUDGET_EXCEEDED waiting for somebody who is asleep."""
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        provider = _provider()

        outcome = await _run(
            scene, thesis, tmp_path, provider=provider, per_run_budget_gbp=Decimal("0.01")
        )

        assert outcome.stopped
        [finding] = outcome.findings
        assert finding.kind is FindingKind.STOPPED
        assert finding.status is None
        assert not finding.opens_gate
        assert "cost ceiling" in finding.justification
        assert outcome.job.status is JobStatus.FAILED
        assert outcome.job.status not in {JobStatus.BUDGET_EXCEEDED, JobStatus.AWAITING_APPROVAL}
        assert outcome.job.error is not None
        assert outcome.job.error["context"]["scope"] == "per_run"
        assert provider.call_count == 0


class TestAPassThatFailsOnSomethingElse:
    async def test_a_provider_failure_fails_the_pass_on_the_record(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """Not the budget: an outage, a reply the schema refused, a broken record. The pass
        commits after every premise, so without this the job would sit RUNNING for ever
        with nothing to finish it and the metered spend of the failed call lost."""
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        broken = FakeProvider(
            fail_with=ExternalServiceError("the provider is unavailable", provider="anthropic")
        )

        with pytest.raises(ExternalServiceError):
            await _run(scene, thesis, tmp_path, provider=broken)

        job = await scene["session"].scalar(
            select(Job).order_by(Job.started_at.desc().nullslast()).limit(1)
        )
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error is not None
        assert "unavailable" in job.error["message"]
        steps = list(
            await scene["session"].scalars(select(JobStep).where(JobStep.job_id == job.id))
        )
        assert steps
        assert all(step.status is JobStatus.FAILED for step in steps)


class TestTheWindowIsNews:
    async def test_an_unobservable_reading_does_not_consume_the_window(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """A growth premise read while one year was stored is unobservable. When the prior
        year arrives it is read again, rather than waiting for the filing after next."""
        held_before_both = datetime(2023, 12, 1, tzinfo=UTC)
        await seed_years(scene, {FY2024: a_year(revenue="1300", gross_profit="900")})
        thesis = await _thesis_with(scene, held_at=held_before_both)

        first = await _run(scene, thesis, tmp_path, provider=_provider())
        [unobservable] = first.findings
        assert unobservable.status is PremiseStatus.UNOBSERVABLE

        await seed_years(scene, {FY2023: a_year()})
        second = await _run(scene, thesis, tmp_path, provider=_provider())

        [reading] = second.findings
        assert reading.status is not PremiseStatus.UNOBSERVABLE
        assert reading.observed is not None
        assert reading.observed["holds"] is True

    async def test_facts_about_an_older_period_are_not_news(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """An amended prior year filed after the last reading changes nothing the premise
        measures: the newest period is the one already read, so no reading is re-issued —
        and a contradicted one does not open its gate twice."""
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        first = await _run(scene, thesis, tmp_path, provider=_provider())
        assert first.read == 1

        scene["session"].add(
            FinancialFact(
                company_id=scene["company"].id,
                source_document_id=scene["document"].id,
                concept="revenue",
                raw_concept="revenue",
                taxonomy="us-gaap",
                value=Decimal("1001"),
                unit=unit_for("revenue"),
                period_start=date(2023, 1, 1),
                period_end=FY2023,
                fiscal_year=2023,
                fiscal_period=ANNUAL,
                filed_date=date(2025, 6, 1),
                form="10-K/A",
                accession="0000000003-00-000001",
                basis=FactBasis.AS_REPORTED,
            )
        )
        await scene["session"].flush()

        second = await _run(scene, thesis, tmp_path, provider=_provider())

        assert second.read == 0
        assert second.nothing_new == 1
        assert second.findings == []


class TestWhatGetsAPass:
    async def test_a_thesis_with_nothing_readable_gets_no_pass(self, scene: dict[str, Any]) -> None:
        """A thesis whose premises are all for a person to review would get a work order
        and an empty pass; the count the button quotes is passes that can read something."""
        unreadable = await _thesis_with(scene, predicate=False)
        readable = await _thesis_with(scene)
        user = await _user_of(scene)

        listed = await monitor.theses_to_monitor(scene["session"], user_id=user.id)

        assert [row.id for row in listed] == [readable.id]
        assert unreadable.id not in {row.id for row in listed}


# -- What a person does about a finding ------------------------------------------------------


async def _one_finding(
    scene: dict[str, Any], tmp_path: Path, *, threshold: Decimal = Decimal(25)
) -> Finding:
    await _two_years(scene)
    thesis = await _thesis_with(scene, threshold=threshold)
    outcome = await _run(scene, thesis, tmp_path, provider=_provider())
    [finding] = outcome.findings
    loaded = await monitor.finding_of(scene["session"], finding.id, user_id=thesis.user_id)
    assert loaded is not None
    return loaded


class TestAFindingIsClosedByAnActWithAReason:
    async def test_dismissing_appends_a_row_and_leaves_the_finding(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        finding = await _one_finding(scene, tmp_path)
        user = await _user_of(scene)

        resolution = await monitor.resolve_finding(
            scene["session"],
            finding=finding,
            actor=user,
            action=FindingAction.DISMISSED,
            reason="Read it; the premise stands.",
        )

        assert resolution.action is FindingAction.DISMISSED
        assert resolution.actor == user.email
        assert not finding.is_open
        assert finding.status is PremiseStatus.UNCHANGED
        assert (await monitor.findings_for(scene["session"], user_id=user.id, open_only=True)) == []
        assert (
            len(await monitor.findings_for(scene["session"], user_id=user.id, open_only=False)) == 1
        )

    async def test_a_reason_is_required(self, scene: dict[str, Any], tmp_path: Path) -> None:
        finding = await _one_finding(scene, tmp_path)

        with pytest.raises(ValidationError, match="needs a reason"):
            await monitor.resolve_finding(
                scene["session"],
                finding=finding,
                actor=await _user_of(scene),
                action=FindingAction.DISMISSED,
                reason="   ",
            )

    async def test_a_second_resolution_is_refused_and_a_reopening_is_not(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        finding = await _one_finding(scene, tmp_path)
        user = await _user_of(scene)
        await monitor.resolve_finding(
            scene["session"],
            finding=finding,
            actor=user,
            action=FindingAction.DISMISSED,
            reason="Read it.",
        )

        with pytest.raises(ConflictError, match="already resolved"):
            await monitor.resolve_finding(
                scene["session"],
                finding=finding,
                actor=user,
                action=FindingAction.DISMISSED,
                reason="Read it again.",
            )
        await monitor.resolve_finding(
            scene["session"],
            finding=finding,
            actor=user,
            action=FindingAction.REOPENED,
            reason="I want to look at this again.",
        )

        assert finding.is_open
        assert [row.action for row in finding.resolutions] == [
            FindingAction.DISMISSED,
            FindingAction.REOPENED,
        ]

    async def test_withdrawing_the_premise_from_a_finding_withdraws_it_on_the_thesis(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        finding = await _one_finding(scene, tmp_path)

        await monitor.resolve_finding(
            scene["session"],
            finding=finding,
            actor=await _user_of(scene),
            action=FindingAction.WITHDRAWN,
            reason="The guide broke it.",
        )

        assert finding.premise is not None
        assert finding.premise.judgement.is_withdrawn
        assert finding.premise.judgement.withdrawn_reason == "The guide broke it."

    async def test_a_gate_whose_pass_is_gone_closes_the_ordinary_way(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """`aer reset-research` removes run roots and leaves findings. A contradicted finding
        must not then be stuck between a gate nothing can decide and a dismissal the gate
        refuses: with no pass, it closes like any other, with the reason."""
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))
        user = await _user_of(scene)
        job = await scene["session"].get(Job, finding.job_id)
        assert job is not None
        order = await scene["session"].get(WorkOrder, job.work_order_id)
        assert order is not None
        await scene["session"].delete(order)
        await scene["session"].flush()
        # The database set the column null; reload that one column, and only that one, so
        # the premise and the resolutions the service loaded eagerly stay loaded.
        await scene["session"].refresh(finding, attribute_names=["job_id"])
        assert finding.job_id is None
        assert finding.opens_gate
        assert not finding.gate_is_decidable

        with pytest.raises(ValidationError, match="no longer on record"):
            await monitor.decide_finding(
                scene["session"],
                finding=finding,
                actor=user,
                decision=Decision.REJECTED,
                reason="r",
                payload_hash=payload_hash_for(monitor.finding_payload(finding)),
            )
        resolution = await monitor.resolve_finding(
            scene["session"],
            finding=finding,
            actor=user,
            action=FindingAction.DISMISSED,
            reason="The pass is gone; read and kept.",
        )

        assert resolution.approval_id is None
        assert not finding.is_open

    async def test_a_contradicted_finding_cannot_be_dismissed_past_its_gate(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))

        with pytest.raises(ValidationError, match="opened a gate"):
            await monitor.resolve_finding(
                scene["session"],
                finding=finding,
                actor=await _user_of(scene),
                action=FindingAction.DISMISSED,
                reason="Whatever.",
            )


class TestTheThesisGate:
    async def test_approving_on_a_premise_already_withdrawn_keeps_the_first_reason(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """Withdrawn from the thesis page since the pass ran: the finding still closes as a
        withdrawal, because that is what happened, and the reason true at the time stands."""
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))
        user = await _user_of(scene)
        assert finding.premise is not None
        await thesis_service.withdraw_premise(
            scene["session"], premise=finding.premise, actor=user, reason="I changed my mind."
        )

        resolution = await monitor.decide_finding(
            scene["session"],
            finding=finding,
            actor=user,
            decision=Decision.APPROVED,
            reason="Growth fell below the floor I set.",
            payload_hash=payload_hash_for(monitor.finding_payload(finding)),
        )

        assert resolution.action is FindingAction.WITHDRAWN
        assert finding.premise.judgement.withdrawn_reason == "I changed my mind."
        assert not finding.is_open

    async def test_withdrawing_records_an_approval_and_withdraws_the_premise(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ADR 0078's one gate: an approvals row with an actor, a hash of what was shown,
        and a chained event — and the premise struck through with the reason."""
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))
        user = await _user_of(scene)
        shown = payload_hash_for(monitor.finding_payload(finding))

        resolution = await monitor.decide_finding(
            scene["session"],
            finding=finding,
            actor=user,
            decision=Decision.APPROVED,
            reason="Growth fell below the floor I set.",
            payload_hash=shown,
        )

        assert resolution.action is FindingAction.WITHDRAWN
        assert resolution.approval_id is not None
        approval = await scene["session"].get(Approval, resolution.approval_id)
        assert approval is not None
        assert approval.gate is GateKind.THESIS
        assert approval.decision is Decision.APPROVED
        assert approval.payload_hash == shown
        assert approval.job_id == finding.job_id
        assert approval.actor_user_id == user.id
        assert finding.premise is not None
        assert finding.premise.judgement.withdrawn_reason == "Growth fell below the floor I set."
        assert not finding.is_open
        witnessed = await scene["session"].scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "approval.approved",
                AuditEvent.subject_id == finding.thesis_id,
            )
        )
        assert witnessed is not None
        assert witnessed.payload["gate"] == "THESIS"

    async def test_keeping_the_premise_is_a_recorded_decision_too(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ "I saw this and chose to do nothing" is decision data (ADR 0078)."""
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))

        resolution = await monitor.decide_finding(
            scene["session"],
            finding=finding,
            actor=await _user_of(scene),
            decision=Decision.REJECTED,
            reason="One soft year; the segment mix explains it.",
            payload_hash=payload_hash_for(monitor.finding_payload(finding)),
        )

        assert resolution.action is FindingAction.DISMISSED
        assert finding.premise is not None
        assert not finding.premise.judgement.is_withdrawn
        assert not finding.is_open

    async def test_a_stale_hash_is_refused(self, scene: dict[str, Any], tmp_path: Path) -> None:
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))

        with pytest.raises(ValidationError, match="changed between"):
            await monitor.decide_finding(
                scene["session"],
                finding=finding,
                actor=await _user_of(scene),
                decision=Decision.APPROVED,
                reason="r",
                payload_hash="0" * 64,
            )

    async def test_a_finding_that_opened_no_gate_cannot_be_decided(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        finding = await _one_finding(scene, tmp_path)

        with pytest.raises(ValidationError, match="opened no gate"):
            await monitor.decide_finding(
                scene["session"],
                finding=finding,
                actor=await _user_of(scene),
                decision=Decision.APPROVED,
                reason="r",
                payload_hash=payload_hash_for(monitor.finding_payload(finding)),
            )

    async def test_the_run_gate_order_refuses_the_thesis_gate(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """ADR 0103 §3: it is decided on a finding, never through a run's gate order."""
        finding = await _one_finding(scene, tmp_path, threshold=Decimal(40))
        job = await scene["session"].get(Job, finding.job_id)
        assert job is not None

        with pytest.raises(ValidationError, match="not part of a run's gate order"):
            await approval_service.record_decision(
                scene["session"],
                job=job,
                gate=GateKind.THESIS,
                decision=Decision.APPROVED,
                actor=await _user_of(scene),
                payload_hash="a" * 64,
            )


# -- The work list -------------------------------------------------------------------------


class TestWhatTheMonitorPutsInFrontOfTheOperator:
    async def test_a_contradicted_premise_is_blocked_and_a_reading_is_idle(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        contradicted = await _one_finding(scene, tmp_path, threshold=Decimal(40))
        user = await _user_of(scene)
        # A second thesis the model reads as weakened, so both tiers are on the list.
        weakened = await _thesis_with(scene)
        await _run(scene, weakened, tmp_path, provider=_provider(_reading(PremiseStatus.WEAKENED)))

        items = await monitor_feed.items(scene["session"], user_id=user.id)

        by_key = {item.key: item for item in items}
        gate = by_key[f"monitor.gate.{contradicted.id}"]
        assert gate.severity is Severity.BLOCKED
        assert "contradicted" in gate.title
        assert gate.href == f"/monitor/findings/{contradicted.id}"
        readings = [item for item in items if item.key.startswith("monitor.finding.")]
        assert len(readings) == 1
        assert readings[0].severity is Severity.IDLE
        # Labelled for what it is, on every surface (ADR 0078).
        assert "finding" in readings[0].title.lower()
        assert "not a decision" in readings[0].detail

    async def test_a_review_past_its_date_is_listed_and_one_ahead_is_not(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        user = await _user_of(scene)
        thesis = await thesis_service.write_thesis(
            scene["session"], user=user, company=scene["company"], title="People"
        )
        for statement, when in (
            ("Management allocates capital well.", date(2020, 1, 1)),
            ("The moat holds.", datetime.now(UTC).date() + timedelta(days=90)),
        ):
            await thesis_service.add_premise(
                scene["session"],
                thesis=thesis,
                actor=user,
                statement=statement,
                basis="b",
                predicate=None,
                review_by=when,
            )

        items = await monitor_feed.items(scene["session"], user_id=user.id)

        reviews = [item for item in items if item.key.startswith("monitor.review.")]
        assert len(reviews) == 1
        assert "due for your review" in reviews[0].title
        assert "Management allocates capital well." in reviews[0].detail
        assert reviews[0].href.startswith(f"/theses/{thesis.id}#premise-")

    async def test_a_stopped_pass_needs_diagnosis(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _two_years(scene)
        thesis = await _thesis_with(scene)
        await _run(
            scene, thesis, tmp_path, provider=_provider(), per_run_budget_gbp=Decimal("0.01")
        )

        items = await monitor_feed.items(scene["session"], user_id=thesis.user_id)

        [stopped] = [item for item in items if item.key.startswith("monitor.stopped.")]
        assert stopped.severity is Severity.BROKEN
        assert "stopped" in stopped.title


# -- The pages -------------------------------------------------------------------------------

_TABLES = (
    "work_orders, audit_events, users, artefacts, companies, theses, judgements, "
    "financial_facts, source_documents"
)


@pytest.fixture
async def committed(db_engine: Any, tmp_path: Path) -> Any:
    """A scene the application can see: committed for real, on the engine the client uses.

    The `scene` fixture lives inside the suite's rolled-back transaction, which the
    application's own sessions cannot read; a page test needs rows that were committed.
    Built through the same helpers as the service tests, then truncated.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = research_request(
            user_id=user.id,
            company_name="Contoso Corporation",
            ticker="CTSO",
            exchange="NASDAQ",
            as_of_date=date(2024, 6, 30),
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
            portfolio_context={},
            point_in_time=True,
        )
        company = Company(
            name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000002"
        )
        artefact = Artefact(
            sha256="d" * 64, size_bytes=10, media_type="application/json", storage_key="dd/d"
        )
        session.add_all([request, company, artefact])
        await session.flush()
        document = SourceDocument(
            work_order_id=request.id,
            artefact_id=artefact.id,
            url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            title="Contoso XBRL company facts",
            retrieved_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()
        scene = {
            "session": session,
            "request": request,
            "company": company,
            "document": document,
            "tmp_path": tmp_path,
        }
        await _two_years(scene)
        await session.commit()
        yield scene
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(api_settings: Settings, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def _committed_finding(committed: dict[str, Any], *, threshold: Decimal) -> Finding:
    thesis = await _thesis_with(committed, threshold=threshold)
    outcome = await _run(committed, thesis, committed["tmp_path"], provider=_provider())
    await committed["session"].commit()
    [finding] = outcome.findings
    return finding


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return found.group(1)


class TestThePages:
    async def test_the_list_groups_a_decision_apart_from_a_question(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        contradicted = await _committed_finding(committed, threshold=Decimal(40))
        weakened = await _thesis_with(committed)
        await _run(
            committed,
            weakened,
            committed["tmp_path"],
            provider=_provider(_reading(PremiseStatus.WEAKENED)),
        )
        await committed["session"].commit()

        page = await api.get("/monitor")

        assert page.status_code == 200
        assert f'data-finding="{contradicted.id}" data-status="contradicted"' in page.text
        assert 'data-status="weakened"' in page.text
        assert "a decision waiting" in page.text
        assert "a finding, not a decision" in page.text
        assert "one premise was contradicted and is waiting for your decision" in page.text.lower()

    async def test_the_gate_is_decided_from_the_page(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        finding = await _committed_finding(committed, threshold=Decimal(40))

        opened = await api.get(f"/monitor/findings/{finding.id}")
        assert opened.status_code == 200
        assert 'id="thesis-gate"' in opened.text
        assert "What do you do about this premise?" in opened.text
        hash_field = re.search(r'id="payload-hash" value="([0-9a-f]{64})"', opened.text)
        assert hash_field is not None

        decided = await api.post(
            f"/monitor/findings/{finding.id}/decide",
            data={
                "csrf_token": _csrf(opened.text),
                "payload_hash": hash_field.group(1),
                "decision": Decision.REJECTED.value,
                "reason": "One soft year.",
            },
        )
        assert decided.status_code == 303, decided.text

        after = await api.get(f"/monitor/findings/{finding.id}")
        assert 'id="thesis-gate"' not in after.text
        assert 'data-resolution="dismissed"' in after.text
        assert "One soft year." in after.text
        assert 'id="reopen-form"' in after.text

    async def test_a_reading_is_dismissed_from_the_page_and_a_blank_reason_is_refused(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        finding = await _committed_finding(committed, threshold=Decimal(25))
        opened = await api.get(f"/monitor/findings/{finding.id}")
        assert 'id="resolve-form"' in opened.text

        refused = await api.post(
            f"/monitor/findings/{finding.id}/resolve",
            data={
                "csrf_token": _csrf(opened.text),
                "action": FindingAction.DISMISSED.value,
                "reason": " ",
            },
        )
        assert refused.status_code == 422
        assert "needs a reason" in refused.text

        recorded = await api.post(
            f"/monitor/findings/{finding.id}/resolve",
            data={
                "csrf_token": _csrf(opened.text),
                "action": FindingAction.DISMISSED.value,
                "reason": "Read it.",
            },
        )
        assert recorded.status_code == 303
        listing = await api.get("/monitor?resolved=1")
        assert 'data-resolved="yes"' in listing.text

    async def test_running_the_monitor_queues_one_pass_per_open_thesis(
        self, api: Any, committed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _thesis_with(committed)
        await _thesis_with(committed)
        await committed["session"].commit()
        queued: list[uuid.UUID] = []

        async def record(redis: Any, thesis_id: uuid.UUID) -> str:
            queued.append(thesis_id)
            return f"task-{thesis_id}"

        monkeypatch.setattr("aer.web.monitor.pages.enqueue_monitor", record)
        page = await api.get("/monitor")
        assert "Run the monitor over 2 theses" in page.text

        response = await api.post("/monitor/run", data={"csrf_token": _csrf(page.text)})

        assert response.status_code == 303
        assert response.headers["location"] == "/monitor?queued=2"
        assert len(queued) == 2
        assert "2 passes are queued" in (await api.get("/monitor?queued=2")).text

    async def test_a_finding_that_is_not_yours_answers_as_missing(self, api: Any) -> None:
        assert (await api.get(f"/monitor/findings/{uuid.uuid4()}")).status_code == 404

    async def test_findings_on_one_thesis_are_one_card_with_a_line_each(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """Three findings on one thesis read as one card with three lines, not three rows
        repeating the title. Each line keeps its own id, status and link, because a line is
        what the work list addresses."""
        thesis = await _thesis_with(committed, threshold=Decimal(25))
        await thesis_service.add_premise(
            committed["session"],
            thesis=thesis,
            actor=await _user_of(committed),
            statement="Revenue keeps growing above 20% a year.",
            basis="The same disclosure, read more cautiously.",
            predicate=Predicate(
                metric="revenue growth",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal(20),
                unit="percent",
            ),
            review_by=None,
            held_at=HELD_BETWEEN,
        )
        # The loaded collection predates the second premise; expire it so the reload reads
        # both, as a pass in its own session would.
        committed["session"].expire(thesis, ["premises"])
        reloaded = await thesis_service.thesis_of(
            committed["session"], thesis.id, user_id=(await _user_of(committed)).id
        )
        assert reloaded is not None
        thesis = reloaded
        await _run(
            committed,
            thesis,
            committed["tmp_path"],
            provider=_provider(_reading(PremiseStatus.WEAKENED)),
        )
        await committed["session"].commit()

        page = await api.get("/monitor")

        assert page.status_code == 200
        assert page.text.count(f'data-thesis="{thesis.id}"') == 1
        # The thesis is named as a link once; the recent-passes list also names it, in words.
        assert page.text.count(f'href="/theses/{thesis.id}"') == 1
        assert page.text.count('data-status="weakened"') == 2
        assert page.text.count("Say what you did") == 2
        company = committed["company"]
        assert f"{company.name} ({company.ticker})" in page.text, "the card names its subject"

    async def test_the_measurement_is_two_figures_and_a_verdict(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """The value against the threshold with the period beneath, the verdict as a chip,
        and the sentence still there saying the same thing in words."""
        finding = await _committed_finding(committed, threshold=Decimal(40))

        opened = await api.get(f"/monitor/findings/{finding.id}")

        assert opened.status_code == 200
        assert 'id="measured"' in opened.text
        assert 'data-field="measured"' in opened.text
        assert "for the period ending 2024-12-31" in opened.text
        assert 'data-field="threshold"' in opened.text
        observed = finding.observed
        assert observed is not None
        # The threshold as the pass recorded it: a per cent is a fraction by the time it is
        # compared (ADR 0027), and the page says what was compared, not what was typed.
        assert f"at least {observed['threshold']} {observed['threshold_unit']}" in opened.text
        assert "The predicate does not hold" in opened.text
        assert f'href="/calculations/{finding.observed["calculation_id"]}"' in opened.text
        assert 'data-field="observed"' in opened.text
