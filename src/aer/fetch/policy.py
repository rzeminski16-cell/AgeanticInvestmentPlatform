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

``REFUSED_HOSTS`` is the one thing here that is not an allowlist, and it exists because
"absent" and "refused" are different states that an allowlist cannot tell apart. A host is
absent because nobody has needed it yet, and ``extra_hosts`` exists so a call site can
admit one for a single request. A *refusal* is a determination that was taken and written
down — a publisher whose terms forbid automated access — and it has to survive that
mechanism, so it is checked before the allowlist, before ``extra_hosts``, under every
provider, on the original URL and again on every redirect hop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from aer.core.enums import Provider, SourceTier
from aer.fetch.errors import UrlNotAllowedError

__all__ = [
    "DEFAULT_POLICIES",
    "REFUSED_HOSTS",
    "DelegatedDownload",
    "FetchPolicy",
    "HostRefusal",
    "RetentionClass",
    "host_matches",
    "may_carry_credential",
    "policy_for",
    "policy_for_url",
    "refusal_for",
]


class RetentionClass(StrEnum):
    """How long this provider's bytes may be kept, as a property of its licence.

    Declared beside ``licence_note`` because it comes from the same paragraph of the same
    agreement. A retention rule kept somewhere else is one that gets out of step with the
    terms it came from.
    """

    PERMANENT = "permanent"
    """Kept for ever. Public filings, official statistics, open-licensed material — nothing
    in their terms asks for deletion, and invariant 1 asks for the opposite."""

    LICENSED = "licensed"
    """Deletable, and one day required to be deleted. A subscription agreement that obliges
    the subscriber to destroy every copy within a month of the subscription ending — EODHD's
    does — cannot be honoured by a store with no delete path. The bytes go; the provenance
    stays. See ADR 0031."""


