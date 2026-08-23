"""The sector block, at the layer that computes rather than the layer that renders.

`docs/phase-3-plan.md` task 28's acceptance criterion is that **a bank ticker cannot produce
a discounted cash flow by any route**, asserted at the calculation layer. That phrasing rules
out the obvious implementation — a check in a service, or a branch in a route — because
either leaves a route that skips it, and the route that skips it is the one somebody adds
next year.

So the check is a type. `discounted_cash_flow` requires a `ValuationMandate`, and a mandate
for `DCF_FCFF` on a bank cannot be constructed: the validation is in `__post_init__`. These
tests assert that from both directions — that the mandate refuses to exist, and that there is
no way to call the function without one.
"""

from __future__ import annotations

import dataclasses
import inspect
from decimal import Decimal

import pytest

from aer.calc.dcf import (
    BridgeItem,
    DcfInputs,
    DriverPath,
    GridAxis,
    GridMeasure,
    TerminalMethod,
    discounted_cash_flow,
    project,
    sensitivity_grid,
)
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, money
from aer.core.sectors import (
    SECTOR_PROFILES,
    ModelNotPermittedError,
    SectorProfile,
    ValuationMandate,
    ValuationModel,
    mandate_for,
    profile_for,
    suggested_profiles,
    unclassified_mandate,
)

ASSUMPTION = SourceRef.assumption("assumption-1")
FACT = SourceRef.fact("fact-1")

BANK = "BARC"
REIT = "SPG"
ORDINARY = "MSFT"


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def rate(value: str) -> Quantity:
    return Quantity.of(Decimal(value), source=ASSUMPTION)


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def flat(name: str, value: str) -> DriverPath:
    return DriverPath.flat(name, rate(value), years=3)


def inputs() -> DcfInputs:
    return DcfInputs(
        base_revenue=usd("1000"),
        revenue_growth=flat("revenue_growth", "0.05"),
        ebit_margin=flat("ebit_margin", "0.25"),
        capex_intensity=flat("capex_intensity", "0.06"),
        depreciation_intensity=flat("depreciation_intensity", "0.05"),
        working_capital_intensity=flat("working_capital_intensity", "0.10"),
        opening_working_capital=usd("100"),
        tax_rate=rate("0.25"),
        wacc=rate("0.10"),
        terminal_growth=rate("0.02"),
        exit_multiple=rate("10"),
        net_debt=usd("400"),
        shares_outstanding=Quantity.of(Decimal("100"), "shares", source=FACT),
        non_operating=(),
    )


def banks() -> object:
    profile = profile_for("banks")
    assert profile is not None
    return profile


# -- The acceptance criterion ----------------------------------------------------------------


