"""The Anthropic provider. **The only module in this codebase that imports the SDK.**

Enforced by a test that scans the source tree, not by convention. The rule costs almost
nothing today and is the difference between adding a second provider in an afternoon and
finding every call site in a codebase that has grown around one vendor's API.

**Structured output is requested, not parsed out.** ``messages.parse`` takes the Pydantic
class, translates it into the wire schema, and returns an instance validated against it.
Extracting JSON from prose with a regular expression is the alternative, and it fails in
exactly the cases that matter: a model that hedges, a model that wraps its answer in an
explanation, a model that emits a trailing comma.

**The SDK owns the wire format, deliberately.** Every request-shape decision below is a
property of the vendor's API rather than a preference, and each is stated where it is
enforced. The first live call this module ever made returned a 400, because the shape had
only ever been exercised against a fake provider — so the shape is now asserted against
the documented contract by :class:`tests.test_anthropic_provider`.

**Both payloads are returned for archiving.** The agent layer stores the request and the
response as artefacts. A model call whose exact input nobody kept is a call nobody can
reproduce, and "why did it say that?" is the first question asked about any output that
looks wrong.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final, Literal

import anyio
import structlog
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from aer.errors import ConfigError, ExternalServiceError, ValidationError
from aer.providers.protocol import BatchRequest, Message, StructuredResult, Usage

if TYPE_CHECKING:  # pragma: no cover -- import-time only for type checking
    from anthropic import AsyncAnthropic

__all__ = ["AnthropicProvider"]

_log = structlog.get_logger("aer.providers.anthropic")

PROVIDER_NAME: Final = "anthropic"

# The effort ladder, in order. Uniform vocabulary across providers so the routing table
# stays readable; what each level means is necessarily provider-specific. Ordered rather
# than a set so the error message for a typo reads as a ladder.
_EFFORT_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")

# Models that accept ``output_config.effort``. The parameter arrived with the 4.6
# generation and is a 400 on anything earlier, which includes ``claude-haiku-4-5`` — the
# model the triage roles route to. An unlisted model simply runs at the API's own default
# effort, so a stale entry here costs quality rather than the whole run.
#
# ``client.models.retrieve(id).capabilities["effort"]`` is the live authority. This is the
# offline copy: a capability lookup before every call would double the request count and
# turn a slow Models endpoint into a stalled run.
#
# ``output_config.format`` needs no such table — structured output is accepted by every
# current model.
_MODELS_ACCEPTING_EFFORT: Final[frozenset[str]] = frozenset(
    {
        "claude-fable-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
    }
)

# Stop reasons that mean there cannot be an answer in the response, and what to do. Read
# before the content, because both leave the content empty and the difference between them
# is the difference between a configuration fix and no fix at all.
_UNUSABLE_STOP_REASONS: Final[dict[str, str]] = {
    "max_tokens": (
        "it ran out of room at the {max_tokens:,}-token ceiling. On current models "
        "max_tokens bounds thinking and visible output *together*, so a limit that is "
        "generous for the answer alone can be spent entirely on reasoning before a word "
        "of it is written. Raise the agent's max_output_tokens."
    ),
    "refusal": (
        "it declined to answer. Anthropic's safety classifiers set this stop reason and no "
        "content follows it, so there is nothing to parse and retrying will not help."
    ),
}

_DEFAULT_TIMEOUT_SECONDS: Final = 600.0

# How the batch path waits. The Batches API is asynchronous by design — results within an
# hour is normal, 24h is the contract — so polling starts patient and backs off to a
# ceiling rather than hammering. The deadline exists because a batch the API has lost
# must eventually become an error here rather than a worker that never returns.
_BATCH_POLL_INITIAL_SECONDS: Final = 2.0
_BATCH_POLL_CEILING_SECONDS: Final = 60.0
_BATCH_DEADLINE_SECONDS: Final = 24 * 60 * 60.0

# How to turn a response object into a dictionary, in order of preference. ``warnings=False``
# is not cosmetic: the SDK's ``ParsedMessage`` annotates ``content`` with the unparameterised
# block union, so serialising a perfectly good parsed response emits a dozen Pydantic
# "unexpected value" warnings. The suite runs with ``filterwarnings = ["error"]``, so
# archiving a real response would raise; in the worker it would fill the log with noise
# about nothing.
_DUMP_ATTEMPTS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("model_dump", {"mode": "json", "warnings": False}),
    ("model_dump", {}),
    ("to_dict", {}),
    ("dict", {}),
)


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
        batch_poll_seconds: float = _BATCH_POLL_INITIAL_SECONDS,
        batch_deadline_seconds: float = _BATCH_DEADLINE_SECONDS,
    ) -> None:
        if not api_key.strip():
            message = (
                "AER_ANTHROPIC_API_KEY is not set. Nothing that needs a model can run "
                "without it; get one at https://platform.claude.com/settings/keys."
            )
            raise ExternalServiceError(message, provider=PROVIDER_NAME, retryable=False)

        self._client = client if client is not None else _build_client(api_key, timeout_seconds)
        self._batch_poll_seconds = batch_poll_seconds
        self._batch_deadline_seconds = batch_deadline_seconds

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
        request = _request_payload(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            max_tokens=max_tokens,
        )

        started = time.perf_counter()
        try:
            # `output_format` rather than a schema in the payload: the SDK translates the
            # Pydantic class into the wire format and validates the reply against it.
            response = await self._client.messages.parse(output_format=schema, **request)
        except PydanticValidationError as exc:
            raise _unreadable_reply(exc, schema=schema, model=model, max_tokens=max_tokens) from exc
        except Exception as exc:
            message = f"The Anthropic API call failed ({type(exc).__name__}: {exc})."
            raise ExternalServiceError(
                message,
                provider=PROVIDER_NAME,
                retryable=_is_retryable(exc),
                context={"model": model},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        parsed = self._parse(response, schema, model=model, max_tokens=max_tokens)
        usage = _usage_from(response, model=model)

        _log.info(
            "provider.completed",
            provider=PROVIDER_NAME,
            model=model,
            effort=effort,
            # What the API was told, which is nothing when the model has no effort
            # parameter. Logging only the requested level would misattribute a triage call
            # that ran at the default.
            effort_applied=request.get("output_config", {}).get("effort"),
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
            request_payload=_archived(request, schema),
            response_payload=_response_payload(response),
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
        """One validated instance per request, via the Messages Batches API.

        The batch endpoint takes raw message params, so the schema is translated to the
        wire format with the SDK's own :func:`anthropic.transform_schema` — the same
        transformation ``messages.parse`` applies — and each reply is validated back
        against the Pydantic class here. Results are matched to requests by ``custom_id``
        and returned in request order, whatever order the API finished them in.

        All or nothing: an errored or expired item fails the whole call, because a
        partial list would silently shift every later result onto the wrong request.
        """
        if not requests:
            return []

        wire_schema = _wire_schema(schema)
        params: list[dict[str, Any]] = []
        for item in requests:
            request = _request_payload(
                system=item.system,
                messages=item.messages,
                model=model,
                effort=effort,
                max_tokens=max_tokens,
            )
            request["output_format"] = {"type": "json_schema", "schema": wire_schema}
            params.append(request)

        started = time.perf_counter()
        try:
            entries: list[Any] = [
                {"custom_id": _custom_id(index), "params": request}
                for index, request in enumerate(params)
            ]
            batch = await self._client.messages.batches.create(requests=entries)
            ended = await self._await_batch(str(batch.id))
            decoder = await self._client.messages.batches.results(str(ended.id))
            by_id = {str(entry.custom_id): entry async for entry in decoder}
        except ExternalServiceError:
            raise
        except Exception as exc:
            message = f"The Anthropic batch call failed ({type(exc).__name__}: {exc})."
            raise ExternalServiceError(
                message,
                provider=PROVIDER_NAME,
                retryable=_is_retryable(exc),
                context={"model": model, "items": len(requests)},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        results: list[StructuredResult[T]] = []
        for index, request in enumerate(params):
            entry = by_id.get(_custom_id(index))
            response = _succeeded_message(entry, index=index, batch_id=str(batch.id))
            value = _validate_batch_reply(
                response, schema, model=model, max_tokens=max_tokens, index=index
            )
            results.append(
                StructuredResult(
                    value=value,
                    usage=_usage_from(response, model=model),
                    # The wall-clock of the whole batch, on every item: an item has no
                    # latency of its own that the API reports, and inventing one by
                    # division would be a made-up figure in an audit record.
                    latency_ms=elapsed_ms,
                    request_payload={
                        "batch_id": str(batch.id),
                        "custom_id": _custom_id(index),
                        **_archived(
                            {k: v for k, v in request.items() if k != "output_format"}, schema
                        ),
                    },
                    response_payload=_response_payload(response),
                )
            )

        _log.info(
            "provider.batch_completed",
            provider=PROVIDER_NAME,
            model=model,
            items=len(results),
            latency_ms=round(elapsed_ms, 2),
        )
        return results

    async def _await_batch(self, batch_id: str) -> Any:
        """Poll until the batch ends, backing off to a ceiling, bounded by a deadline."""
        delay = self._batch_poll_seconds
        deadline = time.monotonic() + self._batch_deadline_seconds
        while True:
            batch = await self._client.messages.batches.retrieve(batch_id)
            status = str(getattr(batch, "processing_status", ""))
            if status == "ended":
                return batch
            if status in {"canceling", "canceled"}:
                message = f"Batch {batch_id} was {status} before it produced results."
                raise ExternalServiceError(
                    message, provider=PROVIDER_NAME, retryable=False, context={"batch": batch_id}
                )
            if time.monotonic() >= deadline:
                message = (
                    f"Batch {batch_id} did not end within "
                    f"{self._batch_deadline_seconds:.0f}s (status {status!r})."
                )
                raise ExternalServiceError(
                    message, provider=PROVIDER_NAME, retryable=True, context={"batch": batch_id}
                )
            await anyio.sleep(delay)
            delay = min(delay * 2, _BATCH_POLL_CEILING_SECONDS)

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

    def _parse[T: BaseModel](
        self, response: Any, schema: type[T], *, model: str, max_tokens: int
    ) -> T:
        """Take the validated object out of the response, or explain why there is not one.

        A response that does not validate is an error, never something to coerce. A
        half-parsed plan with a missing field is worse than no plan, because everything
        downstream treats it as complete.
        """
        raw = _structured_output(response)
        if isinstance(raw, schema):
            return raw

        if raw is None:
            raise ValidationError(
                _nothing_to_parse(response, schema=schema, model=model, max_tokens=max_tokens),
                context={
                    "model": model,
                    "schema": schema.__name__,
                    "stop_reason": getattr(response, "stop_reason", None),
                },
            )

        # An SDK that handed back a mapping rather than an instance. Validated here rather
        # than trusted, for the same reason the whole method exists: a partially valid
        # structured output is worse than none, because everything downstream treats it as
        # complete.
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
                    "errors": _error_summary(exc),
                },
            ) from exc


def _request_payload(
    *,
    system: str,
    messages: Sequence[Message],
    model: str,
    effort: str,
    max_tokens: int,
) -> dict[str, Any]:
    """The keyword arguments for one structured call.

    Three things are absent on purpose, each because the vendor's API says so:

    * **The schema.** It goes to ``messages.parse`` as ``output_format`` so the SDK owns
      the translation. That is not a formality — the API's JSON-schema mode rejects the
      numerical and string constraints Pydantic emits for ``ge``, ``le`` and ``max_length``,
      and requires ``additionalProperties: false`` on every object. Sending
      ``schema.model_json_schema()`` straight through is a 400 waiting for the first field
      with a bound on it.
    * **``thinking``.** Omitting it runs adaptive thinking on the models this platform
      routes to, which is what is wanted. The manual form,
      ``{"type": "enabled", "budget_tokens": n}``, was removed from the 4.7 generation
      onward and returns a 400 of its own.
    * **``output_config.effort``** for models that have no such parameter — see
      :func:`_effort_for`.
    """
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": _role(m.role), "content": m.content} for m in messages],
    }

    applied = _effort_for(model, effort)
    if applied is not None:
        payload["output_config"] = {"effort": applied}

    return payload


def _effort_for(model: str, effort: str) -> str | None:
    """The effort level to send, or ``None`` when this model has no effort parameter.

    Raises:
        ConfigError: The route names a level that does not exist. Sending it would cost a
            round trip and come back as an opaque 400; substituting a default silently is
            how a run costs thirty times what was intended.
    """
    if effort not in _EFFORT_LEVELS:
        message = (
            f"{effort!r} is not an effort level. The ladder is {', '.join(_EFFORT_LEVELS)}; "
            "check AER_MODEL_ROUTES."
        )
        raise ConfigError(message, context={"model": model, "effort": effort})

    return effort if model in _MODELS_ACCEPTING_EFFORT else None


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


def _custom_id(index: int) -> str:
    return f"item-{index}"


def _wire_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The schema as the batch endpoint accepts it.

    The SDK's own transformation — the one ``messages.parse`` applies before sending —
    moves the constraints the API rejects (``ge``, ``le``, ``max_length``) into
    descriptions and stamps ``additionalProperties: false`` on every object. Re-deriving
    that here by hand would drift from the SDK the first time the API's schema dialect
    moved; importing it keeps one translation with one owner.
    """
    from anthropic import transform_schema  # noqa: PLC0415 -- see `_build_client`

    result: dict[str, Any] = transform_schema(schema)
    return result


