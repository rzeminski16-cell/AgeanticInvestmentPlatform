"""``fx_dates``: documents whose publication date is known in advance, hidden in different places.

The extractor is scored against a corpus where the answer is written down before it runs.
:data:`POST_DATED` holds documents dated after :data:`AS_OF`, each hiding its date in a
different place, because an extractor that only reads one of the four kinds of evidence would
score perfectly against a corpus that only used that one. :data:`ADMISSIBLE` holds documents
dated on, just before, and well before it — including the boundary, which the extractor is
still tested on although nothing turns on it any more.

**No case is refused for its date.** A rule refusing a document published after the run's
date was retired with the date it compared against (ADR 0113): the run's date is the day it
was commissioned, so a document dated after it is a mis-dated document, recorded as such.
What admissibility still reads is :data:`UNDATABLE` — no date can be established — and only
where the run refuses undated sources (ADR 0111); by default such a document is admitted,
capped at tier 5 and never primary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final

__all__ = ["ADMISSIBLE", "AS_OF", "POST_DATED", "UNDATABLE", "Planted"]

# The date every case in this module is written against.
AS_OF: Final = date(2022, 7, 31)


@dataclass(frozen=True, slots=True)
class Planted:
    """One document, and what should be concluded about when it was published.

    Args:
        expected: The date the extractor should settle on, or ``None`` where none can be
            established. Written here rather than derived, so the fixture states the answer and
            the test checks it rather than the other way round.
        latest: The newest date any evidence here supports, where that differs from
            ``expected``. Recorded beside the estimate, so a reader can see the evidence
            disagrees — a document whose index says July and whose own text says September
            is dated less firmly than "July" suggests.
    """

    name: str
    expected: date | None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    text: str = ""
    index_date: date | None = None
    latest: date | None = None

    @property
    def conservative(self) -> date | None:
        """The newest date any evidence supports."""
        return self.latest if self.latest is not None else self.expected


# -- Dated after AS_OF, each in a different place. Every one must be dated correctly. --------------

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
    # **The disagreement case.** The filing index says 28 July, which is the better estimate
    # of when this was published. The document's own text says September. Both are kept on
    # the record — the estimate and the bound — because a reader shown only "28 July" would
    # not know the document's own words disagree.
    Planted(
        name="an index date before the as-of, with a September date in the document",
        expected=date(2022, 7, 28),
        latest=date(2022, 9, 15),
        index_date=date(2022, 7, 28),
        text="Interim statement issued 15 September 2022 covering the period to 30 June 2022.",
    ),
    # The one that is only visible in the weakest evidence. If HTTP headers were dropped as
    # unreliable rather than merely scored low, this document would be recorded with no date at
    # all — which is the failure mode that argues for keeping them.
    Planted(
        name="a page datable only from its Last-Modified header",
        expected=date(2022, 11, 4),
        headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"},
        text="Investor relations. Contact us for further information.",
    ),
)


# -- Dated on or before AS_OF. -------------------------------------------------------------------

ADMISSIBLE: Final[tuple[Planted, ...]] = (
    Planted(
        name="a 10-K filed a week before",
        expected=date(2022, 7, 24),
        index_date=date(2022, 7, 24),
        text="Annual report on Form 10-K for the fiscal year ended 30 June 2022.",
    ),
    # The boundary the retired rule once turned on. Kept because the extractor is still
    # expected to date it exactly, and an off-by-one in a date parser is a class of wrong
    # record whatever reads it.
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


# -- No date can be established. Refused only where the run refuses undated sources. --------------

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
