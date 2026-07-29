"""The Anthropic provider's request shape and response handling.

**This file exists because of a production failure.** ``aer.providers.anthropic`` was built
against a fake provider that accepted anything, so nothing checked what actually went on the
wire. The first live call returned ``400 output_config.schema: Extra inputs are not
permitted``, and a second 400 — the removed ``thinking.budget_tokens`` — was queued up
behind it. Two wrong parameter shapes, both provable offline, neither provable by any test
that existed.

So these tests assert the **payload**, against the vendor's documented contract, with the
SDK client stubbed. That is the only layer where a request-shape mistake is catchable without
spending money, and every one of the assertions below would have caught the outage.

The complement is the ``live_llm`` suite, which is the only thing that can prove the contract
has not moved underneath us. It is excluded from the default run because it bills.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any

import pytest

# Imported at module scope, unlike everywhere else in the codebase. ``aer.providers.anthropic``
# defers the SDK import so that a process which never makes a model call never loads it; this
# file's whole purpose is to check the provider against the SDK, so deferring here would buy
# nothing and cost readability. The import-boundary test in ``test_providers.py`` scans ``src``
# and is unaffected.
from anthropic.resources.messages.messages import AsyncMessages
from anthropic.types.output_config_param import OutputConfigParam
from anthropic.types.parsed_message import ParsedMessage, ParsedTextBlock
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from aer.errors import ConfigError, ExternalServiceError, ValidationError
from aer.providers.anthropic import (
    _EFFORT_LEVELS,
    _MODELS_ACCEPTING_EFFORT,
    AnthropicProvider,
)
from aer.providers.costs import DEFAULT_PRICES
from aer.providers.protocol import Message

OPUS = "claude-opus-5"
HAIKU = "claude-haiku-4-5"


class Plan(BaseModel):
    """Carries the constraint kinds the API's schema mode rejects, on purpose."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1, default=0.5)


# -- The stub ------------------------------------------------------------------------------


class _StubUsage:
    input_tokens = 120
    output_tokens = 34
    cache_read_input_tokens = 7
    cache_creation_input_tokens = 0


class _StubTextBlock:
    type = "text"

    def __init__(self, parsed: Any) -> None:
        self.text = "{}"
        self.parsed_output = parsed


class _StubResponse:
    """What ``messages.parse`` returns, in the shape the provider reads.

    Hand-written rather than an SDK ``ParsedMessage``, so that a test asserting on the
    provider's handling of a stop reason is not also asserting that a private SDK
    constructor still works. The shape it presents — ``parsed_output`` on the message and on
    the text block, ``stop_reason``, ``usage``, ``model`` — is checked against the real class
    by :func:`test_the_sdk_still_exposes_what_the_provider_reads`.
    """

    def __init__(
        self,
        parsed: Any = None,
        *,
        stop_reason: str | None = "end_turn",
        blocks: list[Any] | None = None,
    ) -> None:
        self.parsed_output = parsed
        self.content = blocks if blocks is not None else [_StubTextBlock(parsed)]
        self.stop_reason = stop_reason
        self.model = OPUS
        self.usage = _StubUsage()

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {"stop_reason": self.stop_reason, "model": self.model}


