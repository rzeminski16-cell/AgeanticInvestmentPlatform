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

**Refuse an unrunnable call before it is made.** Every composed call is checked at the
provider boundary against the two limits that are real, and only those (ADR 0053): the
model's context window, because a prompt that cannot fit is a guaranteed 400 after the
upload; and the money — the projected cost of the call against what remains of the
request's own budget and the month's. There is no per-role token allowance. A role that
composes a large turn on a large company is doing its job; what it may not do is spend
past a ceiling the operator set, and that refusal names pounds, arrives before the call,
and pauses the run for a decision exactly as the step-level guard does.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from aer.db.models import AgentRun, Cost, Job, JobStep, Prompt, ResearchRequest

# Aliased: `sqlalchemy.exc.IntegrityError` is already in this namespace for the
# prompt-row race, and a shadowed exception is caught by nobody.
from aer.errors import AerError, BudgetExceededError, ValidationError
from aer.errors import IntegrityError as BrokenRecordError
from aer.providers.costs import context_window_for, estimate_gbp, price_usage
from aer.providers.protocol import (
    BatchRequest,
    LLMProvider,
    Message,
    SpentButUnusableError,
    StructuredResult,
)
from aer.providers.router import Router
from aer.services.artefacts import store_artefact
from aer.storage.protocol import ArtefactStore
from aer.tracing import span

__all__ = [
    "Agent",
    "AgentContext",
    "TokenCapExceededError",
    "ToolNotPermittedError",
    "schema_problems",
]

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
    """A composed call cannot fit the routed model's context window, and was refused unmade.

    The window is the vendor's limit, not this platform's: past it the API answers 400,
    but only after the whole prompt has been uploaded. The per-role input allowances that
    used to raise this error are gone — a live run died at 40,367 tokens against a cap of
    30,000 that had been guessed before any run existed to measure, on a composition that
    was simply a big company's evidence doing its job. Affordability is the budget guard's
    question now, asked in pounds (ADR 0053); this error remains only for the composition
    no model can physically run, which no retry and no setting can fix.
    """

    code = "token_cap_exceeded"


