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
from types import SimpleNamespace
from typing import Any, Final

import pytest

# Imported at module scope, unlike everywhere else in the codebase. ``aer.providers.anthropic``
# defers the SDK import so that a process which never makes a model call never loads it; this
# file's whole purpose is to check the provider against the SDK, so deferring here would buy
# nothing and cost readability. The import-boundary test in ``test_providers.py`` scans ``src``
# and is unaffected.
from anthropic.lib.streaming import AsyncMessageStream, AsyncMessageStreamManager
from anthropic.resources.messages.messages import AsyncMessages
from anthropic.types.json_output_format_param import JSONOutputFormatParam
from anthropic.types.messages.batch_create_params import MessageCreateParamsNonStreaming
from anthropic.types.output_config_param import OutputConfigParam
from pydantic import BaseModel, ConfigDict, Field

from aer.errors import ConfigError, ExternalServiceError, ValidationError
from aer.providers.anthropic import (
    _EFFORT_LEVELS,
    _MODELS_ACCEPTING_EFFORT,
    AnthropicProvider,
)
from aer.providers.costs import DEFAULT_PRICES
from aer.providers.protocol import BatchRequest, Message, SpentButUnusableError

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
    """A text block carrying the reply as JSON, which is what the API really sends.

    ``parsed_output`` is deliberately absent. The provider hands the schema to the wire as
    a dict so the SDK does not parse during accumulation (that is what keeps the usage
    intact when a reply is unusable), and a stub still offering a pre-parsed object would
    let a test pass against a code path production no longer takes.
    """

    type = "text"

    def __init__(self, parsed: Any) -> None:
        self.text = parsed.model_dump_json() if isinstance(parsed, BaseModel) else "{}"


class _StubResponse:
    """What ``get_final_message`` returns, in the shape the provider reads.

    Hand-written rather than an SDK ``ParsedMessage``, so that a test asserting on the
    provider's handling of a stop reason is not also asserting that a private SDK
    constructor still works. The shape it presents — text blocks, ``stop_reason``,
    ``usage``, ``model`` — is checked against the real class by
    :func:`test_the_sdk_still_exposes_what_the_provider_reads`.
    """

    def __init__(
        self,
        parsed: Any = None,
        *,
        stop_reason: str | None = "end_turn",
        blocks: list[Any] | None = None,
    ) -> None:
        self.content = blocks if blocks is not None else [_StubTextBlock(parsed)]
        self.stop_reason = stop_reason
        self.model = OPUS
        self.usage = _StubUsage()

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {"stop_reason": self.stop_reason, "model": self.model}


class _StubStream:
    """The accumulating half of ``messages.stream``.

    Only ``get_final_message`` is implemented, because that is all the provider calls. The
    real class also exposes ``text_stream`` and per-event iteration; a stub that grew those
    would be asserting the SDK's behaviour rather than ours.
    """

    def __init__(self, response: Any, *, raises: BaseException | None) -> None:
        self._response = response
        self._raises = raises

    async def get_final_message(self) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._response


class _StubStreamManager:
    """What ``messages.stream`` returns: an async context manager, not an awaitable.

    The distinction matters. The real manager makes the HTTP request in ``__aenter__``, so a
    connection failure surfaces on entry rather than from the call that built the manager --
    which is why the provider's ``try`` has to wrap the ``async with`` and not just its body.
    ``raises_at`` lets a test choose which of the two moments fails.
    """

    def __init__(self, response: Any, *, raises: BaseException | None, raises_at: str) -> None:
        self._response = response
        self._raises = raises
        self._raises_at = raises_at
        self.exited = False

    async def __aenter__(self) -> _StubStream:
        if self._raises is not None and self._raises_at == "enter":
            raise self._raises
        return _StubStream(
            self._response,
            raises=self._raises if self._raises_at == "final" else None,
        )

    async def __aexit__(self, *_: Any) -> bool:
        self.exited = True
        return False


