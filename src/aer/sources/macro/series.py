"""Which macro series this platform may retrieve, and who holds the copyright in each.

**FRED is not one licence.** The St Louis Fed's terms permit free use, and then say this:
*"Redistributing copyrighted data series for commercial use is not allowed unless the data
copyright owner authorizes it."* FRED carries both kinds. CPI is the Bureau of Labor
Statistics, GDP is the Bureau of Economic Analysis and Treasury yields are the Board of
Governors — all works of the US federal government, in the public domain and freely
redistributable. FRED also carries S&P CoreLogic Case-Shiller, the ICE BofA index family and
a large amount of OECD material, none of which is.

So "FRED is fine" and "FRED is refused" are both wrong, and neither is a determination this
module can make once for the whole provider. It is made **per series**, recorded here, and
enforced in code: :func:`series_for` refuses an identifier that is not on this list, so a
copyrighted series cannot be pulled by a caller who did not know the difference.

The refusals below are listed rather than merely omitted, for the same reason
:data:`~aer.calc.quality.UNAVAILABLE_SIGNALS` lists what it cannot compute: a reader has to
be able to tell "we considered this and could not use it" from "nobody thought of it".

**The UK series do not come from FRED.** FRED's UK CPI and rates are OECD-sourced and carry
OECD copyright. The Office for National Statistics publishes the same figures itself under
the Open Government Licence, so the UK adapter goes there instead — which is also the more
authoritative source, so the licence question and the quality question have the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from aer.core.enums import Provider
from aer.errors import ValidationError

__all__ = [
    "MACRO_SERIES",
    "REFUSED_SERIES",
    "RISK_FREE_SERIES",
    "Frequency",
    "MacroSeries",
    "RefusedSeries",
    "SeriesRefusedError",
    "risk_free_series_for",
    "series_for",
]


class Frequency(StrEnum):
    """How often a series is published. Recorded because it changes what a value means."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


@dataclass(frozen=True, slots=True)
class MacroSeries:
    """One series this platform may retrieve, and everything needed to defend using it."""

    key: str
    provider: Provider

    # The identifier at the provider. For FRED this is the series id; for ONS it is the
    # four-character series code, which needs a dataset alongside it.
    identifier: str

    label: str
    unit: str
    frequency: Frequency

    # Who actually produced the numbers, as distinct from who publishes them. FRED is a
    # distributor: the copyright question is about this field, never about FRED.
    originator: str

    # Why this may be redistributed. Stored as the sentence that goes in a source document's
    # licence note, so the claim travels with the data rather than living only here.
    licence: str

    # The ONS dataset a series code belongs to. Empty for providers that do not need one.
    dataset: str = ""

    notes: str = ""


# Works of the US federal government are not subject to copyright within the United States
# (17 U.S.C. § 105), which is why every FRED series below is redistributable and why each one
# names the agency that produced it rather than the site it was downloaded from.
_US_GOVERNMENT: Final = (
    "Work of the US federal government, in the public domain under 17 U.S.C. section 105. "
    "Retrieved via FRED/ALFRED (Federal Reserve Bank of St Louis), which is credited as the "
    "distributor."
)

_ONS_OGL: Final = (
    "Crown copyright, licensed under the Open Government Licence v3.0. Commercial re-use "
    "permitted with source accreditation to the Office for National Statistics."
)

