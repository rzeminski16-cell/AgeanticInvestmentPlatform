"""The additive-only composer: nothing composes looser than the floor, ever.

Task 35, threat T19. The example-based tests pin each rule and its warning; the hypothesis
property at the end is the actual claim — for *any* request the schema admits, against any
allowlist and any ceiling, the composed policy is never looser than the floor, never wider
than the allowlist, never above the ceiling. That is the sentence the containment argument
rests on, so it is tested as a property rather than as a list of examples somebody thought
of.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from aer.core.schemas.skill import EvidencePolicyRequest
from aer.core.skill_policy import (
    BUILTIN_EVIDENCE_FLOOR,
    ComposedSectionPolicy,
    compose_policy,
)

ROLE_TOOLS = frozenset({"search_facts", "search_sources", "fetch_known_url"})
CEILING = 12_000


def _compose(
    *,
    evidence: EvidencePolicyRequest | None = None,
    tools: list[str] | None = None,
    budget: int = 8_000,
    allowlist: frozenset[str] = ROLE_TOOLS,
    ceiling: int = CEILING,
) -> ComposedSectionPolicy:
    return compose_policy(
        requested=evidence if evidence is not None else EvidencePolicyRequest(),
        requested_tools=tools if tools is not None else [],
        requested_budget=budget,
        role_allowlist=allowlist,
        budget_ceiling=ceiling,
    )


class TestTheEvidenceFloor:
    def test_min_sources_zero_clamps_to_the_floor_and_warns(self) -> None:
        composed = _compose(evidence=EvidencePolicyRequest(min_sources=0))

        assert composed.evidence.min_sources == BUILTIN_EVIDENCE_FLOOR.min_sources
        [clamp] = [c for c in composed.clamps if c.field == "evidence_policy.min_sources"]
        assert clamp.requested == "0"
        assert "never lower" in clamp.reason

    def test_asking_for_more_sources_is_granted_without_comment(self) -> None:
        composed = _compose(evidence=EvidencePolicyRequest(min_sources=5, requires_primary=True))

        assert composed.evidence.min_sources == 5
        assert not [c for c in composed.clamps if c.field == "evidence_policy.min_sources"]

    def test_declining_primary_sources_is_not_available(self) -> None:
        composed = _compose(evidence=EvidencePolicyRequest(requires_primary=False))

        assert composed.evidence.requires_primary is True
        [clamp] = [c for c in composed.clamps if c.field == "evidence_policy.requires_primary"]
        assert "not declinable" in clamp.reason

    def test_tightening_the_tier_ceiling_is_granted(self) -> None:
        composed = _compose(
            evidence=EvidencePolicyRequest(min_sources=1, requires_primary=True, max_tier=2)
        )

        assert composed.evidence.max_tier == 2
        assert not [c for c in composed.clamps if c.field == "evidence_policy.max_tier"]

    def test_declining_forward_looking_support_is_a_tightening_and_is_kept(self) -> None:
        composed = _compose(
            evidence=EvidencePolicyRequest(
                min_sources=1, requires_primary=True, allow_forward_looking=False
            )
        )

        assert composed.evidence.allow_forward_looking is False


class TestTheToolIntersection:
    def test_an_unknown_tool_intersects_to_nothing_rather_than_escalating(self) -> None:
        # T19's fifth escalation: "fetch anything from any URL". The answer is an empty
        # intersection and a warning — never a request for a human to grant it.
        composed = _compose(tools=["fetch_arbitrary_url"])

        assert composed.allowed_tools == frozenset()
        [clamp] = [c for c in composed.clamps if c.field == "allowed_tools"]
        assert clamp.requested == "fetch_arbitrary_url"
        assert "never an escalation" in clamp.reason

    def test_tools_the_role_has_are_granted(self) -> None:
        composed = _compose(tools=["search_facts", "search_sources"])

        assert composed.allowed_tools == {"search_facts", "search_sources"}
        assert not [c for c in composed.clamps if c.field == "allowed_tools"]

    def test_a_mixed_request_grants_the_lawful_and_names_the_rest(self) -> None:
        composed = _compose(tools=["search_facts", "shell", "http_get"])

        assert composed.allowed_tools == {"search_facts"}
        named = {c.requested for c in composed.clamps if c.field == "allowed_tools"}
        assert named == {"shell", "http_get"}


class TestTheBudgetClamp:
    def test_a_budget_above_the_ceiling_is_clamped_and_warned(self) -> None:
        composed = _compose(budget=50_000)

        assert composed.token_budget == CEILING
        [clamp] = [c for c in composed.clamps if c.field == "token_budget"]
        assert clamp.requested == "50000"
        assert clamp.effective == str(CEILING)

    def test_a_budget_under_the_ceiling_is_kept(self) -> None:
        composed = _compose(budget=4_000)

        assert composed.token_budget == 4_000


class TestAWellBehavedRequestGetsNoWarnings:
    def test_no_clamps_at_all(self) -> None:
        composed = _compose(
            evidence=EvidencePolicyRequest(min_sources=3, requires_primary=True, max_tier=4),
            tools=["search_facts"],
            budget=8_000,
        )

        assert composed.clamps == ()
        assert composed.evidence.min_sources == 3
        assert composed.evidence.max_tier == 4


# The claim itself, as a property: whatever is asked for, the composed policy is never
# looser than the floor, never wider than the allowlist, never above the ceiling.
_requests = st.builds(
    EvidencePolicyRequest,
    min_sources=st.integers(min_value=0, max_value=50),
    requires_primary=st.booleans(),
    max_tier=st.integers(min_value=1, max_value=5),
    allow_forward_looking=st.booleans(),
)
_tool_names = st.lists(st.text(alphabet="abcdefgh_", min_size=1, max_size=12), max_size=8)
_allowlists = st.frozensets(st.sampled_from(sorted(ROLE_TOOLS)), max_size=3)


class TestThePropertyTheContainmentArgumentRestsOn:
    @given(
        request=_requests,
        tools=_tool_names,
        allowlist=_allowlists,
        budget=st.integers(min_value=1, max_value=200_000),
        ceiling=st.integers(min_value=1, max_value=50_000),
    )
    def test_composition_never_relaxes_anything(
        self,
        request: EvidencePolicyRequest,
        tools: list[str],
        allowlist: frozenset[str],
        budget: int,
        ceiling: int,
    ) -> None:
        composed = compose_policy(
            requested=request,
            requested_tools=tools,
            requested_budget=budget,
            role_allowlist=allowlist,
            budget_ceiling=ceiling,
        )

        assert composed.evidence.min_sources >= BUILTIN_EVIDENCE_FLOOR.min_sources
        assert composed.evidence.requires_primary >= BUILTIN_EVIDENCE_FLOOR.requires_primary
        assert composed.evidence.max_tier <= BUILTIN_EVIDENCE_FLOOR.max_tier
        assert composed.allowed_tools <= allowlist
        assert composed.token_budget <= ceiling
        # And every difference from the request is named: a clamp that happened silently
        # would be safe and baffling.
        if composed.evidence.min_sources != request.min_sources:
            assert any(c.field == "evidence_policy.min_sources" for c in composed.clamps)
        if composed.token_budget != budget:
            assert any(c.field == "token_budget" for c in composed.clamps)
        for dropped in frozenset(tools) - allowlist:
            assert any(
                c.field == "allowed_tools" and c.requested == dropped for c in composed.clamps
            )
