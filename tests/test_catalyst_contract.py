"""A catalyst is an event, not a date in the reporting calendar — gap R7.

The live CHRW note's catalyst section listed nothing but scheduled SEC filings, each dated
by extrapolation from the previous two, and spent its prose explaining what a filing is.
The rule that refuses that has to hold a line in two directions at once, and both halves
are pinned here: the calendar entries the section was full of are refused, and the dated
events the section exists for survive. **A rule that only ever says no would close the
section, which is not the fix.**

Pure: the vocabulary in, the offending labels out, no database and no model.
"""

from __future__ import annotations

from typing import Any

from aer.core.section_output import reporting_calendar_entries


def catalyst(label: str, *, timing: str = "in due course", rationale: str = "A reason.") -> Any:
    return {"catalysts": [{"label": label, "expected_timing": timing, "rationale": rationale}]}


class TestTheReportingCalendarIsRefused:
    """The section's own contents, from the note that produced this finding."""

    def test_a_quarterly_report_is_not_a_catalyst(self) -> None:
        found = reporting_calendar_entries(catalyst("Q3 2026 Form 10-Q filing"))

        assert len(found) == 1
        assert "scheduled periodic filing" in found[0]
        assert "Q3 2026 Form 10-Q filing" in found[0]

    def test_an_annual_report_is_not_a_catalyst(self) -> None:
        assert reporting_calendar_entries(catalyst("FY2026 Form 10-K"))

    def test_a_proxy_statement_is_not_a_catalyst(self) -> None:
        assert reporting_calendar_entries(catalyst("Annual proxy statement (DEF 14A)"))

    def test_a_results_announcement_is_not_a_catalyst(self) -> None:
        """The UK half of the vocabulary: the same event under its own name."""
        assert reporting_calendar_entries(catalyst("Interim results announcement"))
        assert reporting_calendar_entries(catalyst("Full-year results"))
        assert reporting_calendar_entries(catalyst("Trading update"))

    def test_an_earnings_release_is_not_a_catalyst(self) -> None:
        assert reporting_calendar_entries(catalyst("Fourth-quarter earnings release"))

    def test_a_foreign_issuer_form_is_not_a_catalyst(self) -> None:
        assert reporting_calendar_entries(catalyst("Annual report on Form 20-F"))

    def test_every_offending_row_is_named(self) -> None:
        """The refusal has to say which rows to drop, or the retry guesses."""
        content = {
            "catalysts": [
                {"label": "Q3 2026 Form 10-Q", "expected_timing": "Nov", "rationale": "x"},
                {"label": "Investor day", "expected_timing": "12 Nov 2026", "rationale": "x"},
                {"label": "FY2026 Form 10-K", "expected_timing": "Feb", "rationale": "x"},
            ]
        }

        found = reporting_calendar_entries(content)

        assert len(found) == 2
        assert any("10-Q" in problem for problem in found)
        assert any("10-K" in problem for problem in found)
        assert not any("Investor day" in problem for problem in found)


class TestADateInferredFromTheFilingRhythm:
    """The general defect the name check only catches by name: a date nobody disclosed."""

    def test_an_extrapolated_date_is_refused_whatever_the_row_is_called(self) -> None:
        found = reporting_calendar_entries(
            catalyst(
                "Next investor communication",
                rationale=(
                    "The company filed its Q1 2026 report on May 1, 2026 and its Q2 2026 "
                    "report on July 31, 2026, extrapolating this roughly three-month cadence."
                ),
            )
        )

        assert len(found) == 1
        assert "filing rhythm" in found[0]

    def test_a_habitual_filing_claim_is_refused(self) -> None:
        assert reporting_calendar_entries(
            catalyst("Board update", rationale="The company typically files in early February.")
        )

    def test_the_timing_field_is_read_as_well_as_the_rationale(self) -> None:
        """A writer moving the extrapolation into the timing field has not fixed it."""
        assert reporting_calendar_entries(
            catalyst("Board update", timing="extrapolated from the last four filings")
        )


