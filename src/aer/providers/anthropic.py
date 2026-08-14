"""The Anthropic provider. **The only module in this codebase that imports the SDK.**

Enforced by a test that scans the source tree, not by convention. The rule costs almost
nothing today and is the difference between adding a second provider in an afternoon and
finding every call site in a codebase that has grown around one vendor's API.

**Structured output is requested, not parsed out.** The schema goes on the wire as
``output_config.format``, so the server constrains generation and the reply is a JSON
document by construction. Extracting JSON from prose with a regular expression is the
alternative, and it fails in exactly the cases that matter: a model that hedges, a model
that wraps its answer in an explanation, a model that emits a trailing comma.

``output_format`` — the top-level field, and the SDK argument of the same name — is the
older spelling. The argument is still the way to ask on ``messages.stream``, because the
SDK merges it into ``output_config`` client-side; the *field* is deprecated, and only the
batch path is close enough to the wire for the difference to matter.

**The single call is streamed; the batch path is not.** Not for progress — nothing here
reads the deltas — but because a non-streamed request holds an idle connection open for the
whole of a thinking turn, and something between this process and the API closes it long
before the client's own timeout. See :meth:`AnthropicProvider.complete_structured`.

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
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import anyio
import structlog
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from aer.errors import ConfigError, ExternalServiceError, ValidationError
from aer.providers.protocol import (
    BatchRequest,
    Message,
    SpentButUnusableError,
    StructuredResult,
    Usage,
)

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

    async def aclose(self) -> None:
        """Release the SDK client's connection pool, while the event loop still exists.

        The worker holds one provider for the life of its process, so it never needs
        this. A short-lived caller does — the live contract tests found out on Windows,
        where the proactor loop closes before the client's finaliser runs and the
        leftover sockets surface as "Event loop is closed" against a test whose
        assertions had all passed. Closing is idempotent and makes no request.
        """
        closer = getattr(self._client, "close", None)
        if callable(closer):  # the test stubs have no pool to release
            await closer()

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
        """Produce a validated instance of ``schema``.

        **Streamed, and that is not an optimisation.** The first real run of this platform
        failed here, every time, roughly three minutes in::

            httpx.RemoteProtocolError: Server disconnected without sending a response

        ``count_tokens`` against the same host succeeded in the same worker seconds later,
        so it was never DNS, TLS, the key or the network. The cause is that the models this
        platform routes to run adaptive thinking: a hard question can be reasoned about for
        minutes before the first output token exists, and a non-streamed request spends all
        of that holding a connection with no bytes on it. Something in the path — a proxy, a
        NAT table, the API's own idle limit — reaps it. A streamed request carries events
        throughout, so the connection is never idle.

        Nothing here reads the deltas. ``get_final_message`` waits for the accumulated
        message, which is the same object ``messages.parse`` would have returned.

        **The schema goes to the wire; the reply is validated here.** Handing the SDK the
        Pydantic class instead makes it validate *during* accumulation, at
        ``content_block_stop`` — one event before ``message_delta``, which is the event
        carrying the final output-token count. A reply that broke a bound therefore raised
        out of a stream whose usage was still the placeholder from ``message_start``: the
        tokens were spent, no usage figure existed, and no ``costs`` row was ever written.
        Every re-ask was money the budget cap could not see, which is a cap that does not
        work — the thing this platform exists not to have.

        Passing the wire format as a dict makes the SDK send the schema (so the server
        still constrains generation) and skip the client-side parse, so the stream always
        completes, the usage is always whole, and validation happens below where a failure
        is a value this method has already metered. It is also exactly what the batch path
        does, which is now a similarity rather than a coincidence.
        """
        request = _request_payload(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            max_tokens=max_tokens,
        )

        started = time.perf_counter()
        try:
            # `cast` because the SDK types the dict form as a TypedDict it builds
            # itself; the branch that accepts it (`is_dict(output_format)`) is public
            # behaviour, and `_wire_output_format` produces exactly that shape.
            wire_format = cast("Any", _wire_output_format(schema))
            async with self._client.messages.stream(output_format=wire_format, **request) as stream:
                response = await stream.get_final_message()
        except Exception as exc:
            message = f"The Anthropic API call failed ({type(exc).__name__}: {exc})."
            raise ExternalServiceError(
                message,
                provider=PROVIDER_NAME,
                retryable=_is_retryable(exc),
                context={"model": model},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        # Read before anything can fail. Whatever the reply turns out to be, this call was
        # made and billed, and every path out of here from now on carries the bill.
        usage = _usage_from(response, model=model)
        archived_request = _archived(request, schema)
        archived_response = _response_payload(response)

        try:
            parsed = self._parse(response, schema, model=model, max_tokens=max_tokens)
        except ValidationError as unusable:
            raise SpentButUnusableError(
                unusable.message,
                usage=usage,
                request_payload=archived_request,
                response_payload=archived_response,
                latency_ms=elapsed_ms,
                context=unusable.context,
            ) from unusable

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
            request_payload=archived_request,
            response_payload=archived_response,
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
        transformation the single-call path applies — and each reply is validated back
        against the Pydantic class here. Results are matched to requests by ``custom_id``
        and returned in request order, whatever order the API finished them in.

        **Raw params means the SDK's conveniences are ours to reproduce.** On
        ``messages.stream`` the ``output_format`` argument is a client-side one: the SDK
        merges it into ``output_config.format`` before anything is sent (there is a comment
        saying exactly that in ``anthropic.resources.messages.messages``). Nothing merges
        anything here, so writing ``output_format`` into these params put a deprecated
        field on the wire — and because the Batches API validates at result-fetch time
        rather than at submission, it came back as an errored *item* an hour later instead
        of a 400 in the first second.

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
            # `setdefault`, not assignment: `_request_payload` has already put `effort`
            # here for a model that takes one, and overwriting the key would drop it.
            output_config = request.setdefault("output_config", {})
            output_config["format"] = {"type": "json_schema", "schema": wire_schema}
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
                        **_archived(request, schema),
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
                # Built by the same function the real call uses, so a turn split at a
                # cache boundary is counted whole. Counting only `content` would omit the
                # cache prefix — which is the largest part of the turn, and the omission
                # would silently raise every role's effective input cap.
                messages=cast("Any", [_message_payload(m) for m in messages]),
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
        """Validate the reply's text against the schema, or explain why there is nothing to.

        **The provider parses; the SDK no longer does.** The schema goes to the wire as a
        dict, which is what keeps the stream from raising before the usage arrives (see
        :meth:`complete_structured`), and the cost of that is that nothing populates
        ``parsed_output`` — so the text is read and validated here. That is what the batch
        path always did, and both now go through :func:`_validated`.

        A response that does not validate is an error, never something to coerce. A
        half-parsed plan with a missing field is worse than no plan, because everything
        downstream treats it as complete.
        """
        return _validated(response, schema, model=model, max_tokens=max_tokens)


# What a cache breakpoint looks like on the wire. Five-minute ephemeral rather than the
# one-hour form: a research run makes its calls in a burst of minutes, so the shorter TTL
# covers the reuse that actually happens, and it costs a 1.25x write premium against the
# 1h form's 2x. Break-even is two requests rather than three.
_CACHE_BREAKPOINT: Final[dict[str, str]] = {"type": "ephemeral"}


def _system_blocks(system: str) -> list[dict[str, Any]]:
    """The system prompt as one cached text block.

    Marked unconditionally. The system prompt is stable for a role — the platform contract
    then the role's instruction — so every call after the first can read it rather than pay
    for it again. Where the prompt is shorter than the model's minimum cacheable prefix the
    marker is simply ignored: no entry is written, nothing is charged, and
    ``cache_creation_input_tokens`` comes back zero. That silence is why the cache-hit
    figures on the costs page matter — a marker is a request, not a guarantee.
    """
    return [{"type": "text", "text": system, "cache_control": _CACHE_BREAKPOINT}]


def _message_payload(message: Message) -> dict[str, Any]:
    """One turn, split at its cache boundary when it has one."""
    if message.cache_prefix is None:
        return {"role": _role(message.role), "content": message.content}
    return {
        "role": _role(message.role),
        "content": [
            {"type": "text", "text": message.cache_prefix, "cache_control": _CACHE_BREAKPOINT},
            {"type": "text", "text": message.content},
        ],
    }


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

    * **The schema.** It goes to the SDK as ``output_format`` so the SDK owns
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
        "system": _system_blocks(system),
        "messages": [_message_payload(m) for m in messages],
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


def _wire_output_format(schema: type[BaseModel]) -> dict[str, Any]:
    """The schema as the SDK's ``output_format`` **dict** form.

    A dict rather than the Pydantic class, and the difference is the whole of the metering
    fix: given a class the SDK validates the reply during accumulation and raises before
    the usage event arrives; given a dict it sends the same schema and leaves the parsing
    to us. ``is_dict(output_format)`` is the branch in the SDK that decides this.
    """
    return {"type": "json_schema", "schema": _wire_schema(schema)}


def _wire_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The schema as the batch endpoint accepts it.

    The SDK's own transformation — the one ``output_format`` applies before sending —
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
    """Validate one batch reply, naming the item when it fails.

    The same check the single-shot path makes, because it is the same question about the
    same kind of reply — the index is the only thing a batch adds, and it belongs in the
    message rather than in a second implementation.
    """
    try:
        return _validated(response, schema, model=model, max_tokens=max_tokens)
    except ValidationError as unusable:
        raise ValidationError(
            f"Batch item {index}: {unusable.message}",
            context={**unusable.context, "item": index},
        ) from unusable


def _validated[T: BaseModel](response: Any, schema: type[T], *, model: str, max_tokens: int) -> T:
    """The reply's text as an instance of ``schema``.

    The stop reason is read first, because a refusal and a truncation both leave the
    content empty and only one of them has a fix. The API enforces a structured reply's
    *shape* but not its bounds — those reach the model as description text — so a
    structurally perfect reply can still fail here, and :func:`_unreadable_reply` is what
    keeps the two diagnoses apart.
    """
    stop_reason = str(getattr(response, "stop_reason", ""))
    explanation = _UNUSABLE_STOP_REASONS.get(stop_reason)
    if explanation is not None:
        message = f"{model} produced no {schema.__name__}: " + explanation.format(
            max_tokens=max_tokens
        )
        raise ValidationError(
            message,
            context={"model": model, "schema": schema.__name__, "stop_reason": stop_reason},
        )

    text = _text_of(response)
    if not text.strip():
        message = (
            f"{model} returned no text to read as {schema.__name__}. It stopped with "
            f"{stop_reason!r} and the response carried {_content_kinds(response)}."
        )
        raise ValidationError(
            message,
            context={"model": model, "schema": schema.__name__, "stop_reason": stop_reason},
        )

    try:
        return schema.model_validate_json(text)
    except PydanticValidationError as exc:
        raise _unreadable_reply(exc, schema=schema, model=model, max_tokens=max_tokens) from exc


def _text_of(response: Any) -> str:
    """Every text block concatenated. Thinking blocks carry no answer and are skipped."""
    return "".join(
        str(getattr(block, "text", ""))
        for block in getattr(response, "content", []) or []
        if getattr(block, "type", "") == "text"
    )


def _unreadable_reply(
    exc: PydanticValidationError, *, schema: type[BaseModel], model: str, max_tokens: int
) -> ValidationError:
    """The SDK could not validate the reply, raised while it accumulated the stream.

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
    is arbitrary text of arbitrary length. Pydantic's ``msg`` is kept, because it is
    generated from the constraint rather than from the input — "String should have at most
    600 characters" is the whole diagnosis, and a summary of ``loc`` and ``type`` alone
    ("report.findings.2", "value_error") says only that something, somewhere, was wrong.

    Callers feed these back to the model as well as to the log; see
    :func:`aer.agents.worker._schema_problems`.
    """
    return [
        {
            "loc": ".".join(str(part) for part in error["loc"]),
            "type": error["type"],
            "msg": str(error.get("msg", "")),
        }
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

    The schema is put back, because the payload sent to the SDK deliberately
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
