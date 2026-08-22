"""Which valuation models a sector permits, and which it forbids.

A discounted cash flow on a bank is not a rough answer, it is a meaningless one: a bank's
debt is its raw material rather than its financing, so enterprise value and free cash flow
to the firm do not mean what the model assumes they mean. The same is true of an insurer,
and of a REIT whose economics are depreciation-dominated in a way the accounts obscure.

``docs/PLAN.md`` section 2.9 states the rule this encodes: **a blocked model produces a
hard gate, not a footnote.** A report that ran the standard model anyway and disclaimed it
in small print is worse than one that refused, because the number is what a reader
remembers.

**The enforcement is a capability, not a check somebody remembers to run.**
:class:`ValuationMandate` is permission to run one model on one company, and the only way to
obtain one is to pass the sector profile's rules — the validation is in ``__post_init__``, so
there is no constructor, no ``replace`` and no ``__setattr__`` that produces a mandate the
profile would not allow. :func:`aer.calc.dcf.discounted_cash_flow` takes one, so a blocked
model is not something a caller has to avoid: it is something they cannot express.

That is what `docs/phase-3-plan.md` task 28 means by *the block, not the footnote*, and by
*asserted at the calculation layer rather than at the page*. A guard in a route protects that
route. A guard in a service protects callers who go through the service. A required argument
whose type cannot be constructed for a blocked model protects every route there is.

:mod:`aer.db.models.sector_profile` holds the table, seeded in migration 0014, and a test
asserts the seeded rows and :data:`SECTOR_PROFILES` still agree — a migration and a constant
drifting apart would be silent, and the symptom would be a gate that fires on the wrong
sectors.

Pure: no I/O, no database. ``mypy --strict``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from aer.errors import AerError

__all__ = [
    "SECTOR_PROFILES",
    "ModelNotPermittedError",
    "SectorProfile",
    "ValuationMandate",
    "ValuationModel",
    "mandate_for",
    "profile_for",
    "suggested_profiles",
    "unclassified_mandate",
]


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
            "Risk-adjusted NPV over the pipeline is the right model for this company, and "
            "no such valuation is offered here.",
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
            "of the parts is the right approach, and no such valuation is offered here.",
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


# -- The mandate -------------------------------------------------------------------------


class ModelNotPermittedError(AerError):
    """A valuation model was attempted on a business its sector profile does not permit.

    Its own class rather than a `ValidationError`, because the caller's next step is
    different: a malformed request is corrected by the requester, and this is answered by
    running one of the models the profile does allow — or by disagreeing with the
    classification, which is a decision at the sector gate rather than a code change.
    """

    code = "valuation_model_not_permitted"
    http_status = 409


@dataclass(frozen=True, slots=True)
class ValuationMandate:
    """Permission to run one valuation model on one company.

    **Constructing one is the check.** Validation is in ``__post_init__``, so a mandate for a
    blocked model does not exist to be passed around: the construction raises. That is what
    lets :func:`aer.calc.dcf.discounted_cash_flow` take a mandate as a required argument and
    thereby be *uncallable* for a bank, rather than merely refusing when called.

    ``confirmed_by`` is the person who agreed the classification at the sector gate. A
    classification a model proposed and nobody confirmed cannot produce a mandate, because
    an unreviewed guess about what kind of business this is would otherwise decide which
    models are permitted — and the wrong guess in the permissive direction is a discounted
    cash flow on a bank.
    """

    model: ValuationModel

    # The company this mandate covers, as a ticker or an identifier. Not consulted by the
    # arithmetic, which knows nothing about companies; recorded so that a mandate is
    # self-describing in a log line and so the service layer can assert it matches the run.
    subject: str

    # The profile key, or "" for a company nobody classified. Empty is a real state and not
    # a missing value: most companies are ordinary and run the standard model.
    sector_key: str

    # Who confirmed the classification. Required whenever `sector_key` is set, and
    # meaningless when it is not.
    confirmed_by: str

    def __post_init__(self) -> None:
        if not self.subject:
            message = (
                "A valuation mandate names no company. A permission that does not say what "
                "it is a permission for is one that can be reused against anything."
            )
            raise ModelNotPermittedError(message, context={"model": self.model.value})

        if not self.sector_key:
            if self.confirmed_by:
                message = (
                    f"A mandate for {self.subject} names a confirmer but no sector. Either "
                    "the classification was confirmed, in which case it has a key, or it "
                    "was not, in which case nobody confirmed anything."
                )
                raise ModelNotPermittedError(message, context={"subject": self.subject})
            return

        profile = profile_for(self.sector_key)
        if profile is None:
            message = (
                f"{self.sector_key!r} is not a sector profile this platform knows. Known: "
                f"{', '.join(sorted(_BY_KEY))}. A classification into a sector with no "
                "profile enforces nothing, which is worse than no classification because it "
                "looks like enforcement."
            )
            raise ModelNotPermittedError(
                message, context={"sector": self.sector_key, "subject": self.subject}
            )

        if not self.confirmed_by:
            message = (
                f"The classification of {self.subject} as {profile.label} has not been "
                "confirmed by anybody. A model may propose what kind of business this is; "
                "only a person may agree to it, because the proposal decides which "
                "valuation models are permitted and an unreviewed guess in the permissive "
                "direction is a discounted cash flow on a bank."
            )
            raise ModelNotPermittedError(
                message, context={"sector": self.sector_key, "subject": self.subject}
            )

        if not profile.permits(self.model):
            raise ModelNotPermittedError(
                _refusal_message(self.model, profile=profile, subject=self.subject),
                context={
                    "model": self.model.value,
                    "sector": profile.key,
                    "subject": self.subject,
                    "blocked": self.model in profile.blocked_models,
                    "offered": [m.value for m in profile.allowed_models],
                },
            )

    @property
    def profile(self) -> SectorProfile | None:
        """The profile this mandate was granted under, or ``None`` when unclassified."""
        return profile_for(self.sector_key) if self.sector_key else None

    @property
    def warnings(self) -> tuple[str, ...]:
        """What a report produced under this mandate has to say about its own limits."""
        profile = self.profile
        return profile.warnings if profile is not None else ()

    @property
    def required_metrics(self) -> tuple[str, ...]:
        """The metrics a report on this sector must carry, or disclose as absent."""
        profile = self.profile
        return profile.required_metrics if profile is not None else ()

    def __str__(self) -> str:
        where = self.sector_key or "unclassified"
        return f"{self.model.value} for {self.subject} ({where})"


def _refusal_message(model: ValuationModel, *, profile: SectorProfile, subject: str) -> str:
    """Why this model is refused here, and what is offered instead.

    The message carries the profile's own warnings rather than a summary of them. A refusal
    that says "not permitted for this sector" tells a reader they have been stopped; one that
    says *why* enterprise value is meaningless for a bank tells them something they can act
    on, and is the difference between a control and an obstacle.
    """
    offered = (
        ", ".join(sorted(m.value for m in profile.allowed_models))
        if profile.allowed_models
        else "no model this platform implements"
    )

    if model in profile.blocked_models:
        opening = (
            f"{model.value} is blocked for {profile.label.lower()} and {subject} is "
            f"classified as one. This is not an approximation that would be roughly right — "
            f"it is a model whose assumptions the business does not satisfy."
        )
    else:
        opening = (
            f"{model.value} is not implemented for {profile.label.lower()}. It is not "
            "blocked as wrong; nobody has built it, and running a different model and "
            "labelling it this one would be worse."
        )

    lines = [opening, f"What this profile permits: {offered}."]
    lines.extend(profile.warnings)
    return " ".join(lines)


def mandate_for(
    model: ValuationModel, *, subject: str, profile: SectorProfile, confirmed_by: str
) -> ValuationMandate:
    """Permission to run ``model`` on ``subject``, given a confirmed classification.

    Raises:
        ModelNotPermittedError: If the profile blocks the model, does not implement it, or
            nobody confirmed the classification.
    """
    return ValuationMandate(
        model=model,
        subject=subject,
        sector_key=profile.key,
        confirmed_by=confirmed_by,
    )


def unclassified_mandate(model: ValuationModel, *, subject: str) -> ValuationMandate:
    """Permission for a company nobody has classified into a specialist sector.

    The ordinary case, and deliberately permissive: most listed companies are not banks,
    insurers, REITs or pre-revenue biotechs, and requiring a positive classification before
    anything could run would make the common path the exceptional one.

    **The safety of this rests on the gate, not on this function.** A run whose classifier
    proposed a specialist sector stops at ``SECTOR_SPECIALIST`` until somebody decides, so
    "unclassified" reaches here only when nothing was proposed at all.
    """
    return ValuationMandate(model=model, subject=subject, sector_key="", confirmed_by="")


def suggested_profiles(
    sic_code: str, *, profiles: tuple[SectorProfile, ...] = SECTOR_PROFILES
) -> tuple[SectorProfile, ...]:
    """The profiles whose SIC prefixes match, longest prefix first.

    A hint for the classifier, not a classification. SIC codes are self-reported, decades
    old in places, and a holding company files under whatever its largest subsidiary does —
    so this narrows the guess and a person still confirms it. Returning several is honest:
    a reader has to decide whether this filer really is one.

    ``profiles`` is a seam, not an option. **No two seeded profiles currently share a prefix
    relationship**, so the longest-prefix rule below is unobservable against the real
    registry — a test that only used the seed would assert nothing. A test asserts the rule
    against a constructed pair, and a second test asserts the seed's non-overlap so that the
    day it stops holding, somebody is told rather than surprised.

    An empty code needs no special case: no profile declares an empty prefix, so nothing
    matches and the answer is already ``()``.
    """
    cleaned = sic_code.strip()

    matched = [
        (len(prefix), profile)
        for profile in profiles
        for prefix in profile.sic_prefixes
        if cleaned.startswith(prefix)
    ]
    seen: dict[str, SectorProfile] = {}
    for _, profile in sorted(matched, key=lambda pair: -pair[0]):
        seen.setdefault(profile.key, profile)
    return tuple(seen.values())
