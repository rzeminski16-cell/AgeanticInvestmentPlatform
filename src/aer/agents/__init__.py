"""Agents: the parts of the system a language model performs.

Each agent is a narrow, typed operation — plan this request, triage these sources, draft
this section — with a declared role, a tool allowlist, a token cap, and a structured output
schema. None of them produces a number, and none of them asserts an uncited fact.

**What an agent is allowed to do is data, not judgement.** The allowlist, the token caps
and the output contract come from the role's :mod:`aer.agents.registry` definition,
resolved at construction and checked in code before a tool is invoked. Text arriving from
a fetched document cannot widen any of it — nor can the agent's own module, which is the
structural answer to prompt injection and to capability sprawl alike. A new role requires
an ADR, and the registry refuses one without it.

**Every invocation leaves a row.** :class:`~aer.db.models.agent_run.AgentRun` records the
role, the model the router chose, the prompt version, the token counts, and the archived
request and response. A model call nobody kept the input to is a call nobody can explain.
"""

from __future__ import annotations
