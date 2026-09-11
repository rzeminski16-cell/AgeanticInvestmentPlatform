"""Every number a note states, with what the sentence around it says the number is.

The extraction reuses the platform's own numeral scanner (`aer.core.figures.numeral_matches`)
so the audit and the product agree on what a numeral is. What this adds is the reading of
the neighbourhood: the currency, the scale word, whether it is a percentage or a multiple,
the period it belongs to, and the concept the sentence names — the hints a checker needs
before it can ask a filing whether the number is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from aer.core.figures import numeral_matches

__all__ = ["CONCEPTS", "Numeral", "extract_numerals"]

# Context words -> a concept name the ground-truth basket knows. Longest phrase wins.
CONCEPTS: Final[tuple[tuple[str, str], ...]] = (
    ("free cash flow", "free_cash_flow"),
    ("fcf", "free_cash_flow"),
    ("operating cash flow", "operating_cash_flow"),
    ("cash from operations", "operating_cash_flow"),
    ("cash flow from operations", "operating_cash_flow"),
    ("cash provided by operating", "operating_cash_flow"),
    ("capital expenditure", "capex"),
    ("capex", "capex"),
    ("purchases of property", "capex"),
    ("additions to property", "capex"),
    ("research and development", "research_and_development"),
    ("r&d", "research_and_development"),
    ("gross margin", "gross_margin"),
    ("gross profit", "gross_profit"),
    ("operating margin", "operating_margin"),
    ("operating income", "operating_income"),
    ("operating profit", "operating_income"),
    ("ebit margin", "operating_margin"),
    ("ebitda margin", "ebitda_margin"),
    ("ebitda", "ebitda"),
    ("ebit", "operating_income"),
    ("net margin", "net_margin"),
    ("net income", "net_income"),
    ("net profit", "net_income"),
    ("net earnings", "net_income"),
    ("profit attributable", "net_income"),
    ("earnings per share", "eps_diluted"),
    ("diluted eps", "eps_diluted"),
    ("eps", "eps_diluted"),
    ("total revenue", "revenue"),
    ("net sales", "revenue"),
    ("revenues", "revenue"),
    ("revenue", "revenue"),
    ("sales", "revenue"),
    ("cash and cash equivalents", "cash"),
    ("cash and equivalents", "cash"),
    ("cash balance", "cash"),
    ("total debt", "total_debt"),
    ("long-term debt", "long_term_debt"),
    ("gross debt", "total_debt"),
    ("net debt", "net_debt"),
    ("net cash", "net_debt"),
    ("shares outstanding", "shares_outstanding"),
    ("share count", "shares_outstanding"),
    ("diluted shares", "diluted_shares"),
    ("total assets", "total_assets"),
    ("shareholders' equity", "equity"),
    ("stockholders' equity", "equity"),
    ("shareholders’ equity", "equity"),
    ("stockholders’ equity", "equity"),
    ("total equity", "equity"),
    ("book value", "equity"),
    ("return on equity", "roe"),
    ("roe", "roe"),
    ("return on assets", "roa"),
    ("market capitalisation", "market_cap"),
    ("market capitalization", "market_cap"),
    ("market cap", "market_cap"),
    ("enterprise value", "enterprise_value"),
    ("share price", "share_price"),
    ("stock price", "share_price"),
    ("closed at", "share_price"),
    ("trading at", "share_price"),
    ("dividend", "dividends"),
    ("repurchase", "buybacks"),
    ("buyback", "buybacks"),
    ("net interest income", "net_interest_income"),
    ("net interest margin", "net_interest_margin"),
    ("deposits", "deposits"),
    ("loans", "loans"),
    ("terminal growth", "terminal_growth"),
    ("risk-free", "risk_free_rate"),
    ("equity risk premium", "equity_risk_premium"),
    ("wacc", "wacc"),
    ("discount rate", "wacc"),
    ("cost of equity", "cost_of_equity"),
    ("beta", "beta"),
    ("p/e", "pe"),
    ("price-to-earnings", "pe"),
    ("price to earnings", "pe"),
    ("ev/ebitda", "ev_ebitda"),
)
_RATIO_CONCEPTS: Final = frozenset(
    {
        "gross_margin",
        "operating_margin",
        "ebitda_margin",
        "net_margin",
        "roe",
        "roa",
        "net_interest_margin",
        "terminal_growth",
        "risk_free_rate",
        "equity_risk_premium",
        "wacc",
        "cost_of_equity",
        "growth",
    }
)
_SCALES: Final[tuple[tuple[re.Pattern[str], Decimal], ...]] = (
    (re.compile(r"^\s*(billion|bn)\b", re.I), Decimal(1_000_000_000)),
    (re.compile(r"^\s*(million|mn|mm)\b", re.I), Decimal(1_000_000)),
    (re.compile(r"^\s*(thousand|k)\b", re.I), Decimal(1_000)),
    (re.compile(r"^\s*trillion\b", re.I), Decimal(1_000_000_000_000)),
)
_SUFFIX_SCALES: Final = {
    "bn": Decimal(1_000_000_000),
    "m": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "k": Decimal(1_000),
}
_PERIOD: Final = re.compile(
    r"(?:FY\s?'?(?P<fy>\d{2,4}))|(?:fiscal(?: year)?\s+(?P<fiscal>20\d\d))|(?:year(?:s)? end(?:ed|ing)\s+(?:[A-Za-z]+\s+\d{1,2},?\s+|\d{1,2}\s+[A-Za-z]+\s+)(?P<ye>20\d\d))|(?:(?P<q>Q[1-4])\s*(?:FY)?\s*'?(?P<qy>\d{2,4}))|(?:\b(?P<cy>20[12]\d)\b)",
    re.I,
)
_YEAR_LIKE: Final = re.compile(r"^(19|20)\d\d$")
_EXCLUDE_BEFORE: Final = re.compile(
    r"(item|exhibit|form|note|cik|page|p\.|section|§|rule|schedule|fy|q[1-4]|\[\^?)\s*$", re.I
)
_EXCLUDE_AFTER: Final = re.compile(r"^\s*(\]|-k\b|-q\b|-f\b|k\b)", re.I)
_GROWTH_WORDS: Final = re.compile(
    r"\b(grew|growth|increase[ds]?|rose|up|decline[ds]?|fell|down|cagr|year[- ]on[- ]year|yoy)\b",
    re.I,
)
_CONTEXT_CHARS: Final = 90


@dataclass(frozen=True, slots=True)
class Numeral:
    token: str
    value: Decimal
    start: int
    end: int
    context: str
    currency: str | None
    scale: Decimal | None
    percent: bool
    multiple: bool
    period: str | None
    concept: str | None
    excluded: str | None

    @property
    def scaled(self) -> Decimal:
        if self.percent:
            return self.value / Decimal(100)
        return self.value * (self.scale or Decimal(1))


def _period_from(context_before: str, context_after: str) -> str | None:
    for text in (context_before[-60:], context_after[:60]):
        matches = list(_PERIOD.finditer(text))
        if not matches:
            continue
        m = matches[-1] if text is context_before[-60:] else matches[0]
        if m.group("fy"):
            year = m.group("fy")
            return f"FY{('20' + year) if len(year) == 2 else year}"
        if m.group("fiscal"):
            return f"FY{m.group('fiscal')}"
        if m.group("ye"):
            return f"FY{m.group('ye')}"
        if m.group("q"):
            year = m.group("qy")
            return f"{m.group('q').upper()} FY{('20' + year) if len(year) == 2 else year}"
        if m.group("cy"):
            return f"FY{m.group('cy')}"
    return None


_SENTENCE_BREAK: Final = re.compile(r"[.;\n]\s")


def _sentence_window(before: str, after: str) -> tuple[str, str]:
    """The part of the neighbourhood inside the numeral's own sentence."""
    breaks = list(_SENTENCE_BREAK.finditer(before))
    before_sentence = before[breaks[-1].end() :] if breaks else before
    cut = _SENTENCE_BREAK.search(after)
    after_sentence = after[: cut.start()] if cut else after
    return before_sentence, after_sentence


