"""The workflow registry: what this build can run, and what it says when it cannot.

The defect this closed is small and quiet. `declared_steps` compared a job's recorded
`workflow_version` against one imported constant, so any other value produced an empty
tuple — and an empty tuple reaches the run console as a timeline with no steps in it. A
workflow this build does not have and a workflow with nothing in it rendered identically.
"""

from __future__ import annotations

import pathlib

import pytest

from aer.errors import AerError
from aer.services import runs as runs_service
from aer.workflow import registry as registry_module
from aer.workflow.registry import (
    DEFAULT_WORKFLOW_VERSION,
    WorkflowDefinition,
    WorkflowRegistryError,
    registered_workflows,
    resolve_workflow,
)

ADR_ROOT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "adr"


class TestTheRegistry:
    def test_the_default_version_is_registered(self):
        # A default nobody registered would fail at the first run rather than here.
        assert DEFAULT_WORKFLOW_VERSION in registered_workflows()

    def test_a_registered_version_resolves_to_its_steps(self):
        definition = resolve_workflow(DEFAULT_WORKFLOW_VERSION)

        assert definition.version == DEFAULT_WORKFLOW_VERSION
        assert [step.key for step in definition.build_steps()][:3] == [
            "plan",
            "critique_plan",
            "gate_plan",
        ]

    def test_an_unregistered_version_raises_rather_than_answering_nothing(self):
        with pytest.raises(WorkflowRegistryError, match="No workflow named"):
            resolve_workflow("a_workflow_this_build_never_had")

    def test_the_error_is_an_aer_error_with_a_stable_code(self):
        with pytest.raises(AerError) as raised:
            resolve_workflow("nope")

        assert raised.value.code == "workflow_registry"

    def test_steps_are_resolved_lazily_from_a_reference(self):
        # Held as "module:function" rather than as the function, so asking the registry a
        # question does not import every workflow that will ever exist.
        definition = resolve_workflow(DEFAULT_WORKFLOW_VERSION)

        assert definition.build_steps_ref == (
            "aer.workflow.workflows.vertical_slice_v1:build_steps"
        )

    def test_a_reference_this_build_cannot_import_says_so(self):
        broken = WorkflowDefinition(
            version="broken_v1",
            build_steps_ref="aer.workflow.workflows.does_not_exist:build_steps",
            gate_payload_ref="aer.workflow.workflows.does_not_exist:gate_payload",
            adr="0016",
        )

        with pytest.raises(WorkflowRegistryError, match="cannot import"):
            broken.build_steps()

    def test_a_malformed_reference_is_refused_before_it_is_imported(self):
        broken = WorkflowDefinition(
            version="broken_v2",
            build_steps_ref="not_a_reference",
            gate_payload_ref="not_a_reference",
            adr="0016",
        )

        with pytest.raises(WorkflowRegistryError, match="malformed"):
            broken.build_steps()


class TestTheDefinitionsThemselves:
    def test_every_workflow_names_an_adr_that_exists(self):
        # The same rule ADR 0035 applies to agent roles, for the same reason: a workflow is
        # what a run *is*, and one admitted without a decision record is one nobody agreed
        # to. The registry refuses a blank; this refuses one that points nowhere.
        for version in registered_workflows():
            definition = resolve_workflow(version)
            matches = list(ADR_ROOT.glob(f"{definition.adr}-*.md"))
            assert matches, f"workflow {version} cites ADR {definition.adr}, which no file carries"

    def test_a_definition_without_an_adr_is_refused(self):
        bare = WorkflowDefinition(
            version="undocumented", build_steps_ref="a:b", gate_payload_ref="a:b", adr="   "
        )

        with pytest.raises(WorkflowRegistryError, match="names no ADR"):
            registry_module._build((bare,))

    def test_a_version_registered_twice_is_refused(self):
        # Two rows claiming one version means a job's recorded name resolves to whichever
        # was written last, which is a reproducibility question wearing a typo.
        twice = resolve_workflow(DEFAULT_WORKFLOW_VERSION)

        with pytest.raises(WorkflowRegistryError, match="registered twice"):
            registry_module._build((twice, twice))


class TestDeclaredStepsDegradesDeliberately:
    def test_a_registered_version_lists_its_steps(self):
        keys = runs_service.declared_steps(DEFAULT_WORKFLOW_VERSION)

        assert keys[0] == "plan"
        assert "gate_plan" in keys

    def test_an_unregistered_version_is_still_blank_but_now_by_decision(self):
        # The console needs a tuple, not an exception — it renders whatever it is given.
        # What changed is that the blank now comes from the registry saying it has no such
        # workflow, and is logged, rather than from an equality test nobody could see.
        assert runs_service.declared_steps("some_removed_workflow_v3") == ()

    def test_the_engine_runs_the_version_the_job_recorded(self):
        # Not whatever the default happens to be today. A job that recorded a version is a
        # job that must be reproducible against those steps.
        registered = registered_workflows()

        assert all(resolve_workflow(version).build_steps() for version in registered)
