"""The allowlist and per-provider configuration.

``host_matches`` gets the most attention because the classic allowlist bug lives there:
``host.endswith("sec.gov")`` also accepts ``evil-sec.gov``, which is a domain anyone can
register for a few pounds. The tests below are mostly about hosts that must *not* match.

``TestRefusedHosts`` is about the other direction. A refusal that only holds under the
provider it was written for is not a refusal, because ``extra_hosts`` and a different
``provider=`` argument are both one keyword away.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aer.core.enums import Provider, SourceTier
from aer.fetch.errors import UrlNotAllowedError
from aer.fetch.policy import (
    DEFAULT_POLICIES,
    REFUSED_HOSTS,
    host_matches,
    policy_for,
    policy_for_url,
    refusal_for,
)

pytestmark = pytest.mark.usefixtures("no_real_sockets")

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class TestRefusedHosts:
    """A standing refusal, and the three ways it could have been sidestepped."""

    @pytest.mark.parametrize(
        "host",
        ["fca.org.uk", "www.fca.org.uk", "data.fca.org.uk", "DATA.FCA.ORG.UK"],
    )
    def test_the_fca_and_its_subdomains_are_refused(self, host):
        with pytest.raises(UrlNotAllowedError, match="not fetched by this platform"):
            policy_for_url(f"https://{host}/nsm/document/abc", Provider.FCA_NSM)

    @pytest.mark.parametrize("provider", list(Provider))
    def test_no_provider_label_gets_round_the_refusal(self, provider):
        # A refusal is a property of the publisher, not of the provider a call site happens
        # to pass. Attributing the fetch to ISSUER_IR or WEB_SEARCH must not change it.
        with pytest.raises(UrlNotAllowedError):
            policy_for_url("https://data.fca.org.uk/nsm/document/abc", provider)

    def test_extra_hosts_cannot_admit_a_refused_host(self):
        # The bypass that matters: `extra_hosts` exists precisely to widen the allowlist
        # for one request, and issuer-IR discovery passes whatever domain it resolved. A
        # refusal checked after the allowlist would be no refusal at all.
        with pytest.raises(UrlNotAllowedError, match="not fetched by this platform"):
            policy_for_url(
                "https://data.fca.org.uk/nsm/document/abc",
                Provider.ISSUER_IR,
                extra_hosts=("data.fca.org.uk",),
            )

    def test_the_refusal_explains_itself_and_says_where_the_decision_lives(self):
        with pytest.raises(UrlNotAllowedError) as excinfo:
            policy_for_url("https://data.fca.org.uk/x", Provider.FCA_NSM)

        assert "prior written consent" in str(excinfo.value)
        assert excinfo.value.context["determination"].startswith("docs/adr/")

    def test_a_lookalike_is_refused_by_the_allowlist_rather_than_by_the_refusal(self):
        # `fca.org.uk.evil.test` is somebody else's domain. Refusing it under the FCA's
        # terms would be stating a reason that is not true; the allowlist refuses it for
        # the reason that is.
        assert refusal_for("fca.org.uk.evil.test") is None

        with pytest.raises(UrlNotAllowedError, match="not on the allowlist"):
            policy_for_url("https://fca.org.uk.evil.test/x", Provider.FCA_NSM)

    def test_the_fca_provider_allows_no_hosts_of_its_own(self):
        # Belt and braces, and the braces are load-bearing: if REFUSED_HOSTS were ever
        # emptied, an allowlist left populated would silently resume fetching.
        assert DEFAULT_POLICIES[Provider.FCA_NSM].allowed_hosts == ()

    def test_the_fca_licence_note_records_that_documents_arrive_by_hand(self):
        # It is copied onto every source document at acquisition, so it has to describe how
        # the document actually got here.
        note = DEFAULT_POLICIES[Provider.FCA_NSM].licence_note
        assert "prior written consent" in note
        assert "by hand" in note

    def test_every_refusal_points_at_a_determination_that_exists(self):
        # A refusal whose ADR was never written, or was renamed, is a refusal nobody can
        # review or reverse.
        for refusal in REFUSED_HOSTS:
            assert (REPO_ROOT / refusal.determination).is_file(), refusal.determination

    def test_every_refusal_gives_a_reason(self):
        for refusal in REFUSED_HOSTS:
            assert refusal.reason.strip()
