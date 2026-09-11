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
# A measure the filing basket does not hold under that name: a constant-currency or
# non-GAAP figure, a segment line, a quarter, a basis-point move. Set aside rather than
# judged, because "16 % in constant currency" against a filed 17.8 % is not a contradiction.
_QUALIFIED: Final = re.compile(
    r"(constant[- ]currency|\bcc\b|non-?gaap|adjusted|underlying|organic|\bex-|excluding|"
    r"segment|basis points|\bbps?\b|run-?rate|annuali[sz]ed|sequential|quarter|\bq[1-4]\b|"
    r"\bh[12]\b|first half|second half|nine months|six months|three months|per share|"
    r"per diluted share|calendar|commercial|consumer|bookings|backlog|\brpo\b|"
    r"remaining performance|"
    # The subjects' own segment and product lines: a figure for one of them is not the
    # group's, and the filing basket holds only the group's.
    r"intelligent cloud|productivity and business|more personal computing|microsoft cloud|"
    r"azure|linkedin|xbox|windows|server products|dynamics|search and news|gaming|devices|"
    r"office|copilot|oncology|biopharmaceuticals|rare disease|alexion|cardiovascular|"
    r"respiratory|vaccines|commercial bank|retail bank|institutional|wealth)",
    re.I,
)
# A change rather than a level: "less the $36.6bn increase", "rose by $12bn".
_DELTA_BEFORE: Final = re.compile(
    r"\b(by|increase|decrease|change|delta|gain|loss|add-?back|less|plus|minus|added|"
    r"absorbed|contributed)\s+(of\s+|in\s+|the\s+|a\s+|an\s+)?[~≈+\-−]?\s*[$£€]?\s*$",
    re.I,
)
_DELTA_AFTER: Final = re.compile(
    r"^\s*(bn|mn?|million|billion|thousand)?\s*(increase|decrease|change|gain|loss|rise|"
    r"fall|decline|growth|add-?back|of (revenue|sales|growth))\b",
    re.I,
)
# A figure the note reasons towards, not one it reports.
_HYPOTHETICAL: Final = re.compile(
    r"\b(must|would|could|should|implie[sd]|implying|steady-state|scenario|if|were|"
    r"assum\w*|target|required|requires|needs? to|to justify|justified|break-?even)\b",
    re.I,
)
_PRODUCT: Final = re.compile(
    r"\b(Microsoft|Office|Windows|Dynamics|Xbox|Copilot|Series|Model|iPhone|Surface)\s+$"
)
# Phrases that contain a concept's name without being it.
_BLOCKING: Final = (
    "cost of revenue",
    "cost of sales",
    "deferred revenue",
    "unearned revenue",
    "of revenue",
    "of sales",
    "of net income",
    "of operating income",
    "of free cash flow",
    "revenue growth",
    "revenue per",
    "current portion",
    "interest expense",
    "interest income",
    "as a percentage",
    "percentage of",
    "share of",
    "relative to",
    "times",
    "multiple",
)
_FOOTNOTE_LINE: Final = re.compile(r"^\s*\[\^[^\]]+\]:")
_TABLE_SEPARATOR: Final = re.compile(r"^\s*\|?\s*:?-{2,}")
# How far a concept phrase may sit from the number it is said to describe. Beyond this
# the sentence is about something else and the number is left unattributed, which is
# the honest state: an unread number is not a wrong one.
_BEFORE_REACH: Final = 45
_AFTER_REACH: Final = 25


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


_MONTHS: Final = {
    m: i + 1
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        )
    )
}
_FULL_DATE: Final = re.compile(
    r"(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]+)\s+(?P<y1>20\d\d))|"
    r"(?:(?P<m2>[A-Za-z]+)\s+(?P<d2>\d{1,2}),?\s+(?P<y2>20\d\d))"
)


def _full_date(text: str) -> str | None:
    """A day-month-year in the text as ``D<iso>``, or nothing; the matcher resolves it."""
    for m in _FULL_DATE.finditer(text):
        month = _MONTHS.get((m.group("m1") or m.group("m2") or "").lower())
        if month is None:
            continue
        day = int(m.group("d1") or m.group("d2"))
        year = int(m.group("y1") or m.group("y2"))
        if 1 <= day <= 31:
            return f"D{year:04d}-{month:02d}-{day:02d}"
    return None


def _period_from(context_before: str, context_after: str) -> str | None:
    dated = _full_date(context_before[-60:]) or _full_date(context_after[:60])
    if dated is not None:
        return dated
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


