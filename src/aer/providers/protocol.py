"""What every model provider must be able to do.

Deliberately narrow: three operations and two value objects. A wide interface would encode
one vendor's feature set as though it were the shape of the problem, and the next provider
would either have to fake the parts it lacks or the abstraction would leak. The batch
operation (task 39) is the one addition the fan-out judgements earn — many items, one
schema, one route — and it returns exactly what the single-shot form returns, per item,
so nothing downstream cares which path a result travelled.

**Structured output, not free text.** :meth:`LLMProvider.complete_structured` takes a
Pydantic model and returns an instance of it. Every model call in this platform produces a
typed object that a schema validated — a plan, a triage decision, a drafted section — never
a string that something downstream has to parse hopefully. Parsing prose is how a system
ends up with a rating field containing "probably a buy, though it depends".

**Token counting is a separate operation, and it is not optional.** The cost estimate shown
at the approval gate is the difference between a person agreeing to spend money and a
person agreeing to something vague. Guessing from character counts is wrong by enough to
matter, so the protocol requires the provider to count properly — for Anthropic that is a
real API call, and it is worth it.

**Usage is returned, never inferred.** Cache reads and writes are priced differently from
ordinary input tokens, and a meter that assumed they were the same would misreport a
cached run by an order of magnitude in the direction that flatters it.

**A call that fails after the money is gone says so, with the bill.** A reply the schema
refuses is still a reply the provider was paid for, and an error that carried nothing but
a message was an error the meter could not see —
:class:`SpentButUnusableError` carries the usage and both payloads so the caller can record
what it cost before dealing with what went wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from aer.errors import ValidationError

__all__ = [
    "BatchRequest",
    "LLMProvider",
    "Message",
    "SpentButUnusableError",
    "StructuredResult",
    "Usage",
]


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of a conversation.

    ``role`` is ``user`` or ``assistant``. The system prompt is a separate argument rather
    than a message, because that is what it is in every provider's API and pretending
    otherwise means each implementation has to pull it back out of the list.
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    """What one call consumed.

    Cache reads and writes are counted separately because they are **priced** separately —
    typically a tenth of the input rate for a read and a quarter more for a write. Folding
    them into ``input_tokens`` would misreport a heavily cached run by roughly an order of
    magnitude, in the direction that makes the platform look cheaper than it is.
    """

    input_tokens: int
    output_tokens: int
    model: str
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "model": self.model,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True, slots=True)
class BatchRequest:
    """One item of a batch: its own prompt, sharing the batch's schema, model and effort.

    Deliberately just the per-item parts. A batch in which every item could name its own
    model and schema would be a loop wearing a batch's name — the point of the batch path
    is many instances of *one* judgement (validator assists, red-team challenges), and
    the shared parameters travel once, on the call.
    """

    system: str
    messages: tuple[Message, ...]


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    """A validated object, what it cost, and the raw exchange.

    ``request_payload`` and ``response_payload`` are carried so the agent layer can archive
    both as artefacts. A model call whose exact input nobody kept is a model call nobody
    can reproduce, and "why did it say that?" is the most common question asked about one.
    """

    value: T
    usage: Usage
    latency_ms: float
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


class SpentButUnusableError(ValidationError):
    """The call completed and was billed; what came back does not satisfy the schema.

    **The distinction is the meter's, not the caller's.** Every other failure in this
    protocol happens before the money moves — a refused key, a request the API rejects, a
    connection that never opened — and costs nothing. This one happens after: the tokens
    were generated, the bill exists, and only the *usability* of the reply is in question.
    A caller that treats it like any other error writes no cost row, and a budget cap that
    cannot see the spend it is capping does not work.

    So the bill travels with the failure. :attr:`usage` is what the call really consumed,
    and the payloads are the same pair a success carries, because "why did it say that?"
    is asked far more often about the replies that failed.

    A ``ValidationError`` subclass, so callers already catching that keep working and get
    the metering as a consequence of the type rather than by remembering to.
    """

    def __init__(
        self,
        message: str,
        *,
        usage: Usage,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        latency_ms: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.usage = usage
        self.request_payload = request_payload
        self.response_payload = response_payload
        self.latency_ms = latency_ms


@runtime_checkable
class LLMProvider(Protocol):
    """The interface every model provider presents."""

    @property
    def name(self) -> str:
        """Which provider this is, e.g. ``anthropic``. Recorded on every cost row."""
        ...

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
        """Produce an instance of ``schema``.

        Args:
            schema: The Pydantic model the response must satisfy. Not a suggestion — a
                response that does not validate is an error, not something to coerce.
            model: Chosen by :mod:`aer.providers.router`, never at the call site.
            effort: How much reasoning to spend. Provider-specific in its effect and
                uniform in its vocabulary, so the routing table stays readable.

        Raises:
            ExternalServiceError: The provider failed, timed out, or refused.
            ValidationError: The response did not satisfy the schema.
        """
        ...

    async def complete_structured_batch[T: BaseModel](
        self,
        schema: type[T],
        *,
        requests: Sequence[BatchRequest],
        model: str,
        effort: str = "medium",
        max_tokens: int = 4096,
    ) -> list[StructuredResult[T]]:
        """Produce one validated instance of ``schema`` per request, in request order.

        The batch form of :meth:`complete_structured`, for the fan-out judgements —
        validator assists, red-team challenges — where many items share one schema and
        one route. Order is part of the contract: the caller matches results back to what
        it asked by position, so an implementation that returns early completions first
        has broken it.

        Raises:
            ExternalServiceError: The batch failed, expired, or any item errored. All or
                nothing — a partial batch would hand the caller results whose positions
                no longer mean anything.
            ValidationError: An item's response did not satisfy the schema.
        """
        ...

    async def count_tokens(self, *, system: str, messages: Sequence[Message], model: str) -> int:
        """Count the input tokens a call would consume, before making it.

        Used for the cost estimate at the approval gate and for the budget guard. An
        estimate derived from character counts is wrong by enough to make the gate
        misleading, so this must count properly even when that costs a round trip.
        """
        ...
