"""The Anthropic provider. **The only module in this codebase that imports the SDK.**

Enforced by a test that scans the source tree, not by convention. The rule costs almost
nothing today and is the difference between adding a second provider in an afternoon and
finding every call site in a codebase that has grown around one vendor's API.

**Structured output is requested, not parsed out.** The SDK's structured-output support
takes the Pydantic schema directly and returns a validated object. Extracting JSON from
prose with a regular expression is the alternative, and it fails in exactly the cases that
matter: a model that hedges, a model that wraps its answer in an explanation, a model that
emits a trailing comma.

**Both payloads are returned for archiving.** The agent layer stores the request and the
response as artefacts. A model call whose exact input nobody kept is a call nobody can
reproduce, and "why did it say that?" is the first question asked about any output that
looks wrong.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from aer.errors import ExternalServiceError, ValidationError
from aer.providers.protocol import Message, StructuredResult, Usage

if TYPE_CHECKING:  # pragma: no cover -- import-time only for type checking
    from anthropic import AsyncAnthropic

__all__ = ["AnthropicProvider"]

_log = structlog.get_logger("aer.providers.anthropic")

PROVIDER_NAME: Final = "anthropic"

# Effort maps onto the SDK's thinking budget. The vocabulary is uniform across providers so
# the routing table stays readable; what each level means is necessarily provider-specific.
_EFFORT_THINKING_TOKENS: Final[dict[str, int]] = {
    "low": 0,
    "medium": 2_000,
    "high": 8_000,
    "xhigh": 16_000,
    "max": 32_000,
}

_DEFAULT_TIMEOUT_SECONDS: Final = 600.0


class AnthropicProvider:
    """Talks to the Anthropic API.

    Args:
        api_key: Required. Asserted here rather than at startup, so the platform runs
            without a key until something actually needs a model.
        client: An injected SDK client, for tests that want to drive the real code path
            against a stub. Left ``None`` in production.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client: AsyncAnthropic | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key.strip():
            message = (
                "AER_ANTHROPIC_API_KEY is not set. Nothing that needs a model can run "
                "without it; get one at https://platform.claude.com/settings/keys."
            )
            raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False)

        self._client = client if client is not None else _build_client(api_key, timeout_seconds)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

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
        """Produce a validated instance of ``schema``."""
        request = self._request_payload(
            schema=schema,
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            max_tokens=max_tokens,
        )

        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**request)
        except Exception as exc:
            message = f"The Anthropic API call failed ({type(exc).__name__}: {exc})."
            raise ExternalServiceError(
                message,
                provider=PROVIDER_NAME,
                retryable=_is_retryable(exc),
                context={"model": model},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        parsed = self._parse(response, schema, model=model)
        usage = _usage_from(response, model=model)

        _log.info(
            "provider.completed",
            provider=PROVIDER_NAME,
            model=model,
            effort=effort,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            latency_ms=round(elapsed_ms, 2),
            stop_reason=usage.stop_reason,
        )

        return StructuredResult(
            value=parsed,
            usage=usage,
            latency_ms=elapsed_ms,
            request_payload=_redacted(request),
            response_payload=_response_payload(response),
        )

    async def count_tokens(self, *, system: str, messages: Sequence[Message], model: str) -> int:
        """Count the input tokens a call would consume.

        A real API call, because an estimate from character counts is wrong by enough to
        make the cost figure at the approval gate misleading — and the gate is where a
        person decides whether to spend money.
        """
        try:
            counted = await self._client.messages.count_tokens(
                model=model,
                system=system,
                # The SDK types `role` as a literal union; Message carries it as a string
                # so the protocol stays free of any one vendor's type vocabulary.
                messages=[{"role": _role(m.role), "content": m.content} for m in messages],
            )
        except Exception as exc:
            message = f"Counting tokens failed ({type(exc).__name__}: {exc})."
            raise ExternalServiceError(
                message,
                provider=PROVIDER_NAME,
                retryable=_is_retryable(exc),
                context={"model": model},
            ) from exc

        return int(counted.input_tokens)

    # -- Internals -------------------------------------------------------------------------

    def _request_payload(
        self,
        *,
        schema: type[BaseModel],
        system: str,
        messages: Sequence[Message],
        model: str,
        effort: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "output_config": {"schema": schema.model_json_schema()},
        }

        thinking_tokens = _EFFORT_THINKING_TOKENS.get(effort, 0)
        if thinking_tokens:
            payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_tokens}

        return payload

    def _parse[T: BaseModel](self, response: Any, schema: type[T], *, model: str) -> T:
        """Validate the response against the schema.

        A response that does not validate is an error, never something to coerce. A
        half-parsed plan with a missing field is worse than no plan, because everything
        downstream treats it as complete.
        """
        raw = _structured_content(response)
        if raw is None:
            message = (
                f"{model} returned no structured output. The response carried "
                f"{_content_kinds(response)} rather than the requested schema."
            )
            raise ValidationError(message, context={"model": model, "schema": schema.__name__})

        try:
            return schema.model_validate(raw)
        except PydanticValidationError as exc:
            message = (
                f"{model}'s response did not satisfy {schema.__name__}: {exc.error_count()} "
                "field(s) invalid. A partially valid structured output is not usable — "
                "everything downstream would treat it as complete."
            )
            raise ValidationError(
                message,
                context={
                    "model": model,
                    "schema": schema.__name__,
                    "errors": [
                        {"loc": ".".join(str(p) for p in e["loc"]), "type": e["type"]}
                        for e in exc.errors()[:5]
                    ],
                },
            ) from exc