class _RecordingMessages:
    """Captures the call and returns whatever it was told to."""

    def __init__(
        self,
        response: Any = None,
        *,
        raises: BaseException | None = None,
        raises_at: str = "enter",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.streams: list[_StubStreamManager] = []
        self._response = response
        self._raises = raises
        self._raises_at = raises_at

    def stream(self, **kwargs: Any) -> _StubStreamManager:
        """Synchronous, like the real one. ``async with`` does the awaiting."""
        self.calls.append(kwargs)
        manager = _StubStreamManager(self._response, raises=self._raises, raises_at=self._raises_at)
        self.streams.append(manager)
        return manager

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
    response: Any = None, *, raises: BaseException | None = None, raises_at: str = "enter"
) -> tuple[AnthropicProvider, _RecordingMessages]:
    messages = _RecordingMessages(response, raises=raises, raises_at=raises_at)
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


# -- How the call is made ------------------------------------------------------------------


class TestTheCallIsStreamed:
    """The fix for the outage that stopped the first real run.

    A non-streamed request holds a connection with no bytes on it for the whole of a
    thinking turn, and something in the path reaps it: ``RemoteProtocolError: Server
    disconnected without sending a response``, three minutes in, every time. Nothing here
    reads the deltas -- streaming is for the connection, not for progress -- so the
    assertions are about *how* the call is made rather than what comes back.
    """

    async def test_the_request_goes_out_as_a_stream(self) -> None:
        provider, messages = _provider(_StubResponse(Plan(summary="ok")))
        await _call(provider)

        assert len(messages.streams) == 1

    async def test_the_stream_is_closed_even_when_the_reply_is_unusable(self) -> None:
        """``async with``, not a bare call: a stream left open holds a connection."""
        provider, messages = _provider(_StubResponse(None, stop_reason="refusal", blocks=[]))

        with pytest.raises(ValidationError):
            await _call(provider)

        assert messages.streams[0].exited is True

    async def test_a_disconnect_while_the_model_thinks_is_retryable(self) -> None:
        """The observed failure, in the shape the SDK delivers it.

        ``APIConnectionError`` carries no status code, so the classifier has to reach it by
        name. Marking this permanent would turn a dropped connection into a dead run.
        """
        dropped = type("APIConnectionError", (Exception,), {})("Connection error.")
        provider, _ = _provider(raises=dropped, raises_at="final")

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.retryable is True

    async def test_a_connection_refused_on_entry_is_caught_too(self) -> None:
        """The manager makes the request in ``__aenter__``; the ``try`` must wrap that."""
        provider, _ = _provider(raises=RuntimeError("no route"), raises_at="enter")

        with pytest.raises(ExternalServiceError, match="no route"):
            await _call(provider)


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

        sent = messages.last["output_format"]
        assert sent["type"] == "json_schema"
        assert sent["schema"]["properties"].keys() == {"summary", "confidence"}
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
        # A list of blocks rather than a bare string since A14: the system prompt carries a
        # cache breakpoint, and the API takes a breakpoint only on a structured block.
        assert messages.last["system"] == [
            {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
        ]
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

        assert result.request_payload["system"] == [
            {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
        ]
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
    async def test_the_reply_is_read_from_the_text_blocks(self) -> None:
        """The provider parses, because the SDK deliberately no longer does: the schema
        goes to the wire as a dict so the stream cannot raise before the usage arrives."""
        provider, _ = _provider(_StubResponse(Plan(summary="ok", confidence=0.9)))

        result = await _call(provider)

        assert result.value == Plan(summary="ok", confidence=0.9)

    async def test_several_text_blocks_are_read_as_one_reply(self) -> None:
        """The API may split one JSON object across blocks; a reader taking only the first
        would fail on a perfectly good reply."""
        halves = [
            SimpleNamespace(type="text", text='{"summary": "split ac'),
            SimpleNamespace(type="text", text='ross blocks"}'),
        ]
        provider, _ = _provider(_StubResponse(None, blocks=halves))

        assert (await _call(provider)).value.summary == "split across blocks"

    async def test_a_reply_that_does_not_satisfy_the_schema_raises(self) -> None:
        broken = [SimpleNamespace(type="text", text='{"confidence": 0.25}')]
        provider, _ = _provider(_StubResponse(None, blocks=broken))

        with pytest.raises(ValidationError, match="could not be read as Plan"):
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

        with pytest.raises(ValidationError, match="no text to read") as caught:
            await _call(provider)

        assert "thinking" in str(caught.value)


class TestWhenTheReplyCannotBeRead:
    """A reply that breaks a bound, and the bill that comes with it.

    The API enforces a structured reply's *shape* but not its bounds: the SDK moves ``ge``,
    ``le`` and ``max_length`` into the schema's descriptions, where they are guidance rather
    than a rule the server applies. So a structurally perfect reply can still fail here, and
    truncation and a broken bound need opposite fixes.

    **And the call was billed either way.** The failure carries the usage, because a budget
    cap that cannot see this spend is a budget cap that does not work.
    """

    @staticmethod
    def _replying(text: str) -> Any:
        return _StubResponse(None, blocks=[SimpleNamespace(type="text", text=text)])

    async def test_truncated_json_is_diagnosed_as_the_ceiling(self) -> None:
        provider, _ = _provider(self._replying('{"summary": "ab'))

        with pytest.raises(ValidationError, match="stopped mid-object") as caught:
            await _call(provider)

        assert "8,192-token ceiling" in str(caught.value)

    async def test_a_broken_bound_is_not_blamed_on_truncation(self) -> None:
        """Blaming ``max_tokens`` would send someone to raise a ceiling that was never the
        problem."""
        provider, _ = _provider(self._replying('{"summary": "ok", "confidence": 1.7}'))

        with pytest.raises(ValidationError, match="constraint the API does not enforce") as caught:
            await _call(provider)

        assert "ceiling" not in str(caught.value)

    async def test_the_context_carries_the_constraint_that_was_broken(self) -> None:
        """Which field, and what was wrong with it — not merely that something was.

        The summary is read by the research loop as well as by a person: it feeds the
        rejection back to the model so the next attempt can fix it, and "confidence,
        less_than_equal" is not something anything can act on. Pydantic's ``msg`` is
        generated from the constraint rather than from the reply, so keeping it leaks
        nothing of an arbitrary-length model output into a log line.
        """
        provider, _ = _provider(self._replying('{"summary": "ok", "confidence": 1.7}'))

        with pytest.raises(ValidationError) as caught:
            await _call(provider)

        [error] = caught.value.context["errors"]
        assert error["loc"] == "confidence"
        assert "less than or equal to 1" in error["msg"]

    async def test_the_failure_carries_what_the_call_cost(self) -> None:
        """**The metering hole this shape exists to close.** The reply was generated and
        billed; only its usability is in question. An error carrying nothing but a message
        was an error no cost row was ever written for."""
        provider, _ = _provider(self._replying('{"summary": "ok", "confidence": 1.7}'))

        with pytest.raises(SpentButUnusableError) as caught:
            await _call(provider)

        assert caught.value.usage.input_tokens == _StubUsage.input_tokens
        assert caught.value.usage.output_tokens == _StubUsage.output_tokens

    async def test_the_failure_carries_both_payloads(self) -> None:
        """ "Why did it say that?" is asked far more often about the replies that failed."""
        provider, _ = _provider(self._replying('{"summary": "ok", "confidence": 1.7}'))

        with pytest.raises(SpentButUnusableError) as caught:
            await _call(provider)

        assert caught.value.request_payload["messages"]
        assert caught.value.response_payload

    async def test_it_is_still_a_validation_error(self) -> None:
        """Callers already catching ValidationError keep working, and get the metering as a
        consequence of the type rather than by remembering to."""
        provider, _ = _provider(self._replying("{"))

        with pytest.raises(ValidationError):
            await _call(provider)

    async def test_a_failure_before_the_money_moves_carries_no_bill(self) -> None:
        """The distinction the type exists to draw. A connection that never opened cost
        nothing, and a cost row for it would be a fabricated charge."""
        provider, _ = _provider(raises=RuntimeError("connection refused"))

        with pytest.raises(ExternalServiceError):
            await _call(provider)


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


# -- The refusals only the operator can clear ----------------------------------------------


_NO_CREDIT: Final = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. Please go to "
    "Plans & Billing to upgrade or purchase credits.'}}"
)


def _rejection(name: str, message: str, *, status: int | None = None) -> Exception:
    attrs = {"status_code": status} if status is not None else {}
    return type(name, (Exception,), attrs)(message)


class TestARefusalTheOperatorMustClear:
    """An unfunded or misconfigured account is not a service that failed.

    This class exists because of a real run. The drafting step died on
    ``BadRequestError: 400 ... credit balance is too low``, the console reported
    ``external_service_error`` and offered to continue, and continuing bought the same
    refusal — which is the only outcome continuing can buy, for as long as the balance is
    what it is. The failure was legible to the vendor and illegible here.
    """

    async def test_an_empty_balance_says_what_to_top_up_rather_than_quoting_the_sdk(
        self,
    ) -> None:
        provider, _ = _provider(raises=_rejection("BadRequestError", _NO_CREDIT, status=400))

        with pytest.raises(ExternalServiceError) as caught:
            await provider.count_tokens(
                system="s", messages=[Message(role="user", content="q")], model=OPUS
            )

        assert "out of credit" in caught.value.message
        assert "AER_ANTHROPIC_API_KEY" in caught.value.message
        assert caught.value.retryable is False

    async def test_an_empty_balance_keeps_the_vendors_own_words_in_the_record(self) -> None:
        """Out of the sentence, into the detail — `aer diagnose` still prints it whole."""
        provider, _ = _provider(raises=_rejection("BadRequestError", _NO_CREDIT, status=400))

        with pytest.raises(ExternalServiceError) as caught:
            await provider.count_tokens(
                system="s", messages=[Message(role="user", content="q")], model=OPUS
            )

        assert "credit balance is too low" not in caught.value.message
        assert "credit balance is too low" in caught.value.context["provider_error"]
        assert caught.value.context["remedy"] == caught.value.context["remedy"].strip()

    async def test_the_remedy_travels_where_the_console_reads_it(self) -> None:
        """The console decides on `context.remedy` whether continuing is worth offering."""
        provider, _ = _provider(raises=_rejection("BadRequestError", _NO_CREDIT, status=400))

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.to_dict()["context"]["remedy"] in caught.value.message

    @pytest.mark.parametrize(
        ("status", "phrase"),
        [(401, "was rejected"), (403, "may not use the model")],
    )
    async def test_a_rejected_key_and_a_forbidden_model_each_name_their_own_fix(
        self, status: int, phrase: str
    ) -> None:
        provider, _ = _provider(raises=_rejection("APIStatusError", "no", status=status))

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert phrase in caught.value.message
        assert caught.value.retryable is False

    async def test_an_ordinary_bad_request_still_reads_as_the_sdk_said_it(self) -> None:
        """Only the three refusals are translated. A malformed request is our defect."""
        provider, _ = _provider(
            raises=_rejection("BadRequestError", "output_config.schema: Extra inputs", status=400)
        )

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert "BadRequestError: output_config.schema" in caught.value.message
        assert "remedy" not in caught.value.context

    async def test_a_transient_failure_is_untouched_by_any_of_this(self) -> None:
        provider, _ = _provider(raises=_rejection("APIStatusError", "later", status=503))

        with pytest.raises(ExternalServiceError) as caught:
            await _call(provider)

        assert caught.value.retryable is True
        assert "remedy" not in caught.value.context


# -- The contract with the SDK -------------------------------------------------------------


class TestTheSdkContract:
    """The stub above is only worth something if it matches the real SDK.

    These tests are the seam. They check the installed SDK for the surface the provider
    depends on, so an upgrade that moves it fails here rather than on the next live run.
    """

    def test_messages_stream_accepts_the_arguments_the_provider_sends(self) -> None:
        """``stream`` and ``parse`` are different methods, and only one of them is used.

        ``output_format`` on ``stream`` is the assertion that matters: without it the SDK
        would still stream, and the reply would come back as prose with nothing validating
        it.
        """
        parameters = set(inspect.signature(AsyncMessages.stream).parameters)
        assert {
            "output_format",
            "output_config",
            "max_tokens",
            "messages",
            "model",
            "system",
        } <= parameters

    def test_the_stream_is_entered_rather_than_awaited(self) -> None:
        """``await client.messages.stream(...)`` is the mistake this rules out.

        The method is synchronous and returns a manager; the request happens on entry. A
        stub that got this wrong would let a provider bug through, so the shape is checked
        against the SDK rather than assumed.
        """
        assert not inspect.iscoroutinefunction(AsyncMessages.stream)
        assert hasattr(AsyncMessageStreamManager, "__aenter__")
        assert inspect.iscoroutinefunction(AsyncMessageStream.get_final_message)

    def test_output_config_accepts_effort_and_format_and_no_schema(self) -> None:
        """The 400 in one assertion: there is no ``schema`` key at this level."""
        assert set(OutputConfigParam.__annotations__) == {"effort", "format"}

    def test_the_sdk_accepts_the_schema_as_a_dict(self) -> None:
        """What the metering fix rests on.

        Given a Pydantic class the SDK validates the reply *during* accumulation, at
        ``content_block_stop`` — one event before ``message_delta``, which carries the
        final output-token count. A reply that broke a bound therefore raised out of a
        stream whose usage was still a placeholder, and no cost row was ever written for
        tokens that had been billed. Given a dict the SDK sends the same schema and skips
        the parse, so the stream always completes and the usage is always whole.

        The provider reads no parsed output from the SDK at all now, which is why the
        assertion that used to live here — that ``parsed_output`` exists — has gone with it.
        """
        declared = str(inspect.signature(AsyncMessages.stream).parameters["output_format"])

        assert "JSONOutputFormatParam" in declared
        assert set(JSONOutputFormatParam.__annotations__) == {"type", "schema"}

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


# ==========================================================================================
# The batch path (task 39)
# ==========================================================================================


class _JsonBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


def _batch_message(payload: str) -> Any:
    return SimpleNamespace(
        content=[_JsonBlock(payload)],
        stop_reason="end_turn",
        usage=_StubUsage(),
        model=OPUS,
        model_dump=lambda **_: {"stop_reason": "end_turn"},
    )


def _entry(custom_id: str, *, kind: str = "succeeded", payload: str = "{}") -> Any:
    message = _batch_message(payload) if kind == "succeeded" else None
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(
            type=kind, message=message, error="boom" if kind != "succeeded" else None
        ),
    )


