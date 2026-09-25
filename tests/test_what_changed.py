"""The change summary's commentary, held to its own rows (roadmap §3.19 item 74).

The verdict round's refresh lost its valuation, and its rows said so: forty figures "no
longer computed". The commentary the writer produced from them said the valuation chain "has
moved" and that both terminal approaches "have been restated" — the opposite. The writer had
been told every row was a move. Now it is told what happened to each, and a commentary that
passes over figures the refresh no longer holds is refused and asked again.
"""

from __future__ import annotations

from typing import Any

from aer.calc.changes import Movement
from aer.sections.what_changed import what_changed_note, what_changed_problems


def _block(*rows: tuple[str, Movement]) -> dict[str, Any]:
    return {
        "broke": [],
        "moved": [{"label": label, "movement": movement.value} for label, movement in rows],
    }


MOVED_AND_GONE = _block(
    ("Revenue, FY2026", Movement.RELATIVE),
    ("Value per share (exit multiple)", Movement.DISAPPEARED),
    ("Terminal value (Gordon growth)", Movement.DISAPPEARED),
)

# The round's own commentary, in the words it used.
THE_ROUNDS_WORDS = (
    "The valuation chain has moved: its links all differ from the prior report, and both "
    "terminal approaches have been restated."
)


class TestTheWriterIsToldWhatHappenedToEachRow:
    def test_a_gone_figure_is_named_as_gone_not_as_a_move(self) -> None:
        note = what_changed_note(MOVED_AND_GONE)

        assert "Moved materially: Revenue, FY2026." in note
        assert (
            "No longer computed, though the prior report held them: Value per share (exit "
            "multiple), Terminal value (Gordon growth)." in note
        )

    def test_the_terms_are_the_writer_s_to_keep(self) -> None:
        note = what_changed_note(MOVED_AND_GONE)

        assert "has not moved, been revised or been restated" in note


class TestACommentaryThatPassesOverGoneFiguresIsRefused:
    def test_the_rounds_own_words_are_refused(self) -> None:
        problems = what_changed_problems({"commentary": THE_ROUNDS_WORDS}, MOVED_AND_GONE)

        [problem] = problems
        assert problem.startswith("1 figure the report rests on is no longer computed")
        assert "Value per share (exit multiple)" in problem

    def test_a_figure_replaced_under_new_inputs_owes_the_reader_nothing(self) -> None:
        """What the restated-filing scene does: a growth rate over a new window replaces the
        old one's key, so the old one reads as gone. It is not the report's footing."""
        block = _block(("Cagr", Movement.APPEARED), ("Cagr", Movement.DISAPPEARED))

        assert what_changed_problems({"commentary": "Growth held up."}, block) == []

    def test_saying_so_is_accepted(self) -> None:
        commentary = (
            "Revenue for FY2026 rose, and the discounted cash flow is no longer computed on "
            "this refresh, so the value per share and the terminal value are absent."
        )

        assert what_changed_problems({"commentary": commentary}, MOVED_AND_GONE) == []

    def test_with_nothing_gone_there_is_nothing_to_say_about_absence(self) -> None:
        block = _block(("Revenue, FY2026", Movement.RELATIVE))

        assert what_changed_problems({"commentary": "Revenue rose."}, block) == []

    def test_an_instruction_is_still_refused(self) -> None:
        block = _block(("Revenue, FY2026", Movement.RELATIVE))

        [problem] = what_changed_problems(
            {"commentary": "Revenue rose, so you should buy more."}, block
        )
        assert "without issuing an instruction" in problem
