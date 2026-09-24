"""The identity guess, assembled and scored offline — everything except the call itself.

`audit/judges/panel.py` is the one billable half of the panel, and the expensive way to find
a mistake in it is nine reads into a round. So the prompt is built here from the recorded
texts, the assignment is read from the committed pre-registration rather than a constant, and
the scoring is exercised over invented answers, all with no client and no key.

**What these assert that is worth asserting.** That the judge is shown the *blinded* text and
not the recorded one — a prompt built from `report.md` would produce a hit rate of 1.0 and a
confident conclusion about the wrong documents. That the seed is the pre-registration's. That
A and B carry different sides. And that the round's arithmetic survives its own failures: a
reply nobody could parse must not count as a miss, because a rate taken over questions rather
than over answers reports a failed call as evidence the blinding held.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest

from audit.judges.blinding import (
    BASELINE,
    LEFT_STANDING,
    PLATFORM,
    TELLS,
    assign,
    hit_rate,
    tells_in,
)
from audit.judges.panel import (
    COUNTED,
    LIKE_FOR_LIKE,
    PHASE_5_ROUND,
    PHASE_7_ROUND,
    PRE_REGISTRATION,
    SUBJECTS,
    Comparison,
    Guess,
    Round,
    _answer_of,
    _blinded,
    _by_confidence,
    _pair_documents,
    _prompt,
    _seed,
    scored_by_set,
    verdict_round_reading,
)
from audit.judges.rubric import LENS_BRIEFS, LENSES, VERDICT_DIMENSIONS

ROOT: Final = Path(__file__).resolve().parents[1]
RECORDED: Final = ROOT / "docs" / "plan" / "readiness-audit-2026-09"


@pytest.fixture(scope="module")
def assignments() -> tuple[Any, ...]:
    return assign(seed=_seed(), subjects=SUBJECTS)


class TestWhatTheJudgeIsShown:
    def test_the_seed_is_the_pre_registrations(self) -> None:
        """Not a constant in the module: the committed file is the half that is hashed."""
        recorded = json.loads(PRE_REGISTRATION.read_text())
        assert _seed() == int(recorded["blinding"]["assignment_seed"])

    @pytest.mark.parametrize("subject", SUBJECTS)
    def test_both_sides_are_blinded_before_the_prompt_is_built(self, subject: str) -> None:
        """Whatever reaches the judge carries only the residue the record admits to."""
        documents = _blinded(subject)
        assert set(documents) == {PLATFORM, BASELINE}
        for side, text in documents.items():
            fired = frozenset(one.name for one in tells_in(text))
            recorded = LEFT_STANDING.get(subject, frozenset()) if side == PLATFORM else frozenset()
            assert fired == recorded, f"{subject}/{side} carries {sorted(fired)}"

    @pytest.mark.parametrize("subject", SUBJECTS)
    def test_the_recorded_text_would_have_failed_that(self, subject: str) -> None:
        """The negative control: without `neutralise` the pair is trivially separable.

        A blinding test that only ever sees blinded text cannot tell a working neutraliser
        from a broken tell list.
        """
        raw = (RECORDED / subject / "report.md").read_text()
        assert tells_in(raw), "the recorded platform report carries no tell at all"

    def test_the_prompt_carries_both_documents_and_the_lens(
        self, assignments: tuple[Any, ...]
    ) -> None:
        one = assignments[0]
        documents = _blinded(one.subject)
        built = _prompt(lens="sceptic", assignment=one, documents=documents)
        assert "## Document A" in built
        assert "## Document B" in built
        assert documents[one.a][:400] in built
        assert documents[one.b][:400] in built

    def test_the_prompt_never_names_which_side_is_which(self, assignments: tuple[Any, ...]) -> None:
        """The answer is in `Assignment`, and `Assignment` is not in the prompt."""
        one = assignments[0]
        built = _prompt(lens="operator", assignment=one, documents=_blinded(one.subject))
        head = built.split("## Document A")[0]
        assert PLATFORM not in head.lower()
        assert BASELINE not in head.lower()

    def test_every_lens_carries_a_brief_into_the_prompt(self, assignments: tuple[Any, ...]) -> None:
        """Three lenses, because a tell one reader sees and two do not is the finding."""
        one = assignments[0]
        documents = _blinded(one.subject)
        for lens in LENSES:
            assert LENS_BRIEFS[lens] in _prompt(lens=lens, assignment=one, documents=documents)


class TestReadingTheReply:
    def test_a_fenced_object_parses(self) -> None:
        message = _FakeMessage('```json\n{"platform_is": "A"}\n```')
        assert _answer_of(message) == {"platform_is": "A"}

    def test_prose_around_the_object_parses(self) -> None:
        message = _FakeMessage('Here you go: {"platform_is": "B"} — hope that helps.')
        assert _answer_of(message) == {"platform_is": "B"}

    def test_no_object_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError, match="No JSON object"):
            _answer_of(_FakeMessage("I would rather not say."))


class TestTheArithmetic:
    def test_the_rate_is_taken_over_answers_not_questions(self) -> None:
        """Nine asked, three of them failed, six answered: the rate is over six.

        A round that divided by nine would report a failed call as a correct blinding.
        """
        round_ = Round(seed=1)
        round_.guesses.extend(_guess(right=index < 3) for index in range(6))
        round_.failures.extend({"guess": f"x/{n}", "error": "timeout"} for n in range(3))
        assert hit_rate(one.as_scored() for one in round_.guesses) == Decimal("0.5")

    def test_no_answers_is_zero_and_the_caller_must_check_the_count(self) -> None:
        assert hit_rate([]) == Decimal(0)

    def test_confidence_is_tallied_against_being_right(self) -> None:
        guesses = [
            _guess(right=True, confidence="certain"),
            _guess(right=False, confidence="certain"),
            _guess(right=True, confidence="guessing"),
        ]
        assert _by_confidence(guesses) == {
            "certain": {"hits": 1, "misses": 1},
            "guessing": {"hits": 1, "misses": 0},
        }

    def test_an_unstated_confidence_is_still_counted(self) -> None:
        assert _by_confidence([_guess(right=True, confidence="")]) == {
            "unstated": {"hits": 1, "misses": 0}
        }


class TestTheTellsAreStillWorthMeasuring:
    def test_every_tell_fires_somewhere_in_the_recorded_corpus(self) -> None:
        """A tell list nothing fires on would make the negative control above vacuous."""
        recorded = ROOT / "docs" / "plan" / "readiness-audit-2026-09"
        corpus = "\n".join(
            [
                *((recorded / s / "report.md").read_text() for s in SUBJECTS),
                *((recorded / "baseline" / s / "note.md").read_text() for s in SUBJECTS),
            ]
        )
        fired = {one.name for one in tells_in(corpus)}
        assert fired == {one.name for one in TELLS}


def _guess(*, right: bool, confidence: str = "likely") -> Guess:
    return Guess(
        subject="azn",
        lens="operator",
        guessed="A" if right else "B",
        platform_was="A",
        confidence=confidence,
        answer={},
        usage={},
        cost_gbp=Decimal(0),
    )


class _FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


# -- The verdict round's design and arithmetic -------------------------------------------------


def _comparison(key: str, *, handed: str, platform_dimensions: int = 0) -> Comparison:
    """A scored comparison, invented: the judge's letter mapped through a fixed identity."""
    identity = {"A": PLATFORM, "B": BASELINE}
    letter = "A" if handed == PLATFORM else "B"
    dimensions = [
        "A" if index < platform_dimensions else "B" for index in range(len(VERDICT_DIMENSIONS))
    ]
    verdict: dict[str, Any] = dict(zip(VERDICT_DIMENSIONS, dimensions, strict=True))
    verdict["which_would_you_hand_to_a_colleague_and_why"] = (
        "Neither of them, as it happens." if handed == "unreadable" else f"{letter}, it is sound."
    )
    return Comparison(
        subject=key,
        lens="operator",
        identity=identity,
        verdict=verdict,
        usage={},
        cost_gbp=Decimal(0),
    )


