"""The planner's list bounds are stated, and an over-full list is trimmed, not fatal.

Gap A42. A live run died at step one: the planner proposed eleven ``known_risks``
against a schema bound of ten the prompt never mentioned, the planner is one call with
no retry, and the whole run failed carrying a £0.12 bill and a plan sound in every other
respect. The prompt now states the three list bounds beside the character budgets, and
code cuts an over-full list back to its bound from the billed reply — a pure narrowing,
strongest-first by the model's own ordering (ADR 0036).
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from aer.agents import planner as planner_module
from aer.agents.planner import (
    MAX_RISKS,
    MAX_SECTIONS,
    MAX_SOURCES,
    ResearchPlanDraft,
    salvaged_plan,
)
from aer.providers.anthropic import _unreadable_reply
from aer.providers.protocol import SpentButUnusableError, Usage
from aer.workflow.workflows import vertical_slice_v1
from tests.workflow_fixtures import planner_response


def _billed_rejection(payload: dict[str, Any]) -> SpentButUnusableError:
    """The error the provider really raises for a billed plan the schema refuses."""
    try:
        ResearchPlanDraft.model_validate(payload)
    except PydanticValidationError as broken:
        inner = _unreadable_reply(
            broken, schema=ResearchPlanDraft, model="claude-opus-5", max_tokens=16_384
        )
        return SpentButUnusableError(
            inner.message,
            usage=Usage(input_tokens=300, output_tokens=5_500, model="claude-opus-5"),
            request_payload={"model": "claude-opus-5"},
            response_payload={"content": [{"type": "text", "text": json.dumps(payload)}]},
            context=inner.context,
        )
    raise AssertionError("the payload under test must not validate")  # pragma: no cover


def _plan_payload(**overrides: Any) -> dict[str, Any]:
    payload = planner_response().model_dump(mode="json")
    payload.update(overrides)
    return payload


class TestAnOverfullListCostsTheTailNotTheRun:
    def test_an_eleventh_risk_is_trimmed_from_the_billed_reply(self) -> None:
        risks = [f"Risk {index}: something the plan should say." for index in range(MAX_RISKS + 1)]
        rejected = _billed_rejection(_plan_payload(known_risks=risks))

        rescued = salvaged_plan(rejected)

        assert rescued is not None
        draft, trimmed = rescued
        assert trimmed == {"known_risks": 1}
        assert len(draft.known_risks) == MAX_RISKS
        # Strongest first is the prompt's instruction, so the head survives the cut.
        assert draft.known_risks == risks[:MAX_RISKS]

    def test_salvage_declines_when_the_lists_are_not_the_problem(self) -> None:
        """A summary over its ceiling is not repairable by cutting lists; the original
        error stands and the step fails exactly as before."""
        rejected = _billed_rejection(_plan_payload(summary="x" * 5_000))

        assert salvaged_plan(rejected) is None

    def test_a_rejection_with_no_archived_reply_is_declined(self) -> None:
        bare = SpentButUnusableError(
            "no payload",
            usage=Usage(input_tokens=1, output_tokens=1, model="claude-opus-5"),
            request_payload={},
            response_payload={},
        )
        assert salvaged_plan(bare) is None


class TestTheBoundsAreStated:
    def test_the_prompt_states_all_three_list_bounds(self) -> None:
        # The prompt is a constant — asserted on the module's own rendering so an
        # unformatted placeholder or a dropped clause fails here by name.
        prompt = planner_module._SYSTEM_PROMPT

        assert f"at most {MAX_SECTIONS} sections" in prompt
        assert f"{MAX_SOURCES} planned sources" in prompt
        assert f"{MAX_RISKS} known risks" in prompt

    def test_the_plan_step_reaches_for_salvage_before_failing(self) -> None:
        """The wiring, pinned at the source: the step catches the billed error and asks
        for the trimmed plan *before* any re-raise — a raise ahead of the call would be
        the call as decoration, which is what this pin exists to refuse."""
        source = inspect.getsource(vertical_slice_v1._plan)
        caught = source.index("except SpentButUnusableError")
        rescued = source.index("salvaged_plan(")
        reraised = source.index("raise", caught)
        assert caught < rescued < reraised
