"""The red team: a separate context that attacks the thesis, scored and recorded.

Task 40, ADR 0039. The structural properties first — the input type cannot carry working
notes, an unevidenced challenge cannot exist — then the service against a seeded run with
a planted contradiction, the ladder states, and batch parity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.red_team import (
    _SYSTEM_PROMPT,
    CHALLENGE_BASIS_BUDGET,
    CHALLENGE_BASIS_CEILING,
    CHALLENGE_STATEMENT_BUDGET,
    CHALLENGE_STATEMENT_CEILING,
    COVERAGE_NOTE_BUDGET,
    COVERAGE_NOTE_CEILING,
    ChallengeDimension,
    RedTeamChallenge,
    RedTeamInput,
    RedTeamReport,
)
from aer.agents.registry import resolve_role
from aer.config import Settings
from aer.core.disagreement import (
    DisagreementKind,
    ResolutionOutcome,
    ResolutionRule,
    challenge_heading,
)
from aer.core.enums import ClaimKind, FactBasis, GateKind, JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Company,
    Disagreement,
    FinancialFact,
    Job,
    JobStep,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.db.models.report_section import ReportSection
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services.citations import record_claim
from aer.services.disagreements import (
    disagreements_for_job,
    escalations_for_job,
    settle_by_hand,
)
from aer.services.escalation import triggers_for_job
from aer.services.red_team import MATERIAL_SEVERITY, _shortened, run_red_team
from aer.storage.local import LocalArtefactStore
from tests.ledger_fixtures import record_valuation_ledger
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

# The drafting context this fixture plants, which must never reach the adversary.
WORKING_NOTE = "WOLFSBANE-the-bull-case-working-note"


# ==========================================================================================
# Structural isolation and the evidence rule, at the type level
# ==========================================================================================


class TestTheInputCannotCarryWorkingNotes:
    def test_there_is_no_field_for_the_drafting_context(self) -> None:
        # The whole point, asserted twice: by the field list, and by extra="forbid"
        # refusing anything smuggled under another name.
        #
        # `problems` is the one field that carries anything *back* into the context, and it
        # is admissible under ADR 0039 because of what it may hold: this service's own
        # complaints about ids it could not resolve, built by `_unresolvable_evidence` and
        # nowhere else. The red team still never receives a line of the draft's prose, which
        # is what the two tests below hold. This assertion failing is the intended alarm —
        # a field added here is a decision about the isolation, not a refactor.
        assert set(RedTeamInput.model_fields) == {
            "company_name",
            "ticker",
            "as_of_date",
            "claims",
            "facts",
            "calculations",
            "sources",
            "problems",
        }

        with pytest.raises(PydanticValidationError):
            RedTeamInput(
                company_name="Contoso",
                ticker="CTSO",
                as_of_date="2022-06-30",
                working_notes="the bull case, verbatim",  # type: ignore[call-arg]
            )

    def test_section_prose_is_refused_under_any_name(self) -> None:
        with pytest.raises(PydanticValidationError):
            RedTeamInput(
                company_name="Contoso",
                ticker="CTSO",
                as_of_date="2022-06-30",
                sections=[{"content": "prose"}],  # type: ignore[call-arg]
            )


class TestAChallengeStandsOnEvidenceOrDoesNotExist:
    def test_a_challenge_citing_nothing_is_readable_but_marked(self) -> None:
        """The rule moved from the schema to the service, and this is why.

        It was a validator that raised, so one unevidenced challenge failed the parse of the
        whole `RedTeamReport`, failed the step, and failed a live run eight pounds and forty
        minutes in — taking five well-evidenced objections down with the sixth. The schema
        now reads such a challenge and marks it; `services.red_team` drops it beside the ones
        citing ids the run does not hold, and the appendix is no more permissive than before.
        """
        challenge = RedTeamChallenge(
            dimension=ChallengeDimension.GROWTH,
            severity=5,
            statement="Growth is an illusion.",
            basis="Trust me.",
        )

        assert challenge.cites_nothing

    def test_a_challenge_citing_something_is_not_marked(self) -> None:
        challenge = RedTeamChallenge(
            dimension=ChallengeDimension.GROWTH,
            severity=5,
            statement="Growth is an illusion.",
            basis="The filed revenue line.",
            fact_ids=["some-fact"],
        )

        assert not challenge.cites_nothing

    def test_severity_is_bounded(self) -> None:
        with pytest.raises(PydanticValidationError):
            RedTeamChallenge(
                dimension=ChallengeDimension.GROWTH,
                severity=6,
                statement="Beyond the scale.",
                basis="Enthusiasm.",
                fact_ids=["some-fact"],
            )


class TestTheLengthsAreAskedForNotJustEnforced:
    """The section writers' live failure, which the red team then reproduced exactly.

    Six challenge statements and the coverage note came back over the old 600-character
    bounds — bounds the model had never been told about, because `max_length` reaches it
    as description text. On the batch path there is no retry, so the one unreadable reply
    failed the whole red_team step and with it the run. Same cure as the planner and the
    section writers: the ceiling gets headroom, the prompt states the budget, and both
    come from the same constants so they cannot drift apart.
    """

    _PAIRS = (
        ("statement", CHALLENGE_STATEMENT_BUDGET, CHALLENGE_STATEMENT_CEILING),
        ("basis", CHALLENGE_BASIS_BUDGET, CHALLENGE_BASIS_CEILING),
        ("coverage_note", COVERAGE_NOTE_BUDGET, COVERAGE_NOTE_CEILING),
    )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _PAIRS)
    def test_the_ceiling_leaves_real_headroom_over_the_budget(
        self, field: str, budget: int, ceiling: int
    ) -> None:
        assert ceiling >= budget * 2, (
            f"{field} allows {ceiling} and asks for {budget}; an overrun of half would "
            "fail the whole run, because the batch path has no retry"
        )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _PAIRS)
    def test_the_prompt_states_the_budget(self, field: str, budget: int, ceiling: int) -> None:
        assert str(budget) in _SYSTEM_PROMPT
        assert "{" not in _SYSTEM_PROMPT, "a placeholder went unformatted"

    def test_a_reply_at_the_stated_budgets_validates(self) -> None:
        report = RedTeamReport(
            challenges=[
                RedTeamChallenge(
                    dimension=ChallengeDimension.GROWTH,
                    severity=3,
                    statement="s" * CHALLENGE_STATEMENT_BUDGET,
                    basis="b" * CHALLENGE_BASIS_BUDGET,
                    fact_ids=["some-fact"],
                )
            ],
            coverage_note="c" * COVERAGE_NOTE_BUDGET,
        )

        assert len(report.coverage_note) == COVERAGE_NOTE_BUDGET

    def test_the_ceiling_still_refuses_a_runaway(self) -> None:
        with pytest.raises(PydanticValidationError):
            RedTeamReport(coverage_note="c" * (COVERAGE_NOTE_CEILING + 1))


class TestAChallengeAnnouncesItselfWithoutRepeatingItself:
    """What the operator saw on a live review page, and why the topic is not a heading.

    A red-team row's topic is a shortened copy of its statement: enough to name the row in
    a log line and in the fingerprint that stops a retried step recording it twice. The
    review page printed it as the heading, directly above the statement in full — so every
    challenge arrived as the same sentence twice, the first one cut through a word.
    """

    def test_the_heading_is_the_dimension_and_the_severity(self) -> None:
        heading = challenge_heading(
            {"dimension": "competitive_position", "severity": 4}, fallback="unused"
        )

        assert heading == "Red team \N{EM DASH} competitive position, severity 4/5"

    def test_a_conflict_that_is_not_a_challenge_keeps_its_own_topic(self) -> None:
        """A source conflict's topic is a real short label, not a shortening of anything."""
        assert challenge_heading({}, fallback="Revenue FY2021") == "Revenue FY2021"
        assert challenge_heading(None, fallback="Revenue FY2021") == "Revenue FY2021"

    def test_a_challenge_recorded_before_severity_existed_still_reads(self) -> None:
        """These are JSONB from earlier builds; a heading that raised would take the page."""
        assert challenge_heading({"dimension": "growth"}, fallback="x") == "Red team — growth"

    def test_a_shortened_topic_breaks_between_words(self) -> None:
        statement = (
            "The discounted cash flow leans on a terminal growth rate of three per cent, "
            "which exceeds the long-run nominal growth of the economy it operates in."
        )

        shortened = _shortened(statement)

        assert shortened.endswith("\N{HORIZONTAL ELLIPSIS}")
        assert statement.startswith(shortened.rstrip("\N{HORIZONTAL ELLIPSIS}"))
        # The whole point: the last thing before the ellipsis is a word, not part of one.
        assert shortened.rstrip("\N{HORIZONTAL ELLIPSIS}").split()[-1] in statement.split()

    def test_a_statement_short_enough_is_left_alone(self) -> None:
        assert _shortened("The margin path is asserted.") == "The margin path is asserted."

    def test_one_unbroken_word_has_nowhere_to_break(self) -> None:
        """No space to back up to. Cut it, and still say there is more."""
        shortened = _shortened("x" * 300)

        assert shortened == "x" * 120 + "\N{HORIZONTAL ELLIPSIS}"

    def test_the_whole_argument_is_never_what_was_shortened(self) -> None:
        """The statement lives in `detail`, whole, which is what the surfaces print."""
        assert len(_shortened("word " * 100)) < CHALLENGE_STATEMENT_CEILING


