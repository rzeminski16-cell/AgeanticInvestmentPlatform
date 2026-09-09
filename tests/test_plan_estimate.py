"""What gate 1 tells the operator a run will cost, and how long it will take.

The one screen where a person decides whether to spend the money. The first acceptance
pass showed £1.3353 and 120 seconds against a run that cost £7.5023 and worked for some
forty minutes, because the estimate summed the plan step's own spend plus one writing call
per section and nothing else — no critique, no research workers, no red team, no revise,
no retries — and the runtime was a constant that modelled nothing.
"""

from __future__ import annotations

from decimal import Decimal

from aer.workflow.workflows.vertical_slice_v1 import (
    _RUNTIME_ESTIMATE_SECONDS,
    _projected_cost,
    build_steps,
)


def _declared() -> dict[str, Decimal]:
    return {step.key: step.estimated_cost_gbp for step in build_steps()}


class TestTheEstimateCoversTheWholeRun:
    def test_every_step_that_spends_is_in_the_projection(self) -> None:
        """The property that broke. Named steps rather than a total, so a regression says
        which step went missing rather than only that a number moved."""
        projected = _projected_cost(Decimal(0))
        declared = _declared()

        for key in (
            "critique_plan",
            "propose_peers",
            "propose_themes",
            "research_company",
            "research_industry",
            "research_macro",
            "research_recent_developments",
            "research_technical_context",
            "propose_assumptions",
            "draft",
            "validate",
            "red_team",
            "revise",
            "verdict",
            "brief_challenges",
        ):
            assert declared.get(key, Decimal(0)) > 0, f"{key} declares no estimate to project"

        # Everything the workflow declares except the plan step, which is already spent by
        # the time the estimate is written and is added by the caller as its actual cost.
        assert projected == sum(declared.values(), Decimal(0)) - declared["plan"]

    def test_the_projection_is_the_order_of_a_real_run(self) -> None:
        """The live run cost £7.5023 and the operator reports about £10 as typical. An
        estimate under £5 is the defect this replaced; one over £20 would break the
        default per-run cap for the wrong reason."""
        projected = _projected_cost(Decimal(0))
        assert Decimal(5) < projected < Decimal(20)

    def test_the_spine_wins_when_it_knows_more_than_the_flat_estimate(self) -> None:
        """By gate 1 the section set is known, with each section's budget and routed
        model. Where that sums to more than the draft step's flat guard input, it is the
        better figure — and where it sums to less, it is a spine assuming every section
        drafts first time, so the calibrated flat estimate stands."""
        flat = _declared()["draft"]
        assert _projected_cost(flat + Decimal(3)) == _projected_cost(Decimal(0)) + Decimal(3)
        assert _projected_cost(Decimal("0.01")) == _projected_cost(Decimal(0))

    def test_the_runtime_default_is_not_a_shrug(self) -> None:
        """Only ever the fallback — a completed run is measured instead — but a fallback
        of two minutes for a run whose draft step alone spends ten told the operator
        nothing they could plan around."""
        assert _RUNTIME_ESTIMATE_SECONDS >= 20 * 60