@dataclass(frozen=True, slots=True)
class DelegatedDownload:
    """A host a publisher hands its own document downloads off to (ADR 0127).

    Not an allowlist entry, and the difference is the whole point. ``host`` is admitted
    **only** as the target of a redirect issued by ``from_host``, so the platform follows
    the register to its own object store and will not fetch that store on anybody else's
    say-so — including its own, if a URL to it arrives by another route.
    """

    # The destination, matched exactly or as a domain suffix, like `allowed_hosts`.
    host: str

    # The one host whose redirect admits it. A hop from anywhere else is refused.
    from_host: str

    # Why this delegation exists, in a sentence, for the refusal message and the record.
    reason: str


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

    # Where this publisher's own downloads are served from, when that is not one of the
    # hosts above (ADR 0127). Empty for every provider that serves its own bytes.
    delegated_downloads: tuple[DelegatedDownload, ...] = ()

    # Whether the licence obliges deletion at some point. Almost everything here is
    # permanent; a paid feed is the exception, and it is the exception that decides
    # whether an archive can comply with its own terms.
    retention: RetentionClass = RetentionClass.PERMANENT

    # Whether a figure *computed from* this provider's data may leave the machine — a
    # multiple, a ratio, a score — as distinct from the series itself.
    #
    # **Closed by default, and that default is the point.** For an open licence the
    # question does not arise, because the raw data may be published too. It arises only
    # for a paid feed, where the terms usually prohibit redistributing the information and
    # then say nothing at all about what is derived from it. Silence is not permission, so
    # a provider added tomorrow starts unable to publish anything derived, and opening it
    # is a decision somebody has to make and record.
    #
    # When this is true for a licensed provider, the reason is written into the licence
    # note beside it, naming who determined it and when. A flag flipped with no such
    # sentence would be the most consequential undocumented change in this file.
    derived_figures_publishable: bool = False

    # Whether a bounded passage of this provider's content may be reproduced **verbatim**
    # in an exported document — the question ADR 0119 gates a printed excerpt on, and a
    # different question from the one above: that one is about a figure computed from the
    # content, this one is about the content itself.
    #
    # **Closed by default, for the reason the flag above is.** Silence is not permission,
    # so a provider added tomorrow prints no excerpt until somebody reads its terms and
    # says otherwise. Where it is open, the licence note beside it says so in words — the
    # note is what is stamped on every source document and what answers "may we quote
    # this?" years later, and a flag permitting more than the note claimed would be a
    # permission with no stated basis.
    verbatim_excerpt_publishable: bool = False

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
        # Not subject to copyright in the United States, as the note above states.
        verbatim_excerpt_publishable=True,
        burst=8,
        honours_robots=False,
    ),
    Provider.COMPANIES_HOUSE: FetchPolicy(
        provider=Provider.COMPANIES_HOUSE,
        source_tier=SourceTier.T1_REGULATORY,
        allowed_hosts=(".companieshouse.gov.uk", ".company-information.service.gov.uk"),
        licence_note="Open Government Licence v3.0. Attribution required.",
        requests_per_second=1.8,
        # The register's document endpoint answers 302 to a pre-signed object-store URL, so
        # without this no UK filing can be fetched at all (ADR 0127). Admitted only as the
        # target of a redirect from the document API itself: that host serves every AWS
        # customer's bucket in the region, and allowlisting it outright would admit anything
        # that redirected there under this platform's most trusted provider.
        delegated_downloads=(
            DelegatedDownload(
                host="s3.eu-west-2.amazonaws.com",
                from_host="document-api.company-information.service.gov.uk",
                reason=(
                    "Companies House serves filed documents from its own object store and "
                    "redirects to a pre-signed URL there."
                ),
            ),
        ),
        # The OGL permits copying and publishing with attribution, which the appendix gives.
        verbatim_excerpt_publishable=True,
        burst=2,
        honours_robots=False,
    ),
    Provider.FCA_NSM: FetchPolicy(
        provider=Provider.FCA_NSM,
        source_tier=SourceTier.T1_REGULATORY,
        # Empty, and reinforced by REFUSED_HOSTS below: the FCA's terms prohibit automated
        # access to its sites without its prior written consent, which this project does
        # not hold. See ADR 0022. The provider survives the refusal because a *manually*
        # obtained NSM document is still a Tier 1 regulatory filing and still needs these
        # terms recorded against it.
        allowed_hosts=(),
        licence_note=(
            "FCA National Storage Mechanism. Regulated disclosures, free to read on the "
            "FCA's site under its terms of use. Automated access is not permitted without "
            "the FCA's prior written consent, so this document was supplied by hand. "
            "Quoted for research with attribution; not redistributed."
        ),
        requests_per_second=1.0,
        # Quotation with attribution is exactly what the note reserves; the appendix is
        # the attribution, and a bounded passage is not the document redistributed.
        verbatim_excerpt_publishable=True,
    ),
    Provider.EODHD: FetchPolicy(
        provider=Provider.EODHD,
        source_tier=SourceTier.T4_LICENSED_MARKET,
        allowed_hosts=("eodhd.com", "eodhistoricaldata.com"),
        # **Corrected 2026-08-05, and the correction matters.** This note previously said
        # "derived figures may be published, raw series may not". EODHD's published terms do
        # not say that. They prohibit selling, retransmitting, redistributing or *displaying*
        # information in its "original or repackaged form", and there is no derived-data safe
        # harbour anywhere in them — so whether a computed multiple may be published is
        # unresolved rather than permitted. A licence note is stamped on every source document
        # and is what answers "may we quote this?" years later; one that overstated the
        # position would have been the most consequential wrong sentence in the codebase.
        # See ADR 0030.
        licence_note=(
            "Licensed market data under a subscription agreement. Selling, retransmitting, "
            "redistributing or displaying the information in original or repackaged form is "
            "prohibited without prior written approval. Figures computed from it — multiples, "
            "ratios and other derived values — may be published: the operator determined this "
            "on 2026-08-09, having read the executed agreement, and the determination is "
            "theirs rather than an inference from the published terms. It does not extend to "
            "the series itself or to a chart of it, which remain the information in "
            "repackaged form. Copies must be deleted within one month of the subscription "
            "ending."
        ),
        # Set by the operator's determination of 2026-08-09, recorded in the note above and
        # in ADR 0030's amendment. **The note and this flag must agree**: the note is what
        # is stamped on every source document and what answers "may we quote this?" years
        # later, and a flag that permitted more than the note claimed would be a permission
        # with no stated basis. `TestTheEodhdLicenceNote` holds them together.
        derived_figures_publishable=True,
        # From the published limit of 1,000 requests per minute (16.6 per second), with the
        # same headroom the SEC policy keeps below its own published rate. This platform pulls
        # a handful of series per run, roughly weekly, so the ceiling is nowhere near binding
        # and a conservative number costs nothing.
        #
        # **A second limiter is required and is not built.** The daily allowance is 100,000
        # *weighted* calls rather than requests: technical and news endpoints cost 5,
        # fundamentals and options cost 10, whole-exchange bulk requests cost 100. A rolling
        # request limiter cannot see that, so a daily weighted ledger is part of the adapter
        # work rather than of this policy.
        requests_per_second=8.0,
        # The only licensed feed here, and the only one whose bytes have an expiry date:
        # the agreement requires every copy destroyed within a month of the subscription
        # ending. An archive with no delete path cannot honour that, which is why one
        # exists — see ADR 0031.
        retention=RetentionClass.LICENSED,
        # Left closed, and this is the provider the flag exists for. The terms prohibit
        # displaying the information in original or repackaged form, and the bytes carry a
        # deletion obligation: a purge that left them quoted in every exported PDF would
        # not be a purge. See ADR 0119.
        burst=8,
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
        verbatim_excerpt_publishable=True,
        honours_robots=False,
    ),
    Provider.ONS: FetchPolicy(
        provider=Provider.ONS,
        source_tier=SourceTier.T3_OFFICIAL_STATS,
        allowed_hosts=("api.ons.gov.uk", "api.beta.ons.gov.uk", ".ons.gov.uk"),
        licence_note=(
            "Office for National Statistics. Crown copyright, licensed under the Open "
            "Government Licence v3.0; commercial re-use permitted with source accreditation."
        ),
        # A documented public API rather than a site being crawled, so robots does not apply
        # in the sense it applies to an issuer's website -- but the pace stays conservative,
        # because one series pull per run does not need more.
        requests_per_second=2.0,
        verbatim_excerpt_publishable=True,
        honours_robots=False,
    ),
    Provider.ECB: FetchPolicy(
        provider=Provider.ECB,
        source_tier=SourceTier.T3_OFFICIAL_STATS,
        allowed_hosts=("data-api.ecb.europa.eu", ".ecb.europa.eu"),
        licence_note=(
            "European Central Bank. Free to use, including commercially, with the ECB "
            "credited as the source. Euro foreign-exchange reference rates are indicative "
            "and not intended for use in market transactions."
        ),
        # The Data Portal is a documented machine-readable API rather than a site being
        # crawled — and unlike the Bank of England's (ADR 0026) its `robots.txt` does not
        # disallow the route the ECB itself documents for programmatic access. A handful of
        # currency series per run needs nothing faster than this.
        requests_per_second=2.0,
        verbatim_excerpt_publishable=True,
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
        # Fair dealing for research is what the note claims, and a bounded quotation with
        # its source named is the form that claim takes.
        verbatim_excerpt_publishable=True,
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
        # "Short quotation only" is a permission and a bound in one sentence. The bound is
        # the excerpt ceiling, which is what makes the passage a quotation rather than the
        # reproduction the same sentence refuses.
        verbatim_excerpt_publishable=True,
    ),
}