class TestTheRoleIsRegistered:
    def test_the_role_names_its_adr_and_holds_no_tools(self) -> None:
        definition = resolve_role("red_team")

        assert definition.adr == "0039"
        assert definition.allowed_tools == frozenset()
        assert definition.output_schema() is RedTeamReport
        matches = list(Path("docs/adr").glob("0039-*.md"))
        assert len(matches) == 1


# ==========================================================================================
# The service, against a seeded run with a planted contradiction
# ==========================================================================================


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """A drafted run asserting growth while its own evidence shows revenue falling."""
    user = User(email="redteam@example.invalid", display_name="Red")
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
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    step = JobStep(
        job_id=job.id,
        step_key="red_team",
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:red_team",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    db_session.add(step)
    await db_session.flush()

    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)

    payload = b"<html><p>Total revenue fell for fiscal year 2022.</p></html>"
    stored = await store.put_bytes(payload)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="text/html",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    db_session.add(artefact)
    await db_session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        publication_date=date(2022, 3, 1),
        quarantined=False,
    )
    db_session.add(document)
    await db_session.flush()

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    # The planted contradiction: the run's own fact shows revenue below the prior year,
    # while the draft's claim (below) asserts growth.
    fact = FinancialFact(
        company_id=company.id,
        source_document_id=document.id,
        concept="revenue",
        value=Decimal("150000000000"),
        unit="USD",
        period_end=date(2022, 6, 30),
        basis=FactBasis.AS_REPORTED,
        filed_date=date(2022, 7, 28),
    )
    db_session.add(fact)
    await db_session.flush()

    ledger = await record_valuation_ledger(db_session, request=request, job=job, actor=user)

    definition = await db_session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None
    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        # The working note the adversary must never see.
        content={"body": f"Revenue is growing strongly. {WORKING_NOTE}"},
    )
    db_session.add(section)
    await db_session.flush()

    claim = await record_claim(
        db_session,
        section=section,
        kind=ClaimKind.FACTUAL,
        text="Revenue is growing and the trajectory is durable.",
    )

    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "step": step,
        "settings": settings,
        "store": store,
        "document": document,
        "fact": fact,
        "calculation": ledger["rows"][0],
        "section": section,
        "claim": claim,
    }


