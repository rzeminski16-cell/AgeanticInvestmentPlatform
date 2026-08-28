"""The replay harness: stored calculations re-run from their own records.

Two layers, deliberately separate.

The **pure** tests feed :func:`aer.eval.replay.replay` handwritten stored-record shapes and
check the harness itself: that it finds the function by the name the ledger stores, rebuilds
sequence arguments, coerces JSONB-flattened parameters back into the types the guards demand,
and — most importantly — turns every failure into an observation rather than an exception,
because a harness that stops at the first broken record measures nothing after it.

The **integration** tests run the whole loop against Postgres: a chain persisted through the
real services, replayed from the rows the database returns, resolved against the assumptions
table. Each green case has a deliberate-regression twin — corrupt a stored output, rename a
stored function, re-propose a confirmed assumption — proving the metric goes red for the
exact failure it exists to catch, not merely that it goes green on healthy data.
"""

from __future__ import annotations

import sys
import types
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import aer.eval.replay as replay_module
from aer.calc.dcf import TerminalMethod, equity_value
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, Unit
from aer.config import Settings
from aer.db.models import Calculation
from aer.eval.metrics import assumption_completeness, numerical_consistency
from aer.eval.observations import ReplayObservation
from aer.eval.replay import (
    RegistryError,
    completeness_observations_for_job,
    registry,
    relative_delta,
    replay,
    replay_observations_for_job,
)
from aer.services.assumptions import propose
from aer.services.run_replay import divergence_reason, diverges
from aer.storage.local import LocalArtefactStore
from tests.ledger_fixtures import DISCOUNTED_TERMINAL, TERMINAL_VALUE, record_valuation_ledger
from tests.scene_fixtures import build_scene

_SOURCE = {"kind": "fact", "id": "test", "label": "handwritten"}


def _quantity(value: str, unit: str) -> Quantity:
    return Quantity(Decimal(value), Unit.currency(unit), source=SourceRef.financial_fact("f1"))


def _input(name: str, value: str, unit: str) -> dict[str, Any]:
    return {"name": name, "value": value, "unit": unit, "source": dict(_SOURCE)}


# ==========================================================================================
# The harness, on handwritten records
# ==========================================================================================


class TestTheRegistry:
    def test_it_collects_calculations_by_their_stored_names(self):
        found = registry()

        # One from each end of the package: the registry is derived by walking the modules,
        # so presence here means the walk reached them.
        for name in ("growth_rate", "wacc", "gordon_terminal_value", "beta", "fx_convert"):
            assert name in found

    def test_two_functions_claiming_one_name_are_refused(self, monkeypatch: pytest.MonkeyPatch):
        # The ledger stores the name; a duplicate makes every stored record of it ambiguous.
        module = types.ModuleType("fake_calc_module")

        def first() -> None: ...

        def second() -> None: ...

        for function in (first, second):
            function.calculation_name = "duplicated"  # type: ignore[attr-defined]
            function.__module__ = "fake_calc_module"
        module.first = first  # type: ignore[attr-defined]
        module.second = second  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "fake_calc_module", module)
        monkeypatch.setattr(replay_module, "CALC_MODULES", ("fake_calc_module",))
        monkeypatch.setattr(replay_module, "_REGISTRY", None)

        with pytest.raises(RegistryError):
            registry()


class TestAnEmptySeriesIsRecordedRatherThanImplied:
    """The regression from a live run: sixty-two findings, not one of them real.

    `equity_value(adjustments=[])` is the ordinary case — most companies have no
    non-operating items — and an empty series expands to no input rows. A record holding
    none was indistinguishable from one where the argument was never passed, so the harness
    rebuilt the call without it and the function refused with `missing a required argument`.
    `numerical_consistency` then failed the run's validation on every such row.

    The recorder now writes the empty series as a structural parameter, which is what it is:
    no number entered, and that fact is the thing worth keeping.
    """

    def test_the_recorder_keeps_an_empty_series(self):
        context = CalculationContext(code_version="test")
        equity_value(
            context,
            enterprise_value=_quantity("1000", "USD"),
            net_debt=_quantity("200", "USD"),
            adjustments=[],
            method=TerminalMethod.GORDON_GROWTH,
        )
        record = context.records[-1]

        assert record.parameters["adjustments"] == [], (
            "an empty series must survive in the record; without it the row cannot be replayed"
        )

    def test_the_harness_replays_a_record_with_an_empty_series(self):
        observation = replay(
            name="equity_value",
            label="equity_value#0",
            inputs=[_input("enterprise_value", "1000", "USD"), _input("net_debt", "200", "USD")],
            parameters={
                "adjustments": [],
                "method": "gordon_growth",
                "case": "base",
            },
            expected_value=Decimal(800),
            expected_unit="USD",
        )

        assert observation.error is None, observation.error
        assert observation.replayed == Decimal(800)


