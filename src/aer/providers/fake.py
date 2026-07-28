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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Final

from pydantic import BaseModel

from aer.errors import ExternalServiceError
from aer.providers.protocol import Message, StructuredResult, Usage

__all__ = ["FakeProvider", "ScriptedResponse"]

PROVIDER_NAME: Final = "fake"

# Roughly four characters per token, which is close enough for English prose that the
# budget arithmetic under test behaves like the real thing.
_CHARS_PER_TOKEN: Final = 4

# What a fake response "costs" in output tokens when nothing says otherwise.
_DEFAULT_OUTPUT_TOKENS: Final = 250


class ScriptedResponse:
    """One canned answer, and what it claims to have cost."""

    __slots__ = ("output_tokens", "value")

    def __init__(self, value: BaseModel, *, output_tokens: int = _DEFAULT_OUTPUT_TOKENS) -> None:
        self.value = value
        self.output_tokens = output_tokens


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
        | Callable[[type[BaseModel]], BaseModel]
        | None = None,
        *,
        fail_with: Exception | None = None,
    ) -> None:
        self._responses = responses or {}
        self._fail_with = fail_with
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
        input_tokens = _token_estimate(system, messages)

        return StructuredResult(
            value=scripted.value,  # type: ignore[arg-type]
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
            response_payload={"parsed": scripted.value.model_dump(mode="json")},
        )

    async def count_tokens(self, *, system: str, messages: Sequence[Message], model: str) -> int:
        self.token_counts.append({"model": model, "messages": len(messages)})
        return _token_estimate(system, messages)

    def _lookup(self, schema: type[BaseModel]) -> ScriptedResponse:
        if callable(self._responses):
            return ScriptedResponse(self._responses(schema))

        found = self._responses.get(schema.__name__)
        if found is None:
            message = (
                f"FakeProvider has no scripted response for {schema.__name__}. Scripted: "
                f"{sorted(self._responses) or 'nothing'}. A missing script is a test that "
                "would otherwise pass against a response nobody chose."
            )
            raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False)

        return found if isinstance(found, ScriptedResponse) else ScriptedResponse(found)


def _token_estimate(system: str, messages: Sequence[Message]) -> int:
    """Tokens a prompt would consume, near enough for the budget arithmetic under test."""
    characters = len(system) + sum(len(m.content) for m in messages)
    return max(1, characters // _CHARS_PER_TOKEN)