def _succeeded_message(entry: Any, *, index: int, batch_id: str) -> Any:
    """The message inside a succeeded batch entry, or the reason there is not one.

    All or nothing, stated per item: "item 3 errored" beats "the batch was incomplete",
    because the fix starts from knowing which composition failed.
    """
    if entry is None:
        message = f"Batch {batch_id} returned no result for item {index}."
        raise ExternalServiceError(
            message, provider=PROVIDER_NAME, retryable=True, context={"batch": batch_id}
        )
    result = getattr(entry, "result", None)
    kind = str(getattr(result, "type", "missing"))
    reply = getattr(result, "message", None)
    if kind != "succeeded" or reply is None:
        detail = getattr(result, "error", None)
        message = f"Batch {batch_id} item {index} did not succeed ({kind}): {detail}."
        raise ExternalServiceError(
            message,
            provider=PROVIDER_NAME,
            # An expired item is retryable — resubmitting may succeed. An errored one
            # failed on the request's own terms and will fail the same way again.
            retryable=kind == "expired",
            context={"batch": batch_id, "item": index, "kind": kind},
        )
    return reply


def _validate_batch_reply[T: BaseModel](
    response: Any, schema: type[T], *, model: str, max_tokens: int, index: int
) -> T:
    """Validate one batch reply against the Pydantic class.

    The batch endpoint enforces the wire schema's *shape* server-side but, exactly as with
    the sync path, not the bounds — so the reply is validated here against the full class,
    and a constraint miss reads as what it is rather than as truncation.
    """
    stop_reason = str(getattr(response, "stop_reason", ""))
    explanation = _UNUSABLE_STOP_REASONS.get(stop_reason)
    if explanation is not None:
        message = f"Batch item {index} produced no {schema.__name__}: " + explanation.format(
            max_tokens=max_tokens
        )
        raise ValidationError(
            message,
            context={"model": model, "schema": schema.__name__, "stop_reason": stop_reason},
        )

    text = "".join(
        str(getattr(block, "text", ""))
        for block in getattr(response, "content", []) or []
        if getattr(block, "type", "") == "text"
    )
    try:
        return schema.model_validate_json(text)
    except PydanticValidationError as exc:
        raise _unreadable_reply(exc, schema=schema, model=model, max_tokens=max_tokens) from exc


