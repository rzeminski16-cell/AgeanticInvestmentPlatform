"""The allowlist and per-provider configuration.

``host_matches`` gets the most attention because the classic allowlist bug lives there:
``host.endswith("sec.gov")`` also accepts ``evil-sec.gov``, which is a domain anyone can
register for a few pounds. The tests below are mostly about hosts that must *not* match.
"""

from __future__ import annotations

import pytest

from aer.core.enums import Provider, SourceTier
from aer.fetch.errors import UrlNotAllowedError
from aer.fetch.policy import DEFAULT_POLICIES, host_matches, policy_for, policy_for_url

pytestmark = pytest.mark.usefixtures("no_real_sockets")


class TestHostMatching:
    @pytest.mark.parametrize(
        ("host", "pattern"),
        [
            ("sec.gov", ".sec.gov"),
            ("www.sec.gov", ".sec.gov"),
            ("data.sec.gov", ".sec.gov"),
            ("a.b.sec.gov", ".sec.gov"),
            ("WWW.SEC.GOV", ".sec.gov"),
            ("www.sec.gov.", ".sec.gov"),
            ("eodhd.com", "eodhd.com"),
        ],
    )
    def test_permitted_hosts_match(self, host, pattern):
        assert host_matches(host, pattern)

    @pytest.mark.parametrize(
        ("host", "pattern"),
        [
            # The bug a naive suffix check would have: all of these end with "sec.gov".
            ("evil-sec.gov", ".sec.gov"),
            ("notsec.gov", ".sec.gov"),
            ("xsec.gov", ".sec.gov"),
            # A lookalike that merely contains the domain.
            ("sec.gov.evil.test", ".sec.gov"),
            ("evil.test", ".sec.gov"),
            # An exact pattern must not accept subdomains.
            ("sub.eodhd.com", "eodhd.com"),
            ("", ".sec.gov"),
        ],
    )
    def test_lookalike_hosts_do_not_match(self, host, pattern):
        assert not host_matches(host, pattern)


class TestPolicies:
    def test_every_provider_with_a_policy_has_a_licence_note(self):
        # Recorded on every source document at acquisition. "May we quote this?" is
        # answerable now and much harder to reconstruct from a URL a year later.
        for policy in DEFAULT_POLICIES.values():
            assert policy.licence_note.strip()

    def test_every_policy_has_a_positive_rate(self):
        for policy in DEFAULT_POLICIES.values():
            assert policy.requests_per_second > 0
            assert policy.burst >= 1

    def test_the_sec_rate_leaves_headroom_below_its_published_limit(self):
        # The SEC states ten per second and blocks above it. Eight leaves room for clock
        # skew between workers sharing the bucket.
        assert DEFAULT_POLICIES[Provider.SEC_EDGAR].requests_per_second <= 8.0

    def test_regulatory_providers_are_tier_one(self):
        for provider in (Provider.SEC_EDGAR, Provider.COMPANIES_HOUSE, Provider.FCA_NSM):
            assert DEFAULT_POLICIES[provider].source_tier is SourceTier.T1_REGULATORY

    def test_a_provider_with_no_policy_is_refused(self):
        # No policy means no rate limit and no recorded licence terms, and fetching under
        # those conditions is what this module exists to prevent.
        with pytest.raises(UrlNotAllowedError, match="No fetch policy"):
            policy_for(Provider.USER_SUPPLIED)


class TestUrlAdmission:
    def test_an_allowed_host_returns_its_policy(self):
        policy = policy_for_url("https://www.sec.gov/x", Provider.SEC_EDGAR)

        assert policy.provider is Provider.SEC_EDGAR

    def test_a_host_off_the_allowlist_is_refused(self):
        with pytest.raises(UrlNotAllowedError, match="not on the allowlist"):
            policy_for_url("https://example.test/x", Provider.SEC_EDGAR)

    def test_a_host_allowed_for_another_provider_is_still_refused(self):
        # The allowlist is per provider, not global. A Companies House URL fetched under
        # the SEC's rate limit and licence note would be recorded with the wrong terms.
        with pytest.raises(UrlNotAllowedError):
            policy_for_url("https://www.sec.gov/x", Provider.COMPANIES_HOUSE)

    def test_an_issuer_host_can_be_admitted_for_one_request(self):
        # An issuer's investor-relations host differs for every company, so it is supplied
        # once resolved rather than widening the standing allowlist for every future
        # fetch.
        policy = policy_for_url(
            "https://ir.example-plc.com/annual-report.pdf",
            Provider.ISSUER_IR,
            extra_hosts=("ir.example-plc.com",),
        )

        assert policy.source_tier is SourceTier.T2_ISSUER

    def test_an_issuer_host_is_refused_without_that_admission(self):
        with pytest.raises(UrlNotAllowedError):
            policy_for_url("https://ir.example-plc.com/x", Provider.ISSUER_IR)

    def test_the_refusal_names_what_would_have_been_allowed(self):
        with pytest.raises(UrlNotAllowedError) as excinfo:
            policy_for_url("https://evil.test/x", Provider.SEC_EDGAR)

        assert excinfo.value.context["host"] == "evil.test"
        assert ".sec.gov" in excinfo.value.context["allowed"]
