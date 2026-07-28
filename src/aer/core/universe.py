"""Which securities this platform will research, and which it refuses.

The MVP covers **ordinary operating companies listed on a major US or UK exchange**. It
deliberately refuses everything else, and refuses it loudly rather than producing a
report that looks the same as any other while resting on a model that does not apply.

Why each exclusion exists — the reason matters more than the rule, because the rule is
what gets relaxed by someone who has forgotten the reason:

* **Unsupported exchange.** Point-in-time correctness depends on knowing an exchange's
  reporting calendar, its filing regime and its trading days. Those are per-jurisdiction
  facts the platform simply does not have outside the US and UK.
* **OTC and pink-sheet venues.** Disclosure is voluntary and irregular. There is no
  reliable filing stream to hash, so the evidence base a report is supposed to rest on
  does not exist.
* **ETFs and other funds.** A fund has no revenue, no margins, no working capital and no
  free cash flow. A discounted cash flow of one is not a hard analysis; it is a category
  error, and the output would be confidently meaningless.
* **Investment trusts.** Closed-ended funds, valued on net asset value and the discount
  or premium to it. Same category error as an ETF, in a form that looks much more like an
  ordinary UK listed company and so is much easier to run by mistake.
* **Micro-caps.** Thin analyst coverage, sparse comparable companies and illiquid prices
  make the comparable-company and market-implied parts of the analysis unreliable in a
  way the report would not show.

Everything here is a pure function over what the operator typed. Nothing looks anything
up: at request time no external call has been made, by design, so these rules work from
the ticker, the exchange and the company name alone. That makes them heuristics, which is
why the exclusion message always names the rule and says how to proceed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_MICRO_CAP_THRESHOLD_GBP",
    "SUPPORTED_EXCHANGES",
    "Exclusion",
    "ExclusionRule",
    "check_universe",
    "is_micro_cap",
]


class ExclusionRule(StrEnum):
    """Why a security is out of scope. Stable identifiers: they reach the API."""

    UNSUPPORTED_EXCHANGE = "unsupported_exchange"
    OTC_VENUE = "otc_venue"
    EXCHANGE_TRADED_FUND = "exchange_traded_fund"
    INVESTMENT_TRUST = "investment_trust"
    MICRO_CAP = "micro_cap"


@dataclass(frozen=True, slots=True)
class Exclusion:
    """A single reason a security is out of scope, in terms a person can act on."""

    rule: ExclusionRule
    message: str


# The four venues the platform understands. NYSE American is listed separately from NYSE
# because it has its own listing standards and a materially different issuer profile.
SUPPORTED_EXCHANGES: Final[frozenset[str]] = frozenset({"NASDAQ", "NYSE", "NYSE_AMERICAN", "LSE"})

# Venues that are recognisably out of scope, so the error can say *why* rather than only
# "not supported". Anything unrecognised still fails on SUPPORTED_EXCHANGES.
_OTC_VENUES: Final[frozenset[str]] = frozenset(
    {
        "AQSE",  # Aquis Stock Exchange
        "AQUIS",
        "OTC",
        "OTCBB",
        "OTCMKTS",
        "OTCQB",
        "OTCQX",
        "PINK",
        "PINKSHEETS",
    }
)

# £300m. Below this, comparable-company analysis stops being meaningful: the peer set
# thins out, the price is illiquid enough that market-implied inputs are noisy, and there
# is rarely enough analyst coverage to cross-check anything.
DEFAULT_MICRO_CAP_THRESHOLD_GBP: Final = Decimal("300000000")

# Fund and trust naming. Matched on word boundaries: "ETF" as a word is a fund, but
# "ETFS" inside another word is not, and a company legitimately named "Trustpilot" must
# not be mistaken for an investment trust.
_FUND_NAME_PATTERN: Final = re.compile(
    r"\b(?:"
    r"etf|etn|etp|"
    r"exchange[\s-]traded\s+(?:fund|note|product)|"
    r"index\s+fund|tracker\s+fund|mutual\s+fund|"
    r"ucits|sicav|oeic|unit\s+trust|"
    # Fund-only brands. A bare "vanguard" is deliberately absent: Vanguard Natural
    # Resources was a real NASDAQ-listed oil and gas producer, and Invesco Ltd is itself
    # an NYSE-listed operating company — so "invesco qqq" names the product rather than
    # the manager. A brand is evidence of a fund only when the brand is used for nothing
    # else. The genuine funds those two run all say "ETF", "UCITS" or "Index Fund" anyway.
    r"ishares|spdr|wisdomtree|xtrackers|invesco\s+qqq"
    r")\b",
    re.IGNORECASE,
)

_INVESTMENT_TRUST_NAME_PATTERN: Final = re.compile(
    r"\b(?:"
    r"investment\s+trust|"
    r"investment\s+company|"
    r"venture\s+capital\s+trust|"
    r"real\s+estate\s+investment\s+trust|"
    r"vct|reit"
    r")\b",
    re.IGNORECASE,
)

# Known fund tickers whose names would not otherwise give them away. Deliberately short:
# it is a safety net for the most common mistakes, not an attempt at a complete registry,
# which is what a data provider is for from task 8 onward.
_KNOWN_FUND_TICKERS: Final[frozenset[str]] = frozenset(
    {
        "ARKK",
        "DIA",
        "EEM",
        "EFA",
        "GLD",
        "IVV",
        "IWM",
        "QQQ",
        "SPY",
        "TLT",
        "VOO",
        "VTI",
        "VUSA",
        "VWRL",
    }
)


def _normalise(value: str) -> str:
    return " ".join(value.split()).strip()


def is_micro_cap(
    market_cap_gbp: Decimal | None,
    *,
    threshold_gbp: Decimal = DEFAULT_MICRO_CAP_THRESHOLD_GBP,
) -> bool:
    """Whether a market capitalisation falls below the supported threshold.

    Takes the figure rather than fetching it, and returns ``False`` when it is unknown.
    That is not the rule being lenient — it is the rule being honest: at request time no
    external call has been made, so market capitalisation is genuinely not known, and
    guessing it from a ticker would be exactly the kind of invented number this codebase
    exists to prevent.

    The check therefore cannot fire during request creation. It fires once ticker
    resolution supplies a real figure (task 8), against the same threshold, so the rule
    lives in one place from the start.
    """
    if market_cap_gbp is None:
        return False
    return market_cap_gbp < threshold_gbp


def check_universe(
    *,
    ticker: str,
    exchange: str,
    company_name: str,
    market_cap_gbp: Decimal | None = None,
    micro_cap_threshold_gbp: Decimal = DEFAULT_MICRO_CAP_THRESHOLD_GBP,
) -> list[Exclusion]:
    """Return every reason this security is out of scope. Empty means in scope.

    Every reason, not the first: told "wrong exchange", an operator fixes the exchange and
    resubmits, only to be told it is also a fund. One round trip per rule is a bad way to
    learn that something was never going to work.
    """
    exclusions: list[Exclusion] = []

    normalised_exchange = _normalise(exchange).upper().replace(" ", "_").replace("-", "_")
    normalised_ticker = _normalise(ticker).upper()
    normalised_name = _normalise(company_name)

    if normalised_exchange in _OTC_VENUES:
        exclusions.append(
            Exclusion(
                rule=ExclusionRule.OTC_VENUE,
                message=(
                    f"{normalised_exchange} is an over-the-counter venue. Disclosure there "
                    "is voluntary and irregular, so there is no dependable filing stream "
                    "for this platform to cite. Only NASDAQ, NYSE, NYSE American and the "
                    "LSE main market are supported."
                ),
            )
        )
    elif normalised_exchange not in SUPPORTED_EXCHANGES:
        supported = ", ".join(sorted(SUPPORTED_EXCHANGES))
        exclusions.append(
            Exclusion(
                rule=ExclusionRule.UNSUPPORTED_EXCHANGE,
                message=(
                    f"{normalised_exchange or 'This exchange'} is not supported. "
                    "Point-in-time correctness depends on knowing an exchange's filing "
                    f"regime and calendar, which this platform holds only for: {supported}."
                ),
            )
        )

    if normalised_ticker in _KNOWN_FUND_TICKERS or _FUND_NAME_PATTERN.search(normalised_name):
        exclusions.append(
            Exclusion(
                rule=ExclusionRule.EXCHANGE_TRADED_FUND,
                message=(
                    f"{normalised_ticker} appears to be a fund rather than an operating "
                    "company. A fund has no revenue, margins or free cash flow, so a "
                    "discounted cash flow of one would not be a hard analysis — it would "
                    "be meaningless. Research the underlying holdings instead."
                ),
            )
        )

    if _INVESTMENT_TRUST_NAME_PATTERN.search(normalised_name):
        exclusions.append(
            Exclusion(
                rule=ExclusionRule.INVESTMENT_TRUST,
                message=(
                    f"{normalised_name} appears to be a closed-ended fund or trust. These "
                    "are valued on net asset value and the discount or premium to it, not "
                    "on discounted cash flows, and this platform does not implement that "
                    "model."
                ),
            )
        )

    if is_micro_cap(market_cap_gbp, threshold_gbp=micro_cap_threshold_gbp):
        threshold = f"£{micro_cap_threshold_gbp:,.0f}"
        exclusions.append(
            Exclusion(
                rule=ExclusionRule.MICRO_CAP,
                message=(
                    f"Market capitalisation is below {threshold}. Comparable-company "
                    "analysis needs a peer set and a liquid price; below this threshold "
                    "neither is dependable, and the report would not show that."
                ),
            )
        )

    return exclusions