def _structured_output(response: Any) -> Any:
    """Pull the parsed object out of a response, whichever shape it arrived in.

    ``messages.parse`` puts it on the text block as ``parsed_output`` and exposes a
    message-level property over the top. Both are checked rather than one assumed: the
    property is a convenience that a future SDK could rename, while the field is the thing
    the parser actually writes.
    """
    parsed = getattr(response, "parsed_output", None)
    if parsed is not None:
        return parsed

    for block in getattr(response, "content", []) or []:
        candidate = getattr(block, "parsed_output", None)
        if candidate is not None:
            return candidate
    return None


def _nothing_to_parse(
    response: Any, *, schema: type[BaseModel], model: str, max_tokens: int
) -> str:
    """Why a response carried no structured output.

    The stop reason is read first because the two ways this happens look identical from the
    content — both leave it empty — and only one of them has a fix.
    """
    stop_reason = getattr(response, "stop_reason", None)
    explanation = _UNUSABLE_STOP_REASONS.get(str(stop_reason))
    if explanation is not None:
        return f"{model} produced no {schema.__name__}: " + explanation.format(
            max_tokens=max_tokens
        )

    return (
        f"{model} returned no structured output. It stopped with {stop_reason!r} and the "
        f"response carried {_content_kinds(response)} rather than a parsed {schema.__name__}."
    )