class TestReplayNeverRaises:
    def test_a_name_no_function_carries_becomes_the_observation_error(self):
        observation = replay(
            name="a_calculation_that_never_existed",
            label="ghost#0",
            inputs=[_input("start", "1", "USD")],
            parameters={},
            expected_value=Decimal(1),
            expected_unit="USD",
        )

        assert observation.replayed is None
        assert observation.error is not None
        assert "no traced function" in observation.error
        assert not observation.delta.is_finite()

    def test_a_sequence_with_a_missing_element_is_a_broken_record(self):
        # weighted_average stored values[0] and values[2]: element 1 is gone from the row.
        # Replaying the remaining two as if they were the series would compute a different
        # calculation and call it a match.
        observation = replay(
            name="weighted_average",
            label="weighted_average#0",
            inputs=[
                _input("values[0]", "10", "USD"),
                _input("values[2]", "20", "USD"),
                _input("weights[0]", "1", "pure"),
                _input("weights[1]", "3", "pure"),
            ],
            parameters={},
            expected_value=Decimal("17.5"),
            expected_unit="USD",
        )

        assert observation.error is not None
        assert "contiguous" in observation.error

    def test_arithmetic_that_now_refuses_becomes_the_observation_error(self):
        # A denominator of zero is refused by the traced function, as it should be. The
        # harness's job is to report that refusal, not to die of it.
        observation = replay(
            name="multiple",
            label="multiple#0",
            inputs=[
                _input("numerator", "1200", "USD"),
                _input("denominator", "0", "USD"),
            ],
            parameters={"basis": "ttm"},
            expected_value=Decimal(12),
            expected_unit="pure",
        )

        assert observation.error is not None
        assert observation.replayed is None

    def test_the_rebuilt_inputs_carry_their_stored_sources(self):
        # Documented in the harness and pinned here even though no calculation branches on
        # its sources today: a replay that invented sources would be exercising a slightly
        # different function from the one that ran, and the day a function does look at
        # provenance, that difference stops being theoretical.
        rebuilt = replay_module._reconstruct_inputs(
            [
                {
                    "name": "quoted",
                    "value": "4.25",
                    "unit": "pure",
                    "source": {"kind": "assumption", "id": "a-known-id", "label": "stored"},
                }
            ]
        )

        source = rebuilt["quoted"].source
        assert source is not None
        assert source.kind.value == "assumption"
        assert source.identifier == "a-known-id"

    def test_jsonb_flattened_parameters_are_coerced_back(self):
        # JSONB stores the enum as "monthly" and the int as 30; the guards on the other side
        # refuse a bare string, and rightly. The coercion is driven by the function's own
        # annotations, so this passing means the guards ran against real types.
        observation = replay(
            name="beta",
            label="beta#0",
            inputs=[
                _input("subject_market_covariance", "0.02", "pure"),
                _input("market_variance", "0.01", "pure"),
            ],
            parameters={"frequency": "monthly", "observations": 30},
            expected_value=Decimal(2),
            expected_unit="pure",
        )

        assert observation.error is None, observation.error
        assert observation.replayed == Decimal(2)
        assert observation.unit_matches


class TestRelativeDelta:
    def test_an_ordinary_drift_is_a_fraction_of_the_stored_figure(self):
        assert relative_delta(Decimal(100), Decimal(101)) == Decimal("0.01")

    def test_against_a_stored_zero_the_delta_is_absolute(self):
        # A fraction of nothing is undefined, and "it was zero and now it is 3" is exactly
        # as wrong as a large relative move.
        assert relative_delta(Decimal(0), Decimal(3)) == Decimal(3)

    def test_a_replay_that_produced_nothing_is_infinitely_far_away(self):
        observation = ReplayObservation(
            name="gone#0",
            expected=Decimal(100),
            expected_unit="USD",
            replayed=None,
            replayed_unit=None,
            error="broken",
        )

        assert not observation.delta.is_finite()


class TestWhatCountsAsADivergence:
    """The rule "Reproduce this run" applies, kept pure so it can be argued with directly.

    It used to be ``replayed != expected``, and that is wrong for a reason no amount of care
    in the arithmetic can fix: ``calculations.output_value`` is ``NUMERIC(38, 12)``, so what
    the ledger holds is a *rounded* figure and a re-execution carries the full context
    precision. On the 2026-08-24 MSFT run that turned 113 of 1,034 calculations into
    divergences — every ratio, and no sum — while the evaluation gate passed
    ``numerical_consistency`` on the same rows. One tolerance, read from the gate, is the fix.
    """

    def _observed(self, expected: str, replayed: str, unit: str = "pure") -> ReplayObservation:
        return ReplayObservation(
            name="gross_margin#1",
            expected=Decimal(expected),
            expected_unit="pure",
            replayed=Decimal(replayed),
            replayed_unit=unit,
        )

    def test_the_columns_rounding_is_not_a_divergence(self):
        observation = self._observed("0.679546406541", "0.6795464065405211563438896573338275")

        assert not diverges(observation)

    def test_a_figure_wrong_beyond_the_rounding_is(self):
        # A percentage point out on a margin is four hundred million dollars on Microsoft's
        # revenue. A tolerance that swallowed it would make the check decorative.
        assert diverges(self._observed("0.689546406541", "0.679546406541"))

    def test_a_record_that_will_not_re_run_diverges_however_close_it_might_have_been(self):
        observation = ReplayObservation(
            name="equity_value#1",
            expected=Decimal(1275),
            expected_unit="USD",
            replayed=None,
            replayed_unit=None,
            error="TypeError: missing a required argument",
        )

        assert diverges(observation)
        assert "did not re-run" in divergence_reason(observation)

    def test_the_same_digits_in_another_unit_are_not_a_reproduction(self):
        observation = self._observed("0.679546406541", "0.679546406541", unit="USD")

        assert diverges(observation)
        assert divergence_reason(observation) == "replayed in USD, stored pure"

    def test_the_reason_names_both_figures(self):
        reason = divergence_reason(self._observed("0.5", "0.1"))

        assert "stored 0.5" in reason
        assert "replayed 0.1" in reason


