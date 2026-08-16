"""Probe agent roles for tests that exercise the agent base rather than a real agent.

The registry refuses an unregistered role at construction — which is the property under
test in several places, and also the reason a test cannot simply subclass ``Agent`` with a
made-up role any more. These two probe roles are registered for the duration of the test
session by the ``probe_agent_roles`` fixture in ``conftest.py``: minimal schema, no tools,
ordinary caps. They stand in for "any role at all" in the containment and gate suites.

Registered under ADR 0035 — the decision that created the role-requires-an-ADR rule also
covers the test-only probes that prove it.
"""

from __future__ import annotations

from pydantic import BaseModel

from aer.agents.registry import RoleDefinition

__all__ = ["PROBE_DEFINITIONS", "ProbeAnswer"]


class ProbeAnswer(BaseModel):
    verdict: str


PROBE_DEFINITIONS: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        role="injection-probe",
        purpose="Test stand-in for prompt-composition and containment assertions.",
        output_schema_ref="tests.agent_probes:ProbeAnswer",
        allowed_tools=frozenset(),
        max_output_tokens=4096,
        adr="0035",
    ),
    RoleDefinition(
        role="evaluation-probe",
        purpose="Test stand-in for the gate's tool-policy measurements.",
        output_schema_ref="tests.agent_probes:ProbeAnswer",
        allowed_tools=frozenset(),
        max_output_tokens=4096,
        adr="0035",
    ),
)