@dataclass(frozen=True, slots=True)
class HostRefusal:
    """A host this platform will not fetch from, and the determination that says so."""

    # Matched with `host_matches`, so a leading dot covers subdomains. A lookalike such as
    # `fca.org.uk.evil.test` is deliberately *not* caught here: it is refused by the
    # allowlist, which is the honest reason to refuse it. Widening the refusal to catch
    # lookalikes would mean refusing hosts on the grounds of somebody else's terms.
    pattern: str

    # Stated in the error, because a refusal a caller cannot explain to a colleague is one
    # they will work around.
    reason: str

    # Repository-relative path to the ADR. Recording where the decision lives is what makes
    # it reversible: consent obtained later changes one document and one tuple entry.
    determination: str


REFUSED_HOSTS: Final[tuple[HostRefusal, ...]] = (
    HostRefusal(
        pattern=".fca.org.uk",
        reason=(
            "The FCA's terms prohibit using a scraper, robot, spider or any other automated "
            "process to access, acquire, copy or monitor its site or the content on it "
            "without the FCA's prior written consent, which this project does not hold. "
            "National Storage Mechanism documents are obtained by hand or not at all."
        ),
        determination="docs/adr/0022-the-fca-nsm-is-not-fetched-automatically.md",
    ),
)


def refusal_for(host: str) -> HostRefusal | None:
    """The refusal covering ``host``, if there is one."""
    for refusal in REFUSED_HOSTS:
        if host_matches(host, refusal.pattern):
            return refusal
    return None


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
    url: str,
    provider: Provider,
    *,
    extra_hosts: tuple[str, ...] = (),
    came_from: str | None = None,
) -> FetchPolicy:
    """Return the provider's policy, having confirmed the URL's host is permitted.

    Args:
        url: The URL about to be fetched.
        provider: Which provider this fetch is attributed to.
        extra_hosts: Additional hosts permitted for this request only. This is how an
            issuer's investor-relations domain is admitted once it has been resolved from
            a filing, without widening the standing allowlist for every future fetch.
        came_from: The URL that redirected here, on a redirect hop only. Supplied by the
            fetcher's own loop and never by a caller — which is what makes a delegated
            download (ADR 0127) narrower than an allowlist entry: the host is reachable
            only by following the publisher, not by asking for it.

    Raises:
        UrlNotAllowedError: If the host carries a standing refusal, or is not on the
            allowlist for this provider.
    """
    host = (urlsplit(url).hostname or "").lower()

    # Before the provider is even looked up, because a refusal is not a property of the
    # provider: relabelling the fetch, or passing the host in `extra_hosts`, must not get
    # round it.
    refusal = refusal_for(host)
    if refusal is not None:
        message = f"{host} is not fetched by this platform. {refusal.reason}"
        raise UrlNotAllowedError(
            message,
            context={
                "host": host,
                "provider": provider.value,
                "determination": refusal.determination,
            },
        )

    policy = policy_for(provider)
    permitted = (*policy.allowed_hosts, *extra_hosts)
    if any(host_matches(host, pattern) for pattern in permitted):
        return policy

    if came_from is not None and _is_delegated(policy, host=host, came_from=came_from):
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