def _context(scene: dict[str, Any], provider: FakeProvider) -> AgentContext:
    return AgentContext(
        session=scene["session"],
        provider=provider,
        router=Router(scene["settings"]),
        settings=scene["settings"],
        store=scene["store"],
        job_step=scene["step"],
    )


def _fixture_report(scene: dict[str, Any]) -> RedTeamReport:
    """Three scored challenges, led by the planted contradiction."""
    return RedTeamReport(
        challenges=[
            RedTeamChallenge(
                dimension=ChallengeDimension.GROWTH,
                severity=5,
                statement=(
                    "The draft asserts durable revenue growth, but the run's own revenue "
                    "fact for the period shows a decline."
                ),
                basis="The claim contradicts the recorded fact it should rest on.",
                fact_ids=[str(scene["fact"].id)],
            ),
            RedTeamChallenge(
                dimension=ChallengeDimension.VALUATION,
                severity=3,
                statement=(
                    "The terminal value rests on a single confirmed growth assumption "
                    "with no sensitivity shown."
                ),
                basis="One assumption carries the valuation.",
                calculation_ids=[str(scene["calculation"].id)],
            ),
            RedTeamChallenge(
                dimension=ChallengeDimension.COMPETITIVE_POSITION,
                severity=2,
                statement="The evidence base is a single filing from a single source.",
                basis="Nothing corroborates the issuer's own account.",
                source_document_ids=[str(scene["document"].id)],
            ),
        ],
        coverage_note="Attacked growth, valuation and breadth of evidence.",
    )


