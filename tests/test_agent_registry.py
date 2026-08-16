"""The agent registry: capability has one source, and it refuses to fork.

Task 33. The claims under test are structural, so the tests are mostly attempts to get
capability from somewhere else — an unregistered role, a class attribute, a schema the
registry does not name — each of which must refuse loudly rather than default quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import aer.agents.registry as registry_module
import aer.providers.costs as costs_module
from aer.agents.base import Agent, AgentContext, TokenCapExceededError, ToolNotPermittedError
from aer.agents.planner import PlannerAgent
from aer.agents.registry import (
    PLATFORM_CONTRACT,
    RoleDefinition,
    RoleDefinitionError,
    UnknownAgentRoleError,
    registered_roles,
)
from aer.config import ModelRoute, Settings
from aer.core.enums import JobStatus
from aer.db.models import Job, JobStep
from aer.errors import BudgetExceededError
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.storage.local import LocalArtefactStore
from aer.storage.protocol import ArtefactStore
from tests.agent_probes import ProbeAnswer
from tests.workflow_fixtures import WORKFLOW_VERSION, seed_request, seed_user

ADR_ROOT = Path(__file__).resolve().parent.parent / "docs" / "adr"


class _Probe(Agent[str, ProbeAnswer]):
    role = "injection-probe"
    output_schema = ProbeAnswer

    def system_prompt(self, _payload: str) -> str:
        return "Answer briefly."

    def user_message(self, payload: str) -> str:
        return payload


class TestConstructionGoesThroughTheRegistry:
    def test_an_unregistered_role_cannot_construct(self):
        class Rogue(Agent[str, ProbeAnswer]):
            role = "a-role-nobody-registered"
            output_schema = ProbeAnswer

        with pytest.raises(UnknownAgentRoleError, match="No agent role named"):
            Rogue()

    def test_a_declared_schema_that_is_not_the_registered_one_is_refused(self):
        # The registry is the source of record for the contract; a class quietly emitting
        # a different shape is a fork, not a variant.
        class Forked(BaseModel):
            verdict: str

        class Disagreeing(Agent[str, Forked]):
            role = "injection-probe"
            output_schema = Forked

        with pytest.raises(RoleDefinitionError, match="source of record"):
            Disagreeing()

    def test_a_subclass_cannot_declare_capability_at_all(self):
        # A class attribute named allowed_tools would shadow the base property and win
        # attribute lookup — so the name is refused at class definition, not ignored.
        with pytest.raises(RoleDefinitionError, match="refused outright"):

            class Grasping(Agent[str, ProbeAnswer]):
                role = "injection-probe"
                output_schema = ProbeAnswer
                allowed_tools = frozenset({"shell", "http_get"})

    def test_a_subclass_cannot_declare_its_own_token_caps(self):
        with pytest.raises(RoleDefinitionError, match="refused outright"):

            class Budgetless(Agent[str, ProbeAnswer]):
                role = "injection-probe"
                output_schema = ProbeAnswer
                max_output_tokens = 10_000_000

    def test_capability_bolted_on_after_class_creation_grants_nothing(self):
        # `__init_subclass__` refuses the attribute at class definition, but assignment
        # after creation bypasses it and shadows the base property. The authorisation
        # check must therefore never read the property — this is the test that keeps the
        # two layers honestly separate.
        class Sneaky(Agent[str, ProbeAnswer]):
            role = "injection-probe"
            output_schema = ProbeAnswer

        Sneaky.allowed_tools = frozenset({"shell"})  # type: ignore[misc, assignment]

        with pytest.raises(ToolNotPermittedError):
            Sneaky().require_tool("shell")

    def test_the_planner_resolves_and_carries_the_registered_capability(self):
        agent = PlannerAgent()

        assert agent.definition.role == "planner"
        assert agent.allowed_tools == frozenset()
        assert agent.definition.max_output_tokens == 16_384


class TestTheDefinitionsThemselves:
    def test_every_role_names_an_adr_that_exists(self):
        # The rule is "a new agent role requires an ADR", and this is where it bites: a
        # definition pointing at no decision record is a red build, not a convention.
        for definition in registry_module._DEFINITIONS:
            matches = list(ADR_ROOT.glob(f"{definition.adr}-*.md"))
            assert matches, (
                f"the {definition.role} role cites ADR {definition.adr}, which no file carries"
            )

    def test_every_production_role_is_one_the_router_recognises(self):
        # Registered-but-unroutable would fail at the first call with a config error;
        # better to fail here, in a test that names the mismatch.
        known = Router(Settings(http_user_agent="Test test@example.invalid")).roles
        for definition in registry_module._DEFINITIONS:
            assert definition.role in known

    def test_a_definition_without_an_adr_is_refused(self, monkeypatch: pytest.MonkeyPatch):
        bare = RoleDefinition(
            role="undocumented",
            purpose="",
            output_schema_ref="tests.agent_probes:ProbeAnswer",
            allowed_tools=frozenset(),
            max_output_tokens=1,
            adr="  ",
        )
        monkeypatch.setattr(registry_module, "_DEFINITIONS", (bare,))

        with pytest.raises(RoleDefinitionError, match="no ADR reference"):
            registry_module._build()

    def test_two_definitions_claiming_one_role_are_refused(self, monkeypatch: pytest.MonkeyPatch):
        first = registry_module._DEFINITIONS[0]
        monkeypatch.setattr(registry_module, "_DEFINITIONS", (first, first))

        with pytest.raises(RoleDefinitionError, match="Two definitions"):
            registry_module._build()

    def test_a_schema_reference_that_no_longer_resolves_is_loud(self):
        dangling = RoleDefinition(
            role="ghost",
            purpose="",
            output_schema_ref="aer.agents.planner:AClassThatWasRenamed",
            allowed_tools=frozenset(),
            max_output_tokens=1,
            adr="0035",
        )

        with pytest.raises(RoleDefinitionError, match="does not resolve"):
            dangling.output_schema()

    def test_a_role_that_thinks_hard_has_room_to_answer_as_well(self):
        """The two tables are one decision, and they were drifting apart silently.

        ``max_tokens`` bounds thinking and visible output *together* on the models this
        platform routes to. So a role sent to opus at high effort spends an unknown part of
        its ceiling reasoning, and whatever is left is all it has to write with — a ceiling
        chosen from the expected length of the answer is a ceiling chosen from the wrong
        number.

        It failed exactly that way in a live run: ``report_writer`` at 8,192 returned
        ``stop_reason: max_tokens`` and no draft for five of one report's sections. Nothing
        connected the routing table to the registry, so the roles that had the headroom had
        it because somebody thought of it, and the roles that did not were the ones that
        had not been run yet.

        The floor is the figure the roles that survived already carried. Asserted against
        the **defaults** rather than enforced in ``_build``: routes are operator-overridable
        through ``AER_MODEL_ROUTES``, and a configuration edit must never be able to stop
        the package importing.
        """
        floor = 16_384
        hard = {"high", "xhigh", "max"}
        routes = Settings(http_user_agent="Test test@example.invalid").model_routes

        for definition in registry_module._DEFINITIONS:
            route = routes[definition.role]
            if route.effort not in hard:
                continue
            assert definition.max_output_tokens >= floor, (
                f"{definition.role} routes to {route.model} at {route.effort} effort with "
                f"only {definition.max_output_tokens} output tokens; thinking can spend "
                f"that before a word of the answer is written"
            )

    def test_the_probe_roles_are_registered_for_this_suite(self):
        # The session fixture in conftest; asserted so a future rearrangement that drops
        # it fails here with a name rather than in forty containment tests.
        assert {"injection-probe", "evaluation-probe"} <= registered_roles()


async def _seeded_step(
    db_session: AsyncSession, *, max_cost_gbp: Decimal = Decimal("12.00")
) -> JobStep:
    """A job step attached to a run and a request, which the spend guard walks up to."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user, max_cost_gbp=max_cost_gbp)
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
        step_key="probe",
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:probe",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    db_session.add(step)
    await db_session.flush()
    return step


