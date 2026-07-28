"""Which securities are in scope, and which are refused.

Two kinds of test here, and the second matters more than the first.

The obvious kind checks that an ETF is rejected. The important kind checks that
*Trustpilot* is not — because these rules work by pattern-matching names, and a rule that
refuses to research a legitimate company whose name happens to contain "trust" is worse
than no rule at all: it is wrong in a direction the operator cannot work around.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.core.universe import (
    DEFAULT_MICRO_CAP_THRESHOLD_GBP,
    SUPPORTED_EXCHANGES,
    ExclusionRule,
    check_universe,
    is_micro_cap,
)


def rules(**kwargs) -> list[ExclusionRule]:
    defaults = {"ticker": "MSFT", "exchange": "NASDAQ", "company_name": "Microsoft Corporation"}
    return [exclusion.rule for exclusion in check_universe(**{**defaults, **kwargs})]


class TestInScope:
    @pytest.mark.parametrize(
        ("ticker", "exchange", "name"),
        [
            ("MSFT", "NASDAQ", "Microsoft Corporation"),
            ("JNJ", "NYSE", "Johnson & Johnson"),
            ("RIO.L", "LSE", "Rio Tinto plc"),
            ("BP.L", "LSE", "BP p.l.c."),
            ("IMPP", "NYSE_AMERICAN", "Imperial Petroleum Inc."),
        ],
    )
    def test_an_ordinary_operating_company_passes(self, ticker, exchange, name):
        assert check_universe(ticker=ticker, exchange=exchange, company_name=name) == []

    @pytest.mark.parametrize(
        "name",
        [
            "Trustpilot Group plc",
            "Trustmark Inc.",
            "NatWest Group plc",
            "Etsy, Inc.",
            "Vanguard Natural Resources",
        ],
    )
    def test_a_name_that_merely_resembles_a_fund_still_passes(self, name):
        # The false-positive guard. "Trust" inside Trustpilot, "Etsy" containing "ets",
        # and a real operating company that happens to be called Vanguard must all be
        # researchable. A word-boundary miss here silently blocks legitimate work.
        assert rules(ticker="XYZ", exchange="LSE", company_name=name) == []


class TestExchangeRules:
    def test_every_supported_exchange_is_accepted(self):
        for exchange in SUPPORTED_EXCHANGES:
            assert rules(exchange=exchange) == []

    @pytest.mark.parametrize("exchange", ["OTCQB", "OTCQX", "PINK", "OTCMKTS", "AQSE"])
    def test_an_otc_venue_is_refused_as_such(self, exchange):
        # Named specifically rather than lumped in with "unsupported", because the reason
        # is different: there is no dependable filing stream to cite.
        assert rules(exchange=exchange) == [ExclusionRule.OTC_VENUE]

    @pytest.mark.parametrize("exchange", ["XETRA", "TSX", "ASX", "TYO", "EURONEXT", ""])
    def test_an_unsupported_exchange_is_refused(self, exchange):
        assert rules(exchange=exchange) == [ExclusionRule.UNSUPPORTED_EXCHANGE]

    def test_the_refusal_lists_what_is_supported(self):
        exclusions = check_universe(ticker="X", exchange="TSX", company_name="Some Co")
        for exchange in SUPPORTED_EXCHANGES:
            assert exchange in exclusions[0].message

    def test_case_and_punctuation_do_not_matter(self):
        assert rules(exchange="nyse american") == []
        assert rules(exchange="nyse-american") == []


class TestFundRules:
    @pytest.mark.parametrize("ticker", ["SPY", "QQQ", "VOO", "IWM", "VUSA", "ARKK"])
    def test_a_known_fund_ticker_is_refused(self, ticker):
        assert ExclusionRule.EXCHANGE_TRADED_FUND in rules(ticker=ticker, exchange="NYSE")

    @pytest.mark.parametrize(
        "name",
        [
            "SPDR S&P 500 ETF Trust",
            "iShares Core MSCI World UCITS ETF",
            "Vanguard S&P 500 UCITS ETF",
            "Invesco QQQ Trust",
            "WisdomTree Physical Gold",
            "Some Index Fund plc",
        ],
    )
    def test_a_fund_name_is_refused(self, name):
        assert ExclusionRule.EXCHANGE_TRADED_FUND in rules(ticker="ZZZ", company_name=name)

    def test_the_refusal_explains_the_category_error(self):
        exclusions = check_universe(
            ticker="SPY", exchange="NYSE", company_name="SPDR S&P 500 ETF Trust"
        )
        message = " ".join(e.message for e in exclusions)
        assert "no revenue" in message
        assert "underlying holdings" in message


class TestInvestmentTrustRules:
    @pytest.mark.parametrize(
        "name",
        [
            "Scottish Mortgage Investment Trust plc",
            "F&C Investment Trust PLC",
            "British Smaller Companies VCT plc",
            "Segro plc REIT",
            "Foresight Venture Capital Trust",
        ],
    )
    def test_a_closed_ended_fund_is_refused(self, name):
        assert ExclusionRule.INVESTMENT_TRUST in rules(
            ticker="ZZZ", exchange="LSE", company_name=name
        )

    def test_the_refusal_names_the_valuation_model_that_does_not_apply(self):
        exclusions = check_universe(
            ticker="SMT", exchange="LSE", company_name="Scottish Mortgage Investment Trust plc"
        )
        assert "net asset value" in exclusions[0].message


class TestMicroCap:
    def test_below_the_threshold_is_a_micro_cap(self):
        assert is_micro_cap(Decimal("50000000"))

    def test_above_the_threshold_is_not(self):
        assert not is_micro_cap(Decimal("2000000000"))

    def test_exactly_at_the_threshold_is_not(self):
        assert not is_micro_cap(DEFAULT_MICRO_CAP_THRESHOLD_GBP)

    def test_an_unknown_capitalisation_is_not_treated_as_micro_cap(self):
        # Not leniency — honesty. At request time no external call has been made, so the
        # figure is genuinely unknown, and inferring one from a ticker would be exactly
        # the invented number this codebase exists to prevent.
        assert not is_micro_cap(None)

    def test_the_rule_does_not_fire_during_request_creation(self):
        # check_universe is called without a capitalisation while a request is written.
        # Documented here so the absence is a known consequence rather than a gap someone
        # discovers and assumes is a bug.
        assert rules(ticker="TINY", exchange="LSE", company_name="Tiny Co plc") == []

    def test_it_does_fire_once_a_figure_is_supplied(self):
        exclusions = check_universe(
            ticker="TINY",
            exchange="LSE",
            company_name="Tiny Co plc",
            market_cap_gbp=Decimal("10000000"),
        )
        assert [e.rule for e in exclusions] == [ExclusionRule.MICRO_CAP]
        assert "£300,000,000" in exclusions[0].message

    def test_the_threshold_is_adjustable(self):
        exclusions = check_universe(
            ticker="MID",
            exchange="LSE",
            company_name="Mid Co plc",
            market_cap_gbp=Decimal("500000000"),
            micro_cap_threshold_gbp=Decimal("1000000000"),
        )
        assert [e.rule for e in exclusions] == [ExclusionRule.MICRO_CAP]


class TestMultipleReasons:
    def test_every_applicable_rule_is_reported(self):
        # Told "wrong exchange", an operator fixes the exchange and resubmits only to
        # learn it is also a fund. One round trip per rule is a bad way to discover that
        # something was never going to work.
        found = rules(ticker="SPY", exchange="OTCQB", company_name="SPDR S&P 500 ETF Trust")

        assert set(found) == {ExclusionRule.OTC_VENUE, ExclusionRule.EXCHANGE_TRADED_FUND}

    def test_a_fund_and_a_trust_are_reported_separately(self):
        # "ETF Trust" in a fund's name must not also trip the investment-trust rule: an
        # open-ended ETF and a closed-ended trust are different things, and reporting both
        # would tell the operator to fix something that is not wrong.
        assert ExclusionRule.INVESTMENT_TRUST not in rules(
            ticker="SPY", exchange="NYSE", company_name="SPDR S&P 500 ETF Trust"
        )
        assert ExclusionRule.EXCHANGE_TRADED_FUND not in rules(
            ticker="SMT", exchange="LSE", company_name="Scottish Mortgage Investment Trust plc"
        )

    def test_an_exchange_is_reported_once_not_twice(self):
        # OTC and "unsupported" are mutually exclusive descriptions of the same field.
        found = rules(exchange="PINK")
        assert found.count(ExclusionRule.UNSUPPORTED_EXCHANGE) == 0
        assert found.count(ExclusionRule.OTC_VENUE) == 1


class TestRuleIdentifiers:
    def test_every_rule_has_a_distinct_stable_value(self):
        # These reach the API as `code`, so a client can branch on them. Duplicates would
        # make two different refusals indistinguishable.
        values = [rule.value for rule in ExclusionRule]
        assert len(set(values)) == len(values)
        assert all(value.islower() for value in values)