async def _rows(session: AsyncSession, job_id: Any) -> list[Disagreement]:
    return list(
        await session.scalars(
            select(Disagreement)
            .where(Disagreement.job_id == job_id)
            .order_by(Disagreement.created_at, Disagreement.id)
        )
    )


class TestThePlantedContradictionIsChallenged:
    @pytest.fixture
    async def outcome(self, scene: dict[str, Any]) -> dict[str, Any]:
        provider = FakeProvider({"RedTeamReport": _fixture_report(scene)})
        result = await run_red_team(
            _context(scene, provider),
            scene["session"],
            job=scene["job"],
            request=scene["request"],
        )
        return {**scene, "outcome": result, "provider": provider}

    async def test_three_scored_challenges_land_as_disagreements(
        self, outcome: dict[str, Any]
    ) -> None:
        rows = await _rows(outcome["session"], outcome["job"].id)

        assert len(rows) == 3
        assert all(row.kind is DisagreementKind.THESIS_CONFLICT for row in rows)
        assert all(row.rule is ResolutionRule.THESIS_CONFLICT for row in rows)
        # Never auto-resolved: every challenge waits at gate 2 with both positions.
        assert all(row.resolution is ResolutionOutcome.ESCALATED for row in rows)
        assert all(row.escalated_to_gate is GateKind.FINAL for row in rows)

    async def test_the_material_contradiction_is_the_state_task_41_gates_on(
        self, outcome: dict[str, Any]
    ) -> None:
        """The acceptance: a challenge materially contradicting the thesis on a scored
        dimension, visible where the escalation engine will look."""
        escalations = await escalations_for_job(outcome["session"], outcome["job"].id)
        material = [
            row
            for row in escalations
            if row.kind is DisagreementKind.THESIS_CONFLICT and row.material
        ]

        assert len(material) == 1
        assert material[0].topic.startswith("Red team (growth)")
        assert f"severity {MATERIAL_SEVERITY + 1}/5" in str(material[0].position_b)

    async def test_claim_attribution_resolves_to_sections_and_bad_ids_are_dropped(
        self, scene: dict[str, Any]
    ) -> None:
        """ADR 0091: the claims a challenge names route it to their sections, in code.

        Attribution is filtering, not evidence: the fabricated claim id is dropped from
        the detail while the challenge itself stands on its cited fact — unlike a
        fabricated fact id, which refuses the whole argument.
        """
        report = RedTeamReport(
            challenges=[
                RedTeamChallenge(
                    dimension=ChallengeDimension.GROWTH,
                    severity=5,
                    statement="The growth claim contradicts the recorded revenue fact.",
                    basis="The claim contradicts the fact it should rest on.",
                    fact_ids=[str(scene["fact"].id)],
                    claim_ids=[str(scene["claim"].id), str(uuid.uuid4())],
                )
            ],
            coverage_note="Attacked the growth claim.",
        )
        provider = FakeProvider({"RedTeamReport": report})
        result = await run_red_team(
            _context(scene, provider),
            scene["session"],
            job=scene["job"],
            request=scene["request"],
        )

        assert len(result.recorded) == 1
        detail = result.recorded[0].detail or {}
        assert detail["claims"] == [str(scene["claim"].id)]
        assert detail["sections"] == [scene["section"].section_key]

    async def test_severity_decides_the_banner_never_the_record(
        self, outcome: dict[str, Any]
    ) -> None:
        rows = await _rows(outcome["session"], outcome["job"].id)
        by_dimension = {row.topic.split("(")[1].split(")")[0]: row for row in rows}

        # The severity-5 challenge is material; the quibbles are recorded without the
        # banner — escalated and published all the same.
        assert by_dimension["growth"].material is True
        assert by_dimension["valuation"].material is False
        assert by_dimension["competitive_position"].material is False

    async def test_both_positions_and_the_evidence_are_on_the_row(
        self, outcome: dict[str, Any]
    ) -> None:
        rows = await _rows(outcome["session"], outcome["job"].id)
        growth = next(row for row in rows if row.topic.startswith("Red team (growth)"))

        assert growth.position_a["label"].startswith("Base thesis")
        assert "Red team challenge (growth" in growth.position_b["label"]
        # The challenge is a record beside the rationale, never composed into it (gap
        # R5): the appendix renders columns and footnotes, and a blob of ids cannot be
        # un-composed once printed.
        assert growth.detail is not None
        assert "own revenue" in growth.detail["challenge"]
        assert growth.detail["basis"].startswith("The claim contradicts")
        assert growth.detail["severity"] == 5
        assert growth.detail["dimension"] == "growth"
        assert growth.detail["evidence"]["facts"] == [str(outcome["fact"].id)]
        assert str(outcome["fact"].id) not in growth.resolution_rationale

    async def test_a_cited_fact_is_footnoted_through_its_source_document(
        self, outcome: dict[str, Any]
    ) -> None:
        """A fact id names a row in this platform's own tables — provenance no reader can
        follow — so the record carries the document the fact came from beside it."""
        rows = await _rows(outcome["session"], outcome["job"].id)
        growth = next(row for row in rows if row.topic.startswith("Red team (growth)"))

        assert growth.detail is not None
        expected = str(outcome["fact"].source_document_id)
        assert growth.detail["evidence"]["sources"] == [expected]

    async def test_the_working_note_never_reached_the_adversary(
        self, outcome: dict[str, Any]
    ) -> None:
        """Isolation, proved at the prompt: the drafting context was in the database and
        the claims were in the prompt, and only one of them crossed."""
        calls = [c for c in outcome["provider"].calls if c["schema"] == "RedTeamReport"]
        assert len(calls) == 1
        prompt = calls[0]["system"] + calls[0]["messages"][0]["content"]

        assert WORKING_NOTE not in prompt
        assert "Revenue is growing and the trajectory is durable." in prompt
        assert str(outcome["fact"].id) in prompt

    async def test_a_lone_challenge_does_not_wait_on_the_batch_queue(
        self, outcome: dict[str, Any]
    ) -> None:
        """The red team submits through :meth:`run_batch` and is billed and archived per
        item exactly as a batch is — but one item is not sent to the batch endpoint.

        This asserted the opposite until a live run showed what the batch queue costs for a
        single item: 2,356 seconds, two thirds of the whole run, to adjudicate one
        challenge. The audit standard is unchanged, because it never came from the
        transport; only the queue is gone.
        """
        calls = [c for c in outcome["provider"].calls if c["schema"] == "RedTeamReport"]
        assert len(calls) == 1
        assert calls[0].get("batch") is None

    async def test_a_planted_contradiction_is_recorded_without_raising_a_fault(
        self, outcome: dict[str, Any]
    ) -> None:
        """The Phase 4 acceptance, minus the banner it used to raise (2026-08-25).

        The planted contradiction still lands as a `disagreements` row, which is what gate
        3's red-team section and the report's appendix are built from. What it no longer
        does is fire a §2.4 trigger: the red team contradicting the draft is the red team
        working, and a fault banner counting it made two real faults read as three.
        """
        recorded = await disagreements_for_job(outcome["session"], outcome["job"].id)
        challenges = [row for row in recorded if row.kind is DisagreementKind.THESIS_CONFLICT]
        assert any(row.topic.startswith("Red team (growth)") for row in challenges)

        fired = await triggers_for_job(
            outcome["session"], job=outcome["job"], request=outcome["request"]
        )
        assert [t.kind for t in fired] == []


