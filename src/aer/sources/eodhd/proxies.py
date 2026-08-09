"""Which index a share's beta is regressed against, decided per exchange and written down.

A beta is a number *against something*. Regress a London share against the S&P 500 and you
get a figure that is arithmetically correct, entirely plausible in the output, and a
measure of how the company moves with the wrong market — and nothing downstream can tell,
because a beta carries no record of what it was measured against unless somebody puts one
there.

So the choice is a **documented decision per exchange**, in the same shape as
:data:`aer.sources.macro.series.RISK_FREE_SERIES` and for the same reason: an exchange with
no documented proxy raises rather than falling back to a default. The fallback is the
dangerous branch — it is silent, it is usually approximately right, and the one time it is
not, the error is the whole equity risk premium.

**The proxy travels with the beta.** :func:`aer.services.prices.propose_computed_beta`
writes the proxy, the window and the observation count into the assumption's justification,
so an operator confirming a beta is confirming a measurement they can see the terms of.

Index symbols use EODHD's ``.INDX`` suffix. They are quoted, not traded, and carry the same
licence as everything else from that provider — ADR 0030 route 2, nothing leaves the
machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aer.errors import ValidationError

__all__ = [
    "MARKET_PROXIES",
    "MarketProxy",
    "ProxyRefusedError",
    "market_proxy_for",
    "vendor_symbol",
]


class ProxyRefusedError(ValidationError):
    """No market index is documented for this exchange, so no beta may be regressed.

    Deliberately an error rather than a default. A beta against the wrong index looks
    exactly like a beta against the right one.
    """

    code = "market_proxy_refused"


@dataclass(frozen=True, slots=True)
class MarketProxy:
    """The index one exchange's shares are measured against."""

    exchange: str
    symbol: str
    label: str

    # The vendor's suffix for *listings* on this exchange, which is not the exchange code.
    # EODHD keys every US venue as `.US` regardless of whether it is NASDAQ or NYSE, so a
    # symbol built by pasting the exchange on the end resolves to nothing for half the
    # companies this platform covers.
    suffix: str

    # Why this index and not a broader or narrower one. Recorded because "beta against the
    # market" is a modelling choice and the reader is entitled to know which market.
    rationale: str


MARKET_PROXIES: Final[dict[str, MarketProxy]] = {
    "NASDAQ": MarketProxy(
        exchange="NASDAQ",
        symbol="GSPC.INDX",
        label="S&P 500",
        suffix="US",
        rationale=(
            "The S&P 500 rather than the Nasdaq Composite. A Nasdaq listing is a venue, not "
            "a sector, and regressing against a technology-weighted index would understate "
            "the beta of a technology company by measuring it against itself."
        ),
    ),
    "NYSE": MarketProxy(
        exchange="NYSE",
        symbol="GSPC.INDX",
        label="S&P 500",
        suffix="US",
        rationale=(
            "The broad US large-cap index, and the one the equity risk premia most "
            "operators use are themselves estimated against."
        ),
    ),
    "LSE": MarketProxy(
        exchange="LSE",
        symbol="FTAS.INDX",
        label="FTSE All-Share",
        suffix="LSE",
        rationale=(
            "The All-Share rather than the FTSE 100. The 100 is dominated by a handful of "
            "commodity and bank megacaps whose currency exposure is not the UK market's, "
            "and a mid-cap domestic company regressed against it gets somebody else's beta."
        ),
    ),
}
"""Every exchange this platform will regress a beta on.

Short on purpose. Each entry is a decision somebody has to be able to defend, and a list
that grew by pattern-matching would be a list of guesses. An exchange that is not here
raises, and the operator enters a beta by hand with a stated source — which is the
documented route anyway: `aer.services.prices.BETA_ASSUMPTION` is explicit that beta is a
first-class assumption with an optional computed override, not the other way round.
"""


def vendor_symbol(ticker: str, *, exchange: str) -> str:
    """The vendor's own key for a listing, e.g. ``MSFT.US`` or ``BARC.LSE``.

    Built from the documented suffix rather than from the exchange code, because they are
    not the same thing: EODHD keys every US venue as ``.US``, so ``MSFT.NASDAQ`` resolves to
    nothing. Refuses an undocumented exchange for the same reason
    :func:`market_proxy_for` does — a symbol guessed by pasting on a suffix either 404s,
    which is merely wasteful, or resolves to a different company's listing somewhere else,
    which is not.
    """
    return f"{ticker.strip().upper()}.{market_proxy_for(exchange).suffix}"


def market_proxy_for(exchange: str) -> MarketProxy:
    """The index to regress against, for a listing on this exchange.

    Raises:
        ProxyRefusedError: If no index is documented. See the module docstring — the
            fallback is the branch that produces a confident wrong answer.
    """
    found = MARKET_PROXIES.get(exchange.strip().upper())
    if found is not None:
        return found

    known = ", ".join(sorted(MARKET_PROXIES))
    message = (
        f"No market index is documented for the {exchange!r} exchange, so no beta will be "
        f"regressed for a listing on it. Documented: {known}. Enter a beta by hand and say "
        "what it is measured against — a regression against the wrong market is "
        "indistinguishable in the output from one against the right one."
    )
    raise ProxyRefusedError(message, context={"exchange": exchange, "known": known})
