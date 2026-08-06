"""The agent registry: capability has one source, and it refuses to fork.

Task 33. The claims under test are structural, so the tests are mostly attempts to get
capability from somewhere else — an unregistered role, a class attribute, a schema the
registry does not name — each of which must refuse loudly rather than default quietly.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import aer.agents.registry as registry_module
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
from aer.db.models import JobStep
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.storage.protocol import ArtefactStore
from tests.agent_probes import ProbeAnswer

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
            max_input_tokens=1,
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
            max_input_tokens=1,
            max_output_tokens=1,
            adr="0035",
        )

        with pytest.raises(RoleDefinitionError, match="does not resolve"):
            dangling.output_schema()

    def test_the_probe_roles_are_registered_for_this_suite(self):
        # The session fixture in conftest; asserted so a future rearrangement that drops
        # it fails here with a name rather than in forty containment tests.
        assert {"injection-probe", "evaluation-probe"} <= registered_roles()


class TestTheTokenCapAtTheProviderBoundary:
    async def test_a_call_projected_past_the_input_cap_is_refused_unmade(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        tiny = RoleDefinition(
            role="injection-probe",
            purpose="Probe with a cap nothing fits under.",
            output_schema_ref="tests.agent_probes:ProbeAnswer",
            allowed_tools=frozenset(),
            max_input_tokens=3,
            max_output_tokens=4096,
            adr="0035",
        )
        monkeypatch.setitem(registry_module._REGISTRY, "injection-probe", tiny)

        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="unreachable")})
        settings = Settings(
            http_user_agent="Test test@example.invalid",
            model_routes={"injection-probe": ModelRoute(model="claude-haiku-4-5", effort="low")},
        )
        context = AgentContext(
            session=cast(AsyncSession, None),
            provider=provider,
            router=Router(settings),
            settings=settings,
            store=cast(ArtefactStore, None),
            job_step=cast(JobStep, None),
        )

        with pytest.raises(TokenCapExceededError, match="refused before it was made"):
            await _Probe().run(context, "A message far longer than three tokens.")

        # Refused unmade: the count endpoint was consulted, the completion never was.
        assert provider.call_count == 0
        assert len(provider.token_counts) == 1

    async def test_an_ordinary_call_passes_the_cap_and_completes(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # A distinctive output cap, so the assertion below can tell "the registry's value
        # reached the provider" apart from "some default happened to match it".
        distinctive = RoleDefinition(
            role="injection-probe",
            purpose="Probe with a recognisable output cap.",
            output_schema_ref="tests.agent_probes:ProbeAnswer",
            allowed_tools=frozenset(),
            max_input_tokens=50_000,
            max_output_tokens=1234,
            adr="0035",
        )
        monkeypatch.setitem(registry_module._REGISTRY, "injection-probe", distinctive)

        provider = FakeProvider({"ProbeAnswer": ProbeAnswer(verdict="fine")})
        settings = Settings(
            http_user_agent="Test test@example.invalid",
            model_routes={"injection-probe": ModelRoute(model="claude-haiku-4-5", effort="low")},
        )
        # The cap check happens before any use of the persistence half of the context, so
        # a failure of this test with a None-related error means the boundary moved.
        context = AgentContext(
            session=cast(AsyncSession, None),
            provider=provider,
            router=Router(settings),
            settings=settings,
            store=cast(ArtefactStore, None),
            job_step=cast(JobStep, None),
        )

        with pytest.raises(AttributeError):
            # Persistence needs the real session this test deliberately does not carry;
            # reaching persistence is the assertion that the cap let the call through.
            await _Probe().run(context, "Brief.")

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