class _RecordingMessages:
    """Captures the call and returns whatever it was told to."""

    def __init__(self, response: Any = None, *, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response
        self._raises = raises

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response

    async def count_tokens(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _StubUsage()

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


class _StubClient:
    def __init__(self, messages: _RecordingMessages) -> None:
        self.messages = messages


def _provider(
    response: Any = None, *, raises: BaseException | None = None
) -> tuple[AnthropicProvider, _RecordingMessages]:
    messages = _RecordingMessages(response, raises=raises)
    return AnthropicProvider(api_key="sk-test", client=_StubClient(messages)), messages  # type: ignore[arg-type]


async def _call(
    provider: AnthropicProvider, *, model: str = OPUS, effort: str = "high", max_tokens: int = 8192
) -> Any:
    return await provider.complete_structured(
        Plan,
        system="be brief",
        messages=[Message(role="user", content="plan it")],
        model=model,
        effort=effort,
        max_tokens=max_tokens,
    )


# -- The request shape ---------------------------------------------------------------------


class TestTheRequestShape:
    async def test_the_schema_goes_to_output_format_not_into_the_payload(self) -> None:
        """The exact 400 that took the first real run down.

        ``output_config`` accepts ``effort`` and ``format`` and nothing else. A raw
        ``schema`` key at that level is rejected outright, and the SDK's ``output_format``
        argument is what turns a Pydantic class into the ``format`` block.
        """
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider)

        assert messages.last["output_format"] is Plan
        assert "schema" not in messages.last.get("output_config", {})
        assert set(messages.last.get("output_config", {})) <= {"effort", "format"}

    async def test_no_thinking_budget_is_sent(self) -> None:
        """``thinking: {type: enabled, budget_tokens: n}`` is a 400 from Opus 4.7 onward.

        The parameter was removed, not deprecated. Omitting ``thinking`` entirely runs
        adaptive thinking on every model this platform routes to, which is what is wanted —
        so the assertion is that the key is absent, not that it holds some other value.
        """
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider)

        assert "thinking" not in messages.last

    async def test_effort_is_sent_under_output_config(self) -> None:
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider, effort="xhigh")

        assert messages.last["output_config"]["effort"] == "xhigh"

    async def test_effort_is_withheld_from_a_model_that_has_no_such_parameter(self) -> None:
        """``effort`` arrived with the 4.6 generation; Haiku 4.5 predates it and 400s.

        The call still goes out, at the API's own default effort. Failing the run instead
        would trade a working triage call for a dead one.
        """
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider, model=HAIKU, effort="low")

        assert "output_config" not in messages.last

    async def test_an_effort_level_that_does_not_exist_is_refused_before_the_call(self) -> None:
        """A typo in ``AER_MODEL_ROUTES`` should not cost a round trip and an opaque 400."""
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))

        with pytest.raises(ConfigError, match="not an effort level"):
            await _call(provider, effort="very high")

        assert messages.calls == []

    async def test_the_ceiling_and_the_prompt_travel_as_the_api_names_them(self) -> None:
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider, max_tokens=4321)

        assert messages.last["max_tokens"] == 4321
        assert messages.last["system"] == "be brief"
        assert messages.last["model"] == OPUS
        assert messages.last["messages"] == [{"role": "user", "content": "plan it"}]

    async def test_an_unrecognised_role_becomes_a_user_turn(self) -> None:
        """The SDK types ``role`` as a two-value literal; the protocol carries a string."""
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await provider.complete_structured(
            Plan,
            system="s",
            messages=[Message(role="system", content="c")],
            model=OPUS,
        )

        assert messages.last["messages"][0]["role"] == "user"

    def test_every_priced_model_is_a_deliberate_decision_about_effort(self) -> None:
        """Haiku's absence is the point, so it is asserted rather than left to be noticed."""
        assert HAIKU not in _MODELS_ACCEPTING_EFFORT
        assert {OPUS, "claude-sonnet-5"} <= _MODELS_ACCEPTING_EFFORT
        assert set(DEFAULT_PRICES) - _MODELS_ACCEPTING_EFFORT == {HAIKU}


# -- Archiving -----------------------------------------------------------------------------


