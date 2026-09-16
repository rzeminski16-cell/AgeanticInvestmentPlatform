"""What the operator said a run may not read, and how a URL is held to it.

A research request's ``excluded_sources`` is a list of bare domains: the request schema
normalises whatever was pasted — a URL, a host with a port, a ``www.`` prefix — to that
shape, lower-case. This module is the other half of the promise: given a URL or a host,
which of those domains it falls under, if any. The fetch executors ask it before a page
is read, the acquisition service asks it before a document is admitted, and a search
listing asks it before a hit is shown, so the answer has to come from one place.

**A domain excludes itself and everything beneath it.** An operator who writes
``seekingalpha.com`` means the site, not one host on it, so ``news.seekingalpha.com`` is
excluded too. **Plain suffix matching is not the rule**, for the reason the fetch layer's
allowlist gives: ``notseekingalpha.com`` is a domain somebody can register, and a check by
string suffix would refuse it here and admit its mirror image there. The match is on label
boundaries, and only there.

Pure: no session, no clock, no lookup. What the operator typed and what the run is about
to read are the whole of the input.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

__all__ = ["excluded_domain_for", "host_of"]


def host_of(url_or_host: str) -> str | None:
    """The bare host a URL or a host string names, or ``None`` where it names none.

    Lower-case, without a trailing dot and without a leading ``www.`` — the same shape the
    request schema stores an excluded domain in, so the two compare directly.
    """
    candidate = url_or_host.strip()
    if not candidate:
        return None
    if "://" in candidate:
        host = urlsplit(candidate).hostname or ""
    else:
        host = candidate.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    host = host.lower().rstrip(".").removeprefix("www.")
    return host or None


def excluded_domain_for(url_or_host: str, excluded: Iterable[str]) -> str | None:
    """The excluded domain this URL or host falls under, or ``None`` if it falls under none.

    Returns the domain as the operator wrote it, because a refusal should name what they
    asked for rather than a normalised form of it.
    """
    host = host_of(url_or_host)
    if host is None:
        return None
    for domain in excluded:
        bare = domain.strip().lower().rstrip(".").removeprefix("www.")
        if bare and (host == bare or host.endswith("." + bare)):
            return domain
    return None