class TestARealCatalystSurvives:
    """The half that matters most. Every one of these is a dated event a reader can act
    on, and a rule that refused any of them would have closed the section rather than
    improved it."""

    def test_an_announced_investor_day_survives(self) -> None:
        assert (
            reporting_calendar_entries(
                catalyst(
                    "Investor day",
                    timing="12 November 2026",
                    rationale="The company announced the date in its second-quarter release.",
                )
            )
            == []
        )

    def test_a_regulatory_deadline_survives(self) -> None:
        assert (
            reporting_calendar_entries(
                catalyst(
                    "Antitrust decision on the proposed acquisition",
                    timing="by 30 September 2026",
                    rationale="The regulator's statutory deadline, disclosed in the notice.",
                )
            )
            == []
        )

    def test_an_expected_completion_survives(self) -> None:
        """'Expected to complete in the first half' is an estimate, and a legitimate one:
        the rule is about dates inferred from filing frequency, not about estimation."""
        assert (
            reporting_calendar_entries(
                catalyst(
                    "Completion of the Iberia joint venture",
                    timing="expected to complete in the first half of 2027",
                    rationale="Management guided to first-half completion, subject to approval.",
                )
            )
            == []
        )

    def test_a_debt_maturity_survives(self) -> None:
        assert (
            reporting_calendar_entries(
                catalyst(
                    "Revolving credit facility matures",
                    timing="June 2027",
                    rationale="The facility's stated maturity, from the debt note.",
                )
            )
            == []
        )

    def test_a_disposal_clearing_the_comparatives_survives(self) -> None:
        """The one genuine item the live note did find, which must not be swept up with
        the calendar entries beside it."""
        assert (
            reporting_calendar_entries(
                catalyst(
                    "Europe Surface Transportation divestiture anniversaries",
                    timing="second quarter of 2027",
                    rationale="The disposal completed in May 2026, so comparatives clear then.",
                )
            )
            == []
        )


class TestWhatIsScanned:
    def test_an_empty_list_is_not_a_problem(self) -> None:
        """The honest answer when the evidence dates nothing, and it must cost nothing."""
        content = {"catalysts": [], "commentary": "No dated catalyst is disclosed."}

        assert reporting_calendar_entries(content) == []

    def test_a_section_with_no_catalyst_array_is_not_scanned(self) -> None:
        assert reporting_calendar_entries({"commentary": "Prose."}) == []

    def test_commentary_mentioning_the_next_filing_is_left_alone(self) -> None:
        """Context, not a listed catalyst. The rule scans the list a reader reads as
        catalysts, and refusing prose would refuse the sentence explaining the absence."""
        content = {
            "commentary": (
                "No dated catalyst is disclosed in the evidence; the next Form 10-Q would "
                "have to disclose one for that to change."
            ),
            "catalysts": [],
        }

        assert reporting_calendar_entries(content) == []

    def test_a_malformed_row_is_skipped_rather_than_raising(self) -> None:
        """The content is model output: this rule runs before the schema report is acted
        on, so it must survive a shape it did not expect."""
        assert reporting_calendar_entries({"catalysts": ["a bare string", None, 7]}) == []

    def test_a_catalysts_field_that_is_not_a_list_is_ignored(self) -> None:
        assert reporting_calendar_entries({"catalysts": "none identified"}) == []

    def test_an_unlabelled_calendar_row_is_still_named(self) -> None:
        content = {"catalysts": [{"expected_timing": "Nov", "rationale": "It typically files."}]}

        found = reporting_calendar_entries(content)

        assert len(found) == 1
        assert "unlabelled" in found[0]


class TestTheVocabularyIsNarrowOnPurpose:
    """What the rule deliberately does not catch, and why. Each of these was a candidate
    for the vocabulary and was left out because refusing it would cost more in real
    catalysts than it would save in calendar entries."""

    def test_a_bare_result_is_not_a_periodic_report(self) -> None:
        """'Results' earns its refusal from the period qualifier beside it. Without one it
        is an ordinary English word, and the strategic review it names is a real event."""
        assert reporting_calendar_entries(catalyst("Results of the strategic review")) == []

    def test_an_annual_general_meeting_survives(self) -> None:
        """Routine until a vote is contested, and then it is genuinely the catalyst."""
        assert reporting_calendar_entries(catalyst("Annual general meeting vote")) == []

    def test_a_current_report_survives(self) -> None:
        """A Form 8-K exists because something happened, which is the opposite of the
        periodic filings this rule refuses."""
        assert reporting_calendar_entries(catalyst("Form 8-K on the merger completing")) == []
