"""Which index a beta is measured against, and the symbol a listing is fetched by.

Gap B3. Both are decisions rather than lookups, and both fail the same way when guessed: a
London share regressed against the S&P 500 gives a number that is arithmetically correct,
entirely plausible in the output, and a measure of the wrong market. A symbol built by
pasting the exchange onto the ticker either resolves to nothing or — worse — to a different
company's listing on another venue.

So the tests here are mostly about the refusals.
"""

from __future__ import annotations

import pytest

from aer.sources.eodhd.proxies import (
    MARKET_PROXIES,
    ProxyRefusedError,
    market_proxy_for,
    vendor_symbol,
)


class TestTheProxyIsADocumentedChoice:
    @pytest.mark.parametrize("exchange", sorted(MARKET_PROXIES))
    def test_every_documented_exchange_resolves(self, exchange: str) -> None:
        proxy = market_proxy_for(exchange)
        assert proxy.symbol
        assert proxy.label
        assert proxy.suffix

    @pytest.mark.parametrize("exchange", sorted(MARKET_PROXIES))
    def test_every_entry_says_why_that_index(self, exchange: str) -> None:
        # "Beta against the market" is a modelling choice and the reader is entitled to
        # know which market and why. An entry with no rationale is one nobody can defend.
        assert len(market_proxy_for(exchange).rationale) > 60

    def test_an_undocumented_exchange_is_refused(self) -> None:
        # The fallback is the dangerous branch: silent, usually approximately right, and
        # wrong by the whole equity risk premium the one time it is not.
        with pytest.raises(ProxyRefusedError, match="No market index is documented"):
            market_proxy_for("TSX")

    def test_the_refusal_names_what_is_documented(self) -> None:
        with pytest.raises(ProxyRefusedError, match="LSE"):
            market_proxy_for("XETRA")

    def test_the_lookup_is_case_and_space_insensitive(self) -> None:
        assert market_proxy_for("  nasdaq  ").symbol == market_proxy_for("NASDAQ").symbol

    def test_a_uk_listing_is_not_measured_against_a_us_index(self) -> None:
        # The specific mistake this table exists to prevent.
        assert market_proxy_for("LSE").symbol != market_proxy_for("NYSE").symbol


class TestTheVendorSymbol:
    def test_a_us_listing_uses_the_us_suffix_not_its_venue(self) -> None:
        # EODHD keys every US venue as `.US`. `MSFT.NASDAQ` resolves to nothing, which is
        # the bug this exists to prevent — and it would have looked like a company with no
        # price history rather than like a malformed request.
        assert vendor_symbol("MSFT", exchange="NASDAQ") == "MSFT.US"
        assert vendor_symbol("JNJ", exchange="NYSE") == "JNJ.US"

    def test_a_london_listing_uses_the_london_suffix(self) -> None:
        assert vendor_symbol("BARC", exchange="LSE") == "BARC.LSE"

    def test_the_ticker_is_normalised(self) -> None:
        assert vendor_symbol("  msft ", exchange="NASDAQ") == "MSFT.US"

    def test_an_undocumented_exchange_yields_no_symbol(self) -> None:
        # Rather than a plausible-looking one that fetches somebody else's shares.
        with pytest.raises(ProxyRefusedError):
            vendor_symbol("SHOP", exchange="TSX")
