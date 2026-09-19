"""September's rubric, held to September's own output.

`docs/V1.0_Alpha/11-testing-strategy.md` §3.5: *the rubric, lifted verbatim from
`judges/reads.json`, asserted by a test that the key sets diff empty against the recorded
reads. A rubric that has drifted by one key is a new instrument.*

That is the whole point of this module. ISSUE 2's target is a *number* — at least 3 of 6
comparisons not choosing the console — and a number is only comparable against September's
if the instrument that produced both is the same one. A key added, renamed or dropped makes
the round's result a different measurement wearing the same name, and nothing downstream
would notice.

Diffed in **both** directions on purpose. A rubric that has gained a key still answers every
question September asked, so a one-way check would pass it; but the extra key is a question
September's judges never answered, and any comparison across the two rounds that reads it is
comparing against an absence.
"""

from __future__ import annotations

from typing import Any

import pytest

from audit.judges.rubric import (
    AGREEMENT_KEYS,
    CHOICES,
    COMPARE_KEYS,
    FOCUS_QUESTION_KEYS,
    LENS_BRIEFS,
    LENSES,
    READ_KEYS,
    VERDICT_DIMENSIONS,
    VERDICT_KEYS,
    recorded,
)


@pytest.fixture(scope="module")
def september() -> dict[str, Any]:
    return recorded()


class TestTheKeysDiffEmpty:
    def test_a_read_answers_exactly_what_september_answered(
        self, september: dict[str, Any]
    ) -> None:
        for read in september["reads"]:
            keys = set(read["rubric"])
            assert not keys - READ_KEYS, f"September answered {sorted(keys - READ_KEYS)}, we do not"
            assert not READ_KEYS - keys, f"we ask {sorted(READ_KEYS - keys)}, September did not"

    def test_a_focus_question_carries_exactly_september_s_four_fields(
        self, september: dict[str, Any]
    ) -> None:
        for read in september["reads"]:
            for question in read["rubric"]["focus_questions"]:
                assert set(question) == FOCUS_QUESTION_KEYS

    def test_a_comparison_carries_exactly_september_s_fields(
        self, september: dict[str, Any]
    ) -> None:
        for compare in september["compares"]:
            assert set(compare) == COMPARE_KEYS
            assert set(compare["verdict"]) == VERDICT_KEYS

    def test_the_agreement_block_carries_exactly_september_s_fields(
        self, september: dict[str, Any]
    ) -> None:
        for block in september["agreement"].values():
            assert set(block) == AGREEMENT_KEYS


class TestTheInstrumentIsTheShapeTheTargetCounts:
    def test_the_six_dimensions_are_the_six_issue_two_counts(
        self, september: dict[str, Any]
    ) -> None:
        """ISSUE 2 counts comparisons across these six and no others. A seventh would
        change the denominator of a target that is already written down."""
        assert len(VERDICT_DIMENSIONS) == 6

        for compare in september["compares"]:
            for dimension in VERDICT_DIMENSIONS:
                assert compare["verdict"][dimension] in CHOICES

    def test_every_lens_september_used_has_a_brief(self, september: dict[str, Any]) -> None:
        used = {read["lens"] for read in september["reads"]}

        assert used == set(LENSES)
        assert set(LENS_BRIEFS) == set(LENSES)
        for lens, brief in LENS_BRIEFS.items():
            assert brief.strip(), lens

    def test_september_read_every_document_under_every_lens(
        self, september: dict[str, Any]
    ) -> None:
        """Eighteen reads is three subjects by two documents by three lenses. A panel that
        read one side under fewer lenses would be comparing unlike things."""
        seen = {(read["subject"], read["doc"], read["lens"]) for read in september["reads"]}
        subjects = {read["subject"] for read in september["reads"]}

        assert len(seen) == len(september["reads"]) == len(subjects) * 2 * len(LENSES)
