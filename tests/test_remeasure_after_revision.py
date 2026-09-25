"""The final gate reads the draft it shows: a revision that stood is measured again.

Roadmap §3.19 item 75. `validate` measures the draft before the red team and the revise pass,
so a section the revise pass rewrote reached the final gate measured as it had been. The
verdict round's AZN run was refused for an executive-summary sentence that the revise pass
had already rewritten correctly — and a revision that introduced an error would have reached
the gate with nothing measuring it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from aer.services.revision import ReviseOutcome
from aer.workflow.workflows import vertical_slice_v1


@pytest.fixture
def measured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What was measured, in order. The measurements themselves have their own suites."""
    calls: list[str] = []

    async def evaluate(_context: Any, *, job: Any, request: Any) -> list[Any]:
        calls.append("checks")
        return []

    async def consistency(_session: Any, *, job_id: uuid.UUID) -> None:
        calls.append("consistency")

    monkeypatch.setattr(vertical_slice_v1, "evaluate_run", evaluate)
    monkeypatch.setattr(vertical_slice_v1, "check_report_consistency", consistency)
    return calls


async def _remeasure(outcome: ReviseOutcome) -> bool:
    return await vertical_slice_v1.remeasure_after_revision(
        SimpleNamespace(session=object()),  # type: ignore[arg-type]
        job=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        request=object(),  # type: ignore[arg-type]
        outcome=outcome,
    )


class TestTheGateReadsTheDraftItShows:
    async def test_a_revision_that_stood_is_measured_again(self, measured: list[str]) -> None:
        outcome = ReviseOutcome(
            revised=[{"section_key": "executive_summary", "kept_approved_draft": False}]
        )

        assert await _remeasure(outcome) is True
        assert measured == ["checks", "consistency"]

    async def test_a_refused_revision_left_the_measured_draft_standing(
        self, measured: list[str]
    ) -> None:
        """ADR 0098: a refused revision restores the draft `validate` measured."""
        outcome = ReviseOutcome(
            revised=[{"section_key": "executive_summary", "kept_approved_draft": True}]
        )

        assert await _remeasure(outcome) is False
        assert measured == []

    async def test_nothing_revised_nothing_measured_twice(self, measured: list[str]) -> None:
        assert await _remeasure(ReviseOutcome()) is False
        assert measured == []
