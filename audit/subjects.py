"""The four commissions the audit makes, and what the operator brings to each.

Everything a run needs from a person is written down here before any run starts — the
brief, the expected sector, and the assumptions no filing answers, each with the source it
rests on — so the driver's decisions at the gates are the operator's decisions, made in
advance and on the record, rather than a script's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

__all__ = [
    "RATIO_UNIT",
    "SUBJECTS",
    "OperatorAssumption",
    "Subject",
    "brief_for",
    "subject_for",
]

# The unit the platform stores a rate or a ratio under (`aer.services.assumption_proposals`).
RATIO_UNIT: Final = "pure"


@dataclass(frozen=True, slots=True)
class OperatorAssumption:
    """A number the operator states because no filing answers it, and where it comes from."""

    name: str
    value: Decimal
    unit: str
    justification: str


@dataclass(frozen=True, slots=True)
class Subject:
    """One commission: what the platform is asked, and what the operator brings to it."""

    key: str
    company_name: str
    ticker: str
    exchange: str
    base_currency: str
    reporting_currency: str
    horizon_months: int
    focus_questions: tuple[str, ...]
    use_case: str
    # The sector the classifier is expected to propose, or None when the sector gate should
    # not fire at all (an ordinary operating company).
    expected_sector: str | None = None
    # Operator-owned assumptions the driver supplies when the gate lists them outstanding.
    assumptions: tuple[OperatorAssumption, ...] = field(default_factory=tuple)


# The 10-year Treasury constant-maturity yield, FRED series DGS10, observation 2026-09-09:
# 4.83 %. Fetched from the FRED API on 2026-09-11 with the operator's key.
_RISK_FREE = OperatorAssumption(
    name="risk_free_rate",
    value=Decimal("0.0483"),
    unit=RATIO_UNIT,
    justification=(
        "US 10-year Treasury constant-maturity yield, FRED series DGS10, observation "
        "2026-09-09: 4.83 %. Stated by the operator; the platform acquires no macro series "
        "for a run."
    ),
)

_ERP = OperatorAssumption(
    name="equity_risk_premium",
    value=Decimal("0.045"),
    unit=RATIO_UNIT,
    justification=(
        "4.5 %: inside the 4-6 % band the operator's audit policy accepts, and the "
        "neighbourhood of the implied US premium in the Damodaran monthly series through "
        "2026. A judgement, stated as one."
    ),
)

_TERMINAL_GROWTH = OperatorAssumption(
    name="terminal_growth",
    value=Decimal("0.025"),
    unit=RATIO_UNIT,
    justification=(
        "2.5 % nominal, below the risk-free rate and inside the 0-3.5 % band. A convention, "
        "stated as one; used only if the platform proposes none."
    ),
)


def _beta(value: str, why: str) -> OperatorAssumption:
    return OperatorAssumption(
        name="beta",
        value=Decimal(value),
        unit=RATIO_UNIT,
        justification=(
            f"{why} Used only when the platform's own regression proposes nothing, and "
            "recorded as a finding when it is."
        ),
    )


def _derived(name: str, value: str, basis: str) -> OperatorAssumption:
    """A driver the platform normally derives from the filings.

    Supplied only when the gate lists it outstanding — which means the concept map could not
    place the line the derivation needs — and recorded as a completeness finding when it is.
    The value is the operator's trailing-average reading of the filings, stated as such.
    """
    return OperatorAssumption(
        name=name,
        value=Decimal(value),
        unit=RATIO_UNIT,
        justification=(
            f"{basis} Stated by the operator because the platform derived nothing for it; "
            "recorded as a completeness finding."
        ),
    )


_MSFT_DERIVED = (
    _derived(
        "revenue_growth",
        "0.15",
        "Trailing three-year revenue growth near 15 % (FY2023-FY2025 10-Ks).",
    ),
    _derived("ebit_margin", "0.45", "Operating margin near 45 % in FY2025 (10-K)."),
    _derived(
        "capex_intensity",
        "0.25",
        "Capital expenditure near a quarter of revenue in FY2025 (10-K cash flow statement).",
    ),
    _derived(
        "depreciation_intensity",
        "0.10",
        "Depreciation and amortisation near 10 % of revenue in FY2025 (10-K).",
    ),
    _derived(
        "working_capital_intensity", "0.00", "Working capital roughly flat as a share of revenue."
    ),
    _derived("tax_rate", "0.18", "Effective tax rate near 18 % in FY2025 (10-K)."),
)

_AZN_DERIVED = (
    _derived(
        "revenue_growth", "0.12", "Trailing revenue growth in the low teens (20-F, FY2023-FY2025)."
    ),
    _derived("ebit_margin", "0.22", "Operating margin in the low twenties in FY2025 (20-F)."),
    _derived("capex_intensity", "0.05", "Capital expenditure near 5 % of revenue (20-F)."),
    _derived(
        "depreciation_intensity",
        "0.10",
        "Depreciation and amortisation near 10 % of revenue (20-F).",
    ),
    _derived(
        "working_capital_intensity", "0.00", "Working capital roughly flat as a share of revenue."
    ),
    _derived("tax_rate", "0.20", "Effective tax rate near 20 % (20-F)."),
)

_MTB_DERIVED = (
    _derived("return_on_equity", "0.10", "Return on equity near 10 % in FY2025 (10-K)."),
    _derived("payout_ratio", "0.50", "Dividends and buybacks near half of earnings (10-K)."),
)


_COST_OF_DEBT = OperatorAssumption(
    name="cost_of_debt",
    value=Decimal("0.05"),
    unit=RATIO_UNIT,
    justification=(
        "5.0 % pre-tax: the 10-year Treasury yield plus a modest investment-grade spread. "
        "Used only when the filings carry debt and no interest expense the platform maps, "
        "and recorded as a finding when it is."
    ),
)

_MSFT_QUESTIONS: Final = (
    "Is the cloud margin expansion durable, or is capitalised infrastructure flattering it?",
    "What would have to be true for the current multiple to be justified?",
    "How much of the growth is AI revenue that did not exist three years ago?",
)

_AZN_QUESTIONS: Final = (
    "How exposed is revenue to the patent expiries falling due inside the horizon?",
    "Is the oncology pipeline's contribution already in the price?",
    "What does the US drug-pricing environment do to the margin outlook?",
)

_MTB_QUESTIONS: Final = (
    "How exposed is the loan book to commercial real estate, and how is it reserved?",
    "Is the net interest margin at a peak, and what does deposit repricing do to it?",
    "Does the return on tangible equity justify the multiple of book value?",
)

SUBJECTS: Final[tuple[Subject, ...]] = (
    Subject(
        key="msft1",
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        base_currency="USD",
        reporting_currency="USD",
        horizon_months=12,
        focus_questions=_MSFT_QUESTIONS,
        use_case="First deep dive on a US large cap before taking a position.",
        assumptions=(
            _RISK_FREE,
            _ERP,
            _TERMINAL_GROWTH,
            _beta("0.9", "Microsoft's published five-year monthly beta sits near 0.9."),
            _COST_OF_DEBT,
            *_MSFT_DERIVED,
        ),
    ),
    Subject(
        key="msft2",
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        base_currency="USD",
        reporting_currency="USD",
        horizon_months=12,
        focus_questions=_MSFT_QUESTIONS,
        use_case="Refreshing a company already researched, the next day.",
        assumptions=(
            _RISK_FREE,
            _ERP,
            _TERMINAL_GROWTH,
            _beta("0.9", "Microsoft's published five-year monthly beta sits near 0.9."),
            _COST_OF_DEBT,
            *_MSFT_DERIVED,
        ),
    ),
    Subject(
        key="azn",
        company_name="AstraZeneca PLC",
        # EDGAR's exchange file lists the ADR on the NYSE, and the platform resolves the
        # ticker against that file; a request naming NASDAQ is refused with the reason.
        ticker="AZN",
        exchange="NYSE",
        base_currency="USD",
        reporting_currency="USD",
        horizon_months=12,
        focus_questions=_AZN_QUESTIONS,
        use_case="A UK plc researched through its 20-F, under IFRS.",
        assumptions=(
            _RISK_FREE,
            _ERP,
            _TERMINAL_GROWTH,
            _beta("0.4", "A large pharmaceutical's published beta sits well below one."),
            _COST_OF_DEBT,
            *_AZN_DERIVED,
        ),
    ),
    Subject(
        key="azn2",
        company_name="AstraZeneca PLC",
        ticker="AZN",
        exchange="NYSE",
        base_currency="USD",
        reporting_currency="USD",
        horizon_months=12,
        focus_questions=_AZN_QUESTIONS,
        use_case=(
            "The same UK plc again, on the corrected IFRS aliases and after an approved "
            "report exists: the valuation the first run withheld, and the refresh section "
            "that refused MSFT's second run."
        ),
        assumptions=(
            _RISK_FREE,
            _ERP,
            _TERMINAL_GROWTH,
            _beta("0.4", "A large pharmaceutical's published beta sits well below one."),
            _COST_OF_DEBT,
            *_AZN_DERIVED,
        ),
    ),
    Subject(
        key="tsco",
        company_name="Tesco PLC",
        ticker="TSCO",
        exchange="LSE",
        base_currency="GBP",
        reporting_currency="GBP",
        horizon_months=12,
        focus_questions=("Is the UK grocery margin defensible against the discounters?",),
        use_case=(
            "A UK company with no SEC filings: expected to be refused at acquisition, and "
            "driven only offline to prove it."
        ),
    ),
    Subject(
        key="mtb",
        company_name="M&T Bank Corporation",
        ticker="MTB",
        exchange="NYSE",
        base_currency="USD",
        reporting_currency="USD",
        horizon_months=12,
        focus_questions=_MTB_QUESTIONS,
        use_case="A bank: the sector gate and the residual-income model.",
        expected_sector="banks",
        assumptions=(
            _RISK_FREE,
            _ERP,
            _TERMINAL_GROWTH,
            _beta("1.0", "A regional bank's published beta sits near the market's."),
            *_MTB_DERIVED,
        ),
    ),
)


def subject_for(key: str) -> Subject:
    """The commission named ``key``, or a ``KeyError`` naming the ones that exist."""
    for subject in SUBJECTS:
        if subject.key == key:
            return subject
    message = f"No subject {key!r}; the audit knows {', '.join(s.key for s in SUBJECTS)}."
    raise KeyError(message)


def brief_for(subject: Subject, *, as_of: str, section_titles: tuple[str, ...]) -> str:
    """The brief the baseline receives: the same request the platform gets, in prose.

    Written once so the two sides of the comparison are asked the same thing. The platform
    gets its half through :class:`aer.core.schemas.request.ResearchRequestCreate` and its
    section list from ``section_definitions``; the baseline gets both in this text.
    """
    questions = "\n".join(f"- {q}" for q in subject.focus_questions)
    sections = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(section_titles))
    return (
        f"Write an institutional-style equity research note on {subject.company_name} "
        f"({subject.ticker}, {subject.exchange}) as at {as_of}, for a "
        f"{subject.horizon_months}-month investment horizon, in {subject.base_currency}.\n\n"
        "Use web search to find and read the primary sources — the latest annual report or "
        "20-F, the most recent quarterly filing, the proxy statement, the latest results "
        "presentation, and the current share price and share count — and cite a URL for "
        "every figure and every factual claim. Do not state a number you have not read in a "
        "source you fetched. Compute every ratio, growth rate, cost of capital and valuation "
        "from the figures you read, and show the inputs beside each result. Include a "
        "discounted cash flow with every assumption stated and justified, a sensitivity "
        "grid, bear, base and bull scenarios, and a comparable-companies table. Where "
        "something cannot be sourced, say so rather than estimating.\n\n"
        f"Answer these questions in the body:\n{questions}\n\n"
        f"Structure the note under these sections, in this order:\n{sections}\n\n"
        "Finish with an appendix listing every source you used with its URL and the date it "
        "was published. State on the first page that this is a personal research document "
        "and not investment advice."
    )