def _unreadable_reply(
    exc: PydanticValidationError, *, schema: type[BaseModel], model: str, max_tokens: int
) -> ValidationError:
    """The SDK could not validate the reply, raised from inside ``messages.parse``.

    There is no response object to read a stop reason from, so the cause is inferred from
    the errors instead. Two things cause this, they need different fixes, and Pydantic
    distinguishes them cleanly:

    * ``json_invalid`` at the root — the JSON stopped mid-object, which at this layer means
      the ``max_tokens`` ceiling was reached.
    * anything else — a field constraint. The API enforces the *shape* of a structured reply
      but not the bounds: the SDK moves ``ge``, ``le``, ``min_length`` and ``max_length``
      into the schema's descriptions, where they are guidance to the model rather than a
      rule the server applies. So a structurally perfect reply can still fail here, and
      saying "it was truncated" would send someone to fix the wrong thing.
    """
    truncated = any(error["type"] == "json_invalid" for error in exc.errors())
    if truncated:
        diagnosis = (
            f"the JSON stopped mid-object, which means the {max_tokens:,}-token ceiling was "
            "reached. It bounds thinking and output together — raise the agent's "
            "max_output_tokens."
        )
    else:
        diagnosis = (
            f"{exc.error_count()} field(s) broke a constraint the API does not enforce. "
            "Bounds like ge, le and max_length reach the model as description text rather "
            "than as a server-side rule, so a structurally valid reply can still miss them. "
            "Either loosen the constraint or say it in the prompt."
        )

    return ValidationError(
        f"{model}'s reply could not be read as {schema.__name__}: {diagnosis}",
        context={
            "model": model,
            "schema": schema.__name__,
            "max_tokens": max_tokens,
            "errors": _error_summary(exc),
        },
    )