class TestABankCannotProduceADiscountedCashFlow:
    """By any route. Each test closes one of them."""

    def test_the_mandate_cannot_be_minted(self):
        with pytest.raises(ModelNotPermittedError, match="blocked for banks"):
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject=BANK,
                profile=banks(),
                confirmed_by="analyst@example.invalid",
            )

    def test_the_dataclass_cannot_be_constructed_directly_either(self):
        """The factory is a convenience; the validation is in `__post_init__`.

        A guard that lived only in `mandate_for` would be bypassed by anybody who
        constructed the dataclass — which is the first thing a caller in a hurry does.
        """
        with pytest.raises(ModelNotPermittedError, match="blocked for banks"):
            ValuationMandate(
                model=ValuationModel.DCF_FCFF,
                subject=BANK,
                sector_key="banks",
                confirmed_by="analyst@example.invalid",
            )

    def test_a_permitted_mandate_cannot_be_edited_into_a_forbidden_one(self):
        """`dataclasses.replace` re-runs `__post_init__`, so this is closed too."""
        permitted = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject=BANK,
            profile=banks(),
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError, match="blocked for banks"):
            dataclasses.replace(permitted, model=ValuationModel.DCF_FCFF)

    def test_the_mandate_is_frozen_so_it_cannot_be_mutated_into_one(self):
        permitted = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject=BANK,
            profile=banks(),
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            permitted.model = ValuationModel.DCF_FCFF  # type: ignore[misc]

    def test_a_mandate_for_another_model_is_refused_at_the_calculation(self, context):
        """Holding *a* mandate is not holding *the* mandate."""
        comps = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject=BANK,
            profile=banks(),
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError, match="mandate is for comps_multiples"):
            discounted_cash_flow(context, inputs(), mandate=comps)

    def test_an_unclassified_mandate_for_a_bank_still_needs_the_gate(self):
        """The hole this would otherwise leave, closed at the workflow rather than here.

        `unclassified_mandate` is deliberately permissive — most companies are ordinary. What
        stops it being the way round the block is that a run whose classifier proposed a
        specialist sector pauses at `SECTOR_SPECIALIST`, so "unclassified" is only reachable
        when nothing was proposed. `tests/test_sectors_service.py` asserts that half.
        """
        mandate = unclassified_mandate(ValuationModel.DCF_FCFF, subject=ORDINARY)
        assert mandate.sector_key == ""
        assert mandate.profile is None

    @pytest.mark.parametrize("function", [project, discounted_cash_flow, sensitivity_grid])
    def test_the_mandate_is_a_required_argument_with_no_default(self, function):
        """No default means no call that omits it, in any file, ever."""
        parameter = inspect.signature(function).parameters["mandate"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_forecast_alone_is_not_a_way_round_it(self, context):
        """A projection plus a terminal value worked out by hand is a DCF by another name."""
        comps = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject=BANK,
            profile=banks(),
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError, match="does not forecast"):
            project(context, inputs(), mandate=comps)

    def test_the_forecast_accepts_either_free_cash_flow_mandate(self, context):
        """A forecast is not itself a valuation.

        The same projection underlies free cash flow to the firm and to equity; they differ
        in what is done with it. A company blocked from FCFF because enterprise value is
        meaningless for it may well be valued on FCFE, and blocking it from forecasting would
        be blocking the wrong thing.
        """
        fcfe = unclassified_mandate(ValuationModel.DCF_FCFE, subject=ORDINARY)

        years = project(context, inputs(), mandate=fcfe)

        assert len(years) == 3

    def test_but_the_whole_valuation_does_not(self, context):
        """`discounted_cash_flow` *is* the firm model, so an equity mandate is not it.

        The two guards differ, and this is the case that tells them apart. Written after a
        sabotage run showed the narrower check inside `discounted_cash_flow` could be deleted
        without any test noticing, because `project` was making the same assertion.
        """
        fcfe = unclassified_mandate(ValuationModel.DCF_FCFE, subject=ORDINARY)

        with pytest.raises(ModelNotPermittedError, match="mandate is for dcf_fcfe"):
            discounted_cash_flow(context, inputs(), mandate=fcfe)

    def test_the_sensitivity_grid_is_not_a_way_round_it_either(self, context):
        comps = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject=BANK,
            profile=banks(),
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError):
            sensitivity_grid(
                context,
                inputs(),
                rows=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
                columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"))),
                method=TerminalMethod.GORDON_GROWTH,
                measure=GridMeasure.VALUE_PER_SHARE,
                mandate=comps,
            )


# -- What the refusal says -------------------------------------------------------------------


class TestTheRefusalExplainsItself:
    def test_a_bank_refusal_names_the_profile_and_the_reason(self):
        with pytest.raises(ModelNotPermittedError) as excinfo:
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject=BANK,
                profile=banks(),
                confirmed_by="analyst@example.invalid",
            )

        message = str(excinfo.value)
        assert "banks" in message
        assert BANK in message
        # The seeded warning, verbatim rather than paraphrased.
        assert "deposits and debt are raw material, not financing" in message

    def test_a_bank_refusal_names_what_is_offered_instead(self):
        with pytest.raises(ModelNotPermittedError) as excinfo:
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject=BANK,
                profile=banks(),
                confirmed_by="analyst@example.invalid",
            )

        assert "comps_multiples" in str(excinfo.value)
        assert "dividend_discount" in str(excinfo.value)
        assert "residual_income" in str(excinfo.value)
        assert excinfo.value.context["offered"] == [
            "comps_multiples",
            "dividend_discount",
            "residual_income",
        ]

    def test_a_reit_refusal_offers_p_over_ffo(self):
        """`docs/phase-3-plan.md`: "a REIT refuses and offers P/FFO"."""
        reit = profile_for("reits")
        assert reit is not None

        with pytest.raises(ModelNotPermittedError) as excinfo:
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject=REIT,
                profile=reit,
                confirmed_by="analyst@example.invalid",
            )

        message = str(excinfo.value)
        assert "P/FFO" in message
        assert "net_asset_value" in message
        assert "Depreciation dominates reported earnings" in message

    def test_a_not_implemented_model_reads_differently_from_a_blocked_one(self):
        """Two different statements, and conflating them would mislead in both directions."""
        utilities = profile_for("utilities")
        assert utilities is not None

        with pytest.raises(ModelNotPermittedError) as excinfo:
            mandate_for(
                ValuationModel.SUM_OF_THE_PARTS,
                subject="NG",
                profile=utilities,
                confirmed_by="analyst@example.invalid",
            )

        assert "not implemented" in str(excinfo.value)
        assert excinfo.value.context["blocked"] is False

    def test_the_error_carries_a_stable_code(self):
        with pytest.raises(ModelNotPermittedError) as excinfo:
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject=BANK,
                profile=banks(),
                confirmed_by="analyst@example.invalid",
            )

        assert excinfo.value.code == "valuation_model_not_permitted"
        assert excinfo.value.http_status == 409