class TestTheVerdictRoundsDesign:
    def test_its_pairings_are_three_keys_over_two_platform_documents(self) -> None:
        keys = [pair.key for pair in PHASE_7_ROUND.pairs]
        platforms = {pair.platform for pair in PHASE_7_ROUND.pairs}

        assert keys == ["azn", "msft1", "msft1-september"]
        assert len(platforms) == 2
        assert PHASE_7_ROUND.out_file != PHASE_5_ROUND.out_file

    def test_the_counted_six_hold_the_fresh_baseline(self) -> None:
        counted = [pair for pair in PHASE_7_ROUND.pairs if COUNTED in pair.sets]

        assert len(counted) * len(LENSES) == 6
        assert any("msft1-fresh" in str(pair.baseline) for pair in counted)

    def test_the_like_for_like_six_use_the_notes_phase_5_judged_against(self) -> None:
        like = [pair for pair in PHASE_7_ROUND.pairs if LIKE_FOR_LIKE in pair.sets]

        assert len(like) * len(LENSES) == 6
        assert all(pair.baseline.is_relative_to(RECORDED / "baseline") for pair in like)

    def test_phase_5s_design_reads_the_documents_it_judged(self) -> None:
        """The refactor into designs must not have moved what Phase 5's comparisons read."""
        for pair in PHASE_5_ROUND.pairs:
            documents = _pair_documents(pair)
            assert set(documents) == {PLATFORM, BASELINE}
            assert pair.platform.exists()
            assert pair.baseline.exists()

    def test_the_seed_is_dealt_per_key(self) -> None:
        """Two pairings sharing a platform document are two coins, not one."""
        seed = _seed(PHASE_7_ROUND.pre_registration)
        dealt = assign(seed=seed, subjects=[pair.key for pair in PHASE_7_ROUND.pairs])

        assert [one.subject for one in dealt] == ["azn", "msft1", "msft1-september"]
        assert {one.a for one in dealt} == {PLATFORM, BASELINE}