def may_carry_credential(policy: FetchPolicy, url: str) -> bool:
    """Whether this provider's credential may be sent to this URL (ADR 0127).

    **Only to the publisher's own hosts.** A credential is issued by one publisher for its
    own API, and every other host a fetch can reach — a delegated object store, an issuer
    domain admitted for one request — is a third party as far as that credential is
    concerned.

    Measured, not theorised. The first real Companies House document fetch sent the API key
    to Amazon S3 on the redirect, and S3 answered 400 *quoting the header back*, which the
    fetcher then archived: a credential in a third party's error log and in this platform's
    own artefact store. The header had always been attached by provider, which was sound
    while every admitted host was one of the provider's own; a delegated download is the
    case that makes the difference load-bearing.
    """
    host = (urlsplit(url).hostname or "").lower()
    return any(host_matches(host, pattern) for pattern in policy.allowed_hosts)


def _is_delegated(policy: FetchPolicy, *, host: str, came_from: str) -> bool:
    """Whether this hop is the publisher handing off its own download (ADR 0127).

    Both ends are checked, and the origin end is the one doing the work: the destination
    alone would be an allowlist entry by another name, while "only where the register sent
    us" is a door that cannot be walked through from outside.

    One hop only, by construction. A delegated host that redirects onward arrives here with
    itself as the origin, matches no rule, and is refused — so a chain cannot walk out of
    the delegation it was admitted by.
    """
    origin = (urlsplit(came_from).hostname or "").lower()
    return any(
        host_matches(host, rule.host) and host_matches(origin, rule.from_host)
        for rule in policy.delegated_downloads
    )