class TestRejectionAndSkipping:
    async def test_a_challenge_citing_an_unheld_id_is_rejected_whole(
        self, scene: dict[str, Any]
    ) -> None:
        report = RedTeamReport(
            challenges=[
                RedTeamChallenge(
                    dimension=ChallengeDimension.MACRO,
                    severity=4,
                    statement="Rates make the discounting indefensible.",
                    basis="A macro series this run never acquired.",
                    fact_ids=[str(uuid.uuid4())],
                )
            ],
            coverage_note="One challenge.",
        )
        provider = FakeProvider({"RedTeamReport": report})

        outcome = await run_red_team(
            _context(scene, provider), scene["session"], job=scene["job"], request=scene["request"]
        )

        assert outcome.recorded == []
        assert len(outcome.rejected) == 1
        assert "does not hold" in outcome.rejected[0]
        assert await _rows(scene["session"], scene["job"].id) == []

    async def test_an_unevidenced_challenge_is_dropped_and_the_rest_survive(
        self, scene: dict[str, Any]
    ) -> None:
        """The regression this exists for, from a live run that died at £8.

        The adversary returned six challenges and one of them cited nothing. A schema
        validator raised on it, which failed the parse of the whole report, which failed the
        step, which failed the run — discarding five well-evidenced objections to punish the
        sixth, at the second-to-last step of forty minutes' work.

        The rule is unchanged: an objection resting on nothing gets no row. What changed is
        that it costs one challenge instead of a run.
        """
        report = RedTeamReport(
            challenges=[
                RedTeamChallenge(
                    dimension=ChallengeDimension.GROWTH,
                    severity=5,
                    statement="The revenue fact contradicts the growth the draft asserts.",
                    basis="The claim contradicts the recorded fact it should rest on.",
                    fact_ids=[str(scene["fact"].id)],
                ),
                RedTeamChallenge(
                    dimension=ChallengeDimension.COMPETITIVE_POSITION,
                    severity=4,
                    statement="Competition will compress the margin.",
                    basis="A view, held firmly, resting on nothing in the index.",
                ),
            ],
            coverage_note="One evidenced, one not.",
        )
        provider = FakeProvider({"RedTeamReport": report})

        outcome = await run_red_team(
            _context(scene, provider), scene["session"], job=scene["job"], request=scene["request"]
        )

        assert len(outcome.recorded) == 1, "the evidenced challenge must survive its neighbour"
        assert len(outcome.rejected) == 1
        assert "cites no fact" in outcome.rejected[0]
        assert len(await _rows(scene["session"], scene["job"].id)) == 1

    async def test_a_run_with_no_claims_is_skipped_spending_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        session = scene["session"]
        await session.delete(scene["claim"])
        await session.flush()
        provider = FakeProvider()

        outcome = await run_red_team(
            _context(scene, provider), scene["session"], job=scene["job"], request=scene["request"]
        )

        assert outcome.skipped is True
        assert provider.call_count == 0
        assert await _rows(session, scene["job"].id) == []


