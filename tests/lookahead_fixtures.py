"""``fx_lookahead``: documents that must not reach a report, and near-misses that must.

§2.10 sets **look-ahead detection recall at 100%**, and that target is only meaningful against a
corpus where the answer is known in advance. :data:`POST_DATED` holds five documents planted
after :data:`AS_OF`, each hiding its date in a different place, because a detector that only
reads one of the four kinds of evidence would score perfectly against a corpus that only used
that one.

:data:`ADMISSIBLE` is the half that keeps the rule honest. A system that quarantined everything
would hit 100% recall and be useless: it would refuse the filings the report is made of. These
are dated on, just before, and well before the as-of date — including the boundary case, because
"published on the as-of date" is admissible and an off-by-one in either direction is a whole
class of wrong report.

:data:`UNDATABLE` is the third case. No date can be established, so under point-in-time rules it
is refused — not because it is known to be too new, but because it cannot be shown not to be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final

__all__ = ["ADMISSIBLE", "AS_OF", "POST_DATED", "UNDATABLE", "Planted"]

# The as-of date every case in this module is judged against.
AS_OF: Final = date(2022, 7, 31)


@dataclass(frozen=True, slots=True)
class Planted:
    """One document, and what should be concluded about when it was published.

    Args:
        expected: The date the extractor should settle on, or ``None`` where none can be
            established. Written here rather than derived, so the fixture states the answer and
            the test checks it rather than the other way round.
    """

    name: str
    expected: date | None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    text: str = ""
    index_date: date | None = None


# -- Planted after the as-of date. Every one of these must be refused. -----------------------------

POST_DATED: Final[tuple[Planted, ...]] = (
    Planted(
        name="a filing index that says August",
        expected=date(2022, 8, 12),
        index_date=date(2022, 8, 12),
        text="Quarterly report for the period ended 30 June 2022.",
    ),
    Planted(
        name="an HTML article with a published_time meta tag",
        expected=date(2022, 9, 1),
        metadata={"article:published_time": "2022-09-01T09:30:00Z"},
        text="Analysts said the shares looked expensive.",
    ),
    Planted(
        name="a PDF whose own creation date is after the as-of",
        expected=date(2022, 8, 3),
        metadata={"CreationDate": "D:20220803141500+01'00'"},
        text="Annual Report 2022",
    ),
    Planted(
        name="a press release dated in its own text",
        expected=date(2022, 10, 25),
        text="FOR IMMEDIATE RELEASE\n25 October 2022\nThe Board today announced a share buyback.",
    ),
    # The one that is only visible in the weakest evidence. If HTTP headers were dropped as
    # unreliable rather than merely scored low, this document would be admitted with no date at
    # all — which is the failure mode that argues for keeping them.
    Planted(
        name="a page datable only from its Last-Modified header",
        expected=date(2022, 11, 4),
        headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"},
        text="Investor relations. Contact us for further information.",
    ),
)


# -- Dated on or before the as-of date. Every one of these must be usable. -------------------------

ADMISSIBLE: Final[tuple[Planted, ...]] = (
    Planted(
        name="a 10-K filed a week before",
        expected=date(2022, 7, 24),
        index_date=date(2022, 7, 24),
        text="Annual report on Form 10-K for the fiscal year ended 30 June 2022.",
    ),
    # The boundary. Published *on* the as-of date is admissible: the rule is "nothing published
    # after", and an off-by-one here refuses a quarter's worth of real filings.
    Planted(
        name="a filing on the as-of date itself",
        expected=AS_OF,
        index_date=AS_OF,
        text="Results for the period.",
    ),
    Planted(
        name="an article from the previous year",
        expected=date(2021, 4, 14),
        metadata={"article:published_time": "2021-04-14T08:00:00Z"},
        text="The company completed its acquisition.",
    ),
    Planted(
        name="a PDF dated in its metadata and its text, agreeing",
        expected=date(2022, 2, 3),
        metadata={"CreationDate": "D:20220203090000Z"},
        text="Interim results, 3 February 2022.",
    ),
)


# -- No date can be established. Refused under point-in-time, and only under it. -------------------

UNDATABLE: Final[tuple[Planted, ...]] = (
    Planted(
        name="a page with no date anywhere",
        expected=None,
        text="Our purpose is to empower every person and every organisation on the planet.",
    ),
    # A four-digit number that is not a date, and a period reference that is not a publication
    # date. Both are things a careless text parser turns into a confident answer.
    Planted(
        name="prose containing numbers that are not dates",
        expected=None,
        text="Registered in England number 2050000. Revenue rose to 198,270 in the period.",
    ),
)