# -- The confirmation requirement ------------------------------------------------------------


class TestAClassificationNobodyConfirmedPermitsNothing:
    def test_a_specialist_mandate_needs_a_confirmer(self):
        with pytest.raises(ModelNotPermittedError, match="not been confirmed by anybody"):
            ValuationMandate(
                model=ValuationModel.COMPS_MULTIPLES,
                subject=BANK,
                sector_key="banks",
                confirmed_by="",
            )

    def test_a_confirmer_without_a_sector_is_incoherent(self):
        with pytest.raises(ModelNotPermittedError, match="names a confirmer but no sector"):
            ValuationMandate(
                model=ValuationModel.DCF_FCFF,
                subject=ORDINARY,
                sector_key="",
                confirmed_by="analyst@example.invalid",
            )

    def test_an_unknown_sector_key_is_refused_rather_than_ignored(self):
        """A classification into a sector with no profile enforces nothing.

        Ignoring it would be worse than refusing, because the run would look classified and
        behave unclassified.
        """
        with pytest.raises(ModelNotPermittedError, match="not a sector profile"):
            ValuationMandate(
                model=ValuationModel.DCF_FCFF,
                subject=ORDINARY,
                sector_key="crypto_miners",
                confirmed_by="analyst@example.invalid",
            )

    def test_a_mandate_must_name_a_company(self):
        with pytest.raises(ModelNotPermittedError, match="names no company"):
            unclassified_mandate(ValuationModel.DCF_FCFF, subject="")


# -- The ordinary case -----------------------------------------------------------------------


class TestAnUnclassifiedCompanyRunsTheStandardModel:
    def test_the_discounted_cash_flow_computes(self, context):
        mandate = unclassified_mandate(ValuationModel.DCF_FCFF, subject=ORDINARY)

        result = discounted_cash_flow(context, inputs(), mandate=mandate)

        assert result.gordon.value_per_share.value > 0

    def test_it_carries_no_sector_warnings(self):
        mandate = unclassified_mandate(ValuationModel.DCF_FCFF, subject=ORDINARY)

        assert mandate.warnings == ()
        assert mandate.required_metrics == ()

    def test_a_permitted_specialist_sector_computes_too(self, context):
        """Utilities are specialist and permit the standard model. Blocked is not a synonym."""
        utilities = profile_for("utilities")
        assert utilities is not None
        mandate = mandate_for(
            ValuationModel.DCF_FCFF,
            subject="NG",
            profile=utilities,
            confirmed_by="analyst@example.invalid",
        )

        result = discounted_cash_flow(context, inputs(), mandate=mandate)

        assert result.gordon.value_per_share.value > 0
        assert "regulatory settlement" in " ".join(mandate.warnings)

    def test_the_mandate_carries_the_profile_forward(self):
        utilities = profile_for("utilities")
        assert utilities is not None
        mandate = mandate_for(
            ValuationModel.DCF_FCFF,
            subject="NG",
            profile=utilities,
            confirmed_by="analyst@example.invalid",
        )

        assert mandate.required_metrics == (
            "regulated_asset_base",
            "allowed_return",
            "regulatory_period_end",
        )
        assert str(mandate) == "dcf_fcff for NG (utilities)"


# -- The classification hint -----------------------------------------------------------------


