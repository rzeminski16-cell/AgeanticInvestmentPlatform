"""How a figure's name appears in a sentence, and whether that sentence denies it.

The vocabulary half of ADR 0125's negative-assertion scan, tested on its own because it is
pure and because the rules it encodes are all about erring towards saying no. The sentences
marked as the audit's are quoted from the reports in
``docs/plan/readiness-audit-2026-09/``: they are why the check exists and they are the
regression that matters.
"""

from __future__ import annotations

import pytest

from aer.core.figure_names import (
    MIN_PHRASE_WORDS,
    clauses,
    denial_span,
    denies,
    mentions,
    narrows,
    normalised,
    opens_with,
    periods_named,
    phrases_for,
)

# Quoted from the audit's own reports. Each one is a sentence the platform published while
# printing the figure it denies.
AUDIT_DENIALS: tuple[str, ...] = (
    "No discounted cash flow, cost of equity, weighted average cost of capital, terminal "
    "value, value per share or peer multiple sits on this record.",
    "Operating cash flow is not among the figures available here.",
    "Interest cover is not established by the figures available.",
    "No prior-period comparatives sit here to test durability.",
)


class TestPhrasesFor:
    def test_a_name_reads_as_its_own_words(self) -> None:
        assert phrases_for("value_per_share") == ("value per share",)
        assert phrases_for("operating_cash_flow") == ("operating cash flow",)

    def test_a_single_word_figure_has_no_phrase(self) -> None:
        """``revenue``, ``assets`` and ``beta`` are words the whole report is entitled to
        use, so no sentence can be read as denying them by name."""
        assert phrases_for("revenue") == ()
        assert phrases_for("assets") == ()
        assert phrases_for("beta") == ()
        assert MIN_PHRASE_WORDS == 2

    def test_an_alias_is_how_a_reader_writes_it(self) -> None:
        """``wacc`` is one word and would otherwise be unreachable; prose writes it out."""
        assert phrases_for("wacc") == ("weighted average cost of capital", "cost of capital")
        assert phrases_for("capex") == ("capital expenditure", "capital expenditures")

    def test_a_name_that_reads_as_nonsense_matches_nothing_rather_than_guessing(self) -> None:
        """The safe direction: a missed denial, never an invented one. ``pp_and_e_net``'s
        own spelling reads as "pp and e net", which no sentence a person wrote contains, so
        it is carried and never fires; only its alias can."""
        sentence = "Property, plant and equipment, net is not disclosed."
        matched = [phrase for phrase in phrases_for("pp_and_e_net") if mentions(sentence, phrase)]
        assert matched == ["property plant and equipment"]

    def test_the_list_is_stable_and_deduplicated(self) -> None:
        """``interest_cover``'s own spelling is also its first alias; one phrase, not two."""
        assert phrases_for("interest_cover") == ("interest cover", "interest coverage")


class TestMentions:
    def test_whole_words_only(self) -> None:
        assert mentions("Interest cover is 8.11x.", "interest cover")
        assert not mentions("Interest coverage is 8.11x.", "interest cover")

    def test_punctuation_is_not_part_of_a_name(self) -> None:
        for spelling in ("risk-free rate", "risk free rate", "Risk-Free Rate"):
            assert mentions(f"The {spelling} is 4.2%.", "risk free rate")

    def test_an_empty_phrase_matches_nothing(self) -> None:
        """A single-word figure yields no phrase, and a caller that passed one through
        anyway must not match every sentence in the report."""
        assert not mentions("Anything at all.", "")

    def test_normalisation_is_the_same_transform_on_both_sides(self) -> None:
        assert normalised("  Weighted-Average  Cost of Capital! ") == (
            "weighted average cost of capital"
        )


