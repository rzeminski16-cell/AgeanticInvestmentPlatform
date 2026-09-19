"""The blinding dry run, over September's own texts.

Row 7 of the gate before a measurement round (`docs/V1.0_Alpha/11-testing-strategy.md` §5),
and §3.5's *"identically-shaped A/B paths … the blinding test runs over September's texts and
asserts that the platform-isms are gone"*.

**What this found, before it asserted anything.** September's judges were told they were
reading blind. They were not: every tell in :data:`audit.judges.blinding.TELLS` separates the
two sides of the recorded corpus completely — 316 footnote markers against none, 451
bracketed sources against none — and all three console notes open with the assistant's own
narration, twice of them running straight into the title with no newline. The first
assertion below is that separation, measured rather than assumed, because a blinding test
whose tells did not actually distinguish the sides would pass on documents nobody had
blinded.

Offline and free. The judge reads that produce an identity guess are billable and live
elsewhere, marked `live_llm` like every other spending path.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from audit.judges.blinding import (
    BASELINE,
    LEFT_STANDING,
    PLATFORM,
    TELLS,
    assign,
    hit_rate,
    neutralise,
    tells_in,
)

ROOT: Final = Path(__file__).resolve().parents[1]
RECORDED: Final = ROOT / "docs" / "plan" / "readiness-audit-2026-09"

# The three pairs the September panel compared. Each is one subject's platform report
# against the console note written to the same commission.
SUBJECTS: Final = ("msft1", "azn", "mtb")


def _pair(subject: str) -> dict[str, str]:
    return {
        PLATFORM: (RECORDED / subject / "report.md").read_text(),
        BASELINE: (RECORDED / "baseline" / subject / "note.md").read_text(),
    }


@pytest.fixture(scope="module")
def pairs() -> dict[str, dict[str, str]]:
    return {subject: _pair(subject) for subject in SUBJECTS}


class TestTheTellsAreReal:
    """A tell is a measured claim about the corpus, not a style opinion."""

    @pytest.mark.parametrize("tell", TELLS, ids=[tell.name for tell in TELLS])
    def test_each_tell_fires_on_the_side_it_names_and_never_on_the_other(
        self, tell: object, pairs: dict[str, dict[str, str]]
    ) -> None:
        assert hasattr(tell, "betrays")
        betrays = tell.betrays  # type: ignore[attr-defined]
        other = BASELINE if betrays == PLATFORM else PLATFORM

        fires_on, silent_on = 0, 0
        for texts in pairs.values():
            if tell.pattern.search(texts[betrays]):  # type: ignore[attr-defined]
                fires_on += 1
            if not tell.pattern.search(texts[other]):  # type: ignore[attr-defined]
                silent_on += 1

        assert fires_on, f"{tell.name!r} never fires on the {betrays} side it claims to name"
        assert silent_on == len(pairs), (
            f"{tell.name!r} also fires on the {other} side, so it distinguishes nothing "
            "and its presence in a blinded document would be harmless"
        )

    def test_september_was_not_blind(self, pairs: dict[str, dict[str, str]]) -> None:
        """The finding this whole row rests on, asserted so it cannot be forgotten.

        Every recorded document carries at least one tell, so a judge reading any of them
        could name its author without reading the argument. ISSUE 2's September number
        carries that caveat whatever this round finds.
        """
        for subject, texts in pairs.items():
            for side, text in texts.items():
                assert tells_in(text), (
                    f"{subject}/{side} carries no tell, which would mean September's "
                    "reads were blind after all and this row is measuring nothing"
                )


class TestNeutralisingLeavesNothingToGoOn:
    def test_what_survives_on_each_side_is_exactly_what_is_recorded(
        self, pairs: dict[str, dict[str, str]]
    ) -> None:
        """Pinned to :data:`LEFT_STANDING`, which is stronger than asserting none.

        This read `assert not left` until the first live guess, where three judges out of
        three named the machinery under the figures and the list had never heard of it. Two
        reports are still separable afterwards, on full stored precision, and the module
        will not close that by rewriting a digit — so the honest assertion is the *set*,
        not its emptiness: a new residue fails, and so does one of these quietly clearing
        without the record moving.
        """
        for subject, texts in pairs.items():
            for side, text in texts.items():
                left = frozenset(tell.name for tell in tells_in(neutralise(text)))
                recorded = (
                    LEFT_STANDING.get(subject, frozenset()) if side == PLATFORM else frozenset()
                )
                assert left == recorded, f"{subject}/{side} residue is {sorted(left)}"

    def test_the_residue_is_only_ever_the_one_the_neutraliser_refuses(self) -> None:
        """A guard on the record itself, not on the documents.

        `LEFT_STANDING` is an exception list, and an exception list nobody bounds grows
        until it is the rule. The one entry it may hold is the one whose removal would mean
        rewriting a figure; anything else has a remedy and must use it.
        """
        for names in LEFT_STANDING.values():
            assert names == frozenset({"a Decimal printed as it is stored"})

    def test_a_source_address_is_not_a_code_identifier(self) -> None:
        """`api_token` in a vendor's query string cost two citations before this existed.

        The identifier rule is about the author's prose. A URL was copied from a source by
        whichever author copied it, and rewriting one breaks the link a sceptic follows.
        """
        url = "https://eodhd.com/api/eod/AZN.US?api_token=REDACTED&fmt=json&period=d"
        text = f"# T\n\nPrices came from [the vendor]({url}), keyed by citation_accuracy.\n"

        blinded = neutralise(text)

        assert url in blinded
        assert "citation accuracy" in blinded
        assert not tells_in(blinded)

    def test_the_console_narration_goes_even_when_it_runs_into_the_title(self) -> None:
        """Two of the three notes have no newline between the working and the heading.

        Anchoring the title to a line start found the *next* heading instead and left the
        narration in place — the bug that made the first measurement read clean on the
        pattern while the tell was still there.
        """
        run_on = (
            "Let me compute the analytics from the figures I read.# Acme Corp — Initiation\n\nBody."
        )

        blinded = neutralise(run_on)

        assert blinded.startswith("# Acme Corp")
        assert "Let me compute" not in blinded

    def test_the_argument_survives(self, pairs: dict[str, dict[str, str]]) -> None:
        """Blinding the presentation, not the thing being judged.

        A neutraliser that removed enough would pass the assertion above by deleting the
        document. So: the figures, the headings and the reference markers are all still
        there, and the text is still most of its own length.
        """
        for subject, texts in pairs.items():
            for side, text in texts.items():
                blinded = neutralise(text)
                assert len(blinded) > len(text) * 0.8, f"{subject}/{side} lost a fifth of itself"
                assert blinded.count("\n## ") >= 5, f"{subject}/{side} lost its sections"
                assert "http" in blinded, f"{subject}/{side} lost the sources behind its claims"

    def test_both_sides_end_up_with_one_marker_style(
        self, pairs: dict[str, dict[str, str]]
    ) -> None:
        """The two schemes renumber into one sequence from one, so neither the style nor
        the starting number says which system wrote it."""
        for subject, texts in pairs.items():
            for side, text in texts.items():
                blinded = neutralise(text)
                assert "[^" not in blinded, f"{subject}/{side} kept a footnote marker"
                assert not re.search(r"\[S\d+\]", blinded), f"{subject}/{side} kept a source marker"
                # Renumbered from one *where there was anything to renumber*. The AZN
                # console note cites through Markdown links and a numbered table rather
                # than inline markers, so requiring a `[1]` of every document would fail
                # it for a citation style it never used.
                if re.search(r"\[\^\d+\]|\[S\d+\]", text):
                    assert "[1]" in blinded, f"{subject}/{side} does not start its markers at one"


class TestTheAssignmentIsSeededAndBalanced:
    def test_the_same_seed_deals_the_same_sides(self) -> None:
        once = assign(seed=20260919, subjects=["azn", "msft"])
        again = assign(seed=20260919, subjects=["msft", "azn"])

        assert once == again, "the deal depends on the order the subjects were named"

    def test_a_different_seed_can_deal_differently(self) -> None:
        """Not *must*: two seeds agreeing on one pair is a coin landing the same way
        twice. Over a spread of seeds both deals have to appear, or the seed is doing
        nothing."""
        deals = {
            tuple(row.a for row in assign(seed=seed, subjects=["azn", "msft"]))
            for seed in range(40)
        }

        assert len(deals) > 1, "every seed deals the same sides, so the seed is decoration"

    def test_the_platform_takes_the_a_slot_exactly_half_the_time(self) -> None:
        """Balance, not chance. A judge with an untested preference for whichever document
        it reads first would otherwise be preferring a *system* on every comparison of a
        round where the coin happened to land twice the same way."""
        for seed in (1, 20260919, 999_983):
            deal = assign(seed=seed, subjects=["azn", "msft", "mtb", "tsco"])
            first = [row.a for row in deal]
            assert first.count(PLATFORM) == 2, f"seed {seed} dealt {first}"

    def test_the_identity_is_recorded_but_is_not_the_document(self) -> None:
        (azn, _) = assign(seed=20260919, subjects=["azn", "msft"])

        assert set(azn.identity()) == {"A", "B"}
        assert set(azn.identity().values()) == {PLATFORM, BASELINE}


class TestTheHitRateIsHonestAboutWhatItSaw:
    def test_chance_reads_as_a_half(self) -> None:
        guesses = [
            {"guessed": "A", "platform_was": "A"},
            {"guessed": "A", "platform_was": "B"},
        ]

        assert hit_rate(guesses) == Decimal("0.5")

    def test_a_panel_that_always_knows_reads_as_one(self) -> None:
        guesses = [{"guessed": "B", "platform_was": "B"} for _ in range(6)]

        assert hit_rate(guesses) == Decimal(1)

    def test_asking_nobody_is_zero_and_the_caller_has_to_notice(self) -> None:
        """A round that forgot to ask would otherwise read as perfect blinding, which is
        the one wrong answer this number could give."""
        assert hit_rate([]) == Decimal(0)