class TestTheVerdictRoundsReading:
    def _round(self, handed: dict[str, list[str]], platform_dimensions: int = 0) -> dict[str, Any]:
        comparisons = [
            _comparison(key, handed=side, platform_dimensions=platform_dimensions)
            for key, sides in handed.items()
            for side in sides
        ]
        return scored_by_set(comparisons, PHASE_7_ROUND)

    def test_three_off_the_console_and_two_to_the_platform_is_fixed(self) -> None:
        by_set = self._round(
            {
                "azn": [PLATFORM, BASELINE, BASELINE],
                "msft1": [PLATFORM, "unreadable", BASELINE],
                "msft1-september": [BASELINE, BASELINE, BASELINE],
            }
        )
        reading = verdict_round_reading(by_set)

        assert by_set[COUNTED]["comparisons"] == 6
        assert by_set[COUNTED]["no_longer_chose_the_console"] == 3
        assert by_set[COUNTED]["handed_to_the_platform"] == 2
        assert reading["fixed"]

    def test_three_off_the_console_with_one_to_the_platform_is_not(self) -> None:
        by_set = self._round(
            {
                "azn": [PLATFORM, "unreadable", "unreadable"],
                "msft1": [BASELINE, BASELINE, BASELINE],
                "msft1-september": [BASELINE, BASELINE, BASELINE],
            }
        )

        assert not verdict_round_reading(by_set)["fixed"]

    def test_all_to_the_console_at_phase_5s_level_is_nothing_moved(self) -> None:
        by_set = self._round(
            {
                "azn": [BASELINE] * 3,
                "msft1": [BASELINE] * 3,
                "msft1-september": [BASELINE] * 3,
            }
        )
        reading = verdict_round_reading(by_set)

        assert by_set[LIKE_FOR_LIKE]["dimension_verdicts_to_the_platform"] == 0
        assert not reading["fixed"]
        assert reading["nothing_moved_since_phase_5"]

    def test_more_dimensions_than_phase_5_is_movement(self) -> None:
        by_set = self._round(
            {
                "azn": [BASELINE] * 3,
                "msft1": [BASELINE] * 3,
                "msft1-september": [BASELINE] * 3,
            },
            platform_dimensions=1,
        )

        assert by_set[LIKE_FOR_LIKE]["dimension_verdicts_to_the_platform"] == 6
        assert not verdict_round_reading(by_set)["nothing_moved_since_phase_5"]

    def test_a_short_set_cannot_be_read(self) -> None:
        """Fewer than six answered is a shortfall to report, not a smaller denominator."""
        by_set = self._round(
            {
                "azn": [PLATFORM, PLATFORM, PLATFORM],
                "msft1": [PLATFORM],
                "msft1-september": [BASELINE] * 3,
            }
        )
        reading = verdict_round_reading(by_set)

        assert not reading["counted_six_answered"]
        assert not reading["fixed"]
