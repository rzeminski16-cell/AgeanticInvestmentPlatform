"""A provider that costs nothing and answers from a script.

**This is why the abstraction exists.** The entire workflow — planning, drafting, the
budget guard, the approval gates, the rendered report — runs end to end against this class
with zero spend and no network. A test suite that needed a real model call would be run
rarely, which is the same as not having one.

It lives in ``src`` rather than ``tests`` deliberately: it is the reference implementation
of :class:`~aer.providers.protocol.LLMProvider`, and a protocol whose only other
implementation is in the test tree is a protocol nobody has checked is implementable.

**Usage figures are plausible, not zero.** A fake that reported no tokens would make every
cost assertion vacuous and the budget guard untestable. Token counts are derived from the
actual text, so a longer prompt really does cost more and the meter is exercised properly.

**It refuses requests the API would refuse, and answers the API could not have given.**
This is gap A18, and it is exactly how the empty-report bug survived every test for weeks:
a fake that answers from a script tells you nothing about whether the real endpoint would
have accepted the request or produced that reply. Two checks close most of it here —

* the **envelope**, because an empty system prompt or a nil ``max_tokens`` is a 400 from
  the real API and a silent pass from a script; and
* the **reply**, which is serialised and validated back into the requested schema exactly
  as the real provider validates what comes off the wire. A script returning a shape the
  contract forbids is a test asserting against a response that could never have existed.

The third check needs the vendor's own schema transformation to say whether the API would
accept the *contract*, and this module may not import the SDK (see
:mod:`aer.providers`). So it is injected: pass ``inspect_schema`` and the test suite
supplies the SDK-backed check. `tests/workflow_fixtures.py` wires it in for every run.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Final, cast

from pydantic import BaseModel, ValidationError

from aer.errors import ExternalServiceError
from aer.providers.protocol import BatchRequest, Message, StructuredResult, Usage

__all__ = ["FakeProvider", "ScriptedResponse"]

PROVIDER_NAME: Final = "fake"

# Roughly four characters per token, which is close enough for English prose that the
# budget arithmetic under test behaves like the real thing.
_CHARS_PER_TOKEN: Final = 4

# The effort vocabulary, mirrored from `aer.config.Effort` rather than imported: this
# module is the reference implementation of the provider protocol, and a provider reaching
# into application configuration to validate a request would be the wrong direction of
# dependency. A test pins the two lists to each other.
_EFFORTS: Final[frozenset[str]] = frozenset({"low", "medium", "high", "xhigh", "max"})

# What a fake response "costs" in output tokens when nothing says otherwise.
_DEFAULT_OUTPUT_TOKENS: Final = 250


class ScriptedResponse:
    """One canned answer, and what it claims to have cost.

    ``unchecked`` opts out of the reply validation, and is the deliberate way to inject a
    response the API could not have produced. Exactly one kind of test needs it: the one
    asserting that a *consumer* is robust to a malformed reply rather than trusting the
    provider to have filtered it. Defence in depth is worth testing, and the only way to
    test it is to defeat the outer defence on purpose — so the opt-out is named, per
    response, and says so at the call site rather than switching off the check globally.
    """

    __slots__ = ("output_tokens", "unchecked", "value")

    def __init__(
        self,
        value: BaseModel,
        *,
        output_tokens: int = _DEFAULT_OUTPUT_TOKENS,
        unchecked: bool = False,
    ) -> None:
        self.value = value
        self.output_tokens = output_tokens
        self.unchecked = unchecked


class FakeProvider:
    """Returns scripted objects, records what it was asked, and spends nothing.

    Args:
        responses: Either a mapping from schema name to a response, or a callable taking
            the schema and returning one. The mapping form covers most tests; the callable
            form is for a workflow that calls the same schema twice and wants different
            answers.
        fail_with: If set, every call raises this instead. For testing the failure paths
            that a real provider would only exercise by breaking.
    """

    def __init__(
        self,
        responses: dict[str, ScriptedResponse | BaseModel]
        | Callable[[type[BaseModel]], BaseModel | ScriptedResponse]
        | None = None,
        *,
        fail_with: Exception | None = None,
        inspect_schema: Callable[[type[BaseModel]], None] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._fail_with = fail_with
        self._inspect_schema = inspect_schema
        self.calls: list[dict[str, Any]] = []
        self.token_counts: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def call_count(self) -> int:
        """How many completions were requested.

        The assertion the budget-guard test makes: a run that should have been stopped
        before spending anything must leave this at zero.
        """
        return len(self.calls)

    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        *,
        system: str,
        messages: Sequence[Message],
        model: str,
        effort: str = "medium",
        max_tokens: int = 4096,
    ) -> StructuredResult[T]:
        _require_a_sendable_request(
            system=system, messages=messages, model=model, effort=effort, max_tokens=max_tokens
        )
        if self._inspect_schema is not None:
            self._inspect_schema(schema)

        started = time.perf_counter()
        self.calls.append(
            {
                "schema": schema.__name__,
                "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "model": model,
                "effort": effort,
                "max_tokens": max_tokens,
            }
        )

        if self._fail_with is not None:
            raise self._fail_with

        scripted = self._lookup(schema)
        # An unchecked script may be any model at all — that is what it is for — so the
        # cast is the honest description rather than a suppression of a real problem.
        value = (
            cast("T", scripted.value) if scripted.unchecked else _validated(scripted.value, schema)
        )
        input_tokens = _token_estimate(system, messages)

        return StructuredResult(
            value=value,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=scripted.output_tokens,
                model=model,
                stop_reason="end_turn",
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload={
                "model": model,
                "effort": effort,
                "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "schema": schema.__name__,
            },
            response_payload={"parsed": value.model_dump(mode="json")},
        )

    async def complete_structured_batch[T: BaseModel](
        self,
        schema: type[T],
        *,
        requests: Sequence[BatchRequest],
        model: str,
        effort: str = "medium",
        max_tokens: int = 4096,
    ) -> list[StructuredResult[T]]:
        """The batch path: each item answered by the same script the sync path reads.

        That equivalence is the point — the parity test asserts batch and sync produce
        identical rows, and it can only mean anything if the fake answers both from one
        source. Each item is recorded as its own call with the batch flagged, so a test
        can still see which path a result travelled.
        """
        results: list[StructuredResult[T]] = []
        for request in requests:
            result = await self.complete_structured(
                schema,
                system=request.system,
                messages=request.messages,
                model=model,
                effort=effort,
                max_tokens=max_tokens,
            )
            self.calls[-1]["batch"] = True
            results.append(result)
        return results

    async def count_tokens(self, *, system: str, messages: Sequence[Message], model: str) -> int:
        self.token_counts.append({"model": model, "messages": len(messages)})
        return _token_estimate(system, messages)

    def _lookup(self, schema: type[BaseModel]) -> ScriptedResponse:
        if callable(self._responses):
            answered = self._responses(schema)
            # Passed through when the callable already built one, so a script wanting the
            # `unchecked` opt-out can reach it from the callable form too. Wrapping it a
            # second time would bury the flag inside a value nobody reads.
            return (
                answered if isinstance(answered, ScriptedResponse) else ScriptedResponse(answered)
            )

        found = self._responses.get(schema.__name__)
        if found is None:
            message = (
                f"FakeProvider has no scripted response for {schema.__name__}. Scripted: "
                f"{sorted(self._responses) or 'nothing'}. A missing script is a test that "
                "would otherwise pass against a response nobody chose."
            )
            raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False)

        return found if isinstance(found, ScriptedResponse) else ScriptedResponse(found)


def _require_a_sendable_request(
    *,
    system: str,
    messages: Sequence[Message],
    model: str,
    effort: str,
    max_tokens: int,
) -> None:
    """Refuse a request the real endpoint would refuse.

    Gap A18. None of these is hypothetical: each is a 400 from the API and a silent pass
    from a script, which means a caller that composed one would go on passing tests right
    up until the first live run — which is the shape of every expensive bug this platform
    has had.
    """
    problems: list[str] = []
    if not system.strip():
        problems.append("the system prompt is empty")
    if not messages:
        problems.append("there are no messages")
    elif any(not m.content.strip() for m in messages):
        problems.append("a message has no content")
    if not model.strip():
        problems.append("no model was named")
    if effort not in _EFFORTS:
        problems.append(f"{effort!r} is not one of {', '.join(sorted(_EFFORTS))}")
    if max_tokens < 1:
        problems.append(f"max_tokens is {max_tokens}")

    if not problems:
        return

    message = (
        f"FakeProvider refused a request the API would refuse: {'; '.join(problems)}. The "
        "fake enforces this so that a composition error fails in the suite rather than on "
        "the first live call."
    )
    raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False)


def _validated[T: BaseModel](scripted: BaseModel, schema: type[T]) -> T:
    """The scripted answer, put through the schema exactly as a real reply would be.

    **Serialised and re-validated rather than type-checked.** The wire carries JSON and the
    real provider validates that into the contract, so the fake has to do the same or it is
    a different thing wearing the same interface. It also means a script may return the
    declared class where a narrowed subclass was asked for — which the section writer does
    on every call — provided the *data* satisfies the narrower contract, which is precisely
    the question worth asking.

    Raises:
        ExternalServiceError: If it does not. A test scripting a reply the contract forbids
            is asserting against a response the API could never have produced.
    """
    try:
        return schema.model_validate(scripted.model_dump(mode="json", exclude_none=True))
    except ValidationError as invalid:
        message = (
            f"FakeProvider was scripted to answer {schema.__name__} with a "
            f"{type(scripted).__name__} the contract does not permit: {invalid}. A reply "
            "the API could not have produced makes the test that reads it meaningless."
        )
        raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False) from invalid


def _token_estimate(system: str, messages: Sequence[Message]) -> int:
    """Tokens a prompt would consume, near enough for the budget arithmetic under test.

    The cache prefix counts. It is sent as part of the turn and billed as part of the turn
    — at a discount when it is read from cache, but never at nothing — so leaving it out
    would make the fake disagree with the API about whether a call fits its role's cap.
    """
    characters = len(system) + sum(len(m.content) + len(m.cache_prefix or "") for m in messages)
    return max(1, characters // _CHARS_PER_TOKEN)