class TestDenies:
    @pytest.mark.parametrize("sentence", AUDIT_DENIALS)
    def test_the_sentences_the_platform_actually_published(self, sentence: str) -> None:
        assert denies(sentence)

    def test_no_proximity_window_would_survive_the_first_of_them(self) -> None:
        """Nineteen words of enumeration stand between msft1's "No" and its "sits", which
        is why the rule is an order and not a distance."""
        words = normalised(AUDIT_DENIALS[0]).split()
        assert words.index("sits") - words.index("no") > 15

    @pytest.mark.parametrize(
        "sentence",
        [
            "Free cash flow is not disclosed.",
            "Interest cover is unavailable.",
            "The evidence is silent on interest cover.",
            "There is insufficient evidence for the value per share.",
            "Operating cash flow cannot be computed from what was filed.",
        ],
    )
    def test_the_ordinary_phrasings(self, sentence: str) -> None:
        assert denies(sentence)

    @pytest.mark.parametrize(
        "sentence",
        [
            "The value per share is 485.29.",
            "Interest cover of 8.11x is not a cause for concern.",
            "Revenue grew 12% and the operating margin widened.",
            "We do not expect the interest cover to fall below 5x.",
            "Free cash flow rose for the third year running.",
        ],
    )
    def test_a_sentence_about_the_company_is_not_a_denial(self, sentence: str) -> None:
        """Including one with a negator in it. What makes a denial is the *evidence* word
        after it — "concern" and "expect" are about the business, not about the record."""
        assert not denies(sentence)

    def test_without_is_not_a_negator(self) -> None:
        """Quoted from the azn2 record, where the dry run read it as a denial of the cost of
        capital the same sentence goes on to quote."""
        assert not denies(
            "One external reference point is available without leaving primary disclosure"
        )

    def test_a_sentence_that_genuinely_needs_without_carries_another_negator(self) -> None:
        assert denies("Without a disclosed revenue figure the margin cannot be computed.")

    def test_a_negator_after_the_evidence_word_is_not_a_denial(self) -> None:
        """Order is the rule. "The figures are available" followed by a negation about
        something else is not a statement that the platform lacks them."""
        assert not denies("The figures are available, though the trend is not encouraging.")


class TestPeriodsNamed:
    def test_a_fiscal_year(self) -> None:
        assert periods_named("Free cash flow for FY2021 is not disclosed.") == frozenset({"FY2021"})

    def test_a_quarter_is_read_once_rather_than_as_its_parts(self) -> None:
        assert periods_named("Nothing for Q3 FY2025.") == frozenset({"Q3 FY2025"})
        assert periods_named("Nothing for Q3 2025.") == frozenset({"Q3 FY2025"})

    def test_a_bare_year_is_that_fiscal_year(self) -> None:
        """A sentence saying "the 2021 accounts do not disclose free cash flow" is about
        the same period a figure labelled FY2021 is about, and reading the two as different
        would let the commonest phrasing escape the period bound entirely."""
        assert periods_named("The 2021 accounts do not disclose it.") == frozenset({"FY2021"})

    def test_several(self) -> None:
        assert periods_named("Neither 2024 nor FY2025 is reported.") == frozenset(
            {"FY2024", "FY2025"}
        )

    def test_no_period_named(self) -> None:
        """An unqualified denial, which the scan reads against every period the report
        publishes — the msft1 case."""
        assert periods_named("No value per share sits on this record.") == frozenset()

    def test_a_two_digit_year_is_deliberately_unread(self) -> None:
        """Ambiguous against a stored label, and unreading it costs only a denial that goes
        unbound rather than one bound to the wrong year."""
        assert periods_named("FY25 is not disclosed.") == frozenset()

    def test_a_figure_is_not_a_year(self) -> None:
        assert periods_named("Revenue was 1,205 million.") == frozenset()