class _StubBatches:
    """The Batches surface the provider drives: create, poll, stream results."""

    def __init__(self, entries: list[Any], *, statuses: list[str] | None = None) -> None:
        self.created: dict[str, Any] | None = None
        self._entries = entries
        self._statuses = list(statuses or ["ended"])
        self.retrievals = 0

    async def create(self, **kwargs: Any) -> Any:
        self.created = kwargs
        return SimpleNamespace(id="batch_1")

    async def retrieve(self, batch_id: str) -> Any:
        self.retrievals += 1
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(id=batch_id, processing_status=status)

    async def results(self, batch_id: str) -> Any:
        async def stream() -> Any:
            for entry in self._entries:
                yield entry

        return stream()


def _batch_provider(
    entries: list[Any], *, statuses: list[str] | None = None, response: Any = None
) -> tuple[AnthropicProvider, _StubBatches]:
    """A provider whose Batches surface is stubbed.

    ``response`` scripts the *sync* surface as well, which a batch test needs only when it
    submits a single item — that one goes down the single-call path and never touches the
    entries above.
    """
    messages = _RecordingMessages(response)
    batches = _StubBatches(entries, statuses=statuses)
    messages.batches = batches  # type: ignore[attr-defined]
    provider = AnthropicProvider(
        api_key="sk-test",
        client=_StubClient(messages),  # type: ignore[arg-type]
        batch_poll_seconds=0.001,
        batch_deadline_seconds=5,
    )
    return provider, batches


