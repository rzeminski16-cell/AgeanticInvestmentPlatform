"""Which workflows this build can run, and how to reach one by the name a job recorded.

A job stores `workflow_version` so a finished report can be reproduced against the steps
that actually made it. Until now the only way back from that string to those steps was an
equality check against one imported constant, so a version this build did not recognise
resolved to nothing — and `declared_steps` returning `()` is indistinguishable, at the run
console, from a workflow with no steps. The timeline simply went blank, which is the
failure mode ADR 0076 named in the other half of the audit chain: a lookup that answers
"nothing" where it means "I was not told".

This is the registry the second tool needs and the first one already wanted. It follows
`aer/agents/registry.py` exactly — frozen rows, lazy `"module:function"` references, an
`adr` field that refuses to be empty, and a test that walks the references — because that
pattern is how capability is declared everywhere else here, and a second shape for the
same idea is a second thing to learn.

**Gate payloads are deliberately absent.** The obvious next move is a uniform
`gate_payload(session, job, gate)` on this row, and it is wrong. The three payload builders
take three different arguments *on purpose*: `unmapped_gate_payload` reads the extract
step's own frozen output so that "the tags an operator is shown are the tags the extractor
actually could not place — not a re-derivation that might differ", while ADR 0046's
amendment has the assumptions gate assemble from the rows as they stand, precisely because
that gate approves work that has not happened yet. A single signature forces every gate to
re-derive from the session, which would silently reverse both decisions. Whatever unifies
them has to be a decision about what each gate hashes, not a refactor.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from aer.errors import AerError

if TYPE_CHECKING:
    from aer.workflow.engine import WorkflowStep

    # What every workflow's gate-payload entry point looks like.
    GatePayload = Callable[..., Awaitable[Mapping[str, Any]]]

__all__ = [
    "WorkflowDefinition",
    "WorkflowRegistryError",
    "registered_workflows",
    "resolve_workflow",
]


class WorkflowRegistryError(AerError):
    """A workflow definition is malformed, or a version has no definition."""

    code = "workflow_registry"


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """One runnable workflow, named by the version a job records.

    ``build_steps_ref`` is resolved lazily rather than held as a function, so importing the
    registry does not drag in every workflow module — and, more usefully, so the reference
    can be checked as a string by a test that never runs a step.
    """

    version: str
    build_steps_ref: str
    gate_payload_ref: str
    adr: str
    summary: str = ""

    def _resolve(self, reference: str, *, field: str) -> Any:
        module_name, _, attribute = reference.partition(":")
        if not module_name or not attribute:
            message = (
                f"Workflow {self.version!r} has a malformed {field} reference "
                f"{reference!r}; it must read 'module:function'."
            )
            raise WorkflowRegistryError(message, context={"workflow_version": self.version})
        try:
            return getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as lost:
            message = (
                f"Workflow {self.version!r} names {reference!r}, which this build cannot import."
            )
            raise WorkflowRegistryError(
                message, context={"workflow_version": self.version}
            ) from lost

    def build_steps(self) -> Sequence[WorkflowStep]:
        """The steps this version declares, in order."""
        builder: Callable[[], Sequence[WorkflowStep]] = self._resolve(
            self.build_steps_ref, field="build_steps"
        )
        return builder()

    def gate_payload(self) -> GatePayload:
        """What a gate approves, for any gate this workflow declares.

        Held here rather than imported by each page because a gate is a property of the
        workflow that raised it. The run console, the JSON API and a second tool's pages
        can then render an approval without knowing which workflow produced it — which is
        the whole reason a registry beats a hard import.
        """
        resolved: GatePayload = self._resolve(self.gate_payload_ref, field="gate_payload")
        return resolved


# The definitions themselves, in the shape `agents/registry.py` keeps them: a module-level
# tuple, so a test can hand `_build` a deliberately broken one and watch it refuse.
_DEFINITIONS: Final[tuple[WorkflowDefinition, ...]] = (
    WorkflowDefinition(
        version="vertical_slice_v1",
        build_steps_ref="aer.workflow.workflows.vertical_slice_v1:build_steps",
        gate_payload_ref="aer.workflow.workflows.vertical_slice_v1:gate_payload",
        adr="0016",
        summary="One company, one report: plan, acquire, extract, calculate, draft, render.",
    ),
)


def _build(
    definitions: Sequence[WorkflowDefinition] = _DEFINITIONS,
) -> Mapping[str, WorkflowDefinition]:
    registry: dict[str, WorkflowDefinition] = {}
    for definition in definitions:
        if not definition.adr.strip():
            message = (
                f"Workflow {definition.version!r} names no ADR. A workflow is what a run "
                "is, and one admitted without a decision record is one nobody agreed to."
            )
            raise WorkflowRegistryError(message, context={"workflow_version": definition.version})
        if definition.version in registry:
            message = f"Workflow {definition.version!r} is registered twice."
            raise WorkflowRegistryError(message, context={"workflow_version": definition.version})
        registry[definition.version] = definition
    return registry


# Explicit, like `db/models/__init__.py` and `agents/registry.py`. Discovery by entry point
# would make what this build can run a function of what happens to be installed.
_REGISTRY: Final[Mapping[str, WorkflowDefinition]] = _build()

# What `start_run` uses when nothing names a version. A default is a choice about which
# workflow a new run gets, so it lives beside the registry rather than in a caller.
DEFAULT_WORKFLOW_VERSION: Final = "vertical_slice_v1"


def resolve_workflow(version: str) -> WorkflowDefinition:
    """The definition for a recorded version.

    Raises rather than returning ``None``: a job holding a version this build cannot run is
    a reproducibility problem, and the caller that wants to degrade gracefully — the run
    console — should say so at the point it decides to.
    """
    definition = _REGISTRY.get(version)
    if definition is None:
        message = (
            f"No workflow named {version!r} is registered in this build. A job recorded it, "
            "so either the workflow was removed or this checkout predates it."
        )
        raise WorkflowRegistryError(message, context={"workflow_version": version})
    return definition


def registered_workflows() -> frozenset[str]:
    """Every version this build can run."""
    return frozenset(_REGISTRY)
