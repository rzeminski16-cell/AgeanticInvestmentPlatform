"""The sentence a page leads with, and the four ways it lies about the page beneath it.

Composition is testable and truth is not. That a verdict says "two items are waiting for your
decision" when two rows are waiting is checkable; that waiting is the right thing to call them
is a reading, and it is in the by-hand sheet rather than here.

**The tests worth having are the ones about absence.** A zero rendered as a count, a breakdown
that quietly does not sum, an empty verdict on a page designed around leading with one, and —
the one this whole path exists for — a count taken over part of the estate presented as the
all-clear. Each of those reads as a well-formed sentence, which is exactly why none of them
would be noticed by looking at the screen.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from aer.web.overview.attention import Attention, Severity
from aer.web.overview.verdict import NOTHING_WAITING, overview_verdict
from aer.web.verdict import (
    Authored,
    Count,
    Part,
    Verdict,
    VerdictError,
    sentence,
    tally,
)
from aer.web.vocabulary import Tone

ITEMS = "item is waiting for your decision", "items are waiting for your decision"


def _item(*, key: str = "k", severity: Severity = Severity.BLOCKED, gap: bool = False) -> Attention:
    return Attention(
        key=key,
        tool="research",
        severity=severity,
        title="Something",
        detail="Something happened",
        href="/somewhere",
        feed_is_incomplete=gap,
    )


class TestAZeroIsNotACount:
    def test_an_empty_clause_is_dropped(self) -> None:
        """ "0 red-team challenges are available to read" is noise wearing a number."""
        verdict = sentence(
            [Count(0, "challenge is available", "challenges are available"), Count(2, *ITEMS)],
            when_none="nothing is waiting for you",
            tone=Tone.WARNING,
        )
        assert verdict.composed == "Two items are waiting for your decision."

    def test_when_every_clause_drops_the_fallback_is_used(self) -> None:
        verdict = sentence(
            [Count(0, *ITEMS)], when_none="nothing is waiting for you", tone=Tone.SUCCESS
        )
        assert verdict.composed == "Nothing is waiting for you."

    def test_a_verdict_is_never_empty(self) -> None:
        """The page is designed around a leading sentence. A blank where the answer goes is
        not a quieter page, it is a page with the answer missing."""
        with pytest.raises(VerdictError, match="no composed half"):
            Verdict(composed="   ", tone=Tone.INFO)


class TestTheSentenceIsWrittenOnce:
    def test_the_first_clause_takes_the_capital(self) -> None:
        """Clauses are written lowercase so they can appear in any position. Deciding the
        capital in one place is what lets the same phrase lead one page and follow a
        semicolon on another."""
        verdict = sentence([Count(1, *ITEMS)], when_none="nothing", tone=Tone.WARNING)
        assert verdict.composed.startswith("One item")

    def test_clauses_are_joined_with_semicolons(self) -> None:
        verdict = sentence(
            [Count(2, *ITEMS), Count(1, "item needs diagnosis", "items need diagnosis")],
            when_none="nothing",
            tone=Tone.FAILURE,
        )
        assert verdict.composed == (
            "Two items are waiting for your decision; one item needs diagnosis."
        )

    def test_an_existing_full_stop_is_not_doubled(self) -> None:
        verdict = sentence(["the draft is complete."], when_none="nothing", tone=Tone.SUCCESS)
        assert verdict.composed == "The draft is complete."

    def test_a_question_keeps_its_mark(self) -> None:
        verdict = sentence(["is this ready to approve?"], when_none="nothing", tone=Tone.INFO)
        assert verdict.composed.endswith("?")

    def test_a_plain_state_needs_no_count(self) -> None:
        """Half a verdict is a state rather than a number — "approved on 24 August"."""
        verdict = sentence(
            ["approved on 24 August", Count(0, *ITEMS)], when_none="nothing", tone=Tone.SUCCESS
        )
        assert verdict.composed == "Approved on 24 August."


class TestSmallNumbersAreWords:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [(1, "one item"), (2, "two items"), (9, "nine items"), (10, "10 items"), (12, "12 items")],
    )
    def test_up_to_nine_in_a_sentence(self, n: int, expected: str) -> None:
        """A sentence beginning "2 items" is set in the wrong register, and the design
        handoff writes "Two decisions are stopping research; one run needs diagnosis"."""
        assert Count(n, *ITEMS).worded().startswith(expected)

    def test_a_large_number_is_still_readable(self) -> None:
        assert Count(1200, *ITEMS).worded().startswith("1,200 items")

    def test_a_tally_keeps_its_numerals(self) -> None:
        """A tally is a column of figures written on one line and reads as one."""
        assert Count(2, "claim", "claims").numbered() == "2 claims"


class TestTheCallerSuppliesBothInflections:
    def test_one_and_many_are_chosen_not_guessed(self) -> None:
        """Inflecting a noun in code is how "1 companys" reaches a screen — and the phrase
        that inflects is not only the noun, since the verb moves with it."""
        assert Count(1, *ITEMS).phrase == "item is waiting for your decision"
        assert Count(2, *ITEMS).phrase == "items are waiting for your decision"

    def test_zero_takes_the_plural(self) -> None:
        assert Count(0, *ITEMS).phrase.startswith("items")

    def test_a_missing_inflection_is_refused(self) -> None:
        with pytest.raises(VerdictError, match="neither is guessed"):
            Count(1, "item is waiting", "")

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(VerdictError, match="not a number of things"):
            Count(-1, *ITEMS)


class TestABreakdownAccountsForItsTotal:
    """A tally that does not sum tells the reader the rest is nothing.

    The usual cause is a state with no part of its own — a mapping that covers three of four
    verification outcomes renders a sentence that is well-formed, plausible, and short by one.
    """

    def test_the_ordinary_case(self) -> None:
        verdict = tally(
            Count(42, "claim", "claims"),
            [Part(38, "confirmed"), Part(3, "unconfirmed"), Part(1, "failed")],
            when_none="no claims yet",
            tone=Tone.INFO,
        )
        assert verdict.composed == "42 claims · 38 confirmed · 3 unconfirmed · 1 failed"

    def test_parts_that_do_not_sum_are_refused(self) -> None:
        with pytest.raises(VerdictError, match="accounts for 41"):
            tally(
                Count(42, "claim", "claims"),
                [Part(38, "confirmed"), Part(3, "unconfirmed")],
                when_none="no claims yet",
                tone=Tone.INFO,
            )

    def test_an_empty_part_is_dropped_but_still_counted(self) -> None:
        """Dropping the zero is presentation; the sum check runs on what was supplied, so a
        state that happens to be empty today does not excuse a mapping that is missing one."""
        verdict = tally(
            Count(18, "source acquired", "sources acquired"),
            [Part(18, "admissible"), Part(0, "quarantined")],
            when_none="nothing acquired",
            tone=Tone.SUCCESS,
        )
        assert verdict.composed == "18 sources acquired · 18 admissible"

    def test_claiming_nothing_about_the_breakdown_is_allowed(self) -> None:
        verdict = tally(Count(42, "claim", "claims"), when_none="no claims yet", tone=Tone.INFO)
        assert verdict.composed == "42 claims"

    def test_a_total_of_nothing_uses_its_own_words(self) -> None:
        verdict = tally(Count(0, "claim", "claims"), when_none="no claims yet", tone=Tone.INFO)
        assert verdict.composed == "no claims yet"


class TestAPartialCountIsNeverTheAllClear:
    """The rule the whole path exists for.

    An empty feed is a claim — *nothing is waiting for you* — and it is exactly the claim a
    broken query makes by accident. `attention.items_for` refuses to make it silently; this is
    where it would otherwise get made in words.
    """

    def test_an_incomplete_verdict_cannot_be_a_success(self) -> None:
        with pytest.raises(VerdictError, match="must not be presented as the all-clear"):
            Verdict(composed="Nothing is waiting for you.", tone=Tone.SUCCESS, is_complete=False)

    def test_an_incomplete_verdict_must_say_what_is_missing(self) -> None:
        """Silence there reads as a complete count, which is precisely what it is not."""
        with pytest.raises(VerdictError, match="must say what is missing"):
            sentence([Count(1, *ITEMS)], when_none="nothing", tone=Tone.INFO, is_complete=False)

    def test_the_gap_is_appended_to_the_sentence(self) -> None:
        verdict = sentence(
            [Count(1, *ITEMS)],
            when_none="nothing",
            tone=Tone.INFO,
            is_complete=False,
            gap="one tool could not be asked",
        )
        assert verdict.composed == (
            "One item is waiting for your decision; one tool could not be asked."
        )

    def test_the_gap_stands_alone_when_nothing_was_counted(self) -> None:
        verdict = sentence(
            [Count(0, *ITEMS)],
            when_none="nothing here has been counted",
            tone=Tone.INFO,
            is_complete=False,
            gap="the record could not be read",
        )
        assert verdict.composed.startswith("Nothing here has been counted;")
        assert "could not be read" in verdict.composed


class TestTheAuthoredHalfIsNotEvidence:
    """ADR 0087: no claim may name it, no citation may resolve to it.

    Enforced the way ADR 0074 names — capability as a type with no field for the forbidden
    thing. A missing column is the only rule a later prompt, a later template and a later
    person under time pressure are all equally unable to argue with.
    """

    def test_it_carries_a_sentence_and_a_tone_and_nothing_else(self) -> None:
        """Adding a field here means deleting this test, which is the point of it."""
        assert {f.name for f in fields(Authored)} == {"sentence", "tone"}

    def test_the_module_names_no_evidence_type_in_its_code(self) -> None:
        """The structural half: there is no constructor to reach for, because nothing in the
        module refers to one. A verdict that could be cited would launder a model's opinion
        into the evidence chain by way of a nice sentence at the top of a page.

        Read from the syntax tree rather than the text, so the prose above is free to say
        what the rule is — a check a docstring can trip is a check that gets deleted.
        """
        tree = ast.parse(Path("src/aer/web/verdict.py").read_text(encoding="utf-8"))
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Name | ast.Attribute)
        } | {
            alias.asname or alias.name.rpartition(".")[2]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        forbidden = sorted(used & {"SourceRef", "SourceKind", "SourceTable", "Citation", "Claim"})
        assert not forbidden, (
            f"the verdict module refers to {forbidden}. The authored half is interpretation, "
            "and the distance between that and a fact is the whole platform."
        )

    def test_an_empty_authored_sentence_is_refused(self) -> None:
        with pytest.raises(VerdictError, match="nobody used"):
            Authored(sentence="  ", tone=Tone.INFO)

    def test_it_is_never_the_only_thing_on_the_page(self) -> None:
        """A run that failed before its verdict step has no authored half and renders its
        composed one. The reverse — an interpretation with no counts beside it — is the shape
        the ADR forbids, and `Verdict` will not hold it."""
        with pytest.raises(VerdictError, match="no composed half"):
            Verdict(composed="", tone=Tone.INFO, authored=Authored("It reads well.", Tone.INFO))


class TestTheFrontDoorCountsWhatItLists:
    def test_an_ordinary_feed(self) -> None:
        verdict = overview_verdict(
            [
                _item(key="a", severity=Severity.BLOCKED),
                _item(key="b", severity=Severity.BLOCKED),
                _item(key="c", severity=Severity.BROKEN),
            ]
        )
        assert verdict.composed == (
            "Two items are waiting for your decision; one item needs diagnosis."
        )

    def test_a_severity_with_nothing_in_it_says_nothing(self) -> None:
        verdict = overview_verdict([_item(severity=Severity.IDLE)])
        assert verdict.composed == "One item has not been started."

    def test_an_empty_feed_that_was_read_is_the_all_clear(self) -> None:
        verdict = overview_verdict([])
        assert verdict.composed == "Nothing is waiting for you."
        assert verdict.tone is Tone.SUCCESS
        assert verdict.is_complete

    def test_the_clauses_are_said_in_the_order_the_page_lists_them(self) -> None:
        """A sentence leading with the idle work over a page listing the blocked work first
        reads as being about a different screen."""
        verdict = overview_verdict(
            [
                _item(key="i", severity=Severity.IDLE),
                _item(key="b", severity=Severity.BROKEN),
                _item(key="x", severity=Severity.BLOCKED),
            ]
        )
        assert verdict.composed.index("waiting for your decision") < verdict.composed.index(
            "needs diagnosis"
        )
        assert verdict.composed.index("needs diagnosis") < verdict.composed.index(
            "has not been started"
        )


class TestTheFrontDoorsTone:
    """Ordered by what has gone wrong, which is deliberately not the feed's order.

    The feed leads with blocked work because a stopped run resumes the moment somebody
    decides. The tone leads with the fault, because a fault is louder however you sort a list.
    """

    def test_a_fault_is_louder_than_a_decision(self) -> None:
        verdict = overview_verdict(
            [_item(key="x", severity=Severity.BLOCKED), _item(key="b", severity=Severity.BROKEN)]
        )
        assert verdict.tone is Tone.FAILURE

    def test_waiting_on_a_person_is_not_a_fault(self) -> None:
        assert overview_verdict([_item(severity=Severity.BLOCKED)]).tone is Tone.WARNING

    def test_unstarted_work_is_neither(self) -> None:
        assert overview_verdict([_item(severity=Severity.IDLE)]).tone is Tone.INFO


class TestTheFrontDoorAdmitsWhatItCouldNotAsk:
    def test_a_failed_provider_makes_the_verdict_incomplete(self) -> None:
        verdict = overview_verdict([_item(key="research.unavailable", gap=True)])
        assert not verdict.is_complete
        assert "not the whole estate" in verdict.composed

    def test_a_failed_provider_is_never_the_all_clear(self) -> None:
        """The item itself is `BROKEN`, so the tone would be loud anyway. The flag is what
        makes that true when the failure is the *only* thing in the feed and a future severity
        happens to be quieter."""
        assert overview_verdict([_item(key="x.unavailable", gap=True)]).tone is not Tone.SUCCESS

    def test_a_feed_that_was_never_read_is_not_an_empty_feed(self) -> None:
        """The main menu renders with the database down — that is its whole design. An empty
        tuple there means "nothing was asked", and the obvious sentence for it is the one
        wrong answer that looks exactly like the right one."""
        verdict = overview_verdict([], gathered=False)
        assert verdict.tone is not Tone.SUCCESS
        assert not verdict.is_complete
        assert NOTHING_WAITING not in verdict.composed
        assert "could not be read" in verdict.composed

    def test_a_feed_that_was_read_and_is_empty_still_says_so_plainly(self) -> None:
        """The complement: the guard must not make the ordinary quiet day read as a problem."""
        assert overview_verdict([], gathered=True).composed == "Nothing is waiting for you."


class TestAClauseWorksInAnyPosition:
    """The property the lowercase-and-unpunctuated convention exists to give.

    A clause written as a whole sentence still has to read correctly in the middle of one, or
    the convention is only true of the last position and the first caller to break it produces
    "The draft is complete.; two items are waiting".
    """

    def test_a_punctuated_clause_joins_cleanly(self) -> None:
        verdict = sentence(
            ["the draft is complete.", Count(2, *ITEMS)], when_none="nothing", tone=Tone.WARNING
        )
        assert verdict.composed == (
            "The draft is complete; two items are waiting for your decision."
        )

    def test_the_same_clause_alone_is_a_sentence(self) -> None:
        assert (
            sentence(["the draft is complete."], when_none="nothing", tone=Tone.SUCCESS).composed
            == "The draft is complete."
        )


class TestTheFrontDoorDoesNotArgueWithItself:
    """An empty feed is two different facts, and the verdict is the first line of both pages.

    Caught up and never started produce the same empty work list and want opposite sentences.
    Shipping only one of them put "Nothing is waiting for you." two inches above a panel headed
    "Start with two things" — the front door contradicting itself in its own opening line, on
    the one screen a new operator sees first. Found by looking at the page, not by a test.
    """

    def test_a_new_operator_is_not_told_they_are_up_to_date(self) -> None:
        verdict = overview_verdict([], first_run=True)

        assert NOTHING_WAITING not in verdict.composed
        assert "commissioned" in verdict.composed

    def test_and_is_not_congratulated_for_it(self) -> None:
        """Success green over "you have not started" is the platform congratulating somebody
        for not using it."""
        assert overview_verdict([], first_run=True).tone is not Tone.SUCCESS

    def test_a_returning_operator_still_gets_the_all_clear(self) -> None:
        """The complement: the guard must not turn an ordinary quiet day into a to-do list."""
        verdict = overview_verdict([], first_run=False)

        assert verdict.composed == "Nothing is waiting for you."
        assert verdict.tone is Tone.SUCCESS

    def test_a_new_operator_with_work_waiting_reads_as_normal(self) -> None:
        """`first_run` only decides the *empty* sentence. Somebody who wrote a request and
        started it has work waiting like anybody else, and the flag must not reword that."""
        verdict = overview_verdict([_item(severity=Severity.BLOCKED)], first_run=True)

        assert verdict.composed == "One item is waiting for your decision."
