"""What a document is worth as evidence, decided by a table.

:class:`~aer.core.enums.SourceTier` drives two rules that decide what a report may say: a tier-5
source is never the sole support for a number, and a tier-6 source is never citable at all. So
the tier has to be assigned the same way every time, by something a person can read and argue
with — **not** by a model, and not by a heuristic over the URL.

The table is keyed on ``(provider, kind)`` because one publisher spans tiers. An issuer's own
annual report is tier 2; a marketing post on the same investor-relations domain is tier 5. The
figures inside a regulatory filing are tier 1 even when the issuer wrote them, because the tier
records *what compels the publisher to be accurate*, not who typed it.

**The default is the least favourable tier that fits, never the most.** An unrecognised
combination resolves to :attr:`~aer.core.enums.SourceTier.T6_UNVERIFIED`, which is not citable —
so a new adapter that forgets to declare its kinds produces sources nobody can build a report on,
loudly, rather than sources that quietly carry more weight than they have earned.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from aer.core.enums import Provider, SourceTier

__all__ = ["DocumentKind", "tier_for"]


class DocumentKind(StrEnum):
    """What sort of document this is, as far as its weight as evidence goes.

    Deliberately coarse, and **not** persisted. What gets stored is the tier — the decision —
    rather than the inputs to it, because the tier is what every downstream rule reads. A finer
    vocabulary would invite adapters to make distinctions the table does not act on.
    """

    REGULATORY_FILING = "regulatory_filing"
    """Filed with a regulator: a 10-K, an RNS announcement, a Companies House return."""

    STRUCTURED_DATA = "structured_data"
    """A machine-readable feed from the publisher: XBRL facts, a price series, a statistics API."""

    ISSUER_PUBLICATION = "issuer_publication"
    """Published by the company itself: an annual report PDF, a results deck, a transcript."""

    ISSUER_MARKETING = "issuer_marketing"
    """Also from the company, and not an accounting document: a blog post, a press release
    about a partnership, a careers page."""

    NEWS_ARTICLE = "news_article"
    """Reporting by a third party."""

    COMMENTARY = "commentary"
    """Analysis, blogs, forums, and anything whose accuracy nothing compels."""

    UNKNOWN = "unknown"
    """Not established. Resolves to the bottom tier; see the module docstring."""


# The table. Read it as "this provider, publishing this kind of thing, is worth this much".
#
# Entries are exhaustive over the pairs the adapters actually produce; anything absent falls to
# `T6_UNVERIFIED` by `tier_for`, and a test asserts every provider has at least one entry so a
# new one cannot be added and silently forgotten.
_TABLE: Final[dict[tuple[Provider, DocumentKind], SourceTier]] = {
    # -- Regulators. Authoritative for what was reported, because filing it wrongly is an
    # offence rather than an embarrassment.
    (Provider.SEC_EDGAR, DocumentKind.REGULATORY_FILING): SourceTier.T1_REGULATORY,
    (Provider.SEC_EDGAR, DocumentKind.STRUCTURED_DATA): SourceTier.T1_REGULATORY,
    (Provider.COMPANIES_HOUSE, DocumentKind.REGULATORY_FILING): SourceTier.T1_REGULATORY,
    (Provider.COMPANIES_HOUSE, DocumentKind.STRUCTURED_DATA): SourceTier.T1_REGULATORY,
    (Provider.FCA_NSM, DocumentKind.REGULATORY_FILING): SourceTier.T1_REGULATORY,
    (Provider.FCA_NSM, DocumentKind.STRUCTURED_DATA): SourceTier.T1_REGULATORY,
    # An issuer publication *retrieved through a regulator* is still the issuer's document. The
    # annual report attached to a filing was written by the company; the regulator hosting it
    # does not audit it.
    (Provider.SEC_EDGAR, DocumentKind.ISSUER_PUBLICATION): SourceTier.T2_ISSUER,
    (Provider.COMPANIES_HOUSE, DocumentKind.ISSUER_PUBLICATION): SourceTier.T2_ISSUER,
    (Provider.FCA_NSM, DocumentKind.ISSUER_PUBLICATION): SourceTier.T2_ISSUER,
    # -- The issuer's own site.
    (Provider.ISSUER_IR, DocumentKind.ISSUER_PUBLICATION): SourceTier.T2_ISSUER,
    (Provider.ISSUER_IR, DocumentKind.REGULATORY_FILING): SourceTier.T2_ISSUER,
    # A company's own marketing is not an accounting document, and treating it as one is how a
    # forward-looking claim ends up cited as a fact.
    (Provider.ISSUER_IR, DocumentKind.ISSUER_MARKETING): SourceTier.T5_SECONDARY,
    (Provider.ISSUER_IR, DocumentKind.NEWS_ARTICLE): SourceTier.T5_SECONDARY,
    # -- Official statistics.
    (Provider.FRED, DocumentKind.STRUCTURED_DATA): SourceTier.T3_OFFICIAL_STATS,
    (Provider.ONS, DocumentKind.STRUCTURED_DATA): SourceTier.T3_OFFICIAL_STATS,
    # A central bank's own published reference rates. Official statistics rather than market
    # data: the ECB says plainly that these are not intended for market transactions, and
    # T4 would claim a tradability they do not have.
    (Provider.ECB, DocumentKind.STRUCTURED_DATA): SourceTier.T3_OFFICIAL_STATS,
    # -- Licensed market data.
    (Provider.EODHD, DocumentKind.STRUCTURED_DATA): SourceTier.T4_LICENSED_MARKET,
    # -- Everything found by searching. Reporting at best.
    (Provider.WEB_SEARCH, DocumentKind.NEWS_ARTICLE): SourceTier.T5_SECONDARY,
    (Provider.WEB_SEARCH, DocumentKind.REGULATORY_FILING): SourceTier.T5_SECONDARY,
    (Provider.WEB_SEARCH, DocumentKind.ISSUER_PUBLICATION): SourceTier.T5_SECONDARY,
    (Provider.WEB_SEARCH, DocumentKind.COMMENTARY): SourceTier.T6_UNVERIFIED,
    # -- Supplied by hand.
    #
    # A document the operator provides is **not** promoted for having been provided. If it is a
    # 10-K it should be fetched from EDGAR, where its hash can be checked against the regulator's
    # copy; a file on a desk has no such record behind it. So a supplied regulatory filing is
    # tier 5: usable, corroborating, never the sole support for a number.
    (Provider.USER_SUPPLIED, DocumentKind.REGULATORY_FILING): SourceTier.T5_SECONDARY,
    (Provider.USER_SUPPLIED, DocumentKind.ISSUER_PUBLICATION): SourceTier.T5_SECONDARY,
    (Provider.USER_SUPPLIED, DocumentKind.NEWS_ARTICLE): SourceTier.T5_SECONDARY,
    (Provider.USER_SUPPLIED, DocumentKind.COMMENTARY): SourceTier.T6_UNVERIFIED,
}


def tier_for(provider: Provider, kind: DocumentKind = DocumentKind.UNKNOWN) -> SourceTier:
    """The tier for a provider publishing a kind of document.

    Total: every input returns a tier, and an unrecognised pair returns the bottom one. Raising
    instead would turn a new adapter's first unusual document into a failed run, and the
    conservative answer is both safe and visible — a tier-6 source cannot be cited, so the
    mistake surfaces as "nothing could be cited" rather than as a report resting on an
    over-weighted source.
    """
    return _TABLE.get((provider, kind), SourceTier.T6_UNVERIFIED)
