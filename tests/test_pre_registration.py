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