MACRO_SERIES: Final[tuple[MacroSeries, ...]] = (
    # -- US output and prices ---------------------------------------------------------------
    MacroSeries(
        key="us_gdp_nominal",
        provider=Provider.FRED,
        identifier="GDP",
        label="US gross domestic product",
        unit="USD",
        frequency=Frequency.QUARTERLY,
        originator="US Bureau of Economic Analysis",
        licence=_US_GOVERNMENT,
        notes="Billions of dollars, seasonally adjusted annual rate. Revised for years, "
        "which is the reason this platform reads vintages rather than the current series.",
    ),
    MacroSeries(
        key="us_gdp_real",
        provider=Provider.FRED,
        identifier="GDPC1",
        label="US real gross domestic product",
        unit="USD",
        frequency=Frequency.QUARTERLY,
        originator="US Bureau of Economic Analysis",
        licence=_US_GOVERNMENT,
        notes="Chained 2017 dollars. The chain base itself is revised, so two vintages can "
        "differ in level as well as in the latest observations.",
    ),
    MacroSeries(
        key="us_cpi",
        provider=Provider.FRED,
        identifier="CPIAUCSL",
        label="US consumer price index, all urban consumers",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="US Bureau of Labor Statistics",
        licence=_US_GOVERNMENT,
        notes="Index, 1982-84 = 100, seasonally adjusted. Seasonal factors are re-estimated "
        "annually, so historical values move even when the underlying prices do not.",
    ),
    MacroSeries(
        key="us_cpi_core",
        provider=Provider.FRED,
        identifier="CPILFESL",
        label="US consumer price index, less food and energy",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="US Bureau of Labor Statistics",
        licence=_US_GOVERNMENT,
    ),
    MacroSeries(
        key="us_unemployment",
        provider=Provider.FRED,
        identifier="UNRATE",
        label="US unemployment rate",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="US Bureau of Labor Statistics",
        licence=_US_GOVERNMENT,
        notes="Per cent. Stored as published, so 3.7 means 3.7% and not 370%.",
    ),
    # -- US rates -----------------------------------------------------------------------------
    MacroSeries(
        key="us_treasury_10y",
        provider=Provider.FRED,
        identifier="DGS10",
        label="US 10-year Treasury constant maturity yield",
        unit="pure",
        frequency=Frequency.DAILY,
        originator="Board of Governors of the Federal Reserve System (H.15)",
        licence=_US_GOVERNMENT,
        notes="Per cent per annum. The conventional risk-free proxy for a USD discount rate "
        "over an equity holding period.",
    ),
    MacroSeries(
        key="us_treasury_30y",
        provider=Provider.FRED,
        identifier="DGS30",
        label="US 30-year Treasury constant maturity yield",
        unit="pure",
        frequency=Frequency.DAILY,
        originator="Board of Governors of the Federal Reserve System (H.15)",
        licence=_US_GOVERNMENT,
    ),
    MacroSeries(
        key="us_treasury_2y",
        provider=Provider.FRED,
        identifier="DGS2",
        label="US 2-year Treasury constant maturity yield",
        unit="pure",
        frequency=Frequency.DAILY,
        originator="Board of Governors of the Federal Reserve System (H.15)",
        licence=_US_GOVERNMENT,
    ),
    MacroSeries(
        key="us_fed_funds",
        provider=Provider.FRED,
        identifier="FEDFUNDS",
        label="US federal funds effective rate",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="Board of Governors of the Federal Reserve System (H.15)",
        licence=_US_GOVERNMENT,
    ),
    # -- UK, from the ONS rather than from FRED -----------------------------------------------
    MacroSeries(
        key="uk_cpi",
        provider=Provider.ONS,
        identifier="D7BT",
        dataset="MM23",
        label="UK consumer price index",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="Office for National Statistics",
        licence=_ONS_OGL,
        notes="Index, 2015 = 100. FRED carries a UK CPI series, but its UK figures are "
        "OECD-sourced and carry OECD copyright; the ONS publishes the same numbers itself "
        "under the Open Government Licence, and is the more authoritative source anyway.",
    ),
    MacroSeries(
        key="uk_cpi_annual_rate",
        provider=Provider.ONS,
        identifier="D7G7",
        dataset="MM23",
        label="UK CPI annual rate",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="Office for National Statistics",
        licence=_ONS_OGL,
        notes="Per cent, twelve-month rate.",
    ),
    MacroSeries(
        key="uk_gdp_real",
        provider=Provider.ONS,
        identifier="ABMI",
        dataset="QNA",
        label="UK gross domestic product, chained volume measure",
        unit="pure",
        frequency=Frequency.QUARTERLY,
        originator="Office for National Statistics",
        licence=_ONS_OGL,
        notes="Index. Quarterly, so the response's `quarters` block is the one that means "
        "anything -- the same document also carries annual figures at a different scale.",
    ),
    MacroSeries(
        key="uk_cpih",
        provider=Provider.ONS,
        identifier="L55O",
        dataset="MM23",
        label="UK CPIH, including owner-occupiers' housing costs",
        unit="pure",
        frequency=Frequency.MONTHLY,
        originator="Office for National Statistics",
        licence=_ONS_OGL,
    ),
)


@dataclass(frozen=True, slots=True)
class RefusedSeries:
    """A series somebody will reach for, and why it does not appear above."""

    identifier: str
    label: str
    originator: str
    reason: str


