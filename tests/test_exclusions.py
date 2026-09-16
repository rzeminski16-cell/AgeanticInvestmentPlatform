"""The operator's excluded sources, held to on label boundaries (Phase 1.7).

The request schema stores an exclusion as a bare domain; this is the other half — which of
those domains a URL or a host falls under. Pure, so the same question gets the same answer
from the acquisition service, the fetch executors and a search listing.
"""

from __future__ import annotations

import pytest

from aer.core.exclusions import excluded_domain_for, host_of


class TestTheHostOfAThing:
    @pytest.mark.parametrize(
        ("given", "host"),
        [
            ("https://www.sec.gov/Archives/edgar/x.htm", "sec.gov"),
            ("http://News.Example.com:8080/path?q=1", "news.example.com"),
            ("https://user:secret@example.com/", "example.com"),
            ("example.com", "example.com"),
            ("www.example.com/path", "example.com"),
            ("News.Example.com:443", "news.example.com"),
            ("example.com.", "example.com"),
            ("", None),
            ("   ", None),
            ("https:///no-host", None),
        ],
    )
    def test_a_url_or_a_host_names_one_bare_host(self, given: str, host: str | None) -> None:
        assert host_of(given) == host


class TestWhichExclusionBites:
    def test_a_domain_excludes_itself(self) -> None:
        assert excluded_domain_for("https://example.com/page", ["example.com"]) == "example.com"

    def test_a_domain_excludes_everything_beneath_it(self) -> None:
        """An operator who writes the site means the site, not one host on it."""
        assert excluded_domain_for("https://news.example.com/x", ["example.com"]) == "example.com"
        assert excluded_domain_for("https://a.b.example.com/x", ["example.com"]) == "example.com"

    def test_a_look_alike_suffix_is_not_excluded(self) -> None:
        """The classic mistake, from the fetch layer's allowlist: a suffix match would
        refuse a domain somebody can register — and admit its mirror image elsewhere."""
        assert excluded_domain_for("https://notexample.com/x", ["example.com"]) is None
        assert excluded_domain_for("https://example.com.evil.invalid/x", ["example.com"]) is None

    def test_a_subdomain_exclusion_does_not_reach_its_parent(self) -> None:
        assert excluded_domain_for("https://example.com/x", ["news.example.com"]) is None
        assert (
            excluded_domain_for("https://news.example.com/x", ["news.example.com"])
            == "news.example.com"
        )

    def test_case_and_www_do_not_matter_on_either_side(self) -> None:
        assert excluded_domain_for("https://WWW.Example.COM/x", ["example.com"]) == "example.com"
        assert (
            excluded_domain_for("https://example.com/x", ["www.Example.com"]) == "www.Example.com"
        )

    def test_the_domain_comes_back_as_the_operator_wrote_it(self) -> None:
        """A refusal names what they asked for, not a normalised form of it."""
        assert excluded_domain_for("https://news.example.com/", ["Example.com"]) == "Example.com"

    def test_the_first_matching_exclusion_is_the_one_named(self) -> None:
        found = excluded_domain_for(
            "https://news.example.com/", ["example.com", "news.example.com"]
        )
        assert found == "example.com"

    def test_nothing_excluded_excludes_nothing(self) -> None:
        assert excluded_domain_for("https://example.com/x", []) is None

    def test_a_blank_entry_excludes_nothing(self) -> None:
        assert excluded_domain_for("https://example.com/x", ["", "  "]) is None

    def test_a_thing_with_no_host_falls_under_nothing(self) -> None:
        assert excluded_domain_for("", ["example.com"]) is None

    def test_a_bare_host_is_held_to_the_same_rule(self) -> None:
        assert excluded_domain_for("news.example.com", ["example.com"]) == "example.com"
