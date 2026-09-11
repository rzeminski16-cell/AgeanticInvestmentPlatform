"""Every numeral a note states, checked against the filed figures by code.

The concept and period a numeral refers to come from the sentence around it
(`audit.scoring.numerals`); whether the number is right comes from here, and only from
here — the same division the platform draws between a model proposing and code confirming.
A numeral *reads as* a stored figure under the platform's own readings (`reads_as`: the
value at the precision the note chose, in units, thousands, millions, billions, or as a
percentage), and nothing else counts as a match.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any, Final

from aer.core.figures import READINGS, reads_as
from audit.scoring.facts import Truth
from audit.scoring.numerals import Numeral

__all__ = ["Classification", "classify_all", "summarise"]

# A numeral's concept -> the basket concepts it may be checked against.
_TO_BASKET: Final[dict[str, tuple[str, ...]]] = {
    "revenue": ("revenue",),
    "gross_profit": ("gross_profit",),
    "gross_margin": ("gross_margin",),
    "operating_income": ("operating_income",),
    "operating_margin": ("operating_margin",),
    "net_income": ("net_income",),
    "net_margin": ("net_margin",),
    "operating_cash_flow": ("operating_cash_flow",),
    "capex": ("capex",),
    "free_cash_flow": ("free_cash_flow",),
    "cash": ("cash",),
    "total_debt": ("long_term_debt",),
    "long_term_debt": ("long_term_debt",),
    "total_assets": ("total_assets",),
    "equity": ("equity",),
    "research_and_development": ("research_and_development",),
    "eps_diluted": ("eps_diluted",),
    "diluted_shares": ("diluted_shares", "shares_outstanding"),
    "shares_outstanding": ("shares_outstanding", "diluted_shares"),
    "roe": ("roe",),
    "growth": ("revenue_growth", "net_income_growth"),
    "revenue_growth": ("revenue_growth",),
    "net_income_growth": ("net_income_growth",),
    "dividends": ("dividends",),
    "buybacks": ("buybacks",),
    "net_interest_income": ("net_interest_income",),
    "deposits": ("deposits",),
    "loans": ("loans",),
}
# Concepts a filing does not carry: prices, multiples, judgements. Never contradicted here.
_OUTSIDE: Final = frozenset(
    {
        "market_cap",
        "enterprise_value",
        "share_price",
        "pe",
        "ev_ebitda",
        "wacc",
        "cost_of_equity",
        "beta",
        "terminal_growth",
        "risk_free_rate",
        "equity_risk_premium",
        "net_debt",
        "ebitda",
        "ebitda_margin",
        "net_interest_margin",
        "roa",
    }
)
APPROX_TOLERANCE: Final = Decimal("0.02")


@dataclass(frozen=True, slots=True)
class Classification:
    kind: str
    token: str
    context: str
    concept: str | None
    period: str | None
    basket_concept: str | None
    basket_period: str | None
    stored: str | None
    relative_error: str | None


def _result(
    kind: str,
    numeral: Numeral,
    *,
    basket_concept: str | None = None,
    basket_period: str | None = None,
    stored: Decimal | None = None,
    relative_error: Decimal | None = None,
) -> Classification:
    return Classification(
        kind=kind,
        token=numeral.token,
        context=numeral.context,
        concept=numeral.concept,
        period=numeral.period,
        basket_concept=basket_concept,
        basket_period=basket_period,
        stored=str(stored) if stored is not None else None,
        relative_error=(
            str(relative_error.quantize(Decimal("0.0001"))) if relative_error is not None else None
        ),
    )


def _relative_error(quoted: Decimal, stored: Decimal) -> Decimal | None:
    """The smallest relative distance under any reading the platform admits."""
    best: Decimal | None = None
    for scale in READINGS:
        reference = stored * scale
        if reference == 0:
            continue
        try:
            error = abs(quoted - reference) / abs(reference)
        except (DivisionByZero, InvalidOperation):
            continue
        if best is None or error < best:
            best = error
    return best


def _year_of(truth: Truth, period: str | None) -> int | str | None:
    """The fiscal year a period hint names; ``"outside"`` for a date that is not a year end."""
    if period is None:
        return None
    if period.startswith("FY") and period[2:].isdigit():
        return int(period[2:])
    if period.startswith("D"):
        iso = period[1:]
        for obs in truth.observations:
            if obs.end == iso:
                return obs.fiscal_year
        return "outside"
    return None


def _candidates(
    truth: Truth, concepts: tuple[str, ...], period: str | None
) -> list[tuple[str, int, Decimal]]:
    years = truth.periods()
    resolved = _year_of(truth, period)
    if resolved == "outside":
        return []
    wanted_year = resolved if isinstance(resolved, int) else None
    out: list[tuple[str, int, Decimal]] = []
    for concept in concepts:
        for year in years:
            if wanted_year is not None and year != wanted_year:
                continue
            value = truth.value(concept, year)
            if value is not None:
                out.append((concept, year, value))
    return out


def classify(numeral: Numeral, truth: Truth) -> Classification:
    if numeral.excluded:
        return _result("excluded", numeral)
    if numeral.concept in _OUTSIDE:
        return _result("outside-truth", numeral)
    # The basket is annual. A quarter's figure has nothing to be checked against, and
    # checking it against the year it falls in would manufacture a contradiction.
    if numeral.period is not None and numeral.period.startswith("Q"):
        return _result("outside-truth", numeral)
    # A balance at a date that is not a fiscal year end (a quarter-end column) likewise.
    if _year_of(truth, numeral.period) == "outside":
        return _result("outside-truth", numeral)
    concepts = _TO_BASKET.get(numeral.concept or "")
    quoted = numeral.value
    if concepts:
        hinted = _candidates(truth, concepts, numeral.period)
        for concept, year, stored in hinted:
            if reads_as(quoted, stored, sign_matters=False):
                return _result(
                    "matched",
                    numeral,
                    basket_concept=concept,
                    basket_period=f"FY{year}",
                    stored=stored,
                    relative_error=Decimal(0),
                )
        best: tuple[Decimal, str, int, Decimal] | None = None
        for concept, year, stored in hinted:
            error = _relative_error(quoted, stored)
            if error is not None and (best is None or error < best[0]):
                best = (error, concept, year, stored)
        if best is not None:
            error, concept, year, stored = best
            if error <= APPROX_TOLERANCE:
                return _result(
                    "approx",
                    numeral,
                    basket_concept=concept,
                    basket_period=f"FY{year}",
                    stored=stored,
                    relative_error=error,
                )
            if numeral.period is not None:
                return _result(
                    "contradicted",
                    numeral,
                    basket_concept=concept,
                    basket_period=f"FY{year}",
                    stored=stored,
                    relative_error=error,
                )
            return _result(
                "checkable-absent",
                numeral,
                basket_concept=concept,
                stored=stored,
                relative_error=error,
            )
        return _result("checkable-absent", numeral, basket_concept=concepts[0])
    # No usable hint: does it read as anything in the basket at all? Small integers read as
    # too many things to mean anything, so they stay unknown.
    every = tuple(sorted({c for cs in _TO_BASKET.values() for c in cs}))
    if abs(quoted) >= Decimal(1):
        for concept, year, stored in _candidates(truth, every, numeral.period):
            if reads_as(quoted, stored, sign_matters=False):
                return _result(
                    "matched-unhinted",
                    numeral,
                    basket_concept=concept,
                    basket_period=f"FY{year}",
                    stored=stored,
                    relative_error=Decimal(0),
                )
    return _result("unknown", numeral)


def classify_all(numerals: tuple[Numeral, ...], truth: Truth) -> list[Classification]:
    return [classify(n, truth) for n in numerals]


def summarise(classifications: list[Classification]) -> dict[str, Any]:
    kinds = Counter(c.kind for c in classifications)
    checkable = sum(kinds[k] for k in ("matched", "matched-unhinted", "approx", "contradicted"))
    return {
        "numerals": len(classifications),
        "by_kind": dict(kinds),
        "checkable": checkable,
        "matched": kinds["matched"] + kinds["matched-unhinted"],
        "approx": kinds["approx"],
        "contradicted": kinds["contradicted"],
        "contradicted_share_of_checkable": (kinds["contradicted"] / checkable)
        if checkable
        else None,
        "contradictions": [asdict(c) for c in classifications if c.kind == "contradicted"],
        "approximations": [asdict(c) for c in classifications if c.kind == "approx"][:40],
    }