class TestWhatIsArchived:
    async def test_the_archived_request_records_the_schema_that_was_asked_for(self) -> None:
        """The sent payload omits the schema; an artefact that omitted it too would not
        describe the call well enough to reproduce it."""
        provider, _ = _provider(_StubResponse(Plan(summary="ok")))
        result = await _call(provider)

        archived = result.request_payload["output_config"]["format"]
        assert archived["type"] == "json_schema"
        assert archived["schema"] == Plan.model_json_schema()
        assert result.request_payload["output_config"]["effort"] == "high"

    async def test_the_archived_request_still_carries_the_prompt(self) -> None:
        provider, _ = _provider(_StubResponse(Plan(summary="ok")))
        result = await _call(provider)

        assert result.request_payload["system"] == "be brief"
        assert result.request_payload["max_tokens"] == 8192

    async def test_archiving_a_haiku_call_does_not_invent_an_effort(self) -> None:
        provider, _ = _provider(_StubResponse(Plan(summary="ok")))
        result = await _call(provider, model=HAIKU, effort="low")

        assert "effort" not in result.request_payload["output_config"]
        assert "format" in result.request_payload["output_config"]

    async def test_usage_is_read_from_the_response_rather_than_estimated(self) -> None:
        provider, _ = _provider(_StubResponse(Plan(summary="ok")))
        result = await _call(provider)

        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 34
        assert result.usage.cache_read_tokens == 7
        assert result.usage.stop_reason == "end_turn"


# -- Reading the response ------------------------------------------------------------------


class TestReadingTheResponse:
    async def test_the_parsed_object_is_returned_as_it_is(self) -> None:
        """``messages.parse`` has already validated it; re-validating would be theatre."""
        plan = Plan(summary="ok", confidence=0.9)
        provider, _ = _provider(_StubResponse(plan))
        result = await _call(provider)

        assert result.value is plan

    async def test_a_block_level_parse_is_found_when_the_message_property_is_absent(
        self,
    ) -> None:
        """The message-level ``parsed_output`` is a convenience over the block field."""
        plan = Plan(summary="ok")
        response = _StubResponse(None, blocks=[_StubTextBlock(plan)])
        provider, _ = _provider(response)

        assert (await _call(provider)).value is plan

    async def test_a_mapping_is_validated_rather_than_trusted(self) -> None:
        provider, _ = _provider(_StubResponse({"summary": "ok", "confidence": 0.25}))
        result = await _call(provider)

        assert result.value.confidence == pytest.approx(0.25)

    async def test_a_mapping_that_does_not_satisfy_the_schema_raises(self) -> None:
        provider, _ = _provider(_StubResponse({"confidence": 0.25}))

        with pytest.raises(ValidationError, match="did not satisfy Plan"):
            await _call(provider)

    async def test_hitting_the_token_ceiling_says_so_and_says_why(self) -> None:
        """The message has to name ``max_tokens``, because the cause is not obvious: the
        ceiling bounds thinking as well as output, so a run can spend all of it reasoning
        and return nothing at all."""
        provider, _ = _provider(_StubResponse(None, stop_reason="max_tokens", blocks=[]))

        with pytest.raises(ValidationError, match="8,192-token ceiling") as caught:
            await _call(provider)

        assert "max_output_tokens" in str(caught.value)

    async def test_a_refusal_is_reported_as_a_refusal(self) -> None:
        """Opus 5's safety classifiers set this, and there is no content behind it."""
        provider, _ = _provider(_StubResponse(None, stop_reason="refusal", blocks=[]))

        with pytest.raises(ValidationError, match="declined to answer"):
            await _call(provider)

    async def test_an_empty_response_with_an_ordinary_stop_reason_names_what_arrived(
        self,
    ) -> None:
        thinking_only = _StubResponse(None, blocks=[type("B", (), {"type": "thinking"})()])
        provider, _ = _provider(thinking_only)

        with pytest.raises(ValidationError, match="no structured output"):
            await _call(provider)


