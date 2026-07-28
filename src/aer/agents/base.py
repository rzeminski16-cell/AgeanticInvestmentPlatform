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

**Never widen the allowlist.** ``allowed_tools`` is a property of the agent, and the check
that a tool is permitted happens in Python before the tool runs. Text inside a fetched
document can ask for anything it likes; there is no path from what a model emits to what an
agent is permitted to do.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import AgentRun, Cost, JobStep, Prompt
from aer.errors import AerError
from aer.providers.costs import price_usage
from aer.providers.protocol import LLMProvider, Message, StructuredResult
from aer.providers.router import Router
from aer.services.artefacts import store_artefact
from aer.storage.protocol import ArtefactStore

__all__ = ["Agent", "AgentContext", "ToolNotPermittedError"]

_log = structlog.get_logger("aer.agents")


class ToolNotPermittedError(AerError):
    """An agent tried to use a tool outside its allowlist.

    Always a bug or an attack, never a condition to recover from by widening the list.
    """

    code = "tool_not_permitted"
    http_status = 403


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

    Subclasses declare four class attributes and implement two methods:

    * ``role`` — what the router resolves.
    * ``output_schema`` — the Pydantic model the response must satisfy.
    * ``allowed_tools`` — a frozen set, checked in code. Empty means no tools at all,
      which is the correct default and what every Phase 1 agent uses.
    * ``max_output_tokens`` — a hard cap, so one agent cannot consume a run's budget.

    * :meth:`system_prompt` — the instruction, which becomes a versioned ``prompts`` row.
    * :meth:`user_message` — the request, built from typed input rather than interpolated
      free text.
    """

    role: ClassVar[str]
    output_schema: ClassVar[type[BaseModel]]
    allowed_tools: ClassVar[frozenset[str]] = frozenset()
    max_output_tokens: ClassVar[int] = 4096
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: InputT) -> str:
        raise NotImplementedError

    def user_message(self, payload: InputT) -> str:
        raise NotImplementedError

    # -- The one public operation ------------------------------------------------------------

    async def run(self, context: AgentContext, payload: InputT) -> OutputT:
        """Perform the agent's work: one routed, archived, metered model call."""
        choice = context.router.resolve(self.role)
        system = self.system_prompt(payload)
        messages = [Message(role="user", content=self.user_message(payload))]

        started = time.perf_counter()
        result = await context.provider.complete_structured(
            self.output_schema,
            system=system,
            messages=messages,
            model=choice.model,
            effort=choice.effort,
            max_tokens=self.max_output_tokens,
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
            schema=self.output_schema.__name__,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=round(elapsed_ms, 2),
        )
        return result.value  # type: ignore[return-value]

    async def estimate_input_tokens(self, context: AgentContext, payload: InputT) -> int:
        """Count what this agent's call would consume, without making it.

        What the approval gate shows and what the budget guard compares against. Counted
        by the provider rather than estimated from characters, because the gate is where a
        person decides whether to spend money and a misleading figure there is worse than
        no figure.
        """
        choice = context.router.resolve(self.role)
        return await context.provider.count_tokens(
            system=self.system_prompt(payload),
            messages=[Message(role="user", content=self.user_message(payload))],
            model=choice.model,
        )

    def require_tool(self, name: str) -> None:
        """Confirm a tool is on this agent's allowlist.

        Raises:
            ToolNotPermittedError: If it is not. Never widened at runtime — the whole
                point is that what an agent may do is fixed before any untrusted text is
                read.
        """
        if name in self.allowed_tools:
            return
        message = (
            f"The {self.role} agent may not use the tool {name!r}. Permitted: "
            f"{sorted(self.allowed_tools) or 'none'}. An agent's tool allowlist is fixed "
            "in code and is never widened at runtime."
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
        """
        key = f"agent.{self.role}"
        digest = sha256_hex(template.encode("utf-8"))

        existing = await context.session.scalar(
            select(Prompt).where(Prompt.key == key, Prompt.content_hash == digest)
        )
        if existing is not None:
            return existing

        # A new hash under an existing version means the template changed without the
        # version being bumped. Recorded under a hash-suffixed version rather than
        # refused: losing the run would be a worse outcome than a version string that
        # says plainly that somebody edited a prompt in place.
        version = self.prompt_version
        clash = await context.session.scalar(
            select(Prompt).where(Prompt.key == key, Prompt.version == version)
        )
        if clash is not None:
            version = f"{self.prompt_version}+{digest[:8]}"

        prompt = Prompt(key=key, version=version, template=template, content_hash=digest)
        context.session.add(prompt)
        await context.session.flush()
        return prompt

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
