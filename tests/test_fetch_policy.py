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
    FetchPolicy,
    RetentionClass,
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


class TestTheEodhdLicenceNote:
    """The note this platform stamps on every price document, pinned.

    It has been wrong once and the shape of the error is worth keeping in view. Until
    5 August 2026 it said "derived figures may be published, raw series may not" as though
    the terms said so; reading them showed they contain no derived-data exemption at all,
    and the note was corrected to say the question was unresolved. On 9 August 2026 the
    operator determined, having read the executed agreement, that derived figures may in
    fact be published — so the note says that again, and this time it says **who decided**.

    That is the distinction these tests exist to hold. The permission is the same; its basis
    is not. A note asserting a right with no stated basis is the original error, and it is
    the one that matters, because this sentence is what answers "may we quote this?" long
    after everybody has forgotten who read what.
    """

    def _note(self) -> str:
        return DEFAULT_POLICIES[Provider.EODHD].licence_note

    def test_it_permits_derived_figures(self):
        note = self._note().lower()
        assert "may be published" in note

    def test_it_says_whose_determination_that_is(self):
        """The whole difference between this note and the wrong one it replaced."""
        note = self._note().lower()
        assert "the operator determined" in note
        assert "rather than an inference from the published terms" in note

    def test_it_does_not_extend_the_permission_to_the_series_itself(self):
        """A chart of the price history is the information in repackaged form, which the
        terms prohibit without ambiguity. The determination was about derived figures."""
        note = self._note().lower()
        assert "does not extend to" in note
        assert "repackaged form" in note

    def test_the_flag_and_the_note_agree(self):
        """A flag permitting more than the note claims would be a permission with no stated
        basis, which is exactly the error of the note this replaced."""
        policy = DEFAULT_POLICIES[Provider.EODHD]

        assert policy.derived_figures_publishable is True
        assert "may be published" in policy.licence_note.lower()

    def test_a_provider_with_no_determination_publishes_nothing_derived(self):
        """Silence is not permission. Every other provider is either openly licensed — where
        the question does not arise — or has had no determination made, and starts closed."""
        undetermined = [
            provider
            for provider, policy in DEFAULT_POLICIES.items()
            if policy.retention is RetentionClass.LICENSED
            and "the operator determined" not in policy.licence_note.lower()
        ]

        for provider in undetermined:
            assert not DEFAULT_POLICIES[provider].derived_figures_publishable, provider.value

    def test_the_default_is_closed(self):
        """A paid feed added tomorrow inherits no permission from this one."""
        assert FetchPolicy.__dataclass_fields__["derived_figures_publishable"].default is False

    def test_it_names_the_display_and_repackaging_prohibition(self):
        """Not just redistribution. The wider wording is the whole reason the note changed."""
        note = self._note().lower()
        assert "displaying" in note
        assert "repackaged" in note

    def test_it_records_the_post_termination_deletion_obligation(self):
        """One month after the subscription ends, and the artefact store cannot delete.

        Carried on the note because it is a property of the data rather than of the run, and
        because the conflict with the immutable store is the thing a reader has to know.
        """
        assert "one month" in self._note()

    def test_the_rate_leaves_headroom_below_the_published_limit(self):
        """1,000 requests a minute is the published ceiling; this stays well under it."""
        policy = DEFAULT_POLICIES[Provider.EODHD]
        assert policy.requests_per_second * 60 < 1000