class TestClauses:
    def test_an_enumeration_stays_one_clause(self) -> None:
        """The commas in msft1's denial separate the figures it denies, not clauses. Split
        there and the denial and the figure it denies land on opposite sides."""
        assert clauses(AUDIT_DENIALS[0]) == [AUDIT_DENIALS[0]]

    def test_a_coordinating_comma_is_a_boundary(self) -> None:
        """Quoted from the msft2 record: one clause names short-term investments, the next
        denies a netted leverage figure, and read whole the sentence reports a
        contradiction that is not there."""
        parts = clauses(
            "Cash of $20.9 billion and short-term investments of $55.9 billion sit against "
            "those borrowings, and no netted leverage figure is recorded here."
        )
        assert len(parts) == 2
        assert mentions(parts[0], "short term investments")
        assert not mentions(parts[1], "short term investments")
        assert denies(parts[1])
        assert not denies(parts[0])

    def test_a_semicolon_is_a_boundary(self) -> None:
        parts = clauses("Gross margin was 68.8 percent; the segment split is not disclosed.")
        assert len(parts) == 2
        assert not denies(parts[0])

    def test_a_dash_is_a_boundary(self) -> None:
        assert len(clauses("The margin held — the segment split is not disclosed.")) == 2

    def test_empty_pieces_are_dropped(self) -> None:
        assert clauses("   ") == []


class TestWhatTheNegationReaches:
    """Which noun a negator governs — the last question the scan had to answer, and the one
    the re-seeded corpus asked three times with the same sentence."""

    WACC_NOTE = (
        "Book equity was used as the equity weight because no market capitalisation was available"
    )

    def test_the_span_stops_at_the_word_about_the_record(self) -> None:
        assert denial_span(self.WACC_NOTE) == "no market capitalisation was available"

    def test_a_figure_before_the_negator_is_not_in_the_span(self) -> None:
        """`aer.calc.wacc` writes this into every run with no market price, and it was read
        as a denial of the equity weight on three of the four re-seeded runs."""
        assert not mentions(denial_span(self.WACC_NOTE), "equity weight")
        assert not opens_with(self.WACC_NOTE, "equity weight")

    def test_a_figure_after_the_word_about_the_record_is_not_denied(self) -> None:
        clause = (
            "where no cloud margin is disclosed, the consolidated operating income "
            "line is the fallback"
        )
        assert not mentions(denial_span(clause), "operating income")
        assert not opens_with(clause, "operating income")

    def test_an_enumeration_inside_the_span_is_denied(self) -> None:
        """msft1's sentence again: nineteen words between the "No" and the "sits", and the
        figure it denies is every one of them."""
        assert mentions(denial_span(AUDIT_DENIALS[0]), "value per share")

    def test_a_self_negating_word_carries_the_span_to_the_end(self) -> None:
        """ "Silent" negates and speaks about the record in one word, so its subject follows
        it and there is nothing in between to stop at."""
        assert mentions(denial_span("The evidence is silent on interest cover"), "interest cover")

    def test_the_subject_counts_even_before_the_negator(self) -> None:
        assert opens_with(
            "Operating cash flow is not among the figures available here", "operating cash flow"
        )

    def test_a_determiner_does_not_displace_the_subject(self) -> None:
        for opening in ("A", "The", "Its"):
            assert opens_with(f"{opening} value per share is not available here", "value per share")

    def test_nothing_negating_has_no_span(self) -> None:
        assert denial_span("The value per share is 485.29") == ""


class TestNarrows:
    @pytest.mark.parametrize(
        "clause",
        [
            "No segment-level revenue, cost or capital expenditure figures were available",
            "quarterly capital expenditure cadence within fiscal 2026 is not available",
            "no geographic breakdown of operating income is disclosed",
        ],
    )
    def test_a_part_of_a_figure_is_not_the_figure(self, clause: str) -> None:
        assert narrows(clause)

    @pytest.mark.parametrize("clause", AUDIT_DENIALS)
    def test_the_denials_the_check_exists_for_are_not_narrowed(self, clause: str) -> None:
        assert not narrows(clause)
