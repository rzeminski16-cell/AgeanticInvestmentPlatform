"""The assumption-proposal role: two fields, bounds in code, and a refusal that explains.

ADR 0046. The claims under test are structural rather than behavioural — a model cannot be
tested into proposing sensible numbers, but it can be *prevented* from proposing anything
else. So most of what follows is an attempt to get a third assumption out of the contract,
or an out-of-band number past the bounds, each of which must fail.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.assumptions import (
    EXIT_MULTIPLE_CEILING,
    EXIT_MULTIPLE_FLOOR,
    PROPOSED_BY,
    TERMINAL_GROWTH_CEILING,
    TERMINAL_GROWTH_FLOOR,
    AssumptionProposalAgent,
    AssumptionProposalDraft,
    AssumptionProposalInput,
    OpinionProposal,
    within_bounds,
)
from aer.agents.base import AgentContext, ToolNotPermittedError
from aer.agents.registry import (
    PLATFORM_CONTRACT,
    UnknownAgentRoleError,
    registered_roles,
    resolve_role,
)
from aer.agents.untrusted import CONTAINMENT_RULE
from aer.config import DEFAULT_MODEL_ROUTES, Settings
from aer.core.enums import JobStatus
from aer.db.models import AgentRun, Job, JobStep
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services.assumption_proposals import PROPOSED_BY as DERIVED_PROPOSED_BY
from aer.storage.local import LocalArtefactStore
from tests.workflow_fixtures import WORKFLOW_VERSION, seed_request, seed_user

pytestmark = pytest.mark.anyio

DISCOUNT_RATE = Decimal("0.085")


def _opinion(
    value: str, *, justification: str = "Because of the stated reasons."
) -> OpinionProposal:
    return OpinionProposal(value=Decimal(value), justification=justification, confidence=0.6)


def _draft(*, growth: str = "0.024", multiple: str = "12.5") -> AssumptionProposalDraft:
    return AssumptionProposalDraft(
        terminal_growth=_opinion(growth), exit_multiple=_opinion(multiple)
    )


def _bounded(
    draft: AssumptionProposalDraft, *, discount_rate: Decimal = DISCOUNT_RATE
) -> dict[str, Any]:
    return {p.name: p for p in within_bounds(draft, discount_rate=discount_rate)}


# ==========================================================================================
# The contract is the confinement
# ==========================================================================================


class TestTwoFieldsAndNoOthers:
    def test_the_draft_has_exactly_the_two_opinions(self) -> None:
        # ADR 0046's containment, asserted as a field list. A role that could also return
        # a revenue path would be a model setting every number in a valuation.
        assert set(AssumptionProposalDraft.model_fields) == {"terminal_growth", "exit_multiple"}

    @pytest.mark.parametrize(
        "smuggled",
        ["revenue_growth", "ebit_margin", "discount_rate", "tax_rate", "wacc"],
    )
    def test_a_third_assumption_cannot_be_smuggled_in(self, smuggled: str) -> None:
        payload = {
            "terminal_growth": {"value": "0.02", "justification": "x", "confidence": 0.5},
            "exit_multiple": {"value": "10", "justification": "x", "confidence": 0.5},
            smuggled: {"value": "0.11", "justification": "x", "confidence": 0.5},
        }
        with pytest.raises(PydanticValidationError, match=r"extra_forbidden|Extra inputs"):
            AssumptionProposalDraft.model_validate(payload)

    def test_an_opinion_cannot_carry_extra_keys_either(self) -> None:
        # Otherwise the confinement holds at the top level and leaks one nesting down.
        with pytest.raises(PydanticValidationError, match=r"extra_forbidden|Extra inputs"):
            OpinionProposal.model_validate(
                {"value": "0.02", "justification": "x", "confidence": 0.5, "unit": "GBP"}
            )

    def test_the_input_cannot_carry_an_extra_hint_either(self) -> None:
        # The confinement has to hold in both directions. A caller able to attach a field
        # of its own would be composing an instruction the role's prompt never describes.
        with pytest.raises(PydanticValidationError, match=r"extra_forbidden|Extra inputs"):
            AssumptionProposalInput.model_validate(
                {
                    "company_name": "Microsoft Corporation",
                    "ticker": "MSFT",
                    "as_of_date": "2024-06-30",
                    "base_currency": "USD",
                    "discount_rate": "0.085",
                    "preferred_terminal_growth": "0.035",
                }
            )

    def test_a_proposal_must_carry_its_reasons(self) -> None:
        with pytest.raises(PydanticValidationError):
            OpinionProposal(value=Decimal("0.02"), justification="", confidence=0.5)

    def test_a_justification_cannot_run_to_an_essay(self) -> None:
        # The ceiling is on the contract rather than in the prompt, because a reviewer
        # asked to read a thousand words per assumption reads neither.
        with pytest.raises(PydanticValidationError):
            OpinionProposal(value=Decimal("0.02"), justification="x" * 5_000, confidence=0.5)

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_is_a_probability(self, confidence: float) -> None:
        with pytest.raises(PydanticValidationError):
            OpinionProposal(value=Decimal("0.02"), justification="x", confidence=confidence)


# ==========================================================================================
# The bounds: applied in code, and a breach is a refusal
# ==========================================================================================


class TestTheBoundsRefuseRatherThanClamp:
    def test_an_ordinary_pair_is_accepted_with_its_reasons_intact(self) -> None:
        proposals = _bounded(_draft(growth="0.021", multiple="11"))

        assert proposals["terminal_growth"].accepted
        assert proposals["exit_multiple"].accepted
        assert proposals["terminal_growth"].value == Decimal("0.021")
        assert proposals["terminal_growth"].justification == "Because of the stated reasons."
        assert proposals["terminal_growth"].confidence == 0.6

    def test_a_growth_rate_at_the_discount_rate_is_refused(self) -> None:
        # At the discount rate the Gordon denominator is zero. Not "large" — undefined.
        refused = _bounded(_draft(growth="0.085"), discount_rate=Decimal("0.085"))
        assert not refused["terminal_growth"].accepted
        assert "discount rate" in (refused["terminal_growth"].refusal or "")

    def test_a_growth_rate_above_the_discount_rate_is_refused(self) -> None:
        # Below the platform ceiling and still impossible, because the discount rate here
        # is lower than the ceiling: the two checks are genuinely different rules.
        refused = _bounded(_draft(growth="0.03"), discount_rate=Decimal("0.025"))
        assert not refused["terminal_growth"].accepted
        assert refused["terminal_growth"].value <= TERMINAL_GROWTH_CEILING

    def test_a_growth_rate_above_the_platform_ceiling_is_refused(self) -> None:
        # Comfortably below a 20% discount rate, so only the ceiling can be refusing it.
        refused = _bounded(_draft(growth="0.09"), discount_rate=Decimal("0.20"))
        assert not refused["terminal_growth"].accepted
        assert str(TERMINAL_GROWTH_CEILING) in (refused["terminal_growth"].refusal or "")

    def test_a_growth_rate_below_the_floor_is_refused(self) -> None:
        refused = _bounded(_draft(growth="-0.05"))
        assert not refused["terminal_growth"].accepted
        assert str(TERMINAL_GROWTH_FLOOR) in (refused["terminal_growth"].refusal or "")

    @pytest.mark.parametrize("edge", [TERMINAL_GROWTH_FLOOR, TERMINAL_GROWTH_CEILING])
    def test_the_growth_bounds_are_inclusive(self, edge: Decimal) -> None:
        # A ceiling that refuses its own value would silently narrow the stated band, and
        # the prompt tells the model the band is what these constants say.
        accepted = _bounded(_draft(growth=str(edge)), discount_rate=Decimal("0.20"))
        assert accepted["terminal_growth"].accepted

    @pytest.mark.parametrize("out_of_band", ["1.5", "0.01", "250", "41"])
    def test_an_exit_multiple_outside_the_band_is_refused(self, out_of_band: str) -> None:
        refused = _bounded(_draft(multiple=out_of_band))
        assert not refused["exit_multiple"].accepted
        assert f"{out_of_band}x" in (refused["exit_multiple"].refusal or "")

    @pytest.mark.parametrize("edge", [EXIT_MULTIPLE_FLOOR, EXIT_MULTIPLE_CEILING])
    def test_the_multiple_bounds_are_inclusive(self, edge: Decimal) -> None:
        assert _bounded(_draft(multiple=str(edge)))["exit_multiple"].accepted

    def test_a_refused_value_is_carried_through_unchanged(self) -> None:
        # The whole of the "refuse, never clamp" decision. Clamping would attribute this
        # platform's number to the model's reasoning.
        refused = _bounded(_draft(growth="0.09", multiple="120"), discount_rate=Decimal("0.20"))

        assert refused["terminal_growth"].value == Decimal("0.09")
        assert refused["exit_multiple"].value == Decimal("120")
        assert TERMINAL_GROWTH_CEILING not in {p.value for p in refused.values()}
        assert EXIT_MULTIPLE_CEILING not in {p.value for p in refused.values()}

    def test_a_refusal_on_one_leaves_the_other_standing(self) -> None:
        mixed = _bounded(_draft(growth="0.9", multiple="9"))

        assert not mixed["terminal_growth"].accepted
        assert mixed["exit_multiple"].accepted

    def test_the_discount_rate_is_the_reason_given_when_both_rules_are_broken(self) -> None:
        # 9% breaches the ceiling and sits above a 5% discount rate. The undefined-terminal
        # -value explanation is the one an operator needs, so it is the one reported.
        refusal = _bounded(_draft(growth="0.09"), discount_rate=Decimal("0.05"))[
            "terminal_growth"
        ].refusal
        assert refusal is not None
        assert "discount rate" in refusal

    def test_every_refusal_says_what_to_do_instead(self) -> None:
        for draft, rate in (
            (_draft(growth="0.5"), DISCOUNT_RATE),
            (_draft(growth="0.09"), Decimal("0.20")),
            (_draft(growth="-0.5"), DISCOUNT_RATE),
            (_draft(multiple="900"), DISCOUNT_RATE),
        ):
            refusals = [p.refusal for p in within_bounds(draft, discount_rate=rate) if p.refusal]
            assert refusals, "a breach produced no refusal"
            for refusal in refusals:
                assert "Not proposed" in refusal

    def test_the_bounds_are_a_pure_function_of_the_draft_and_the_rate(self) -> None:
        # No session, no clock, no settings: the check must be reproducible from the
        # recorded draft alone when somebody asks why an assumption is missing.
        assert list(inspect.signature(within_bounds).parameters) == ["draft", "discount_rate"]


# ==========================================================================================
# The registry admits the role, and grants it nothing
# ==========================================================================================


class TestTheRoleIsRegisteredAndRoutable:
    def test_the_definition_names_the_adr_that_admitted_it(self) -> None:
        assert resolve_role("assumption_proposal").adr == "0046"

    def test_the_registered_contract_is_the_two_field_draft(self) -> None:
        assert resolve_role("assumption_proposal").output_schema() is AssumptionProposalDraft

    def test_the_role_holds_no_tools(self) -> None:
        assert resolve_role("assumption_proposal").allowed_tools == frozenset()

    def test_the_input_cap_is_a_tripwire_rather_than_a_context_window(self) -> None:
        # The role is handed two short summaries. A cap anywhere near the model's context
        # would happily carry the whole evidence pack, which is the composition this cap
        # exists to catch — so the number is pinned, not merely present.
        assert resolve_role("assumption_proposal").max_input_tokens == 20_000

    def test_the_agent_constructs_and_cannot_reach_a_tool(self) -> None:
        agent = AssumptionProposalAgent()

        assert agent.allowed_tools == frozenset()
        with pytest.raises(ToolNotPermittedError, match="may not use the tool"):
            agent.require_tool("fetch_known_url")

    def test_the_role_has_a_model_route(self) -> None:
        # An unrouted role fails at the first call with a config error; the route is part
        # of landing the role, not a later configuration chore.
        assert "assumption_proposal" in DEFAULT_MODEL_ROUTES
        settings = Settings(http_user_agent="Test test@example.invalid")
        assert Router(settings).resolve("assumption_proposal").model

    def test_the_router_recognises_it(self) -> None:
        settings = Settings(http_user_agent="Test test@example.invalid")
        router = Router(settings)
        assert "assumption_proposal" not in router.unknown_roles()
        assert "assumption_proposal" not in router.missing_roles()

    def test_it_did_not_move_into_the_reserved_interpretation_role(self) -> None:
        # ADR 0046's "what this role is not". Interpreting a finished valuation and
        # choosing one of its inputs are different jobs at opposite ends of the pipeline,
        # and merging them would put a writing role in the position of deciding numbers.
        # `valuation_interpretation` is routed but unbuilt, and stays that way.
        assert AssumptionProposalAgent.role == "assumption_proposal"
        assert "valuation_interpretation" not in registered_roles()
        with pytest.raises(UnknownAgentRoleError):
            resolve_role("valuation_interpretation")


# ==========================================================================================
# The prompt states the bounds it will be held to
# ==========================================================================================


class TestThePromptTellsTheModelWhatWillBeRefused:
    def test_the_system_prompt_quotes_every_bound(self) -> None:
        # A model that does not know the band wastes a proposal on a number that will be
        # discarded, and the operator gets a blank where a suggestion should be.
        prompt = AssumptionProposalAgent().system_prompt(_input())

        for bound in (
            TERMINAL_GROWTH_CEILING,
            TERMINAL_GROWTH_FLOOR,
            EXIT_MULTIPLE_FLOOR,
            EXIT_MULTIPLE_CEILING,
        ):
            assert str(bound) in prompt

    def test_the_system_prompt_says_a_person_confirms(self) -> None:
        assert "confirms" in AssumptionProposalAgent().system_prompt(_input())

    def test_the_platform_contract_leads_the_composed_prompt(self) -> None:
        composed = AssumptionProposalAgent().composed_system_prompt(_input())
        assert composed.startswith(PLATFORM_CONTRACT)

    def test_no_containment_rule_because_there_is_no_untrusted_content(self) -> None:
        # The role reads findings, not documents. A containment rule here would describe a
        # call that does not happen.
        agent = AssumptionProposalAgent()
        assert agent.untrusted_sources(_input()) == []
        assert CONTAINMENT_RULE not in agent.composed_system_prompt(_input())

    def test_the_user_message_carries_the_discount_rate(self) -> None:
        message = AssumptionProposalAgent().user_message(_input())
        assert "0.085" in message

    def test_the_derived_history_reaches_the_model(self) -> None:
        message = AssumptionProposalAgent().user_message(
            _input(derived=("revenue_growth = 0.114 — compound over four years",))
        )
        assert "revenue_growth = 0.114" in message
        assert "compound over four years" in message

    def test_the_findings_reach_the_model(self) -> None:
        message = AssumptionProposalAgent().user_message(
            _input(findings=("The category is growing at mid single digits.",))
        )
        assert "The category is growing at mid single digits." in message

    def test_empty_sections_are_left_out_rather_than_headed_and_blank(self) -> None:
        message = AssumptionProposalAgent().user_message(_input())

        assert "Assumptions already derived" not in message
        assert "What the research found" not in message
        assert "Sector model" not in message

    def test_the_sector_is_named_when_there_is_one(self) -> None:
        assert "Sector model: software" in AssumptionProposalAgent().user_message(
            _input(sector="software")
        )


def _input(**overrides: Any) -> AssumptionProposalInput:
    fields: dict[str, Any] = {
        "company_name": "Microsoft Corporation",
        "ticker": "MSFT",
        "as_of_date": "2024-06-30",
        "base_currency": "USD",
        "discount_rate": DISCOUNT_RATE,
    }
    fields.update(overrides)
    return AssumptionProposalInput(**fields)


# ==========================================================================================
# One real call, through the base: routed, archived, metered
# ==========================================================================================


class TestTheAgentRunsThroughTheBase:
    async def test_a_scripted_call_returns_the_draft_and_records_the_run(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = Job(
            request_id=request.id,
            workflow_version=WORKFLOW_VERSION,
            code_version="test",
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        db_session.add(job)
        await db_session.flush()
        step = JobStep(
            job_id=job.id,
            step_key="propose_assumptions",
            sequence=0,
            status=JobStatus.RUNNING,
            attempt=0,
            idempotency_key=f"{job.id}:propose_assumptions",
            input_hash="0" * 64,
            started_at=datetime.now(UTC),
        )
        db_session.add(step)
        await db_session.flush()

        settings = Settings(
            http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
        )
        store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
        provider = FakeProvider({"AssumptionProposalDraft": _draft(growth="0.022", multiple="13")})
        context = AgentContext(
            session=db_session,
            provider=provider,
            router=Router(settings),
            settings=settings,
            store=store,
            job_step=step,
        )

        draft = await AssumptionProposalAgent().run(context, _input())

        assert draft.terminal_growth.value == Decimal("0.022")
        assert draft.exit_multiple.value == Decimal("13")

        recorded = (
            await db_session.scalars(select(AgentRun).where(AgentRun.job_step_id == step.id))
        ).all()
        assert [run.agent_role for run in recorded] == ["assumption_proposal"]
        # The registry's cap reached the provider. Pinned to a literal as well as to the
        # definition: comparing the provider's value against the same constant the code
        # read would pass however the cap was changed.
        assert resolve_role("assumption_proposal").max_output_tokens == 8_192
        assert provider.calls[0]["max_tokens"] == 8_192


# ==========================================================================================
# Who proposed what
# ==========================================================================================


class TestTheTwoProposersAreDistinguishable:
    def test_the_agent_and_the_derivation_sign_their_work_differently(self) -> None:
        # An operator at the gate is told which assumptions came from the filings and
        # which from a model. One `proposed_by` for both would erase the distinction the
        # whole of ADR 0046 rests on.
        assert PROPOSED_BY != DERIVED_PROPOSED_BY
        assert PROPOSED_BY == "aer.agents.assumptions"
