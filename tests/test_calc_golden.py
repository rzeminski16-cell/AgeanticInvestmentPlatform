"""Thirty known answers, replayed through the harness that scores real runs.

The corpus in ``tests/fixtures/calc/golden.json`` is hand-written: each entry is a stored
calculation row whose answer was worked out on paper, in the same shape the ``calculations``
table persists. Replaying it through :func:`aer.eval.replay.replay` therefore tests two things
at once — that the arithmetic still produces the answers a person computed, and that the
replay harness can actually reconstruct a calculation from its stored form, because the gate's
numerical-consistency metric is only as good as that reconstruction.

The golden tolerance is 0.01% (``docs/PLAN.md`` §2.10), tighter than the gate's own 0.5%
threshold: a golden case has no rounding excuse, its inputs were chosen to be exact.

The corpus-integrity tests are as load-bearing as the replays. A corpus that silently shrank,
or a new calc module that never gained a golden case, would leave the harness green while its
coverage rotted.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import aer.calc
from aer.eval.observations import ReplayObservation
from aer.eval.replay import CALC_MODULES, registry, relative_delta, replay

FIXTURE = Path(__file__).parent / "fixtures" / "calc" / "golden.json"

GOLDEN_CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text())["cases"]

# §2.10: within 0.01%. Deliberately tighter than the gate's numerical-consistency threshold —
# a hand-computed case with exact inputs has nothing to round.
GOLDEN_TOLERANCE = Decimal("0.0001")

# Raised from thirty when K3's assumption_delta gained its hand-computed case, and again
# for `pooled_cost` — the deliberate act this pin exists to force. That last one is the case
# most worth having: ADR 0081's worked example is the one where the three cost conventions
# disagree, so it fails if pooling is ever quietly replaced by first-in-first-out.
EXPECTED_CORPUS_SIZE = 32


def _replayed(case: dict[str, Any]) -> ReplayObservation:
    return replay(
        name=case["name"],
        label=case["name"],
        inputs=case["inputs"],
        parameters=case["parameters"],
        expected_value=Decimal(case["expected"]["value"]),
        expected_unit=case["expected"]["unit"],
    )


class TestTheThirtyGoldenCalculations:
    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["name"] for c in GOLDEN_CASES])
    def test_replays_without_error(self, case: dict[str, Any]):
        observation = _replayed(case)

        assert observation.error is None, observation.error

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["name"] for c in GOLDEN_CASES])
    def test_unit_is_the_hand_computed_one(self, case: dict[str, Any]):
        observation = _replayed(case)

        assert observation.unit_matches, (
            f"expected {observation.expected_unit!r}, replayed {observation.replayed_unit!r}"
        )

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["name"] for c in GOLDEN_CASES])
    def test_value_is_within_a_hundredth_of_a_percent(self, case: dict[str, Any]):
        observation = _replayed(case)

        assert observation.replayed is not None
        delta = relative_delta(observation.expected, observation.replayed)
        assert delta <= GOLDEN_TOLERANCE, (
            f"{case['name']}: expected {observation.expected}, "
            f"replayed {observation.replayed}, relative delta {delta}"
        )


class TestTheCorpusItself:
    def test_exactly_the_pinned_number_of_cases(self):
        # The number is the spec's, and pinning it stops the corpus quietly shrinking. Adding
        # a thirty-first golden case means raising this deliberately, which is the point.
        assert len(GOLDEN_CASES) == EXPECTED_CORPUS_SIZE

    def test_each_calculation_appears_once(self):
        names = [case["name"] for case in GOLDEN_CASES]

        assert len(names) == len(set(names))

    def test_every_case_names_a_registered_calculation(self):
        known = registry()
        unknown = [case["name"] for case in GOLDEN_CASES if case["name"] not in known]

        assert unknown == []

    def test_every_calc_module_is_represented(self):
        # One golden case per module is the floor, not the ambition — but it guarantees a new
        # calc module cannot ship without at least one hand-computed answer.
        reg = registry()
        modules_covered = {reg[case["name"]].__module__ for case in GOLDEN_CASES}

        assert modules_covered == set(CALC_MODULES)

    def test_every_input_is_sourced(self):
        # The replay harness reuses stored sources, so a golden case without one would pass
        # for the wrong reason if the engine ever stopped refusing unsourced values.
        for case in GOLDEN_CASES:
            for stored in case["inputs"]:
                assert stored.get("source", {}).get("kind"), (
                    f"{case['name']}: input {stored['name']!r} has no source"
                )


class TestTheRegistryCoversTheCodebase:
    def test_calc_modules_lists_every_module_with_traced_functions(self):
        # CALC_MODULES is a hand-kept list so that importing the registry stays deliberate.
        # This is the check that keeps it honest: walk the real package, and any module
        # defining a traced calculation must be on the list.
        with_traced = set()
        for info in pkgutil.iter_modules(aer.calc.__path__, prefix="aer.calc."):
            module = importlib.import_module(info.name)
            for value in vars(module).values():
                if (
                    getattr(value, "calculation_name", None) is not None
                    and getattr(value, "__module__", "") == module.__name__
                ):
                    with_traced.add(module.__name__)
                    break

        assert with_traced == set(CALC_MODULES)

    def test_no_two_calculations_share_a_name(self):
        # registry() raises on duplicates; building it is the assertion.
        assert len(registry()) > 0