def _batch_requests(count: int) -> list[BatchRequest]:
    return [
        BatchRequest(system="be brief", messages=(Message(role="user", content=f"plan {i}"),))
        for i in range(count)
    ]


def _two_succeeded() -> list[Any]:
    """Two answered items, for the tests that are about the batch endpoint itself.

    Every one of them used to submit a single request, which is the one input the batch
    endpoint no longer receives: a lone item now takes the single-call path and never
    reaches these stubs. Two is the smallest batch that is still a batch.
    """
    return [
        _entry("item-0", payload='{"summary": "ok", "confidence": 0.5}'),
        _entry("item-1", payload='{"summary": "ok", "confidence": 0.5}'),
    ]


class TestTheBatchPath:
    async def test_a_single_request_never_reaches_the_batch_endpoint(self) -> None:
        """Gap O2: the queue is only worth its discount when it is amortised.

        A live run spent 2,356 seconds — 39 minutes, two thirds of the run — waiting on a
        batch holding one red-team challenge, which is longer than drafting all sixteen
        sections took. The batch endpoint is therefore never created for one item; the
        result is the same validated object, off the single-call path.
        """
        provider, batches = _batch_provider(
            _two_succeeded(), response=_StubResponse(Plan(summary="ok"))
        )

        results = await provider.complete_structured_batch(
            Plan, requests=_batch_requests(1), model=OPUS, effort="high"
        )

        assert batches.created is None
        assert len(results) == 1
        assert isinstance(results[0].value, Plan)
        assert results[0].value.summary == "ok"

    async def test_results_come_back_validated_and_in_request_order(self) -> None:
        # The API finished them backwards; the caller must not notice.
        entries = [
            _entry("item-1", payload='{"summary": "second", "confidence": 0.5}'),
            _entry("item-0", payload='{"summary": "first", "confidence": 0.5}'),
        ]
        provider, _ = _batch_provider(entries)

        results = await provider.complete_structured_batch(
            Plan, requests=_batch_requests(2), model=OPUS, effort="high"
        )

        assert [r.value.summary for r in results] == ["first", "second"]
        assert all(isinstance(r.value, Plan) for r in results)

    async def test_the_wire_schema_is_the_sdk_transformation(self) -> None:
        """The 400 the sync path avoids via ``messages.parse``, avoided here explicitly.

        The API rejects ``maximum`` and requires ``additionalProperties: false``; the
        SDK's ``transform_schema`` moves the bounds into descriptions. Sending the raw
        Pydantic schema would be the batch path's version of the sync path's first
        live-call failure.
        """
        provider, batches = _batch_provider(_two_succeeded())

        await provider.complete_structured_batch(
            Plan, requests=_batch_requests(2), model=OPUS, effort="high"
        )

        assert batches.created is not None
        params = batches.created["requests"][0]["params"]
        schema = params["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert "maximum" not in schema["properties"]["confidence"]

    async def test_the_schema_goes_under_output_config_beside_the_effort(self) -> None:
        """The deprecated field, and the assignment that would drop ``effort`` with it.

        ``output_format`` at the top level is the older spelling. On ``messages.stream`` the
        SDK merges the argument of that name into ``output_config`` before sending, so the
        single-call path was right on the wire while looking identical to this one; the
        batch endpoint takes raw params and merges nothing, so the batch path sent the
        deprecated field for as long as it existed.

        It failed late, too. The Batches API validates when results are fetched, not when
        the batch is submitted, so a live red-team step ran to completion and then returned
        "item 0 did not succeed (errored)" — an hour of latency between the mistake and any
        sign of it.

        ``effort`` is asserted in the same breath because the obvious fix — assigning
        ``output_config`` outright — trades a deprecation warning for a silently cheaper
        model call, and nothing downstream would say so.
        """
        provider, batches = _batch_provider(_two_succeeded())

        await provider.complete_structured_batch(
            Plan, requests=_batch_requests(2), model=OPUS, effort="xhigh"
        )

        assert batches.created is not None
        params = batches.created["requests"][0]["params"]
        assert "output_format" not in params
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert params["output_config"]["effort"] == "xhigh"

    async def test_batch_params_carry_only_keys_the_sdk_declares(self) -> None:
        """The general form of the bug, asked of the SDK rather than enumerated by hand.

        ``batch_create_params.Request["params"]`` is ``MessageCreateParamsNonStreaming`` —
        the exact TypedDict the endpoint accepts. It has no ``output_format`` member, which
        is the whole failure in one lookup, and it will not have the next removed field
        either. Listing the permitted keys here instead would have needed updating by
        whoever already knew.
        """
        provider, batches = _batch_provider(_two_succeeded())

        await provider.complete_structured_batch(
            Plan, requests=_batch_requests(2), model=OPUS, effort="high"
        )

        assert batches.created is not None
        params = batches.created["requests"][0]["params"]
        declared = set(MessageCreateParamsNonStreaming.__annotations__)
        assert set(params) <= declared, (
            f"not accepted by the batch endpoint: {set(params) - declared}"
        )
        assert "output_format" not in declared

    async def test_an_errored_item_fails_the_whole_batch(self) -> None:
        entries = [
            _entry("item-0", payload='{"summary": "ok", "confidence": 0.5}'),
            _entry("item-1", kind="errored"),
        ]
        provider, _ = _batch_provider(entries)

        with pytest.raises(ExternalServiceError, match="item 1"):
            await provider.complete_structured_batch(
                Plan, requests=_batch_requests(2), model=OPUS, effort="high"
            )

    async def test_polling_waits_for_the_batch_to_end(self) -> None:
        provider, batches = _batch_provider(
            _two_succeeded(),
            statuses=["in_progress", "in_progress", "ended"],
        )

        results = await provider.complete_structured_batch(
            Plan, requests=_batch_requests(2), model=OPUS, effort="high"
        )

        assert len(results) == 2
        assert batches.retrievals >= 3

    async def test_a_reply_missing_the_schema_is_a_validation_error(self) -> None:
        provider, _ = _batch_provider(
            [
                _entry("item-0", payload='{"confidence": 0.5}'),
                _entry("item-1", payload='{"summary": "ok", "confidence": 0.5}'),
            ]
        )

        with pytest.raises(ValidationError):
            await provider.complete_structured_batch(
                Plan, requests=_batch_requests(2), model=OPUS, effort="high"
            )

    async def test_an_unusable_reply_carries_the_whole_batchs_bill(self) -> None:
        """Gap A36: the batch completed at the API — every item billed — then one reply
        failed validation here, and the bare error carried no usage, so no cost row was
        ever written for money that moved. The failure now travels as the same billed
        type the single-call path meters, and the bill is the *sum*: the sibling items
        were billed too, and metering only the failed item's share would hide most of it.
        """
        entries = [
            _entry("item-0", payload='{"summary": "sound", "confidence": 0.5}'),
            _entry("item-1", payload='{"confidence": 0.5}'),  # no summary: unreadable
        ]
        provider, _ = _batch_provider(entries)

        with pytest.raises(SpentButUnusableError) as caught:
            await provider.complete_structured_batch(
                Plan, requests=_batch_requests(2), model=OPUS, effort="high"
            )

        spent = caught.value
        assert "Batch item 1" in spent.message
        assert spent.usage.input_tokens == 240  # both items, not the failed one's 120
        assert spent.usage.output_tokens == 68
        assert spent.usage.cache_read_tokens == 14
        assert spent.context["item"] == 1
        assert spent.context["items_billed"] == 2
        # The archived pair is the failed exchange — the one somebody will want to read.
        assert spent.request_payload["custom_id"] == "item-1"
