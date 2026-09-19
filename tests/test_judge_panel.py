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
    PRE_REGISTRATION,
    SUBJECTS,
    Guess,
    Round,
    _answer_of,
    _blinded,
    _by_confidence,
    _prompt,
    _seed,
)
from audit.judges.rubric import LENS_BRIEFS, LENSES

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