class TestTheLadderStatesAreReachable:
    async def test_a_challenge_can_be_settled_by_hand_like_any_escalation(
        self, scene: dict[str, Any]
    ) -> None:
        provider = FakeProvider({"RedTeamReport": _fixture_report(scene)})
        await run_red_team(
            _context(scene, provider), scene["session"], job=scene["job"], request=scene["request"]
        )
        session = scene["session"]
        rows = await _rows(session, scene["job"].id)
        growth = next(row for row in rows if row.topic.startswith("Red team (growth)"))

        settled = await settle_by_hand(
            session,
            disagreement=growth,
            outcome=ResolutionOutcome.CHOSE_B,
            actor=scene["user"],
            rationale="The red team is right: the fact contradicts the claim.",
        )

        assert settled.resolution is ResolutionOutcome.CHOSE_B
        assert settled.escalated_to_gate is None
        open_rows = await escalations_for_job(session, scene["job"].id)
        assert growth.id not in {row.id for row in open_rows}


class TestBatchAndSyncParity:
    async def test_both_paths_produce_identical_rows(self, scene: dict[str, Any]) -> None:
        session = scene["session"]

        def snapshot(rows: list[Disagreement]) -> list[tuple[str, ...]]:
            return sorted(
                (
                    row.topic,
                    row.kind.value,
                    row.resolution.value,
                    row.rule.value,
                    str(row.material),
                    row.position_a["label"],
                    row.position_b["label"],
                    row.fingerprint,
                )
                for row in rows
            )

        sync_provider = FakeProvider({"RedTeamReport": _fixture_report(scene)})
        await run_red_team(
            _context(scene, sync_provider),
            session,
            job=scene["job"],
            request=scene["request"],
            use_batch=False,
        )
        sync_rows = snapshot(await _rows(session, scene["job"].id))
        assert not any(call.get("batch") for call in sync_provider.calls)

        await session.execute(delete(Disagreement).where(Disagreement.job_id == scene["job"].id))
        await session.flush()

        batch_provider = FakeProvider({"RedTeamReport": _fixture_report(scene)})
        await run_red_team(
            _context(scene, batch_provider),
            session,
            job=scene["job"],
            request=scene["request"],
            use_batch=True,
        )
        batch_rows = snapshot(await _rows(session, scene["job"].id))

        assert batch_rows == sync_rows

    async def test_recording_is_idempotent_across_a_retried_step(
        self, scene: dict[str, Any]
    ) -> None:
        provider = FakeProvider({"RedTeamReport": _fixture_report(scene)})
        context = _context(scene, provider)

        await run_red_team(context, scene["session"], job=scene["job"], request=scene["request"])
        await run_red_team(context, scene["session"], job=scene["job"], request=scene["request"])

        assert len(await _rows(scene["session"], scene["job"].id)) == 3