def _role(value: str) -> Literal["user", "assistant"]:
    """Narrow a message role to what the SDK's types accept.

    The protocol carries `role` as a plain string so it stays free of any one vendor's
    type vocabulary. Anything that is not "assistant" is a user turn -- there are only two
    kinds, and the system prompt travels separately.
    """
    return "assistant" if value == "assistant" else "user"


def _build_client(api_key: str, timeout_seconds: float) -> AsyncAnthropic:
    """Construct the SDK client.

    Imported inside the function rather than at module scope so that importing this module
    — which the import-boundary test does — does not require the SDK to be installed.
    """
    from anthropic import AsyncAnthropic  # noqa: PLC0415 -- see the docstring

    return AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)


def _structured_content(response: Any) -> Any:
    """Pull the structured object out of a response, whichever shape it arrived in.

    The SDK exposes parsed output differently depending on version and on whether thinking
    was enabled. Checked in order rather than assumed, because the failure mode of assuming
    is an exception deep in an agent rather than a clear message here.
    """
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed

    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) in {"output", "structured_output", "json"}:
            for attribute in ("parsed", "value", "json", "input"):
                candidate = getattr(block, attribute, None)
                if candidate is not None:
                    return candidate
    return None


def _content_kinds(response: Any) -> str:
    kinds = [getattr(block, "type", "?") for block in getattr(response, "content", []) or []]
    return ", ".join(kinds) or "no content"


def _usage_from(response: Any, *, model: str) -> Usage:
    usage = getattr(response, "usage", None)
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        model=str(getattr(response, "model", model)),
        stop_reason=getattr(response, "stop_reason", None),
    )


def _response_payload(response: Any) -> dict[str, Any]:
    """The response as a plain dictionary, for archiving."""
    for method in ("model_dump", "to_dict", "dict"):
        dumper = getattr(response, method, None)
        if callable(dumper):
            try:
                dumped = dumper()
            except (TypeError, ValueError):  # pragma: no cover -- SDK shape varies
                continue
            if isinstance(dumped, dict):
                return dumped
    return {"repr": repr(response)}


def _redacted(request: dict[str, Any]) -> dict[str, Any]:
    """The request as archived. The API key is never in it — the SDK holds that."""
    return dict(request)


def _is_retryable(exc: Exception) -> bool:
    """Whether the failure is worth another attempt.

    Rate limits, timeouts and 5xx are transient. A 400 means the request was wrong and
    sending it again would be wrong again, more expensively.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        server_error = 500
        too_many_requests = 429
        return status >= server_error or status == too_many_requests
    return "timeout" in type(exc).__name__.lower() or "connection" in type(exc).__name__.lower()
