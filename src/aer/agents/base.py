"""The base every agent is built on: route, call, archive, meter.

One class, and four things it does that no agent should have to remember.

**Route by role.** The agent names a role; the router picks the model. No agent contains a
model identifier, so changing the cost profile of a run is a configuration edit.

**Archive both payloads.** The exact request and the exact response go to the
content-addressed store before anything else happens with the result. This is the same
store the fetched filings go to, hashed the same way, for the same reason: "why did it say
that?" is unanswerable from a token count.

**Meter every call.** Usage becomes ``costs`` rows in USD and GBP with the exchange rate on
each row. Not a reporting feature — this is what the budget cap reads.

**Never widen the allowlist.** What an agent may do comes from
:mod:`aer.agents.registry`, resolved at construction, and the check that a tool is
permitted happens in Python before the tool runs. A class attribute grants nothing; text
inside a fetched document can ask for anything it likes; there is no path from what a model
emits — or what an agent's own module declares — to what the role is permitted.

**Refuse an oversized call before it is made.** The role's input cap is checked against a
real token count at the provider boundary, so a runaway composition fails for free instead
of spending its way to the budget guard.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Final

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.registry import PLATFORM_CONTRACT, RoleDefinitionError, resolve_role
from aer.agents.untrusted import CONTAINMENT_RULE, UntrustedSource, wrap_untrusted
from aer.config import Settings
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import AgentRun, Cost, JobStep, Prompt
from aer.errors import AerError
from aer.providers.costs import price_usage
from aer.providers.protocol import BatchRequest, LLMProvider, Message, StructuredResult
from aer.providers.router import Router
from aer.services.artefacts import store_artefact
from aer.storage.protocol import ArtefactStore

__all__ = ["Agent", "AgentContext", "TokenCapExceededError", "ToolNotPermittedError"]

_log = structlog.get_logger("aer.agents")

# Passes at find-or-create for a prompt row. Two is the shape of the race — look,
# lose, look again and find the winner — and the third is there so a pathological
# interleaving raises rather than spins.
_PROMPT_ATTEMPTS: Final = 3


class ToolNotPermittedError(AerError):
    """An agent tried to use a tool outside its role's allowlist.

    Always a bug or an attack, never a condition to recover from by widening the list.
    """

    code = "tool_not_permitted"
    http_status = 403


class TokenCapExceededError(AerError):
    """A composed call would exceed the role's input cap, and was refused unmade.

    The registry's cap is a statement about what a role's input *can legitimately be* —
    the planner reads a request, not a filing. Exceeding it means a caller composed
    something the role was never meant to carry, and the right failure is here, before
    any money is spent, rather than at the budget guard afterwards.
    """

    code = "token_cap_exceeded"


@dataclass(slots=True)
class AgentContext:
    """Everything an agent needs that is not its input.

    Passed rather than reached for. An agent that constructed its own provider or read
    global settings would be an agent no test could run cheaply, and the whole workflow
    test suite depends on being able to substitute a fake provider.
    """

    session: AsyncSession
    provider: LLMProvider
    router: Router
    settings: Settings
    store: ArtefactStore
    job_step: JobStep

    # Accumulates across the calls an agent makes, so a caller can attribute a step's spend
    # without querying.
    spend_gbp: Decimal = field(default_factory=lambda: Decimal(0))


class Agent[InputT, OutputT: BaseModel]:
    """Base for every agent.

    Subclasses declare two class attributes and implement two methods:

    * ``role`` — the name resolved against :mod:`aer.agents.registry` for capability and
      against the router for a model. Constructing an agent whose role is unregistered
      raises; a role's tools and token caps come from its :class:`RoleDefinition` and from
      nowhere else, so nothing a subclass declares can widen anything.
    * ``output_schema`` — the Pydantic model the response must satisfy. Declared on the
      class because the type parameter needs a value at hand, and verified at construction
      to be the schema the registry registers for the role — the two naming different
      contracts is a fork in the single source of capability.

    * :meth:`system_prompt` — the instruction, which becomes a versioned ``prompts`` row.
    * :meth:`user_message` — the request, built from typed input rather than interpolated
      free text.
    """

    role: ClassVar[str]
    output_schema: ClassVar[type[BaseModel]]
    prompt_version: ClassVar[str] = "1"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass that declares capability of its own.

        A class attribute named ``allowed_tools`` would shadow the base property and win
        attribute lookup — the exact quiet-widening path the registry exists to close. So
        the names are unrepresentable on a subclass, not merely ignored.
        """
        super().__init_subclass__(**kwargs)
        claimed = {"allowed_tools", "max_output_tokens", "max_input_tokens"} & set(cls.__dict__)
        if claimed:
            message = (
                f"{cls.__name__} declares {sorted(claimed)}. Tool allowlists and token "
                "caps come from the role's registry definition and from nowhere else; a "
                "class declaration would shadow the registry and is refused outright."
            )
            raise RoleDefinitionError(message, context={"agent": cls.__name__})

    def __init__(self) -> None:
        self.definition = resolve_role(type(self).role)

        registered = self.definition.output_schema()
        if type(self).output_schema is not registered:
            message = (
                f"The {self.role} agent declares the output contract "
                f"{type(self).output_schema.__qualname__!r}, but the registry registers "
                f"{registered.__qualname__!r} for that role. One of them changed without "
                "the other; the registry is the source of record."
            )
            raise RoleDefinitionError(
                message,
                context={
                    "role": self.role,
                    "declared": type(self).output_schema.__qualname__,
                    "registered": registered.__qualname__,
                },
            )

    @property
    def allowed_tools(self) -> frozenset[str]:
        """The role's allowlist, from the registry. Read-only, and there is no setter."""
        return self.definition.allowed_tools

    def system_prompt(self, payload: InputT) -> str:
        raise NotImplementedError

    def user_message(self, payload: InputT) -> str:
        raise NotImplementedError

    def response_schema(self, payload: InputT) -> type[BaseModel]:  # noqa: ARG002
        """The schema this particular call asks for. The role's contract by default.

        **An override may narrow the contract; it can never replace it.** Whatever comes
        back is re-validated into :attr:`output_schema` by :meth:`run`, so a subclass
        cannot widen what the role may return — the registry binding still decides that.

        The one thing this exists for is a field the declared contract can only describe
        as "an object". ``dict[str, Any]`` becomes ``{"properties": {},
        "additionalProperties": false}`` on the wire, because the API's JSON-schema mode
        has no way to say "any object" — so the model is handed a schema that permits it
        to return nothing, and it duly returns nothing. A caller that knows the shape at
        call time can say so here. See :mod:`aer.agents.contract_schema`.
        """
        return type(self).output_schema

    def untrusted_sources(self, payload: InputT) -> list[UntrustedSource]:  # noqa: ARG002
        """Fetched content this call needs the model to read. Empty by default.

        **Declared rather than interpolated.** An agent that pasted a filing into
        :meth:`user_message` itself would be an agent that could forget to delimit it, and the
        one that forgets is the one that gets exploited. Returning the sources hands the
        wrapping to :meth:`run`, which cannot forget.

        ``payload`` is unused in the default and is part of the interface, because an agent
        that fetches decides *from its input* what it fetched.
        """
        return []

    # -- Composed by the base, not by an agent -----------------------------------------------

    def composed_system_prompt(self, payload: InputT) -> str:
        """The platform contract, the agent's instruction, and the containment rule.

        In that order, and the order is doing work. The contract leads because it is the
        text every role shares and must not be able to displace — and because prompt
        caching keys on a stable prefix, so the invariant text comes first and the
        per-call content last.

        The containment rule is appended only when this call carries untrusted content.
        Adding it unconditionally would put a rule about quoted documents in front of an
        agent that reads none, and the prompt recorded against a run should describe that
        run. The variants hash differently and become different ``prompts`` rows, which is
        correct: they are different instructions, and a run must be attributable to the
        one it actually used.
        """
        composed = f"{PLATFORM_CONTRACT}\n\n{self.system_prompt(payload)}"
        if not self.untrusted_sources(payload):
            return composed
        return f"{composed}\n\n{CONTAINMENT_RULE}"

    def composed_user_message(self, payload: InputT) -> str:
        """The agent's request, with any fetched content quoted beneath it."""
        quoted = wrap_untrusted(self.untrusted_sources(payload))
        if not quoted:
            return self.user_message(payload)
        return f"{self.user_message(payload)}\n\n{quoted}"

    # -- The one public operation ------------------------------------------------------------

    async def run(self, context: AgentContext, payload: InputT) -> OutputT:
        """Perform the agent's work: one routed, archived, metered model call."""
        choice = context.router.resolve(self.role)
        # Composed, never raw. Untrusted content is wrapped and the containment rule attached
        # here, where an agent has no opportunity to skip either.
        system = self.composed_system_prompt(payload)
        messages = [Message(role="user", content=self.composed_user_message(payload))]

        # The role's input cap, checked against a real count before any money moves. The
        # count itself is free, and a refused call costs nothing at all.
        projected = await context.provider.count_tokens(
            system=system, messages=messages, model=choice.model
        )
        if projected > self.definition.max_input_tokens:
            message = (
                f"The {self.role} agent composed a call of {projected} input tokens against "
                f"its registered cap of {self.definition.max_input_tokens}. The call was "
                "refused before it was made — a composition this size means something was "
                "included that this role is not meant to carry."
            )
            raise TokenCapExceededError(
                message,
                context={
                    "role": self.role,
                    "projected": projected,
                    "cap": self.definition.max_input_tokens,
                },
            )

        started = time.perf_counter()
        result = await context.provider.complete_structured(
            self.response_schema(payload),
            system=system,
            messages=messages,
            model=choice.model,
            effort=choice.effort,
            max_tokens=self.definition.max_output_tokens,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        agent_run = await self._record(
            context,
            result=result,
            system=system,
            choice_model=choice.model,
            effort=choice.effort,
            elapsed_ms=elapsed_ms,
        )
        await self._meter(context, result=result, agent_run=agent_run)

        _log.info(
            "agent.completed",
            role=self.role,
            model=choice.model,
            schema=type(result.value).__name__,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=round(elapsed_ms, 2),
        )
        return self._as_declared(result.value)

    def _as_declared(self, value: BaseModel) -> OutputT:
        """Bring a narrowed reply back to the role's declared contract.

        A no-op unless :meth:`response_schema` was overridden. When it was, the reply is an
        instance of a class built for this call, and everything downstream — the validators,
        the recorders, the renderer — is written against the declared one. Re-validating
        rather than casting is what keeps the registry binding meaningful: a narrowed schema
        that produced something the role may not return fails here, loudly.

        ``exclude_none`` because an optional field the model left out arrives as ``None``,
        and a declared field holding null passes "is it present?" and fails every reader
        after that. ``by_alias`` because the aliases are the contract's own field names.
        """
        declared = type(self).output_schema
        # Exact type, not `isinstance`: a narrowed schema is built as a *subclass* of the
        # declared one, so an instance check would pass it straight through with its
        # content still a nested model where every reader expects a mapping.
        if type(value) is declared:
            return value  # type: ignore[return-value]
        return declared.model_validate(  # type: ignore[return-value]
            value.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    async def run_batch(self, context: AgentContext, payloads: Sequence[InputT]) -> list[OutputT]:
        """Perform many instances of this agent's work as one provider batch.

        Everything :meth:`run` guarantees, per item: the same composition (so nothing can
        skip the wrapping), the same input-cap refusal *before* any money moves, and one
        archived, metered ``agent_runs`` row per item — a batch is a transport choice,
        not a different audit standard. Results come back in payload order, which the
        provider protocol makes part of its contract.
        """
        if not payloads:
            return []

        choice = context.router.resolve(self.role)
        requests: list[BatchRequest] = []
        for payload in payloads:
            system = self.composed_system_prompt(payload)
            messages = (Message(role="user", content=self.composed_user_message(payload)),)
            projected = await context.provider.count_tokens(
                system=system, messages=messages, model=choice.model
            )
            if projected > self.definition.max_input_tokens:
                message = (
                    f"The {self.role} agent composed a batch item of {projected} input "
                    f"tokens against its registered cap of {self.definition.max_input_tokens}. "
                    "The whole batch was refused before it was made."
                )
                raise TokenCapExceededError(
                    message,
                    context={
                        "role": self.role,
                        "projected": projected,
                        "cap": self.definition.max_input_tokens,
                    },
                )
            requests.append(BatchRequest(system=system, messages=messages))

        started = time.perf_counter()
        results = await context.provider.complete_structured_batch(
            self.output_schema,
            requests=requests,
            model=choice.model,
            effort=choice.effort,
            max_tokens=self.definition.max_output_tokens,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        values: list[OutputT] = []
        for request, result in zip(requests, results, strict=True):
            agent_run = await self._record(
                context,
                result=result,
                system=request.system,
                choice_model=choice.model,
                effort=choice.effort,
                elapsed_ms=result.latency_ms,
            )
            await self._meter(context, result=result, agent_run=agent_run)
            values.append(result.value)  # type: ignore[arg-type]

        _log.info(
            "agent.batch_completed",
            role=self.role,
            model=choice.model,
            schema=self.output_schema.__name__,
            items=len(values),
            latency_ms=round(elapsed_ms, 2),
        )
        return values

    async def estimate_input_tokens(self, context: AgentContext, payload: InputT) -> int:
        """Count what this agent's call would consume, without making it.

        What the approval gate shows and what the budget guard compares against. Counted
        by the provider rather than estimated from characters, because the gate is where a
        person decides whether to spend money and a misleading figure there is worse than
        no figure.
        """
        choice = context.router.resolve(self.role)
        # The composed forms, because those are what will be sent. Counting the bare prompt
        # would under-report by the length of every quoted document, which is most of the call
        # — and the figure this produces is what a person sees before agreeing to spend money.
        return await context.provider.count_tokens(
            system=self.composed_system_prompt(payload),
            messages=[Message(role="user", content=self.composed_user_message(payload))],
            model=choice.model,
        )

    def require_tool(self, name: str) -> None:
        """Confirm a tool is on this agent's allowlist.

        Raises:
            ToolNotPermittedError: If it is not. Never widened at runtime — the whole
                point is that what an agent may do is fixed before any untrusted text is
                read.
        """
        # The definition, not the property: an attribute anywhere on the class hierarchy
        # must have no way to answer this question.
        if name in self.definition.allowed_tools:
            return
        message = (
            f"The {self.role} agent may not use the tool {name!r}. Permitted: "
            f"{sorted(self.allowed_tools) or 'none'}. A role's tool allowlist is fixed in "
            "its registry definition and is never widened at runtime."
        )
        raise ToolNotPermittedError(message, context={"role": self.role, "tool": name})

    # -- Internals ---------------------------------------------------------------------------

    async def _record(
        self,
        context: AgentContext,
        *,
        result: StructuredResult[Any],
        system: str,
        choice_model: str,
        effort: str,
        elapsed_ms: float,
    ) -> AgentRun:
        """Archive the exchange and write the ``agent_runs`` row."""
        prompt = await self._ensure_prompt(context, system)

        request_artefact = await store_artefact(
            context.session,
            context.store,
            data=_canonical_bytes(result.request_payload),
            media_type="application/json",
        )
        response_artefact = await store_artefact(
            context.session,
            context.store,
            data=_canonical_bytes(result.response_payload),
            media_type="application/json",
        )

        agent_run = AgentRun(
            job_step_id=context.job_step.id,
            agent_role=self.role,
            provider=context.provider.name,
            model=choice_model,
            effort=effort,
            prompt_id=prompt.id,
            request_payload_ref=request_artefact.artefact.id,
            response_payload_ref=response_artefact.artefact.id,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_tokens=result.usage.cache_read_tokens,
            cache_write_tokens=result.usage.cache_write_tokens,
            stop_reason=result.usage.stop_reason,
            latency_ms=int(elapsed_ms),
        )
        context.session.add(agent_run)
        await context.session.flush()
        return agent_run

    async def _ensure_prompt(self, context: AgentContext, template: str) -> Prompt:
        """Find or create the versioned prompt row for this system prompt.

        Keyed by content hash as well as by key and version, so an edit that forgot to bump
        the version is detected rather than silently reusing the old row and attributing a
        run to an instruction it did not use.

        **Find-or-create, under concurrency, means retrying.** The five research workers run
        as parallel nodes with a session each, share the ``analysis`` role, and carry a
        different topic brief — so five different hashes race for ``(agent.analysis, 1)``.
        All five looked, all five found nothing, all five inserted, and four died on
        ``uq_prompts_key_version``. That is not a survivable error where it landed: it broke
        the flush, which broke the session, which broke the commit that records the
        failure, which is how a run ended up dead with the console still showing it
        running.

        The insert therefore happens inside a savepoint. A conflict rolls back the savepoint
        alone, leaving the session usable, and the next pass sees what the winner wrote —
        PostgreSQL makes the loser wait on the unique index until the winner commits, so by
        the time the error arrives the row is really there. Either it is the same template,
        and it is returned, or it is a different one, and this pass takes the hash-suffixed
        version that only it can want.
        """
        key = f"agent.{self.role}"
        digest = sha256_hex(template.encode("utf-8"))

        for attempt in range(_PROMPT_ATTEMPTS):
            existing = await context.session.scalar(
                select(Prompt).where(Prompt.key == key, Prompt.content_hash == digest)
            )
            if existing is not None:
                return existing

            # A new hash under an existing version means the template changed without the
            # version being bumped -- or, as above, that a sibling role got there first.
            # Recorded under a hash-suffixed version rather than refused: losing the run
            # would be a worse outcome than a version string that says plainly that this
            # text is not the one version 1 originally named.
            version = self.prompt_version
            clash = await context.session.scalar(
                select(Prompt).where(Prompt.key == key, Prompt.version == version)
            )
            if clash is not None:
                version = f"{self.prompt_version}+{digest[:8]}"

            prompt = Prompt(key=key, version=version, template=template, content_hash=digest)
            try:
                async with context.session.begin_nested():
                    context.session.add(prompt)
                return prompt
            except IntegrityError:
                if attempt == _PROMPT_ATTEMPTS - 1:
                    raise
                _log.info("agent.prompt_raced", key=key, version=version)

        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    async def _meter(
        self, context: AgentContext, *, result: StructuredResult[Any], agent_run: AgentRun
    ) -> None:
        """Turn usage into ``costs`` rows, and add the total to the context's running spend."""
        lines = price_usage(
            result.usage,
            provider=context.provider.name,
            usd_to_gbp=context.settings.usd_to_gbp,
        )

        for line in lines:
            context.session.add(
                Cost(
                    job_id=context.job_step.job_id,
                    job_step_id=context.job_step.id,
                    agent_run_id=agent_run.id,
                    category=line.category.value,
                    provider=line.provider,
                    model=line.model,
                    units=line.units,
                    unit_type=line.unit_type,
                    amount_usd=line.amount_usd,
                    amount_gbp=line.amount_gbp,
                    fx_rate=line.fx_rate,
                )
            )
            context.spend_gbp += line.amount_gbp

        await context.session.flush()


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Serialise a payload for archiving, deterministically.

    Uses the same canonical form as the audit chain: sorted keys, minimal separators. The
    same request archived twice is therefore the same bytes and the same artefact, so a
    content-addressed store holds one copy of a prompt however many times it is sent.
    """
    return canonical_json(payload).encode("utf-8")
