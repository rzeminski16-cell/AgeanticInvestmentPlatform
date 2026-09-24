"""The Phase 5 pre-registration is fixed before the round, and stays fixed.

Row 8 of the gate before a measurement round (`docs/V1.0_Alpha/11-testing-strategy.md` §5):
*the pre-registration file committed and hashed*. The point is not the file — it is that the
readings cannot be widened after the result is in. Git history already records an edit; these
tests make one **visible without reading history**, by holding the file to a hash written
down somewhere else.

So a change to the readings is a two-file change, and a two-file change in the commit that
also reports the round's result is a thing a reader can see. That is as far as a test can go
against somebody determined; it is well past what an accident survives, and the accident is
the realistic failure.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from aer.core.hashing import sha256_hex

ROOT: Final = Path(__file__).resolve().parents[1]
PRE_REGISTRATION: Final = ROOT / "docs" / "plan" / "phase-5-pre-registration.json"
STRATEGY: Final = ROOT / "docs" / "V1.0_Alpha" / "11-testing-strategy.md"

# The three readings the delivery plan's kill-gate table states, by the ids the file gives
# them. A round with a fourth reading, or with one of these missing, is a different
# instrument — and the abandonment criterion is the one that makes the rest mean anything.
EXPECTED_READINGS: Final = ("thesis_holds", "necessary_not_sufficient", "abandon")


def _loaded() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PRE_REGISTRATION.read_text())
    return data


class TestItIsThere:
    def test_the_file_exists_and_parses(self) -> None:
        assert PRE_REGISTRATION.exists(), (
            f"{PRE_REGISTRATION.relative_to(ROOT)} is missing. Row 8 of the measurement gate "
            "is the readings being written down before the round, and a round that starts "
            "without them produces a number nobody can defend."
        )
        assert _loaded()["phase"] == 5

    def test_the_hash_recorded_in_the_strategy_matches_the_file(self) -> None:
        """**The mechanism.** Editing the readings after the round means editing the recorded
        hash too, in a different document, in the same commit."""
        digest = sha256_hex(PRE_REGISTRATION.read_bytes())
        recorded = set(re.findall(r"\b[0-9a-f]{64}\b", STRATEGY.read_text()))

        assert digest in recorded, (
            f"The pre-registration hashes to {digest}, which is not among the digests recorded "
            f"in {STRATEGY.relative_to(ROOT)}. Either the file was edited and the record was "
            "not, or the record was edited and the file was not. Both are the thing this test "
            "exists to catch."
        )


class TestTheReadingsAreTheOnesAgreed:
    def test_all_three_are_present_and_no_others(self) -> None:
        ids = tuple(reading["id"] for reading in _loaded()["readings"])
        assert ids == EXPECTED_READINGS

    def test_each_reading_says_when_it_holds_and_what_follows(self) -> None:
        """A reading with no trigger is not pre-registered, and one with no consequence is
        not a decision."""
        for reading in _loaded()["readings"]:
            assert reading["when"].strip(), reading["id"]
            assert reading["means"].strip(), reading["id"]
            assert reading["then"].strip(), reading["id"]

    def test_the_abandonment_criterion_says_stop(self) -> None:
        """The line that makes every other gate real. A criterion that does not name stopping
        is a criterion nobody will apply."""
        abandon = next(r for r in _loaded()["readings"] if r["id"] == "abandon")

        assert abandon["then"].lstrip().startswith("Stop.")
        assert "evidence base" in abandon["then"]


class TestTheOperatorsTypingIsWhatIsFixed:
    """The assumption half, and the distinction it turns on: the platform's derivations are
    the product being measured and must be allowed to vary; a number somebody typed must
    not, because two runs differing only in the typing produce two valuations and no
    information.
    """

    def test_a_derived_assumption_is_not_frozen(self) -> None:
        derived = set(_loaded()["assumptions"]["derived_and_not_fixed"])

        assert {"beta", "risk_free_rate"} <= derived, (
            "Freezing a derived input measures a constant. The beta from prices and the "
            "risk-free rate from the published series are the product working."
        )

    def test_every_subject_of_the_round_has_its_typed_values_fixed(self) -> None:
        data = _loaded()
        subjects = {run["subject"] for run in data["round"]["runs"]}
        fixed = data["assumptions"]["operator_supplied_and_fixed"]

        assert subjects <= set(fixed), f"no fixed typing for {sorted(subjects - set(fixed))}"
        for subject, values in fixed.items():
            assert values, f"{subject} fixes nothing"

    def test_no_assumption_is_both_derived_and_typed(self) -> None:
        """The two lists are a partition of what a run needs, not overlapping opinions."""
        data = _loaded()["assumptions"]
        derived = set(data["derived_and_not_fixed"])
        for subject, values in data["operator_supplied_and_fixed"].items():
            clash = derived & set(values)
            assert not clash, f"{subject} both derives and fixes {sorted(clash)}"

    def test_each_correction_names_the_value_it_replaces_and_why(self) -> None:
        """Two hand-typed betas in the corpus are why this section exists. A correction with
        no reason is a preference."""
        corrections = _loaded()["assumptions"]["corrections_from_the_corpus"]

        assert corrections
        for row in corrections:
            for field in ("subject", "assumption", "corpus_value", "corpus_source"):
                assert row[field], f"a correction with no {field}: {row}"
            assert len(row["why"]) > 40, row


class TestTheBlindingIsDecidedInAdvance:
    def test_the_assignment_is_seeded(self) -> None:
        assert isinstance(_loaded()["blinding"]["assignment_seed"], int)

    def test_a_high_identity_guess_rate_attaches_a_caveat_rather_than_voiding_the_round(
        self,
    ) -> None:
        """Decided now, because deciding it after seeing the rate is how a caveat gets
        dropped."""
        blinding = _loaded()["blinding"]

        assert float(blinding["identity_guess_at_chance"]) == 0.5
        assert float(blinding["identity_guess_reported_with_caveat_above"]) > 0.5


# -- The verdict round (Phase 7) -------------------------------------------------------------------

PHASE_7: Final = ROOT / "docs" / "plan" / "phase-7-pre-registration.json"

# Delivery plan §12's target for ISSUE 2, word for word. The round is read against this
# sentence and nothing looser, so a copy that drifts from it is a different target.
SECTION_12_TARGET: Final = (
    "at least 3 of 6 comparisons do not choose the console, and at least 2 of 6 choose the "
    "platform, with the fresh baseline in the set"
)


def _phase_7() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PHASE_7.read_text())
    return data


class TestTheVerdictRoundIsFixedInAdvance:
    """The same mechanism as Phase 5's, over the verdict round's own file."""

    def test_the_file_is_hashed_where_the_strategy_records_it(self) -> None:
        digest = sha256_hex(PHASE_7.read_bytes())
        recorded = set(re.findall(r"\b[0-9a-f]{64}\b", STRATEGY.read_text()))

        assert _phase_7()["phase"] == 7
        assert digest in recorded, (
            f"The verdict round's pre-registration hashes to {digest}, which is not among the "
            f"digests recorded in {STRATEGY.relative_to(ROOT)}."
        )

    def test_the_readings_are_three_and_read_in_order(self) -> None:
        """Phase 5's file let two readings fit at once. These are ordered, so the first that
        holds is the round's and no choice is left for after the result."""
        readings = _phase_7()["readings"]

        assert [r["id"] for r in readings] == ["fixed", "abandon", "not_yet"]
        assert [r["order"] for r in readings] == [1, 2, 3]
        for reading in readings:
            assert reading["when"].strip(), reading["id"]
            assert reading["means"].strip(), reading["id"]
            assert reading["then"].strip(), reading["id"]

    def test_the_target_is_the_delivery_plans_own_words(self) -> None:
        fixed = next(r for r in _phase_7()["readings"] if r["id"] == "fixed")

        assert SECTION_12_TARGET in fixed["when"]
        plan = (ROOT / "docs" / "V1.0_Alpha" / "05-delivery-plan.md").read_text()
        assert "3 of 6 comparisons do not choose the console" in plan

    def test_the_abandonment_criterion_still_says_stop(self) -> None:
        abandon = next(r for r in _phase_7()["readings"] if r["id"] == "abandon")

        assert abandon["then"].lstrip().startswith("Stop.")
        assert "evidence base" in abandon["then"]
        assert "no judge's stated reason changes category" in abandon["when"]

    def test_the_seed_is_fresh_and_is_what_the_panel_reads(self) -> None:
        """A round seeded like the last one deals the same sides to the same keys."""
        from audit.judges.panel import PHASE_7_ROUND, _seed  # noqa: PLC0415

        seed = _phase_7()["blinding"]["assignment_seed"]
        assert isinstance(seed, int)
        assert seed != _loaded()["blinding"]["assignment_seed"]
        assert PHASE_7_ROUND.pre_registration == PHASE_7
        assert _seed(PHASE_7_ROUND.pre_registration) == seed

    def test_the_pairings_are_the_panels(self) -> None:
        """The file and the code describe one round, or the code wins silently — the failure
        Phase 5's file met through its assumptions block."""
        from audit.judges.panel import PHASE_7_ROUND  # noqa: PLC0415

        written = [
            (p["key"], p["platform"], p["comparator"], tuple(p["sets"]))
            for p in _phase_7()["round"]["panel"]["pairings"]
        ]
        coded = [
            (
                pair.key,
                str(pair.platform.relative_to(ROOT)),
                str(pair.baseline.relative_to(ROOT)),
                pair.sets,
            )
            for pair in PHASE_7_ROUND.pairs
        ]
        assert written == coded

    def test_the_fresh_baseline_is_in_the_counted_set(self) -> None:
        pairings = _phase_7()["round"]["panel"]["pairings"]
        counted = [p for p in pairings if "counted" in p["sets"]]
        fresh = _phase_7()["round"]["comparator"]["fresh"]

        assert len(counted) * 3 == 6
        assert any(fresh["label"] in p["comparator"] for p in counted)

    def test_the_typed_assumptions_are_the_runners(self) -> None:
        """Written from `audit/subjects.py`, keyed by the runner's own subject keys."""
        from audit.subjects import subject_for  # noqa: PLC0415

        fixed = _phase_7()["assumptions"]["operator_supplied_and_fixed"]
        derived = set(_phase_7()["assumptions"]["derived_and_not_fixed"])
        for key, values in fixed.items():
            typed = {a.name: a.value for a in subject_for(key).assumptions}
            assert {name: Decimal(value) for name, value in values.items()} == typed, key
            assert not derived & set(typed), f"{key} types a value the round derives"

    def test_the_spend_order_fits_the_budget(self) -> None:
        data = _phase_7()
        total = sum(Decimal(stage["estimate_gbp"]) for stage in data["spend_order"])

        assert total <= Decimal(data["budget_gbp"])
