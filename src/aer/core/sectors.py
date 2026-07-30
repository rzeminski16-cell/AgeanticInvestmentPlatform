"""Which valuation models a sector permits, and which it forbids.

A discounted cash flow on a bank is not a rough answer, it is a meaningless one: a bank's
debt is its raw material rather than its financing, so enterprise value and free cash flow
to the firm do not mean what the model assumes they mean. The same is true of an insurer,
and of a REIT whose economics are depreciation-dominated in a way the accounts obscure.

``docs/PLAN.md`` section 2.9 states the rule this encodes: **a blocked model produces a
hard gate, not a footnote.** A report that ran the standard model anyway and disclaimed it
in small print is worse than one that refused, because the number is what a reader
remembers.

**This module is the vocabulary and the seed; nothing consumes it yet.** Phase 3 builds the
classifier and the gate. It lives here now because the seed ships in migration 0014, and a
table seeded with rows nobody can compare against application code is data with no owner.
:mod:`aer.db.models.sector_profile` holds the table, and a test asserts the seeded rows and
:data:`SECTOR_PROFILES` still agree — a migration and a constant drifting apart would be
silent, and the symptom would be a gate that fires on the wrong sectors.

Pure: no I/O, no database. ``mypy --strict``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

__all__ = ["SECTOR_PROFILES", "SectorProfile", "ValuationModel", "profile_for"]


class ValuationModel(StrEnum):
    """A way of arriving at a value.

    Named rather than described, so "may we run this here?" is a set membership test in
    code instead of a judgement in prose.
    """

    DCF_FCFF = "dcf_fcff"
    """Discounted free cash flow to the firm. The standard model, and the one most often
    blocked."""

    DCF_FCFE = "dcf_fcfe"
    """Free cash flow to equity. Survives some cases FCFF does not, because it does not
    require enterprise value to mean anything."""

    DIVIDEND_DISCOUNT = "dividend_discount"
    COMPS_MULTIPLES = "comps_multiples"
    """Comparable-company multiples. Almost always allowed: it asserts a relative
    judgement rather than a model of the business."""

    NET_ASSET_VALUE = "net_asset_value"
    RISK_ADJUSTED_NPV = "risk_adjusted_npv"
    """Pipeline-probability weighted NPV, for pre-revenue biotech."""

    SUM_OF_THE_PARTS = "sum_of_the_parts"


@dataclass(frozen=True, slots=True)
class SectorProfile:
    """What the platform may and may not do for one kind of business.

    ``blocked_models`` is not the complement of ``allowed_models``. A model appears in
    neither list when it is simply not implemented — which is a different statement from
    "this would be wrong here", and only the second justifies stopping a run.
    """

    key: str
    label: str

    # Classification hints. Coarse on purpose: the classifier is a Phase 3 agent whose
    # proposal a human confirms, and a lookup table pretending to be exhaustive would
    # invite trusting it.
    sic_prefixes: tuple[str, ...] = ()
    icb_codes: tuple[str, ...] = ()

    allowed_models: tuple[ValuationModel, ...] = ()
    blocked_models: tuple[ValuationModel, ...] = ()

    # Metrics without which a report on this sector is not worth reading. Absence is
    # disclosed rather than passed over.
    required_metrics: tuple[str, ...] = ()

    warnings: tuple[str, ...] = field(default_factory=tuple)

    def permits(self, model: ValuationModel) -> bool:
        return model in self.allowed_models and model not in self.blocked_models


_STANDARD: Final = (
    ValuationModel.DCF_FCFF,
    ValuationModel.DCF_FCFE,
    ValuationModel.COMPS_MULTIPLES,
)


SECTOR_PROFILES: Final[tuple[SectorProfile, ...]] = (
    SectorProfile(
        key="banks",
        label="Banks",
        sic_prefixes=("602", "6021", "6022", "6029"),
        icb_codes=("301010",),
        allowed_models=(ValuationModel.COMPS_MULTIPLES, ValuationModel.DIVIDEND_DISCOUNT),
        blocked_models=(ValuationModel.DCF_FCFF,),
        required_metrics=(
            "net_interest_margin",
            "cet1_ratio",
            "cost_income_ratio",
            "loan_loss_provisions",
            "tangible_book_value_per_share",
        ),
        warnings=(
            "Enterprise value and free cash flow to the firm are not meaningful for a bank: "
            "deposits and debt are raw material, not financing.",
            "Capital adequacy, net interest margin and provisioning govern the valuation. "
            "This build produces P/TBV and P/E comparables only, and does not implement a "
            "specialist bank model.",
        ),
    ),
    SectorProfile(
        key="insurers",
        label="Insurers",
        sic_prefixes=("631", "632", "633", "6311", "6331"),
        icb_codes=("303010", "303020"),
        allowed_models=(ValuationModel.COMPS_MULTIPLES, ValuationModel.DIVIDEND_DISCOUNT),
        blocked_models=(ValuationModel.DCF_FCFF,),
        required_metrics=(
            "combined_ratio",
            "reserve_development",
            "embedded_value",
            "solvency_ratio",
        ),
        warnings=(
            "Free cash flow to the firm is not meaningful for an insurer; float is a "
            "liability that funds the assets.",
            "Combined ratio, reserve development and embedded value are required and are "
            "flagged as absent rather than estimated.",
        ),
    ),
    SectorProfile(
        key="reits",
        label="REITs and property companies",
        sic_prefixes=("6798", "6512", "6531"),
        icb_codes=("351020", "351030"),
        allowed_models=(ValuationModel.COMPS_MULTIPLES, ValuationModel.NET_ASSET_VALUE),
        blocked_models=(ValuationModel.DCF_FCFF,),
        required_metrics=("ffo", "affo", "net_asset_value", "occupancy", "loan_to_value"),
        warnings=(
            "Depreciation dominates reported earnings for a property company, so FCFF is "
            "not the cash the business generates.",
            "FFO/AFFO and NAV are required. This build produces P/FFO comparables and "
            "discloses that NAV was not computed.",
        ),
    ),
    SectorProfile(
        key="utilities",
        label="Utilities and regulated networks",
        sic_prefixes=("491", "492", "493", "4911", "4931"),
        icb_codes=("651010", "651020"),
        allowed_models=_STANDARD,
        required_metrics=("regulated_asset_base", "allowed_return", "regulatory_period_end"),
        warnings=(
            "Returns are set by a regulator over a defined period, so the terminal value "
            "depends on a regulatory settlement rather than on competitive dynamics.",
            "State the regulatory period and the allowed return, or the model is a guess "
            "dressed as a forecast.",
        ),
    ),
    SectorProfile(
        key="biotech_pre_revenue",
        label="Pre-revenue biotechnology",
        sic_prefixes=("2836", "8731"),
        icb_codes=("201020",),
        allowed_models=(ValuationModel.COMPS_MULTIPLES,),
        blocked_models=(ValuationModel.DCF_FCFF, ValuationModel.DCF_FCFE),
        required_metrics=("cash_runway_months", "pipeline_stage", "trial_readout_dates"),
        warnings=(
            "There are no cash flows to discount. A DCF here is arithmetic performed on "
            "assumptions, and the answer is whatever the assumptions were.",
            "Risk-adjusted NPV over the pipeline is the right model and is not implemented "
            "in this build.",
        ),
    ),
    SectorProfile(
        key="mining_energy",
        label="Mining, oil and gas",
        sic_prefixes=("101", "131", "1311", "1221"),
        icb_codes=("551010", "601010"),
        allowed_models=_STANDARD,
        required_metrics=("reserve_life", "commodity_price_deck", "all_in_sustaining_cost"),
        warnings=(
            "Reserve life and the commodity price deck dominate the valuation; the discount "
            "rate is a detail beside them.",
            "State the commodity assumption explicitly. A resource model with an implicit "
            "price forecast is untestable.",
        ),
    ),
    SectorProfile(
        key="early_stage_tech",
        label="Early-stage and loss-making technology",
        sic_prefixes=("737", "7372", "7379"),
        icb_codes=("101010", "102010"),
        allowed_models=_STANDARD,
        required_metrics=("revenue_growth", "gross_margin", "rule_of_40", "cash_runway_months"),
        warnings=(
            "Terminal value will be most of the answer. Report the terminal-value share and "
            "run a sensitivity on it, or the model conceals where the number came from.",
        ),
    ),
    SectorProfile(
        key="holding_companies",
        label="Holding companies and conglomerates",
        sic_prefixes=("6719", "6742"),
        icb_codes=("302020",),
        allowed_models=(ValuationModel.COMPS_MULTIPLES,),
        required_metrics=("segment_revenue", "segment_operating_profit", "stake_percentages"),
        warnings=(
            "A consolidated model of a holding company values an accounting artefact. Sum "
            "of the parts is the right approach and is not implemented in this build.",
            "Disclose the holding-company discount as an assumption rather than embedding "
            "it in a multiple.",
        ),
    ),
)


_BY_KEY: Final[dict[str, SectorProfile]] = {profile.key: profile for profile in SECTOR_PROFILES}


def profile_for(key: str) -> SectorProfile | None:
    """The profile with this key, or ``None``.

    ``None`` rather than a permissive default. A sector nobody has written a profile for is
    an ordinary company under the standard model, and the caller says so explicitly instead
    of receiving a blank profile that silently allows everything.
    """
    return _BY_KEY.get(key)
