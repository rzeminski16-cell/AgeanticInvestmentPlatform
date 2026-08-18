"""The gate that guards work which has not happened yet.

Gap B2c, ADR 0046. Three things are under test.

**Which runs stop.** A bank is never given a forecast and must not wait to approve one; a
run missing an input nobody can supply must not stop either, because a gate an operator
cannot clear is a run that pauses and never resumes.

**What the operator is shown.** Every proposal with its value, unit, proposer and
justification, every refusal with its reason, and every gap with a sentence saying why —
approving a list you cannot interrogate is not a control.

**That an operator can complete the set.** The valuation needs three numbers no source in
this workflow produces. Before the create route there was no way to supply them, so the
gate was unreachable by construction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.assumptions import PROPOSED_BY as OPINION_BY
from aer.agents.assumptions import AssumptionProposalDraft, OpinionProposal
from aer.agents.base import AgentContext
from aer.calc.dcf import DRIVER_NAMES
from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import profile_for
from aer.db.models import Approval, Assumption, JobStep, ResearchRequest, User
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import approvals as approval_service
from aer.services.approvals import GATE_ORDER
from aer.services.assumption_gate import (
    COST_OF_CAPITAL_NAMES,
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    PROPOSABLE_NAMES,
    REQUIRED_NAMES,
    RISK_FREE_ASSUMPTION,
    AssumptionGateOutcome,
    assemble,
    dcf_permitted,
    gate_payload,
    gate_required,
    outstanding_for,
)
from aer.services.assumption_proposals import PROPOSED_BY as DERIVED_BY
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation import SCALAR_NAMES
from aer.storage.local import LocalArtefactStore
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.workflows.vertical_slice_v1 import (
    assumptions_gate_payload,
    assumptions_gate_required,
    sector_key_of,
)
from tests.api_fixtures import build_app, client_for
from tests.assumption_fixtures import a_year, analysed, seed_years
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_YEARS = {
    date(2022, 12, 31): a_year(revenue="1000", operating_income="240"),
    date(2023, 12, 31): a_year(revenue="1150", operating_income="290"),
    date(2024, 12, 31): a_year(revenue="1300", operating_income="340"),
}


# ==========================================================================================
# Which runs get a gate at all
# ==========================================================================================


class TestWhoMayHaveADiscountedCashFlow:
    def test_an_unclassified_company_may(self) -> None:
        # The trap this function exists to close. `_classify` emits an empty allowed-models
        # list for a company matching no specialist profile, so "is DCF in the allowed
        # list?" would refuse a forecast for almost every company on the exchange.
        assert dcf_permitted("") is True

    @pytest.mark.parametrize("blocked", ["banks", "insurers", "reits", "biotech_pre_revenue"])
    def test_a_specialist_whose_profile_blocks_the_model_may_not(self, blocked: str) -> None:
        # Real profile keys, checked against the registry rather than assumed. An earlier
        # version of this test passed `"bank"`, which is not a key any profile carries — so
        # it was green because the *unknown-key* branch refused it, and would have stayed
        # green if the blocking rule had been deleted outright.
        assert profile_for(blocked) is not None, f"{blocked} is not a profile this build has"
        assert dcf_permitted(blocked) is False

    @pytest.mark.parametrize("permitted", ["utilities", "mining_energy"])
    def test_a_specialist_whose_profile_allows_the_model_may(self, permitted: str) -> None:
        # The rule has to be read off the profile, not "specialist means no".
        assert dcf_permitted(permitted) is True

    def test_an_unrecognised_classification_is_treated_as_specialist(self) -> None:
        # Cautious in the only direction that matters: the permissive mistake is a
        # discounted cash flow on a business the model does not fit.
        assert dcf_permitted("a-profile-this-build-does-not-carry") is False


class TestWhenStoppingTheRunAchievesSomething:
    def test_a_blocked_sector_does_not_stop(self) -> None:
        assert gate_required({"dcf_permitted": False, "assumptions": [{"name": "x"}]}) is False

    def test_a_run_with_nothing_proposed_does_not_stop(self) -> None:
        assert gate_required({"dcf_permitted": True, "assumptions": [], "outstanding": []}) is False

    def test_a_run_missing_an_input_stops_so_the_operator_can_supply_it(self) -> None:
        # This asserted the opposite while the assumptions surface could amend rows but
        # not create one — pausing over a missing beta left the run stopped for nothing.
        # The surface creates rows now (gap S2), so a gap is exactly what stopping is
        # for: the live AAPL run sailed through in 9ms with four inputs missing and its
        # own red team called the absent valuation material.
        assert (
            gate_required(
                {
                    "dcf_permitted": True,
                    "assumptions": [{"name": "revenue_growth"}],
                    "outstanding": [{"name": "beta", "reason": "no price history"}],
                }
            )
            is True
        )

    def test_a_run_with_only_gaps_still_stops(self) -> None:
        # Nothing proposed, everything missing: the operator supplying the first value
        # is the only way this run ever reaches a forecast.
        assert (
            gate_required(
                {
                    "dcf_permitted": True,
                    "assumptions": [],
                    "outstanding": [{"name": "risk_free_rate", "reason": "no macro adapter"}],
                }
            )
            is True
        )

    def test_a_complete_set_stops(self) -> None:
        assert (
            gate_required(
                {
                    "dcf_permitted": True,
                    "assumptions": [{"name": "revenue_growth"}],
                    "outstanding": [],
                }
            )
            is True
        )

    def test_the_workflow_asks_the_same_question(self) -> None:
        # Two names for one rule. A workflow that drifted from the service would stop runs
        # the service thinks are fine, or fail to stop ones it does not.
        for produced in (
            {"dcf_permitted": False, "assumptions": [{"name": "x"}], "outstanding": []},
            {"dcf_permitted": True, "assumptions": [], "outstanding": []},
            {"dcf_permitted": True, "assumptions": [{"name": "x"}], "outstanding": [{"name": "b"}]},
            {"dcf_permitted": True, "assumptions": [{"name": "x"}], "outstanding": []},
        ):
            assert assumptions_gate_required(produced) == gate_required(produced)


class TestTheStepReadsTheClassification:
    """What the step passes to the service decides whether a bank gets a forecast."""

    def test_a_specialist_classification_is_carried_through(self) -> None:
        outputs = {"classify": {"sector_key": "banks", "sector_label": "Banks"}}
        assert sector_key_of(outputs) == "banks"
        assert dcf_permitted(sector_key_of(outputs)) is False

    def test_an_ordinary_company_reads_as_empty(self) -> None:
        assert sector_key_of({"classify": {"sector_key": ""}}) == ""

    def test_a_run_that_has_not_classified_yet_reads_as_empty(self) -> None:
        # Empty is a real answer here, so an absent step must not raise — but it must also
        # not be reached in practice, which the step's `needs` ordering is what enforces.
        assert sector_key_of({}) == ""


class TestTheGateTakesItsPlaceInTheOrder:
    def test_it_sits_before_the_final_gate(self) -> None:
        # The numbers have to be agreed before the draft that quotes them is approved.
        assert GATE_ORDER.index(GateKind.ASSUMPTIONS) < GATE_ORDER.index(GateKind.FINAL)

    def test_it_sits_after_the_sector_gate(self) -> None:
        # What kind of business this is decides whether there are assumptions at all.
        assert GATE_ORDER.index(GateKind.ASSUMPTIONS) > GATE_ORDER.index(GateKind.SECTOR_SPECIALIST)


# ==========================================================================================
# What a discounted cash flow still needs
# ==========================================================================================


class TestWhatIsStillMissing:
    def test_every_driver_and_scalar_and_cost_of_capital_input_is_required(self) -> None:
        assert set(REQUIRED_NAMES) == {*DRIVER_NAMES, *SCALAR_NAMES, *COST_OF_CAPITAL_NAMES}

    def test_the_discount_rate_itself_is_not_an_assumption(self) -> None:
        # ADR 0046. One unexplained number would otherwise stand in for the whole
        # cost-of-capital chain.
        assert "wacc" not in REQUIRED_NAMES
        assert "discount_rate" not in REQUIRED_NAMES

    def test_nothing_present_means_everything_outstanding(self) -> None:
        assert outstanding_for([], years=5) == REQUIRED_NAMES

    def test_a_flat_driver_satisfies_its_name(self) -> None:
        rows = [_row(name) for name in REQUIRED_NAMES]
        assert outstanding_for(rows, years=5) == ()

    def test_a_complete_per_year_path_satisfies_the_driver(self) -> None:
        rows = [_row(name) for name in REQUIRED_NAMES if name != "revenue_growth"]
        rows += [_row(f"revenue_growth_y{year}") for year in range(1, 6)]
        assert outstanding_for(rows, years=5) == ()

    def test_a_path_with_a_hole_does_not(self) -> None:
        # `aer.services.valuation._path_for` refuses a partial path rather than filling the
        # gap, so a gate that treated four years of five as satisfied would send the run
        # forward to a refusal it could have named here.
        rows = [_row(name) for name in REQUIRED_NAMES if name != "revenue_growth"]
        rows += [_row(f"revenue_growth_y{year}") for year in range(1, 5)]
        assert outstanding_for(rows, years=5) == ("revenue_growth",)


def _row(name: str) -> Any:
    return Assumption(
        request_id=uuid.uuid4(),
        name=name,
        value=Decimal("0.1"),
        unit="pure",
        justification="because",
        proposed_by="test",
        approved=False,
    )


# ==========================================================================================
# What the operator is shown
# ==========================================================================================


class TestThePayloadExplainsItself:
    def test_every_row_carries_its_reasons_and_its_proposer(self) -> None:
        rows = [_row("revenue_growth")]
        rows[0].justification = "The compound rate from FY2022 to FY2024."
        rows[0].proposed_by = DERIVED_BY

        entry = gate_payload(rows, AssumptionGateOutcome())["assumptions"][0]

        assert entry["name"] == "revenue_growth"
        assert entry["justification"] == "The compound rate from FY2022 to FY2024."
        assert entry["proposed_by"] == DERIVED_BY
        assert entry["unit"] == "pure"

    def test_values_are_strings(self) -> None:
        # A JSON number would round a Decimal, and a hash over a rounded figure is a hash
        # over something nobody displayed.
        row = _row("terminal_growth")
        row.value = Decimal("0.024500")
        assert gate_payload([row], AssumptionGateOutcome())["assumptions"][0]["value"] == "0.024500"

    def test_confirmation_state_is_not_in_the_payload(self) -> None:
        # Otherwise confirming a row on the assumptions page changes the hash, and the
        # approval no longer matches what was shown — a gate that invalidates itself the
        # moment the operator does what it asked.
        entry = gate_payload([_row("beta")], AssumptionGateOutcome())["assumptions"][0]
        assert "approved" not in entry

    def test_rows_are_ordered_by_name(self) -> None:
        # The hash is over this structure, so the order has to be a property of the content
        # rather than of whatever the database returned.
        rows = [_row("terminal_growth"), _row("beta"), _row("ebit_margin")]
        names = [
            item["name"] for item in gate_payload(rows, AssumptionGateOutcome())["assumptions"]
        ]
        assert names == ["beta", "ebit_margin", "terminal_growth"]

    def test_the_workflow_payload_is_the_same_content(self) -> None:
        produced = {
            "assumptions": [{"name": "beta", "value": "1.1"}],
            "outstanding": [{"name": "risk_free_rate", "reason": "no series"}],
            "refused": [{"name": "terminal_growth", "value": "0.09", "reason": "above ceiling"}],
            "skipped": ["capex intensity could not be derived"],
            # Present in the step output and deliberately absent from the payload: the run
            # is not asking the operator to approve its own permission check.
            "dcf_permitted": True,
            "payload_hash": "unused",
        }
        payload = assumptions_gate_payload(produced)

        assert set(payload) == {"assumptions", "outstanding", "refused", "skipped"}
        assert payload["refused"][0]["reason"] == "above ceiling"


# ==========================================================================================
# Assembling it against a real run
# ==========================================================================================


class TestAssembly:
    async def test_a_blocked_sector_proposes_nothing_at_all(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        session: AsyncSession = scene["session"]

        outcome = await assemble(
            session,
            None,
            request=scene["request"],
            analysis=await analysed(scene),
            sector_key="banks",
            years=5,
        )

        assert outcome == AssumptionGateOutcome()
        rows = (await session.scalars(select(Assumption))).all()
        assert list(rows) == [], "a blocked run wrote assumptions for a forecast it cannot have"

    async def test_the_six_derived_are_written_unconfirmed(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        session: AsyncSession = scene["session"]

        outcome = await assemble(
            session,
            None,
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        written = {row.name: row for row in await session.scalars(select(Assumption))}
        assert "revenue_growth" in written
        assert written["revenue_growth"].proposed_by == DERIVED_BY
        assert all(not row.approved for row in written.values())
        assert {item.name for item in outcome.derived.derived} <= set(written)

    async def test_no_agent_means_no_model_call_and_no_opinions(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)

        outcome = await assemble(
            scene["session"],
            None,
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        assert outcome.model_consulted is False
        assert outcome.opinions == ()

    async def test_the_cost_of_capital_inputs_are_outstanding_with_reasons(
        self, scene: dict[str, Any]
    ) -> None:
        # The three this workflow cannot source. Each has to say *why*, because an operator
        # who cannot tell "not wired" from "broken" cannot decide whether to type it.
        await seed_years(scene, _YEARS)

        outcome = await assemble(
            scene["session"],
            None,
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        outstanding = dict(outcome.outstanding)
        for name in (RISK_FREE_ASSUMPTION, BETA_ASSUMPTION, EQUITY_RISK_PREMIUM_ASSUMPTION):
            assert name in outstanding
            assert len(outstanding[name]) > 40, f"{name} has no usable explanation"

    async def test_the_two_opinions_are_written_when_the_model_answers(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        await seed_years(scene, _YEARS)
        session: AsyncSession = scene["session"]

        outcome = await assemble(
            session,
            await _agent_context(scene, tmp_path, _in_bounds()),
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        written = {row.name: row for row in await session.scalars(select(Assumption))}
        assert outcome.model_consulted is True
        assert written["terminal_growth"].value == Decimal("0.021000000000")
        assert written["exit_multiple"].value == Decimal("11.000000000000")
        assert written["terminal_growth"].proposed_by == OPINION_BY
        assert written["terminal_growth"].approved is False

    async def test_a_refused_opinion_is_carried_but_never_written(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        # ADR 0046's "refuse, never clamp", at the layer that persists. A refused value
        # must reach the operator as an explanation and must not reach the assumptions
        # table, where confirming it would put a number outside the platform's own bounds
        # into a forecast.
        await seed_years(scene, _YEARS)
        session: AsyncSession = scene["session"]

        outcome = await assemble(
            session,
            await _agent_context(scene, tmp_path, _out_of_bounds()),
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        written = {row.name for row in await session.scalars(select(Assumption))}
        assert "terminal_growth" not in written
        assert "exit_multiple" not in written
        assert {item.name for item in outcome.refused} == {"terminal_growth", "exit_multiple"}
        assert all(item.refusal for item in outcome.refused)

    async def test_a_refusal_leaves_the_name_outstanding_with_the_models_reason(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        await seed_years(scene, _YEARS)

        outcome = await assemble(
            scene["session"],
            await _agent_context(scene, tmp_path, _out_of_bounds()),
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        assert "terminal_growth" in dict(outcome.outstanding)

    async def test_an_underived_assumption_is_explained_rather_than_absent(
        self, scene: dict[str, Any]
    ) -> None:
        # One year only: no trend, no average, nothing derivable. The run has to say so.
        await seed_years(scene, {date(2024, 12, 31): a_year()})

        outcome = await assemble(
            scene["session"],
            None,
            request=scene["request"],
            analysis=await analysed(scene),
            years=5,
        )

        assert outcome.derived.derived == ()
        assert dict(outcome.outstanding).keys() >= set(DRIVER_NAMES)
        assert all(reason.strip() for _, reason in outcome.outstanding)


def _in_bounds() -> AssumptionProposalDraft:
    return AssumptionProposalDraft(
        terminal_growth=OpinionProposal(
            value=Decimal("0.021"), justification="Long-run nominal growth.", confidence=0.6
        ),
        exit_multiple=OpinionProposal(
            value=Decimal("11"), justification="Mid-range for the sector.", confidence=0.5
        ),
    )


def _out_of_bounds() -> AssumptionProposalDraft:
    """Both outside the stated bands: 9% for ever, and a 250x exit."""
    return AssumptionProposalDraft(
        terminal_growth=OpinionProposal(
            value=Decimal("0.09"), justification="Optimistic.", confidence=0.9
        ),
        exit_multiple=OpinionProposal(
            value=Decimal("250"), justification="Very optimistic.", confidence=0.9
        ),
    )


async def _agent_context(
    scene: dict[str, Any], tmp_path: Any, draft: AssumptionProposalDraft
) -> AgentContext:
    session: AsyncSession = scene["session"]
    step = JobStep(
        job_id=scene["job"].id,
        step_key="propose_assumptions",
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{scene['job'].id}:propose_assumptions",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    session.add(step)
    await session.flush()

    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    return AgentContext(
        session=session,
        provider=FakeProvider({"AssumptionProposalDraft": draft}),
        router=Router(settings),
        settings=settings,
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        job_step=step,
    )


# ==========================================================================================
# The route that makes the gate reachable
# ==========================================================================================
#
# The HTTP half lives in `tests/test_assumptions_surface.py`, beside the rest of that
# surface and its committed-request fixtures. What belongs here is the vocabulary itself.


class TestAnOperatorCanSupplyWhatTheRunCouldNot:
    def test_every_input_the_run_cannot_source_is_one_a_person_may_propose(self) -> None:
        # If any of the three were unproposable the gate would be unreachable by
        # construction: it fires only on a complete set, and nothing in this workflow
        # produces a risk-free rate, a beta or an equity risk premium.
        for name in COST_OF_CAPITAL_NAMES:
            assert name in PROPOSABLE_NAMES

    def test_every_required_name_is_proposable(self) -> None:
        assert set(REQUIRED_NAMES) <= set(PROPOSABLE_NAMES)

    def test_per_year_driver_paths_are_proposable(self) -> None:
        # `_path_for` accepts a path instead of a flat value, so an operator wanting a fade
        # has to be able to enter one.
        assert "revenue_growth_y1" in PROPOSABLE_NAMES
        assert "revenue_growth_y5" in PROPOSABLE_NAMES

    def test_a_near_miss_name_is_not_proposable(self) -> None:
        # The whole reason the vocabulary is closed: `inputs_from` looks assumptions up by
        # name, so a plausible-looking near miss would be stored, listed, confirmed and
        # then silently ignored.
        assert "terminal_growth_rate" not in PROPOSABLE_NAMES
        assert "wacc" not in PROPOSABLE_NAMES


# ==========================================================================================
# The page an operator clears the gate from
# ==========================================================================================


_GATE_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


def _proposed_output(*, dcf_permitted_: bool = True) -> dict[str, Any]:
    """What `_propose_assumptions` writes to its step row, hashed as the workflow hashes it."""
    output: dict[str, Any] = {
        "dcf_permitted": dcf_permitted_,
        "sector_key": "",
        "assumptions": [
            {
                "name": "terminal_growth",
                "value": "0.025",
                "unit": "pure",
                "justification": "Long-run nominal growth, below the economy's own rate.",
                "proposed_by": OPINION_BY,
                "confidence": 0.6,
            }
        ],
        "outstanding": [
            {"name": "risk_free_rate", "reason": "No macro series is acquired by this workflow."}
        ],
        "refused": [],
        "skipped": [],
        "model_consulted": True,
    }
    output["payload_hash"] = sha256_hex(canonical_json(assumptions_gate_payload(output)))
    return output


async def _seed_paused_run(engine: Any, *, dcf_permitted_: bool = True) -> dict[str, Any]:
    """A run stopped at the assumptions gate, committed so the application's session sees it."""
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_GATE_TABLES} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        research_request = ResearchRequest(
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
        session.add(research_request)
        await session.flush()

        job = await seed_job(session, request=research_request)
        job.status = JobStatus.AWAITING_APPROVAL
        # Gates are passed in order, so this one needs the plan gate behind it. Without it
        # the approval service refuses out of order and the page's form would look broken
        # for a reason that has nothing to do with the page.
        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash="1" * 64,
        )

        produced = _proposed_output(dcf_permitted_=dcf_permitted_)
        session.add(
            JobStep(
                job_id=job.id,
                step_key="propose_assumptions",
                sequence=10,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{job.id}:propose_assumptions",
                input_hash="0" * 64,
                output_ref=produced,
            )
        )
        # The paused gate step, shaped as `StepPaused` records it: `pending_gate` reads the
        # gate out of exactly this field, which is what puts the link on the console. A run
        # whose sector blocks a forecast never pauses here — `_gate_assumptions` returns
        # rather than raising — so seeding one would be a state the workflow cannot reach.
        if dcf_permitted_:
            session.add(
                JobStep(
                    job_id=job.id,
                    step_key="gate_assumptions",
                    sequence=11,
                    status=JobStatus.AWAITING_APPROVAL,
                    idempotency_key=f"{job.id}:gate_assumptions",
                    input_hash="0" * 64,
                    # The shape `StepPaused.to_dict` really records — code, message and
                    # context together. `pending_gate` reads the gate out of the context,
                    # and the console prints the message, so a fixture missing either half
                    # would be testing a state the platform never writes.
                    error={
                        "code": "step_paused",
                        "message": (
                            "This run is waiting for the ASSUMPTIONS gate. Nothing further "
                            "happens, and nothing further is spent, until somebody approves "
                            "or rejects it."
                        ),
                        "context": {"gate": GateKind.ASSUMPTIONS.value},
                    },
                )
            )
        else:
            job.status = JobStatus.RUNNING
        await session.commit()
        return {"job": job, "request": research_request, "user": user, "produced": produced}


