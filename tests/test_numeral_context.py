"""A refused numeral says where it sat, not only what it was (gap A48).

Four sections of the AAPL run of 2026-08-20 were refused over `2025` and `2026`, each
costing a full retry at Opus prices, and the log could not distinguish the two possible
causes: a gap in the reference eraser, or a writer ignoring the prompt rule that ADR 0054
chose instead of a broad year exemption. The refusal named the value and never the span.

These tests hold the instrumentation and — more importantly — hold that it is *only*
instrumentation: the same numerals are flagged before and after, because nothing here
excuses anything.
"""

from __future__ import annotations

from aer.core.section_output import numeral_context, unsourced_numerals


class TestTheRefusalQuotesTheSpan:
    def test_a_flagged_numeral_carries_its_neighbourhood(self) -> None:
        problems = unsourced_numerals({"commentary": "Revenue rose to 4,321 last year."}, [])

        assert len(problems) == 1
        assert "Seen as:" in problems[0]
        assert "Revenue rose to" in problems[0]

    def test_a_bare_year_shows_what_stood_beside_it(self) -> None:
        """The live case: which words failed to anchor the year is the whole question."""
        problems = unsourced_numerals(
            {"commentary": "The 2025 restructuring reduced the cost base."}, []
        )

        assert len(problems) == 1
        assert "2025" in problems[0]
        assert "restructuring" in problems[0]

    def test_the_window_is_the_text_the_scan_read(self) -> None:
        """Quoted from the erased text, so the window shows what survived erasure.

        ``March 2026`` is a recognised date span and is erased before the scan; the
        window must therefore not contain it, or the message would be quoting a
        neighbourhood the scanner never saw.
        """
        problems = unsourced_numerals({"commentary": "In March 2026 revenue was 4321."}, [])

        assert len(problems) == 1
        assert "March" not in problems[0]
        assert "revenue was" in problems[0]

    def test_a_json_number_is_located_by_path_alone(self) -> None:
        """There is no neighbourhood to quote, and an empty quotation would be noise."""
        problems = unsourced_numerals({"figures": {"value": 4321}}, [])

        assert len(problems) == 1
        assert "content.figures.value" in problems[0]
        assert "Seen as:" not in problems[0]


class TestItExcusesNothing:
    def test_a_covered_numeral_is_still_not_flagged(self) -> None:
        assert unsourced_numerals({"commentary": "Revenue was 4321."}, ["Revenue was 4321"]) == []

    def test_an_uncovered_numeral_is_still_flagged(self) -> None:
        problems = unsourced_numerals({"commentary": "Revenue was 4321."}, ["Costs were 99"])

        assert len(problems) == 1
        assert "4321" in problems[0]

    def test_the_line_between_anchored_and_bare_is_where_it_was(self) -> None:
        """ADR 0054's decision stands: instrumentation reports, it does not excuse.

        An anchored year passes as it always did; a bare one is refused as it always
        was — and now says what stood next to it.
        """
        assert unsourced_numerals({"commentary": "Margins improved in 2025."}, []) == []

        bare = unsourced_numerals({"commentary": "The 2025 plan cut the cost base."}, [])
        assert len(bare) == 1
        assert "2025" in bare[0]


class TestTheWindowItself:
    def test_it_reports_nothing_for_a_numeral_that_is_absent(self) -> None:
        assert numeral_context("Revenue rose sharply.", "4321") == ""

    def test_it_marks_where_it_cut(self) -> None:
        text = "x" * 200 + " 4321 " + "y" * 200
        window = numeral_context(text, "4321")

        assert window.startswith("…")
        assert window.endswith("…")
        assert "4321" in window

    def test_it_collapses_whitespace_so_one_refusal_is_one_line(self) -> None:
        window = numeral_context("Revenue\n\n  rose   to 4321", "4321")

        assert "\n" not in window
        assert "Revenue rose to 4321" in window
