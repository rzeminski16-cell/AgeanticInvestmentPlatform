"""The fake refuses what the API would refuse, and answers only what it could have.

Gap A18. A fake that answers from a script tells you nothing about whether the real
endpoint would have accepted the request or produced that reply — which is precisely how
the empty-report bug survived every test for weeks. Three holes are closed:

* the **request envelope**, so a composition the API returns 400 for fails in the suite
  rather than on the first live call;
* the **reply**, validated back into the requested contract exactly as the real provider
  validates what comes off the wire; and
* the **schema**, through the vendor's own transformation, injected from the test side
  because `aer.providers.fake` may not import the SDK.

The tests below are mostly attempts to get something past each of those.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from aer.config import DEFAULT_MODEL_ROUTES
from aer.errors import ExternalServiceError
from aer.providers.fake import _EFFORTS, FakeProvider
from aer.providers.protocol import Message
from tests.schema_guard import SchemaTheApiWouldRejectError, refuse_unanswerable_schema

pytestmark = pytest.mark.anyio


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    score: int = Field(ge=0, le=10)


class OtherAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    something_else: str


class Unanswerable(BaseModel):
    """A contract with a field the API's dialect closes to nothing. The original bug."""

    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]


def _provider(**kwargs: Any) -> FakeProvider:
    return FakeProvider({"Answer": Answer(verdict="fine", score=5)}, **kwargs)


async def _call(provider: FakeProvider, **overrides: Any) -> Any:
    request: dict[str, Any] = {
        "system": "You answer briefly.",
        "messages": [Message(role="user", content="A question.")],
        "model": "claude-haiku-4-5",
        "effort": "low",
        "max_tokens": 1024,
    }
    request.update(overrides)
    return await provider.complete_structured(Answer, **request)


class TestTheEnvelopeTheApiWouldRefuse:
    async def test_an_ordinary_request_is_answered(self) -> None:
        result = await _call(_provider())
        assert result.value.verdict == "fine"

    @pytest.mark.parametrize(
        ("override", "expected"),
        [
            ({"system": ""}, "system prompt is empty"),
            ({"system": "   "}, "system prompt is empty"),
            ({"messages": []}, "no messages"),
            ({"messages": [Message(role="user", content="  ")]}, "no content"),
            ({"model": ""}, "no model was named"),
            ({"effort": "enormous"}, "not one of"),
            ({"max_tokens": 0}, "max_tokens is 0"),
            ({"max_tokens": -1}, "max_tokens is -1"),
        ],
    )
    async def test_a_request_the_api_would_reject_fails_here(
        self, override: dict[str, Any], expected: str
    ) -> None:
        # Each of these is a 400 from the real endpoint and a silent pass from a script, so
        # a caller composing one would go on passing tests until the first live run.
        with pytest.raises(ExternalServiceError, match=expected):
            await _call(_provider(), **override)

    async def test_a_refused_request_is_not_recorded_as_a_call(self) -> None:
        # Otherwise the budget assertions count a call that never happened.
        provider = _provider()
        with pytest.raises(ExternalServiceError):
            await _call(provider, system="")

        assert provider.call_count == 0

    async def test_the_effort_vocabulary_matches_the_configuration(self) -> None:
        # Mirrored rather than imported, so the two have to be pinned to each other or the
        # fake will refuse an effort the router happily configures.
        assert {route.effort for route in DEFAULT_MODEL_ROUTES.values()} | {
            "xhigh",
            "max",
        } == _EFFORTS


class TestTheReplyTheApiCouldNotHaveGiven:
    async def test_a_scripted_answer_of_the_wrong_shape_is_refused(self) -> None:
        # The script names the right schema and returns something else. Before this, the
        # object went straight through and every assertion downstream read a reply the
        # contract forbids.
        provider = FakeProvider({"Answer": OtherAnswer(something_else="nope")})

        with pytest.raises(ExternalServiceError, match="the contract does not permit"):
            await _call(provider)

    async def test_a_scripted_answer_breaking_a_field_constraint_is_refused(self) -> None:
        provider = FakeProvider({"Answer": Answer.model_construct(verdict="fine", score=99)})

        with pytest.raises(ExternalServiceError, match="the contract does not permit"):
            await _call(provider)

    async def test_the_value_returned_is_an_instance_of_the_requested_schema(self) -> None:
        result = await _call(_provider())
        assert isinstance(result.value, Answer)

    async def test_the_archived_payload_is_the_validated_reply(self) -> None:
        # What the audit trail keeps has to be what the caller received, not what the
        # script happened to hold.
        result = await _call(_provider())
        assert result.response_payload["parsed"] == {"verdict": "fine", "score": 5}


class TestTheSchemaTheApiWouldReject:
    def test_the_original_bug_is_caught_at_the_schema(self) -> None:
        # `content: dict[str, Any]` becomes an object with no permitted keys once the SDK
        # transforms it. Eighteen sections came back empty this way.
        with pytest.raises(SchemaTheApiWouldRejectError, match="unable to hold anything"):
            refuse_unanswerable_schema(Unanswerable)

    def test_an_ordinary_contract_passes(self) -> None:
        refuse_unanswerable_schema(Answer)

    async def test_the_hook_runs_before_the_script_is_consulted(self) -> None:
        # So a run composing an unanswerable schema fails on the schema, not on a missing
        # script — the second message would send somebody looking in the wrong place.
        provider = FakeProvider({}, inspect_schema=refuse_unanswerable_schema)

        with pytest.raises(SchemaTheApiWouldRejectError):
            await provider.complete_structured(
                Unanswerable,
                system="You answer briefly.",
                messages=[Message(role="user", content="A question.")],
                model="claude-haiku-4-5",
            )

    async def test_without_the_hook_the_fake_stays_usable(self) -> None:
        # The hook is injected, not mandatory: `aer.providers.fake` may not import the SDK,
        # so a caller outside the test suite gets the envelope and reply checks only.
        provider = FakeProvider({"Unanswerable": Unanswerable(content={"a": 1})})
        result = await provider.complete_structured(
            Unanswerable,
            system="You answer briefly.",
            messages=[Message(role="user", content="A question.")],
            model="claude-haiku-4-5",
        )
        assert result.value.content == {"a": 1}


class TestTheBatchPathIsHeldToTheSameStandard:
    async def test_it_validates_each_reply(self) -> None:
        from aer.providers.protocol import BatchRequest  # noqa: PLC0415

        provider = FakeProvider({"Answer": OtherAnswer(something_else="nope")})

        with pytest.raises(ExternalServiceError, match="the contract does not permit"):
            await provider.complete_structured_batch(
                Answer,
                requests=[
                    BatchRequest(
                        system="You answer briefly.",
                        messages=(Message(role="user", content="A question."),),
                    )
                ],
                model="claude-haiku-4-5",
            )

    async def test_it_refuses_an_empty_system_prompt(self) -> None:
        from aer.providers.protocol import BatchRequest  # noqa: PLC0415

        with pytest.raises(ExternalServiceError, match="system prompt is empty"):
            await _provider().complete_structured_batch(
                Answer,
                requests=[
                    BatchRequest(system="", messages=(Message(role="user", content="A question."),))
                ],
                model="claude-haiku-4-5",
            )
