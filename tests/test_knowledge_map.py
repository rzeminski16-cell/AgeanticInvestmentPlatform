"""The knowledge map's enumerable claims, pinned to the code they describe.

`docs/developers/knowledge-map.md` is the orientation layer for a developer who has never seen this
repository. Documents in that role have exactly one failure mode — drift — and this
codebase already knows what an unpinned claim is worth: the monthly budget cap lived in
prose for the whole life of the engine (gap A22), because a claim nobody encoded cannot
fail. So the map's checkable statements are checked.

Only the *enumerable* facts are pinned: the workflow's step keys, the module inventory,
the ADR references, the invariant table. The prose around them — what a thing is for, why
a boundary exists — is routing, and its accuracy is the ordinary review burden every
document carries. A test that tried to verify prose would only verify that nobody had
rephrased it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aer.workflow.workflows.vertical_slice_v1 import build_steps

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "developers" / "knowledge-map.md"
ADR_ROOT = ROOT / "docs" / "adr"
SRC = ROOT / "src" / "aer"


@pytest.fixture(scope="module")
def text() -> str:
    return MAP.read_text(encoding="utf-8")


class TestTheMapNamesWhatExists:
    def test_every_workflow_step_appears(self, text: str) -> None:
        """The run walkthrough is the map's spine; a step it omits is a step a new
        developer will not know can pause, spend or fail."""
        missing = [step.key for step in build_steps() if step.key not in text]
        assert not missing, (
            f"steps missing from docs/developers/knowledge-map.md: {missing}. "
            "The workflow changed; "
            "update section 2 (the diagram and the table)."
        )

    def test_every_module_under_src_appears(self, text: str) -> None:
        """The inventory's value is completeness: the one module it does not mention is
        exactly the one a newcomer will assume is unimportant."""
        names = sorted(
            entry.stem if entry.is_file() else entry.name
            for entry in SRC.iterdir()
            if not entry.name.startswith("_") and entry.name != "__pycache__"
        )
        assert names, "src/aer is empty; the path is wrong"

        missing = [name for name in names if not re.search(rf"\b{re.escape(name)}\b", text)]
        assert not missing, (
            f"modules missing from docs/developers/knowledge-map.md: {missing}. "
            "A package was added "
            "or renamed; update section 4's inventory."
        )

    def test_every_adr_reference_resolves(self, text: str) -> None:
        """A route to a decision record that no longer exists is worse than no route.

        Every ADR number is zero-padded to four digits and starts with 0, and nothing
        else in the map matches that shape — costs carry decimal points, token counts
        carry commas — so the bare pattern is the reference list.
        """
        cited = sorted(set(re.findall(r"\b(0\d{3})\b", text)))
        assert cited, "the map cites no ADRs; the regex or the document is wrong"

        dangling = [number for number in cited if not list(ADR_ROOT.glob(f"{number}-*.md"))]
        assert not dangling, (
            f"docs/developers/knowledge-map.md cites ADRs that do not exist: {dangling}. "
            "Fix the reference, or restore the record it points at."
        )

    def test_the_invariant_table_is_whole(self, text: str) -> None:
        # Eight invariants in CLAUDE.md, eight numbered rows here. The count is asserted
        # rather than the wording, because renumbering is the drift that breaks routes.
        for number in range(1, 9):
            assert f"| {number} |" in text, (
                f"invariant {number} has no row in the map's section 5 table"
            )