def _probe_context(
    db_session: AsyncSession,
    step: JobStep,
    provider: FakeProvider,
    tmp_path: Path,
    **settings_overrides: Any,
) -> AgentContext:
    settings = Settings(
        http_user_agent="Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        model_routes={"injection-probe": ModelRoute(model="claude-haiku-4-5", effort="low")},
        **settings_overrides,
    )
    return AgentContext(
        session=db_session,
        provider=provider,
        router=Router(settings),
        settings=settings,
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        job_step=step,
    )


class TestWhatTheProviderBoundaryRefuses:
    """ADR 0053: two refusals precede every call, and neither is a per-role token guess.

    A live run died at 40,367 input tokens against an `analysis` allowance of 30,000 —
    a big company's evidence doing its job, refused by a number chosen before any run
    existed to measure it. The allowances are gone. What remains is the model's own
    context window (a 400 refused for free) and the money, priced per call against the
    budgets the operator actually set.
    """

    async def test_a_composition_no_model_can_run_is_refused_unmade(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Shrink the routed model's window below the probe's own output ceiling, so any
        # composition at all is unrunnable. The dict is the patch point; the check reads
        # it through `context_window_for` at call time.
        monkeypatch.setitem(costs_module.CONTEXT_WINDOW_TOKENS, "claude-haiku-4-5", 10)

        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="unreachable")})
        settings = Settings(
            http_user_agent="Test test@example.invalid",
            model_routes={"injection-probe": ModelRoute(model="claude-haiku-4-5", effort="low")},
        )
        # No session on purpose: the window check needs nothing persistent, and reaching
        # for the DB before it would mean the free refusal had stopped being free.
        context = AgentContext(
            session=cast(AsyncSession, None),
            provider=provider,
            router=Router(settings),
            settings=settings,
            store=cast(ArtefactStore, None),
            job_step=cast(JobStep, None),
        )

        with pytest.raises(TokenCapExceededError, match="cannot fit"):
            await _Probe().run(context, "Any message at all.")

        # Refused unmade: the count endpoint was consulted, the completion never was.
        assert provider.call_count == 0
        assert len(provider.token_counts) == 1

    async def test_a_call_the_run_cannot_afford_is_refused_unmade(
        self, db_session: AsyncSession, tmp_path: Path
    ):
        # A cap below the worst case of even a tiny probe call — the probe's 4,096-token
        # output ceiling alone prices past two pence on haiku. A penny is the smallest
        # cap the column holds: NUMERIC(10,2), and the check constraint wants > 0.
        step = await _seeded_step(db_session, max_cost_gbp=Decimal("0.01"))
        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="unreachable")})
        context = _probe_context(db_session, step, provider, tmp_path)

        with pytest.raises(BudgetExceededError, match="refused before it was made") as caught:
            await _Probe().run(context, "Brief.")

        assert caught.value.context["scope"] == "per_run"
        assert provider.call_count == 0

    async def test_the_monthly_ceiling_binds_each_call_too(
        self, db_session: AsyncSession, tmp_path: Path
    ):
        step = await _seeded_step(db_session)
        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="unreachable")})
        context = _probe_context(
            db_session, step, provider, tmp_path, monthly_budget_gbp=Decimal("0.000001")
        )

        with pytest.raises(BudgetExceededError) as caught:
            await _Probe().run(context, "Brief.")

        assert caught.value.context["scope"] == "monthly"
        assert provider.call_count == 0

    async def test_an_unaffordable_batch_is_refused_whole_before_any_money_moves(
        self, db_session: AsyncSession, tmp_path: Path
    ):
        # The batch prices as one question: three items' output ceilings together breach
        # a cap that any single item might have crept under. Refused whole — a partially
        # affordable batch would be items silently shifted onto the wrong answers.
        step = await _seeded_step(db_session, max_cost_gbp=Decimal("0.04"))
        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="unreachable")})
        context = _probe_context(db_session, step, provider, tmp_path)

        with pytest.raises(BudgetExceededError) as caught:
            await _Probe().run_batch(context, ["one", "two", "three"])

        assert caught.value.context["scope"] == "per_run"
        assert provider.call_count == 0

    async def test_an_affordable_call_that_fits_completes(
        self, db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A distinctive output cap, so the assertion below can tell "the registry's value
        # reached the provider" apart from "some default happened to match it".
        distinctive = RoleDefinition(
            role="injection-probe",
            purpose="Probe with a recognisable output cap.",
            output_schema_ref="tests.agent_probes:ProbeAnswer",
            allowed_tools=frozenset(),
            max_output_tokens=1234,
            adr="0035",
        )
        monkeypatch.setitem(registry_module._REGISTRY, "injection-probe", distinctive)

        step = await _seeded_step(db_session)
        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="fine")})
        context = _probe_context(db_session, step, provider, tmp_path)

        answer = await _Probe().run(context, "Brief.")

        assert answer.verdict == "fine"
        assert provider.call_count == 1
        # The provider received the registry's output cap, not a default that happened
        # to coincide with it.
        assert provider.calls[0]["max_tokens"] == 1234

    def test_the_output_cap_is_the_registrys_not_a_default(self):
        assert _Probe().definition.max_output_tokens == 4096
        assert PlannerAgent().definition.max_output_tokens == 16_384


class TestThePlatformContract:
    def test_every_composed_prompt_starts_with_it(self):
        composed = _Probe().composed_system_prompt("anything")

        assert composed.startswith(PLATFORM_CONTRACT)
        assert composed.endswith("Answer briefly.")

    def test_it_states_the_rules_that_hold_for_every_role(self):
        # Pinned loosely — the wording may improve, but a contract that stopped saying
        # numbers are not the model's, or that quoted text is data, is a different design.
        assert "never produce a figure" in PLATFORM_CONTRACT
        assert "data" in PLATFORM_CONTRACT
        assert "precedence" in PLATFORM_CONTRACT