def _concept_from(before: str, after: str, *, percent: bool, money: bool) -> str | None:
    """The concept phrase nearest the numeral inside its sentence; before beats after."""
    before_s, after_s = _sentence_window(before, after)
    lowered_before, lowered_after = before_s.lower(), after_s.lower()
    best: tuple[int, int, str] | None = None
    for phrase, concept in CONCEPTS:
        idx = lowered_before.rfind(phrase)
        if idx >= 0:
            distance = len(lowered_before) - (idx + len(phrase))
            candidate = (distance, -len(phrase), concept)
            if best is None or candidate < best:
                best = candidate
        idx = lowered_after.find(phrase)
        if idx >= 0:
            distance = idx + 40  # a phrase after the numeral is a weaker attribution
            candidate = (distance, -len(phrase), concept)
            if best is None or candidate < best:
                best = candidate
    if (
        percent
        and not money
        and _GROWTH_WORDS.search(lowered_before[-40:] + " " + lowered_after[:40])
    ):
        return "growth"
    return best[2] if best else None


def extract_numerals(text: str) -> tuple[Numeral, ...]:
    """Every numeral in ``text`` with its hints, and the reason any was set aside."""
    found: list[Numeral] = []
    for match, token in numeral_matches(text):
        start, end = match.start(), match.end()
        before = text[max(0, start - _CONTEXT_CHARS) : start]
        after = text[end : end + _CONTEXT_CHARS]
        raw = match.group(0)
        try:
            value = Decimal(token)
        except InvalidOperation:
            continue
        excluded: str | None = None
        if (
            _YEAR_LIKE.match(match.group("digits").replace(",", ""))
            and not before.rstrip().endswith(("$", "£", "€"))
            and not re.match(r"^\s*(million|billion|%|bn|m\b)", after, re.I)
        ):
            excluded = "year"
        elif _EXCLUDE_BEFORE.search(before) or _EXCLUDE_AFTER.match(after):
            excluded = "reference"
        elif "http" in before[-200:] and " " not in before[before.rfind("http") :]:
            excluded = "url"
        elif re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s*$",
            before,
            re.I,
        ) or re.match(
            r"^\s+(January|February|March|April|May|June|July|August|September|October|November|December)\b",
            after,
            re.I,
        ):
            excluded = "date"
        currency = None
        stripped = before.rstrip()
        if stripped.endswith("$") or stripped.endswith("US$") or re.search(r"\bUSD\s*$", stripped):
            currency = "USD"
        elif stripped.endswith("£") or re.search(r"\bGBP\s*$", stripped):
            currency = "GBP"
        elif stripped.endswith("€") or re.search(r"\bEUR\s*$", stripped):
            currency = "EUR"
        elif re.match(r"^\s*(USD|US dollars|dollars)\b", after):
            currency = "USD"
        scale = None
        suffix = raw[
            len(match.group("digits"))
            + (len(match.group("word") or "") + len(match.group("mark") or "")) :
        ].lower()
        if suffix in _SUFFIX_SCALES:
            scale = _SUFFIX_SCALES[suffix]
        for pattern, factor in _SCALES:
            if pattern.match(after):
                scale = factor
                break
        percent = suffix == "%" or bool(re.match(r"^\s*(percent|per cent|%)", after, re.I))
        multiple = suffix in {"x", "×"} or bool(re.match(r"^\s*(x|×|times)\b", after, re.I))
        context = (before[-70:] + raw + after[:70]).replace("\n", " ")
        found.append(
            Numeral(
                token=token,
                value=value,
                start=start,
                end=end,
                context=context,
                currency=currency,
                scale=scale,
                percent=percent,
                multiple=multiple,
                period=_period_from(before, after),
                concept=_concept_from(before, after, percent=percent, money=currency is not None),
                excluded=excluded,
            )
        )
    return tuple(found)