class TestWhenTheReplyCannotBeRead:
    """``messages.parse`` raises before the provider sees a response, so the cause has to be
    inferred from the Pydantic errors. Truncation and a broken bound need different fixes."""

    @staticmethod
    def _validation_error(text: str) -> BaseException:
        try:
            TypeAdapter(Plan).validate_json(text)
        except PydanticValidationError as exc:
            return exc
        raise AssertionError("expected the payload to be rejected")  # pragma: no cover

    async def test_truncated_json_is_diagnosed_as_the_ceiling(self) -> None:
        provider, _ = _provider(raises=self._validation_error('{"summary": "ab'))

        with pytest.raises(ValidationError, match="stopped mid-object") as caught:
            await _call(provider)

        assert "8,192-token ceiling" in str(caught.value)

    async def test_a_broken_bound_is_not_blamed_on_truncation(self) -> None:
        """The API enforces the shape, not the bounds — the SDK moves ``ge``/``le`` into the
        description, where they are guidance. Blaming ``max_tokens`` would send someone to
        raise a ceiling that was never the problem."""
        provider, _ = _provider(
            raises=self._validation_error('{"summary": "ok", "confidence": 1.7}')
        )

        with pytest.raises(ValidationError, match="constraint the API does not enforce") as caught:
            await _call(provider)

        assert "ceiling" not in str(caught.value)


class TestFailures:
    async def test_a_missing_key_is_refused_at_construction(self) -> None:
        with pytest.raises(ExternalServiceError, match="AER_ANTHROPIC_API_KEY"):
            AnthropicProvider(api_key="   ")

    async def test_an_api_failure_becomes_an_external_service_error(self) -> None:
        provider, _ = _provider(raises=RuntimeError("upstream exploded"))

        with pytest.raises(ExternalServiceError, match="upstream exploded"):
            await _call(provider)

    async def test_a_400_is_not_retried(self) -> None:
        """Retrying a rejected request is wrong again, more expensively."""
        boom = type("BadRequest", (Exception,), {"status_code": 400})("no")
        provider, _ = _provider(raises=boom)

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.retryable is False

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_rate_limit_or_a_server_error_is_retryable(self, status: int) -> None:
        boom = type("Transient", (Exception,), {"status_code": status})("later")
        provider, _ = _provider(raises=boom)

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.retryable is True

    async def test_a_timeout_is_retryable_without_a_status_code(self) -> None:
        boom = type("APITimeoutError", (Exception,), {})("slow")
        provider, _ = _provider(raises=boom)

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.retryable is True

    async def test_counting_tokens_reports_its_own_failure(self) -> None:
        provider, _ = _provider(raises=RuntimeError("nope"))

        with pytest.raises(ExternalServiceError, match="Counting tokens failed"):
            await provider.count_tokens(
                system="s", messages=[Message(role="user", content="q")], model=OPUS
            )


# -- The contract with the SDK -------------------------------------------------------------


class TestTheSdkContract:
    """The stub above is only worth something if it matches the real SDK.

    These tests are the seam. They check the installed SDK for the surface the provider
    depends on, so an upgrade that moves it fails here rather than on the next live run.
    """

    def test_messages_parse_accepts_the_arguments_the_provider_sends(self) -> None:
        parameters = set(inspect.signature(AsyncMessages.parse).parameters)
        assert {
            "output_format",
            "output_config",
            "max_tokens",
            "messages",
            "model",
            "system",
        } <= parameters

    def test_output_config_accepts_effort_and_format_and_no_schema(self) -> None:
        """The 400 in one assertion: there is no ``schema`` key at this level."""
        assert set(OutputConfigParam.__annotations__) == {"effort", "format"}

    def test_the_sdk_still_exposes_what_the_provider_reads(self) -> None:
        assert isinstance(ParsedMessage.parsed_output, property)
        assert "parsed_output" in ParsedTextBlock.model_fields

    def test_the_effort_ladder_matches_the_sdk(self) -> None:
        """Ours is a tuple so the error message reads in order; the API's is the authority."""
        # `Optional[Literal[...]]`, and the annotations are unevaluated forward references.
        declared = typing.get_type_hints(OutputConfigParam)["effort"]
        levels = {
            arg
            for member in typing.get_args(declared)
            for arg in typing.get_args(member)
            if isinstance(arg, str)
        }
        assert set(_EFFORT_LEVELS) == levels
