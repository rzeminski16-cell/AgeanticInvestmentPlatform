"""What may be fetched, from whom, how fast, and under what terms.

An **allowlist**, not a blocklist. This platform reads from a known, small set of
publishers — a regulator, a registry, a market-data vendor, an issuer's own site. Anything
else arriving in a URL is a mistake or an attempt, and a blocklist would have to
anticipate every host worth refusing while an allowlist only has to name the ones worth
reading.

Each provider carries its terms with it. ``licence_note`` is recorded on every source
document at acquisition, because "may we quote this?" is answerable now and much harder to
reconstruct from a URL a year later. ``requests_per_second`` is politeness expressed as a
number: the SEC publishes a rate it expects clients to respect, and exceeding it gets an
IP banned rather than throttled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from aer.core.enums import Provider, SourceTier
from aer.fetch.errors import UrlNotAllowedError

__all__ = [
    "DEFAULT_POLICIES",
    "FetchPolicy",
    "host_matches",
    "policy_for",
    "policy_for_url",
]


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Everything the fetch layer needs to know about one provider."""

    provider: Provider
    source_tier: SourceTier

    # Hosts this provider is permitted to serve. A leading dot means "this domain and any
    # subdomain"; anything else must match exactly. Suffix matching without the dot is how
    # an allowlist for "sec.gov" ends up permitting "notsec.gov".
    allowed_hosts: tuple[str, ...]

    # What is known about reusing this content. Recorded on every source document.
    licence_note: str

    requests_per_second: float
    burst: int = 1

    # Longest a single request may take. A provider that hangs must not hold a research
    # run open indefinitely.
    timeout_seconds: float = 30.0

    # Consecutive failures before the circuit opens, and how long it stays open.
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0

    # Whether robots.txt applies. False only where access is by a documented API contract
    # rather than by crawling, which is what a provider's own API endpoint is.
    honours_robots: bool = True

    extra_headers: dict[str, str] = field(default_factory=dict)


# Rates follow each provider's published guidance. The SEC states 10 requests per second
# and blocks above it; 8 leaves headroom for clock skew between workers sharing the
# bucket. Companies House allows 600 per five minutes, which is 2/s sustained; 1.8 leaves
# the same margin.
DEFAULT_POLICIES: Final[dict[Provider, FetchPolicy]] = {
    Provider.SEC_EDGAR: FetchPolicy(
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        allowed_hosts=(".sec.gov",),
        licence_note=(
            "US government work; not subject to copyright in the United States. Access "
            "is conditional on sending a descriptive User-Agent identifying the operator."
        ),
        requests_per_second=8.0,
        burst=8,
        honours_robots=False,
    ),
    Provider.COMPANIES_HOUSE: FetchPolicy(
        provider=Provider.COMPANIES_HOUSE,
        source_tier=SourceTier.T1_REGULATORY,
        allowed_hosts=(".companieshouse.gov.uk", ".company-information.service.gov.uk"),
        licence_note="Open Government Licence v3.0. Attribution required.",
        requests_per_second=1.8,
        burst=2,
        honours_robots=False,
    ),
    Provider.FCA_NSM: FetchPolicy(
        provider=Provider.FCA_NSM,
        source_tier=SourceTier.T1_REGULATORY,
        allowed_hosts=(".fca.org.uk", "data.fca.org.uk"),
        licence_note=(
            "FCA National Storage Mechanism. Regulated disclosures, free to access; "
            "check the FCA's terms before any redistribution."
        ),
        requests_per_second=1.0,
    ),
    Provider.EODHD: FetchPolicy(
        provider=Provider.EODHD,
        source_tier=SourceTier.T4_LICENSED_MARKET,
        allowed_hosts=("eodhd.com", "eodhistoricaldata.com"),
        licence_note=(
            "Licensed market data. Redistribution is prohibited by the subscription "
            "agreement; derived figures may be published, raw series may not."
        ),
        requests_per_second=2.0,
        burst=4,
        honours_robots=False,
    ),
    Provider.FRED: FetchPolicy(
        provider=Provider.FRED,
        source_tier=SourceTier.T3_OFFICIAL_STATS,
        allowed_hosts=("api.stlouisfed.org", ".stlouisfed.org"),
        licence_note=(
            "Federal Reserve Bank of St Louis. Free to use with attribution; some "
            "underlying series carry their own provider's terms."
        ),
        requests_per_second=2.0,
        honours_robots=False,
    ),
    Provider.ISSUER_IR: FetchPolicy(
        provider=Provider.ISSUER_IR,
        source_tier=SourceTier.T2_ISSUER,
        # Deliberately empty. An issuer's investor-relations host is different for every
        # company, so the allowlist is supplied per request once the issuer is resolved.
        # An empty tuple refuses everything, which is the right default for a provider
        # whose legitimate hosts are not known in advance.
        allowed_hosts=(),
        licence_note=(
            "Issuer-published material. Copyright the issuer; quoted under fair dealing "
            "for research and reported with attribution."
        ),
        requests_per_second=1.0,
    ),
    Provider.WEB_SEARCH: FetchPolicy(
        provider=Provider.WEB_SEARCH,
        source_tier=SourceTier.T5_SECONDARY,
        allowed_hosts=(),
        licence_note=(
            "Third-party publication. Copyright the publisher; cited by link and short "
            "quotation only, never reproduced."
        ),
        requests_per_second=1.0,
    ),
}


def policy_for(provider: Provider) -> FetchPolicy:
    """The policy for a provider.

    Raises:
        UrlNotAllowedError: If no policy is configured. A provider without a policy has no
            rate limit and no licence note, and fetching under those conditions is exactly
            what this module exists to prevent.
    """
    policy = DEFAULT_POLICIES.get(provider)
    if policy is None:
        message = (
            f"No fetch policy is configured for {provider.value}. A provider with no "
            "policy has no rate limit and no recorded licence terms."
        )
        raise UrlNotAllowedError(message, context={"provider": provider.value})
    return policy


def host_matches(host: str, pattern: str) -> bool:
    """Whether ``host`` is permitted by one allowlist entry.

    A leading dot means the domain and its subdomains. Without it the match is exact.
    Plain suffix matching is the classic mistake here: ``endswith("sec.gov")`` also
    accepts ``evil-sec.gov``, which is a host an attacker can register.
    """
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("."):
        bare = pattern[1:]
        return host == bare or host.endswith(pattern)
    return host == pattern


def policy_for_url(
    url: str, provider: Provider, *, extra_hosts: tuple[str, ...] = ()
) -> FetchPolicy:
    """Return the provider's policy, having confirmed the URL's host is permitted.

    Args:
        url: The URL about to be fetched.
        provider: Which provider this fetch is attributed to.
        extra_hosts: Additional hosts permitted for this request only. This is how an
            issuer's investor-relations domain is admitted once it has been resolved from
            a filing, without widening the standing allowlist for every future fetch.

    Raises:
        UrlNotAllowedError: If the host is not on the allowlist for this provider.
    """
    policy = policy_for(provider)
    host = (urlsplit(url).hostname or "").lower()

    permitted = (*policy.allowed_hosts, *extra_hosts)
    if any(host_matches(host, pattern) for pattern in permitted):
        return policy

    message = (
        f"{host or 'This host'} is not on the allowlist for {provider.value}. This "
        "platform fetches only from publishers it is configured to read, so an "
        "unexpected host is refused rather than tried."
    )
    raise UrlNotAllowedError(
        message,
        context={"host": host, "provider": provider.value, "allowed": sorted(permitted)},
    )