def _concept_from(before: str, after: str) -> str | None:
    """The concept phrase nearest the numeral inside its sentence; before beats after."""
    before_s, after_s = _sentence_window(before, after)
    lowered_before, lowered_after = before_s.lower(), after_s.lower()
    best: tuple[int, int, str] | None = None
    for phrase, concept in CONCEPTS:
        idx = lowered_before.rfind(phrase)
        if idx >= 0:
            distance = len(lowered_before) - (idx + len(phrase))
            if distance <= _BEFORE_REACH:
                candidate = (distance, -len(phrase), concept)
                if best is None or candidate < best:
                    best = candidate
        idx = lowered_after.find(phrase)
        if 0 <= idx <= _AFTER_REACH:
            distance = idx + 40  # a phrase after the numeral is a weaker attribution
            candidate = (distance, -len(phrase), concept)
            if best is None or candidate < best:
                best = candidate
    chosen: str | None = best[2] if best else None
    if chosen is not None and any(b in lowered_before[-45:] for b in _BLOCKING):
        chosen = None
    return chosen


def _compatible(
    concept: str | None,
    *,
    value: Decimal,
    percent: bool,
    money: bool,
    scale: Decimal | None,
    multiple: bool,
    near: str,
) -> str | None:
    """A percentage is not a revenue and a sum of money is not a margin.

    A percentage beside revenue or net income with a growth word is that line's growth;
    beside anything else it is unattributed rather than wrong. A multiple is never a level.
    A bare number in the hundreds of thousands is not a margin, whatever the row is called.
    A figure in billions is not an EPS.
    """
    if concept is None:
        return None
    if multiple:
        return concept if concept in {"pe", "ev_ebitda"} else None
    if percent and not money:
        if concept in _RATIO_CONCEPTS:
            return concept
        if _GROWTH_WORDS.search(near):
            return {"revenue": "revenue_growth", "net_income": "net_income_growth"}.get(concept)
        return None
    if concept in _RATIO_CONCEPTS:
        return None if (money or abs(value) > 100) else concept
    if concept == "eps_diluted" and (scale is not None or abs(value) >= 1000):
        return None
    return concept


def _table_hints(text: str, start: int) -> tuple[str | None, str | None] | None:
    """Concept and period for a numeral in a Markdown table cell, or None when not in one.

    A table row is not a sentence: the number's concept is the row label and its period is
    the column header (or the row's own period cell, as the front page's headline table has
    it). Reading the neighbouring cells as prose was how one revenue column contradicted the
    next, in the platform report and the baseline alike.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    line_end = len(text) if line_end < 0 else line_end
    line = text[line_start:line_end]
    if not line.lstrip().startswith("|"):
        return None
    cells = line.split("|")
    column = line[: start - line_start].count("|")
    header: list[str] | None = None
    cursor = line_start
    while cursor > 0:
        previous_end = cursor - 1
        previous_start = text.rfind("\n", 0, previous_end) + 1
        previous = text[previous_start:previous_end]
        if not previous.lstrip().startswith("|"):
            break
        if _TABLE_SEPARATOR.match(previous.replace("|", "", 1)) and previous_start > 0:
            head_end = previous_start - 1
            head_start = text.rfind("\n", 0, head_end) + 1
            header = text[head_start:head_end].split("|")
            break
        cursor = previous_start
    label = cells[1].lower() if len(cells) > 1 else ""
    concept: str | None = None
    for phrase, name in CONCEPTS:
        if phrase in label and (concept is None or len(phrase) > len(_phrase_of(concept))):
            concept = name
    if concept is not None and any(b in label for b in _BLOCKING):
        concept = None
    period = None
    if header is not None and column < len(header):
        period = _period_from(header[column], "")
    if period is None:
        period = _period_from(line[: start - line_start], "") or _period_from("", line)
    return concept, period


def _phrase_of(concept: str) -> str:
    return max((phrase for phrase, name in CONCEPTS if name == concept), key=len, default="")


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
        line_start = text.rfind("\n", 0, start) + 1
        if _FOOTNOTE_LINE.match(text[line_start : line_start + 12]):
            excluded = "footnote"
        elif (
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
        money = currency is not None or scale is not None
        sentence_before, sentence_after = _sentence_window(before, after)
        if excluded is None and _QUALIFIED.search(sentence_before[-60:] + " " + after[:30]):
            excluded = "qualified"
        elif excluded is None and (_DELTA_BEFORE.search(before) or _DELTA_AFTER.match(after)):
            excluded = "delta"
        elif excluded is None and _HYPOTHETICAL.search(sentence_before[-80:]):
            excluded = "hypothetical"
        elif excluded is None and not money and not percent and _PRODUCT.search(before):
            excluded = "product"
        context = (before[-70:] + raw + after[:70]).replace("\n", " ")
        table = _table_hints(text, start)
        if table is not None:
            concept, period = table
            near = (table[0] or "") + " " + sentence_after[:40]
        else:
            period = _period_from(before, after)
            concept = _concept_from(before, after)
            near = sentence_before[-40:] + " " + sentence_after[:40]
        concept = _compatible(
            concept,
            value=value,
            percent=percent,
            money=money,
            scale=scale,
            multiple=multiple,
            near=near,
        )
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
                period=period,
                concept=concept,
                excluded=excluded,
            )
        )
    return tuple(found)