@pytest.fixture
async def at_the_gate(db_engine: Any) -> Any:
    return await _seed_paused_run(db_engine)


@pytest.fixture
async def api(api_settings: Settings, db_engine: Any, fake_redis: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class TestTheOperatorCanReachTheGate:
    """The gate paused runs with nowhere to go.

    `gate_assumptions` shipped with the workflow and without a surface: the console offered
    links for the plan, the sector, the peer set, the financials and the draft, and a run
    that stopped here showed a banner with no button and no page behind it. A gate an
    operator cannot clear is a run that pauses and never resumes — the same failure
    `TestWhenStoppingTheRunAchievesSomething` refuses one step earlier.
    """

    async def test_the_page_shows_every_proposal_with_its_justification(
        self, api: Any, at_the_gate: dict
    ) -> None:
        page = await api.get(f"/runs/{at_the_gate['job'].id}/assumptions")

        assert page.status_code == 200
        assert "terminal_growth" in page.text
        assert "0.025" in page.text
        assert "Long-run nominal growth" in page.text
        # And the gap, said plainly rather than defaulted to a number nobody chose.
        assert "risk_free_rate" in page.text
        assert "No macro series" in page.text

    async def test_the_form_carries_the_hash_the_workflow_will_check(
        self, api: Any, at_the_gate: dict
    ) -> None:
        """The whole point of the hash: approving this page approves these figures."""
        page = await api.get(f"/runs/{at_the_gate['job'].id}/assumptions")

        assert at_the_gate["produced"]["payload_hash"] in page.text

    async def test_the_console_offers_the_link_when_the_run_stopped_here(
        self, api: Any, at_the_gate: dict
    ) -> None:
        console = await api.get(f"/runs/{at_the_gate['job'].id}")

        assert 'id="review-assumptions"' in console.text
        assert f"/runs/{at_the_gate['job'].id}/assumptions" in console.text

    async def test_approving_records_the_decision_and_resumes_the_run(
        self, api: Any, at_the_gate: dict, db_engine: Any
    ) -> None:
        """The acceptance line: a person can clear this gate from a browser."""
        job_id = at_the_gate["job"].id
        page = await api.get(f"/runs/{job_id}/assumptions")
        token = page.cookies.get("aer_csrf") or ""

        response = await api.post(
            f"/runs/{job_id}/gates/{GateKind.ASSUMPTIONS.value}",
            data={
                CSRF_FIELD_NAME: token,
                "payload_hash": at_the_gate["produced"]["payload_hash"],
                "decision": "APPROVED",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            approval = await session.scalar(
                select(Approval).where(
                    Approval.job_id == job_id, Approval.gate == GateKind.ASSUMPTIONS
                )
            )
        assert approval is not None
        # The hash the workflow compares against, so `_require_approval` continues rather
        # than pausing again on an approval of something else.
        assert approval.payload_hash == at_the_gate["produced"]["payload_hash"]

    async def test_a_run_whose_sector_blocks_a_forecast_is_told_there_is_nothing_to_confirm(
        self, api: Any, db_engine: Any
    ) -> None:
        """A link to a page that says "nothing to confirm" is worse than no link."""
        blocked = await _seed_paused_run(db_engine, dcf_permitted_=False)

        page = await api.get(f"/runs/{blocked['job'].id}/assumptions")

        assert page.status_code == 404
        assert "does not permit a discounted cash flow" in page.text
        assert "review-assumptions" not in (await api.get(f"/runs/{blocked['job'].id}")).text