class TestSuggestingFromSic:
    @pytest.mark.parametrize(
        ("sic", "expected"),
        [
            ("6021", "banks"),
            ("6022", "banks"),
            ("6798", "reits"),
            ("4911", "utilities"),
            ("2836", "biotech_pre_revenue"),
            ("7372", "early_stage_tech"),
        ],
    )
    def test_a_matching_code_suggests_its_profile(self, sic, expected):
        assert suggested_profiles(sic)[0].key == expected

    def test_an_ordinary_code_suggests_nothing(self):
        # Retail bakeries. Not a sector this platform treats specially, and it should not
        # invent a classification for one.
        assert suggested_profiles("5461") == ()

    def test_an_empty_code_suggests_nothing(self):
        assert suggested_profiles("") == ()
        assert suggested_profiles("   ") == ()

    def test_the_longest_prefix_wins(self):
        """Asserted against a constructed pair, because the seed cannot show it.

        No two seeded profiles share a prefix relationship, so against the real registry this
        rule is unobservable and a test using the seed would assert nothing — which is what a
        sabotage run demonstrated by reversing the sort order and passing.
        """
        broad = SectorProfile(key="broad", label="Broad", sic_prefixes=("65",))
        narrow = SectorProfile(key="narrow", label="Narrow", sic_prefixes=("6512",))

        assert suggested_profiles("6512", profiles=(broad, narrow))[0].key == "narrow"
        assert suggested_profiles("6512", profiles=(narrow, broad))[0].key == "narrow"

    def test_no_two_seeded_profiles_overlap_by_prefix(self):
        """Why the rule above is currently unobservable — and a warning when it stops being.

        A profile added with prefix `65` while REITs hold `6512` would make the ordering
        load-bearing for the first time. This fails on that day rather than after somebody
        notices a misclassification.
        """
        pairs = [
            (a.key, prefix_a, b.key, prefix_b)
            for a in SECTOR_PROFILES
            for b in SECTOR_PROFILES
            if a.key != b.key
            for prefix_a in a.sic_prefixes
            for prefix_b in b.sic_prefixes
            if prefix_b.startswith(prefix_a)
        ]
        assert pairs == []

    def test_it_is_a_hint_and_says_so_by_returning_several(self):
        """A code matching two profiles returns both rather than picking silently."""
        matches = {profile.key for profile in suggested_profiles("6512")}
        assert matches == {"reits"}


# -- The profiles themselves -----------------------------------------------------------------


class TestTheProfiles:
    def test_no_model_is_both_allowed_and_blocked(self):
        """The invariant the schema could not express as a CHECK constraint."""
        for profile in SECTOR_PROFILES:
            overlap = set(profile.allowed_models) & set(profile.blocked_models)
            assert not overlap, f"{profile.key}: {overlap}"

    def test_every_profile_that_blocks_something_offers_something(self):
        """A profile that blocks every model it knows leaves an operator nowhere to go."""
        for profile in SECTOR_PROFILES:
            if profile.blocked_models:
                assert profile.allowed_models, profile.key

    def test_every_profile_states_its_warnings(self):
        for profile in SECTOR_PROFILES:
            assert profile.warnings, profile.key

    def test_every_profile_names_the_metrics_it_needs(self):
        for profile in SECTOR_PROFILES:
            assert profile.required_metrics, profile.key

    @pytest.mark.parametrize("key", ["banks", "insurers", "reits", "biotech_pre_revenue"])
    def test_the_sectors_that_must_block_fcff_do(self, key):
        """Named individually, so removing a block from the seed fails here rather than
        showing up as a plausible discounted cash flow on a bank."""
        profile = profile_for(key)
        assert profile is not None
        assert ValuationModel.DCF_FCFF in profile.blocked_models
        assert not profile.permits(ValuationModel.DCF_FCFF)

    def test_comparable_multiples_are_permitted_everywhere(self):
        """Relative judgement survives where a model of the business does not."""
        for profile in SECTOR_PROFILES:
            assert profile.permits(ValuationModel.COMPS_MULTIPLES), profile.key


# -- Non-operating items are unaffected ------------------------------------------------------


class TestTheMandateDoesNotChangeTheArithmetic:
    def test_the_same_inputs_give_the_same_answer_under_any_permitting_mandate(self, context):
        """The block is permission, not a modifier. A permitted DCF is the ordinary DCF."""
        utilities = profile_for("utilities")
        assert utilities is not None

        unclassified = discounted_cash_flow(
            context, inputs(), mandate=unclassified_mandate(ValuationModel.DCF_FCFF, subject="X")
        )
        classified = discounted_cash_flow(
            context,
            inputs(),
            mandate=mandate_for(
                ValuationModel.DCF_FCFF,
                subject="X",
                profile=utilities,
                confirmed_by="analyst@example.invalid",
            ),
        )

        assert unclassified.gordon.value_per_share.value == classified.gordon.value_per_share.value

    def test_a_bridge_item_is_still_a_bridge_item(self, context):
        mandate = unclassified_mandate(ValuationModel.DCF_FCFF, subject=ORDINARY)
        plain = discounted_cash_flow(context, inputs(), mandate=mandate)
        adjusted = discounted_cash_flow(
            context,
            dataclasses.replace(
                inputs(), non_operating=(BridgeItem("Listed investments", usd("300")),)
            ),
            mandate=mandate,
        )

        assert adjusted.gordon.equity_value.value - plain.gordon.equity_value.value == Decimal(
            "300"
        )