def schema_problems(rejected: ValidationError) -> list[str]:
    """A rejected reply, said back to the model in terms it can act on.

    The exception's own message is written for whoever reads the failed step: it names the
    model, the schema, the count, and — for a truncation — that ``max_output_tokens`` wants
    raising, which is advice the model can do nothing with. What a retry needs is narrower
    and more useful: which field, and what was wrong with it.

    Shared rather than the research worker's own, which is where it was until a live run
    showed what its absence costs everywhere else. Three sections of one report failed
    with "22 field(s) broke a constraint the API does not enforce" — true, unactionable,
    and handed verbatim to a retry that then made the same mistake and burned the
    section. The detail was in the exception's context the whole time; only the worker
    was reading it.
    """
    errors = rejected.context.get("errors")
    if not isinstance(errors, list) or not errors:
        # No field-level detail, which at this layer means the reply carried no structured
        # output at all — a refusal, or the token ceiling reached before the JSON began.
        return [
            "Your last reply could not be read as a turn. It must be one JSON object "
            "matching the schema, and short enough to finish."
        ]

    problems: list[str] = []
    for error in errors:
        if not isinstance(error, dict):  # pragma: no cover -- context is ours; belt and braces
            continue
        if error.get("type") == "json_invalid":
            problems.append(
                "Your last reply was cut off before it was complete. Say the same thing in "
                "fewer words: fewer findings, shorter statements."
            )
            continue
        where = str(error.get("loc") or "your reply")
        detail = str(error.get("msg") or error.get("type") or "was rejected")
        problems.append(f"{where}: {detail}")
    return problems


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

    def stable_context(self, payload: InputT) -> str:
        """The head of the user turn that repeats across calls, or empty for none.

        Overridden by roles that send the same large block more than once — a run's
        evidence listing goes to every section that shares an evidence policy, and to every
        retry of one section. Returning it here rather than from :meth:`user_message` is
        what lets it be cached: it becomes its own content block with a breakpoint after
        it, ahead of the part that varies.

        **Empty by default, and that is the safe answer.** A role that returns something
        which is not in fact identical between calls gets no cache hits and pays a write
        premium each time, which is worse than not asking. Only override where the block
        genuinely repeats byte for byte.
        """
        del payload
        return ""

    def composed_user_message(self, payload: InputT) -> str:
        """The agent's request, with any fetched content quoted beneath it.

        The stable context is deliberately *not* here: it is sent as a separate leading
        block by :meth:`run`. This method returns what changes from call to call.
        """
        quoted = wrap_untrusted(self.untrusted_sources(payload))
        if not quoted:
            return self.user_message(payload)
        return f"{self.user_message(payload)}\n\n{quoted}"

    def compose_turn(self, payload: InputT) -> Message:
        """The user turn exactly as it goes to the provider — from **every** path.

        :meth:`run` and :meth:`run_batch` both need it, and both used to build it for
        themselves. The batch one predated :meth:`stable_context` and silently left it out,
        so a batch call sent a prompt with its evidence missing — no exception, no failed
        schema, just a model asked to write from nothing. A third caller, an input-token
        estimator, had the same omission and no callers of its own; it is gone.

        One method, so a further path cannot diverge either.
        """
        return Message(
            role="user",
            content=self.composed_user_message(payload),
            cache_prefix=self.stable_context(payload) or None,
        )

    # -- The two refusals that precede every call (ADR 0053) ---------------------------------

    def _refuse_what_cannot_fit(self, *, model: str, projected_input_tokens: int) -> None:
        """Refuse a composition the routed model cannot physically run.

        The vendor's limit, not ours: `max_tokens` is carved out of the same window the
        prompt occupies, so the sum has to fit. Past it the API answers 400 — but only
        after the whole prompt has been uploaded, so this is the same refusal for free.
        """
        window = context_window_for(model)
        ceiling = self.definition.max_output_tokens
        if projected_input_tokens + ceiling <= window:
            return
        message = (
            f"The {self.role} agent composed a call of {projected_input_tokens:,} input "
            f"tokens which, with its {ceiling:,}-token output ceiling, cannot fit "
            f"{model}'s {window:,}-token context window. The call was refused before it "
            "was made; no smaller retry exists, so the composition itself has to shrink."
        )
        raise TokenCapExceededError(
            message,
            context={
                "role": self.role,
                "model": model,
                "projected": projected_input_tokens,
                "window": window,
            },
        )

    async def _refuse_what_cannot_be_afforded(
        self,
        context: AgentContext,
        *,
        model: str,
        input_tokens: int,
        output_ceiling_tokens: int,
    ) -> None:
        """Refuse a call whose worst case breaks a ceiling the operator set — in pounds.

        This is the per-call half of invariant 6, the piece ADR 0052 recorded as missing:
        the step-level guard runs before a step, so the many calls inside one ran
        unchecked between checks. Here every call's worst case — the counted input at the
        uncached rate plus the full output ceiling, since `max_tokens` is the only hard
        bound on the expensive direction — is priced and compared against what remains of
        the request's own budget and of the month's, using the same scope names the
        step-level guard writes, so the run console's banner needs no second vocabulary.

        Worst case deliberately ignores prompt caching: a projection that assumed cache
        hits would under-guard exactly when the cache goes cold.
        """
        # Deferred: `aer.workflow` pulls in the workflow definitions, which import the
        # agents package — a module-level import here would be a cycle.
        from aer.workflow.engine import spend_so_far, spend_this_month  # noqa: PLC0415

        projected_gbp = estimate_gbp(
            model=model,
            input_tokens=input_tokens,
            expected_output_tokens=output_ceiling_tokens,
            usd_to_gbp=context.settings.usd_to_gbp,
        )

        job = await context.session.get(Job, context.job_step.job_id)
        request = (
            None if job is None else await context.session.get(ResearchRequest, job.request_id)
        )
        if job is None or request is None:
            # Referential breakage, not a budget question — and a guard that shrugged
            # here would be a guard any orphaned step walks straight past.
            message = (
                f"The {self.role} agent's job step is not attached to a request, so the "
                "spend guard has no per-run cap to read. Refusing the call."
            )
            raise BrokenRecordError(message, context={"job_step_id": str(context.job_step.id)})

        spent = await spend_so_far(context.session, job_id=job.id)
        self._refuse_over_ceiling(
            scope="per_run",
            noun="run",
            model=model,
            spent=spent,
            projected_gbp=projected_gbp,
            cap=Decimal(str(request.max_cost_gbp)),
            remedy="Raise the cap on this request to continue.",
        )

        this_month = await spend_this_month(context.session, now=datetime.now(UTC))
        self._refuse_over_ceiling(
            scope="monthly",
            noun="month",
            model=model,
            spent=this_month,
            projected_gbp=projected_gbp,
            cap=context.settings.monthly_budget_gbp,
            remedy=(
                "This is the ceiling across every run this month, so raising this "
                "request's own cap will not release it."
            ),
        )

    def _refuse_over_ceiling(
        self,
        *,
        scope: str,
        noun: str,
        model: str,
        spent: Decimal,
        projected_gbp: Decimal,
        cap: Decimal,
        remedy: str,
    ) -> None:
        total = spent + projected_gbp
        if total <= cap:
            return
        message = (
            f"The {self.role} agent's next call is projected to cost up to "
            f"£{projected_gbp:.4f}, which would take the {noun} to £{total:.4f} against a "
            f"cap of £{cap:.2f}. The call was refused before it was made and the run is "
            f"paused for a decision. {remedy}"
        )
        raise BudgetExceededError(
            message,
            context={
                "spent_gbp": str(spent),
                "projected_gbp": str(projected_gbp),
                "cap_gbp": str(cap),
                "scope": scope,
                "role": self.role,
                "model": model,
            },
        )

    # -- The one public operation ------------------------------------------------------------

    async def run(self, context: AgentContext, payload: InputT) -> OutputT:
        """Perform the agent's work: one routed, archived, metered model call."""
        choice = context.router.resolve(self.role)
        # Composed, never raw. Untrusted content is wrapped and the containment rule attached
        # here, where an agent has no opportunity to skip either.
        system = self.composed_system_prompt(payload)
        turn = self.compose_turn(payload)
        messages = [turn]

        # Checked against a real count before any money moves. The count itself is free,
        # and a refused call costs nothing at all.
        projected = await context.provider.count_tokens(
            system=system, messages=messages, model=choice.model
        )
        self._refuse_what_cannot_fit(model=choice.model, projected_input_tokens=projected)
        await self._refuse_what_cannot_be_afforded(
            context,
            model=choice.model,
            input_tokens=projected,
            output_ceiling_tokens=self.definition.max_output_tokens,
        )

        started = time.perf_counter()
        try:
            with span(
                f"model.{self.role}",
                **{
                    "aer.role": self.role,
                    "aer.model": choice.model,
                    "aer.effort": choice.effort,
                    "aer.input_tokens_projected": projected,
                    "aer.cache_prefix": turn.cache_prefix is not None,
                },
            ):
                result = await context.provider.complete_structured(
                    self.response_schema(payload),
                    system=system,
                    messages=messages,
                    model=choice.model,
                    effort=choice.effort,
                    max_tokens=self.definition.max_output_tokens,
                )
        except SpentButUnusableError as unusable:
            # The reply is no good and the money is gone. Recording it is not bookkeeping
            # for its own sake: the budget cap reads the `costs` table, so spend it cannot
            # see is spend it cannot cap. The archived payloads go down too, because a
            # reply nobody kept is a reply nobody can explain — and these are the ones
            # somebody will want to read.
            await self._meter_a_failure(context, unusable, choice_model=choice.model, system=system)
            raise
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
        not a different audit standard. That includes the failure book-keeping: a reply
        that fails validation arrives as the same billed error the single path meters,
        and it is metered here before it continues. Results come back in payload order,
        which the provider protocol makes part of its contract.
        """
        if not payloads:
            return []

        choice = context.router.resolve(self.role)
        requests: list[BatchRequest] = []
        batch_input_tokens = 0
        for payload in payloads:
            system = self.composed_system_prompt(payload)
            messages = (self.compose_turn(payload),)
            projected = await context.provider.count_tokens(
                system=system, messages=messages, model=choice.model
            )
            # The window is per item — each is its own call at the API — but the money is
            # one question for the whole batch, asked once below: the items are submitted
            # together, so item three cannot be affordable in any sense that items one and
            # two are not.
            self._refuse_what_cannot_fit(model=choice.model, projected_input_tokens=projected)
            batch_input_tokens += projected
            requests.append(BatchRequest(system=system, messages=messages))

        await self._refuse_what_cannot_be_afforded(
            context,
            model=choice.model,
            input_tokens=batch_input_tokens,
            output_ceiling_tokens=self.definition.max_output_tokens * len(requests),
        )

        started = time.perf_counter()
        try:
            results = await context.provider.complete_structured_batch(
                self.output_schema,
                requests=requests,
                model=choice.model,
                effort=choice.effort,
                max_tokens=self.definition.max_output_tokens,
            )
        except SpentButUnusableError as unusable:
            # The same book-keeping the single path does, for the same reason: the batch
            # completed at the API and was billed before any reply could fail validation
            # here (gap A36). The bill it carries covers every item, so one row puts the
            # whole of the money on the ledger; the archived exchange is the failed
            # item's, whose system prompt names the row.
            item = unusable.context.get("item")
            which = item if isinstance(item, int) and 0 <= item < len(requests) else 0
            await self._meter_a_failure(
                context, unusable, choice_model=choice.model, system=requests[which].system
            )
            raise
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

    async def _meter_a_failure(
        self,
        context: AgentContext,
        unusable: SpentButUnusableError,
        *,
        choice_model: str,
        system: str,
    ) -> None:
        """Record what an unusable reply cost, then let the error continue on its way.

        The same two writes a success gets — an ``agent_runs`` row with both payloads
        archived, and the ``costs`` rows priced off the usage — because a call that failed
        is not a call that was free. ``stop_reason`` carries the schema's verdict rather
        than the API's, which is what makes these rows findable afterwards.

        Never raises. This runs on the way out of a failure, and a second failure here
        would replace a diagnosis the caller can act on with a database error nobody asked
        about. The spend is logged in that case, so it is at least visible somewhere.
        """
        try:
            agent_run = await self._record(
                context,
                result=StructuredResult(
                    value=None,
                    usage=unusable.usage,
                    latency_ms=unusable.latency_ms,
                    request_payload=unusable.request_payload,
                    response_payload=unusable.response_payload,
                ),
                system=system,
                choice_model=choice_model,
                effort=context.router.resolve(self.role).effort,
                elapsed_ms=unusable.latency_ms,
                stop_reason="schema_rejected",
            )
            await self._meter(
                context,
                result=StructuredResult(
                    value=None,
                    usage=unusable.usage,
                    latency_ms=unusable.latency_ms,
                    request_payload=unusable.request_payload,
                    response_payload=unusable.response_payload,
                ),
                agent_run=agent_run,
            )
        except Exception:
            _log.warning(
                "agent.unmetered_failure",
                role=self.role,
                model=choice_model,
                input_tokens=unusable.usage.input_tokens,
                output_tokens=unusable.usage.output_tokens,
                exc_info=True,
            )

    async def _record(
        self,
        context: AgentContext,
        *,
        result: StructuredResult[Any],
        system: str,
        choice_model: str,
        effort: str,
        elapsed_ms: float,
        stop_reason: str | None = None,
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
            # The schema's verdict when there is one, so an unusable reply is findable
            # as such rather than sitting under whatever the API happened to say.
            stop_reason=stop_reason or result.usage.stop_reason,
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