def _error_summary(exc: PydanticValidationError) -> list[dict[str, str]]:
    """The first few schema failures, without the offending values.

    Values are left out deliberately: this goes into a log line, and a truncated model reply
    is arbitrary text of arbitrary length.
    """
    return [
        {"loc": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()[:5]
    ]


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
    """The response as a plain dictionary, for archiving. See :data:`_DUMP_ATTEMPTS`."""
    for method, kwargs in _DUMP_ATTEMPTS:
        dumper = getattr(response, method, None)
        if not callable(dumper):
            continue
        try:
            dumped = dumper(**kwargs)
        except (TypeError, ValueError):  # pragma: no cover -- SDK shape varies
            continue
        if isinstance(dumped, dict):
            return dumped
    return {"repr": repr(response)}


def _archived(request: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    """The request as stored. The API key is never in it — the SDK holds that.

    The schema is put back, because the payload sent to ``messages.parse`` deliberately
    leaves it out and an archived call that does not say what shape it asked for cannot be
    reproduced. It is stored as JSON Schema rather than as the class, since
    ``<class 'aer.agents.planner.ResearchPlanDraft'>`` in an artefact reproduces nothing.

    What is stored is therefore the schema **as requested**, not byte-for-byte as
    transmitted: the SDK strips the constraints the API does not accept before sending. That
    is the right way round for an audit record — the requested schema is what the platform
    asked for and what the reply was checked against on the way back in.
    """
    return {
        **request,
        "output_config": {
            **request.get("output_config", {}),
            "format": {"type": "json_schema", "schema": schema.model_json_schema()},
        },
    }


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