# Listed rather than omitted. Every one of these is a series a reasonable analyst would ask
# for, and an empty response with no explanation invites somebody to add it without checking.
REFUSED_SERIES: Final[tuple[RefusedSeries, ...]] = (
    RefusedSeries(
        identifier="CSUSHPINSA",
        label="S&P CoreLogic Case-Shiller US national home price index",
        originator="S&P Dow Jones Indices LLC",
        reason=(
            "Copyright S&P Dow Jones Indices. FRED distributes it under terms that do not "
            "extend to commercial redistribution, which is what this platform's output would "
            "be. A licence from S&P would be needed and is not held."
        ),
    ),
    RefusedSeries(
        identifier="BAMLH0A0HYM2",
        label="ICE BofA US high-yield index option-adjusted spread",
        originator="ICE Data Indices, LLC",
        reason=(
            "Copyright ICE Data Indices. Redistribution requires ICE's permission; FRED's "
            "own page for the series carries that restriction."
        ),
    ),
    RefusedSeries(
        identifier="GBRCPIALLMINMEI",
        label="UK consumer prices, OECD main economic indicators",
        originator="Organisation for Economic Co-operation and Development",
        reason=(
            "OECD copyright, and unnecessary: the Office for National Statistics publishes "
            "UK CPI itself under the Open Government Licence. See `uk_cpi`."
        ),
    ),
)


class SeriesRefusedError(ValidationError):
    """A series that is not on the allowlist was asked for.

    A `ValidationError` subclass with its own code, because the caller's next step differs:
    a malformed request is fixed by the caller, and this is fixed by somebody establishing
    the copyright position and adding a row.
    """

    code = "macro_series_refused"


_BY_KEY: Final[dict[str, MacroSeries]] = {series.key: series for series in MACRO_SERIES}
_REFUSED_BY_ID: Final[dict[str, RefusedSeries]] = {
    refused.identifier: refused for refused in REFUSED_SERIES
}


def series_for(key: str) -> MacroSeries:
    """The series this key names.

    **The enforcement point.** Nothing in this platform builds a macro URL from an
    identifier a caller supplied; a caller supplies a *key*, this resolves it, and an
    identifier that is not here produces no request at all. That is the same shape as the
    SEC client taking a CIK rather than a URL, and it is what stops a copyrighted series
    being retrieved by somebody who did not know it was one.

    Raises:
        SeriesRefusedError: If the key is unknown. The message names a refused series
            explicitly where one matches, so "why can I not have Case-Shiller?" is answered
            at the point it is asked.
    """
    found = _BY_KEY.get(key)
    if found is not None:
        return found

    refused = _REFUSED_BY_ID.get(key)
    if refused is not None:
        message = (
            f"{key} ({refused.label}) is deliberately not available. {refused.reason} "
            f"Copyright is held by {refused.originator}."
        )
        raise SeriesRefusedError(message, context={"series": key, "originator": refused.originator})

    message = (
        f"{key!r} is not a macro series this platform knows. Series are allowlisted by key "
        "because the copyright position differs series by series, and one that has not been "
        "established cannot be redistributed. Known keys: "
        f"{', '.join(sorted(_BY_KEY))}."
    )
    raise SeriesRefusedError(message, context={"series": key})


# Which series stands in for the risk-free rate in each currency.
#
# **A documented choice, not a lookup.** The risk-free rate is the single most consequential
# input to a discount rate, and "the ten-year government yield" hides three decisions: whose
# government, which maturity, and nominal or real. Each is made here, in the open, so a
# reviewer can disagree with the choice rather than with the answer.
#
# The maturity matches an equity holding period rather than a money-market one: an equity is
# a perpetual claim, and discounting it at a three-month bill rate imports a term premium
# error that grows with the forecast horizon.
RISK_FREE_SERIES: Final[dict[str, str]] = {
    "USD": "us_treasury_10y",
    # No GBP entry. The UK risk-free proxy is the ten-year gilt yield, which the Bank of
    # England publishes and which this platform does not yet retrieve — see ADR 0026. A
    # missing key here produces a refusal naming that, which is better than silently
    # discounting a sterling valuation at a US Treasury yield.
}


def risk_free_series_for(currency: str) -> MacroSeries:
    """The series this platform uses as the risk-free rate for a currency.

    Raises:
        SeriesRefusedError: If no series is documented for the currency. Refused rather than
            defaulted: a sterling valuation discounted at a US Treasury yield is wrong by the
            whole of the rate differential and looks entirely ordinary.
    """
    key = RISK_FREE_SERIES.get(currency.upper())
    if key is None:
        message = (
            f"No risk-free series is documented for {currency.upper()}. This platform "
            "retrieves one for "
            f"{', '.join(sorted(RISK_FREE_SERIES))} only. Substituting another currency's "
            "government yield would be wrong by the whole rate differential and would look "
            "entirely ordinary in the output, so the valuation stops here instead."
        )
        raise SeriesRefusedError(message, context={"currency": currency.upper()})
    return series_for(key)