# ==========================================================================================
# The whole loop, against Postgres
# ==========================================================================================


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Tracework Eval test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def ledger(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    scene = await build_scene(db_session, store)
    recorded = await record_valuation_ledger(
        db_session, request=scene["request"], job=scene["job"], actor=scene["user"]
    )
    return {**scene, **recorded}


@pytest.mark.integration
class TestReplayingAStoredRun:
    async def test_every_stored_row_replays_exactly(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        observations = await replay_observations_for_job(db_session, ledger["job"].id)

        assert [row.error for row in observations] == [None, None]
        assert observations[0].replayed == TERMINAL_VALUE
        assert observations[1].replayed == DISCOUNTED_TERMINAL

        result = numerical_consistency(observations)
        assert result.passed, result.describe()
        assert result.value == 0

    async def test_the_labels_carry_the_ledger_position(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        # name#sequence, so a failure names one row of one run rather than "a gordon
        # terminal value, somewhere".
        observations = await replay_observations_for_job(db_session, ledger["job"].id)

        assert [row.name for row in observations] == [
            "gordon_terminal_value#0",
            "present_value#1",
        ]

    async def test_a_corrupted_output_turns_the_metric_red(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        # The deliberate regression. If editing a stored figure does not fail the gate, the
        # gate is not checking stored figures.
        ledger["rows"][1].output_value = Decimal(900)
        await db_session.flush()

        observations = await replay_observations_for_job(db_session, ledger["job"].id)
        result = numerical_consistency(observations)

        assert not result.passed
        assert "present_value#1" in result.describe()

    async def test_a_function_the_ledger_names_but_the_code_lost_is_a_failure(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        # Renaming a stored row's function simulates the drift the registry exists to catch:
        # the code moved on and the record can no longer be re-run. That must fail the
        # metric, not shrink its population.
        ledger["rows"][0].name = "a_calculation_this_code_no_longer_has"
        await db_session.flush()

        observations = await replay_observations_for_job(db_session, ledger["job"].id)
        result = numerical_consistency(observations)

        assert not result.passed
        assert len(observations) == 2
        assert "did not replay" in result.describe()


@pytest.mark.integration
class TestAssumptionCompletenessEndToEnd:
    async def test_a_confirmed_assumption_is_complete(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        observations = await completeness_observations_for_job(db_session, ledger["job"].id)

        cited = [row for row in observations if row.rests_on_assumptions]
        assert len(cited) == 1
        assert cited[0].assumption_ids == (str(ledger["assumption"].id),)

        result = assumption_completeness(observations)
        assert result.passed, result.describe()

    async def test_reproposing_pulls_the_ground_from_under_the_run(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        # The deliberate regression. Proposing again withdraws the approval — the value the
        # calculation used is no longer the value anybody agreed to — and the run recorded
        # before the re-proposal must go red without being touched.
        await propose(
            db_session,
            request_id=ledger["request"].id,
            name="terminal_growth",
            value=Decimal("0.03"),
            unit="pure",
            justification="Revised after the run: nominal growth looks nearer three per cent.",
            proposed_by="planner",
        )

        observations = await completeness_observations_for_job(db_session, ledger["job"].id)
        result = assumption_completeness(observations)

        assert not result.passed
        assert "unconfirmed" in result.describe()

    async def test_an_assumption_that_no_longer_resolves_is_a_failure(
        self, db_session: AsyncSession, ledger: dict[str, Any]
    ):
        # A row citing an assumption id that matches nothing. The schema cannot forbid this
        # (inputs are JSONB, by design), so the metric has to notice it.
        vanished = str(uuid.uuid4())
        template = ledger["rows"][0]
        db_session.add(
            Calculation(
                job_id=ledger["job"].id,
                sequence=2,
                name=template.name,
                formula=template.formula,
                function_ref=template.function_ref,
                code_version=template.code_version,
                inputs=[
                    {
                        "name": "terminal_growth",
                        "value": "0.02",
                        "unit": "pure",
                        "source": {"kind": "assumption", "id": vanished, "label": "gone"},
                    }
                ],
                parameters={},
                assumptions=[],
                output_value=Decimal(1275),
                output_unit="USD",
            )
        )
        await db_session.flush()

        observations = await completeness_observations_for_job(db_session, ledger["job"].id)
        result = assumption_completeness(observations)

        assert not result.passed
        assert "no longer resolve" in result.describe()
