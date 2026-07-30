"""When a document was published, and how much to believe it.

Look-ahead bias (threat T13) is the failure this exists to prevent, and it is a quiet one: a
report that cites a document published after its own as-of date looks completely normal. Nothing
about the prose gives it away. The only defence is knowing when each source was published, so
the date has to be **extracted and scored rather than trusted**.

**Every candidate is kept, and the winner says why it won.** A confidence of 0.5 with no
explanation is a number a reviewer cannot act on; "the filing index said 28 July, the PDF's own
metadata said 3 August, and the index won because it is the regulator's record" is. That is why
:class:`PublicationDate` carries the whole list.

## The order of trust, which is not the order the plan listed

`docs/phase-2-plan.md` lists the sources as *"HTTP headers, document metadata, filing indexes and
in-document text, in that order of trust"*. Taken literally that puts HTTP headers first, and
that is wrong on the merits, so this module does not do it:

1. **The filing index.** The regulator's own record of when a document was filed. It is the
   thing the publication date *is*, not evidence about it.
2. **Document metadata.** A PDF's creation date or an article's `published_time`, set by
   whoever produced the file. Usually right; occasionally the date a template was made.
3. **In-document text.** "28 July 2022" on a cover page. Real evidence, but a filing is full
   of other dates — period ends, comparatives, signature dates — so parsing is ambiguous.
4. **HTTP headers.** `Last-Modified` describes a *file on a server*, not a document. A CDN
   re-upload, a migration, or a nightly sync moves it years after publication. It is the last
   resort and it is scored like one.

## Choosing conservatively, because the two questions differ

The best estimate of a publication date and the answer to "can this be shown to predate the
as-of date?" are not the same question, and this module answers both separately.

:attr:`PublicationDate.chosen` is the best estimate — the highest-trust candidate — and is what
gets displayed and stored. :attr:`PublicationDate.latest` is the newest date any evidence
supports, and is what the point-in-time rule uses. If a filing index says July and the document's
own text says September, the honest position is that this document **might** be from September,
and admitting it as at 31 July would be exactly the mistake the rule exists to prevent.

Being wrong in that direction costs a quarantine an operator can override with a reason. Being
wrong the other way costs a report that used information nobody had at the time, and says
nothing about having done so.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Final

__all__ = [
    "DateCandidate",
    "DateEvidence",
    "PublicationDate",
    "extract_publication_date",
    "from_headers",
    "from_metadata",
    "from_text",
]


class DateEvidence(StrEnum):
    """Where a candidate date came from. Ordered by trust; see the module docstring."""

    FILING_INDEX = "filing_index"
    """The publisher's own index. The regulator's record of when it was filed."""

    DOCUMENT_METADATA = "document_metadata"
    """A PDF's creation date, or an HTML page's publication meta tag."""

    IN_DOCUMENT_TEXT = "in_document_text"
    """A date printed in the document itself."""

    HTTP_HEADER = "http_header"
    """``Last-Modified``. About a file on a server, not about a document."""


# What each kind of evidence is worth on its own, before disagreement is taken into account.
# These are the numbers stored on `source_documents.publication_date_confidence`, so they are
# named constants rather than literals: a reviewer comparing two sources is comparing these.
_BASE_CONFIDENCE: Final[dict[DateEvidence, float]] = {
    DateEvidence.FILING_INDEX: 0.99,
    DateEvidence.DOCUMENT_METADATA: 0.80,
    DateEvidence.IN_DOCUMENT_TEXT: 0.60,
    DateEvidence.HTTP_HEADER: 0.30,
}

_TRUST_ORDER: Final[tuple[DateEvidence, ...]] = (
    DateEvidence.FILING_INDEX,
    DateEvidence.DOCUMENT_METADATA,
    DateEvidence.IN_DOCUMENT_TEXT,
    DateEvidence.HTTP_HEADER,
)

# How far apart two candidates may be before the disagreement counts against confidence. A day
# either side is a timezone; a fortnight is two different events.
_TOLERATED_DISAGREEMENT_DAYS: Final = 1

# What a material disagreement costs. Multiplicative so that the ordering of the base
# confidences survives it — a disputed filing index still outranks an undisputed HTTP header,
# which is the right answer.
_DISAGREEMENT_PENALTY: Final = 0.6

# Nothing before this is a plausible publication date for a listed company's filing, and a
# four-digit number that happens to look like a year is common in prose. Bounds the text parser.
_EARLIEST_PLAUSIBLE_YEAR: Final = 1990

# How much text to read when looking for a printed date. A publication date appears on the cover
# or in the header; scanning a 200-page annual report end to end would find every period end in
# it and pick the wrong one.
_TEXT_WINDOW: Final = 4000

# How many text candidates to keep. The cover of a filing has a handful of dates on it and the
# rest is noise.
_TEXT_CANDIDATES: Final = 6

# Meta tag names that carry a publication date, lowercased. Ordered only for readability; all
# are treated as equally good metadata, because arguing about whether Dublin Core beats
# OpenGraph is not a distinction any real document respects.
_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "article:published_time",
        "article:modified_time",
        "citation_publication_date",
        "date",
        "dc.date",
        "dc.date.issued",
        "dcterms.date",
        "dcterms.issued",
        "creationdate",
        "moddate",
        "og:published_time",
        "og:updated_time",
        "parsely-pub-date",
        "prism.publicationdate",
        "pubdate",
        "publication_date",
        "publish-date",
        "published_time",
        "sailthru.date",
    }
)

_HEADER_KEYS: Final[tuple[str, ...]] = ("last-modified", "date")


@dataclass(frozen=True, slots=True, order=True)
class DateCandidate:
    """One date, where it came from, and the exact string it was read out of.

    ``raw`` is kept so a reviewer can see what was parsed. "2022-07-28, from the text
    ``Filed 28 July 2022``" is checkable; "2022-07-28, confidence 0.6" is not.
    """

    value: date
    evidence: DateEvidence
    raw: str

    @property
    def confidence(self) -> float:
        return _BASE_CONFIDENCE[self.evidence]


@dataclass(frozen=True, slots=True)
class PublicationDate:
    """What was concluded about a document's publication date, and on what basis."""

    chosen: DateCandidate
    candidates: tuple[DateCandidate, ...]
    confidence: float

    @property
    def value(self) -> date:
        """The best estimate. What gets displayed and stored."""
        return self.chosen.value

    @property
    def latest(self) -> date:
        """The newest date any evidence supports.

        **What the point-in-time rule uses.** The question there is not "when was this probably
        published" but "can this be shown to predate the as-of date", and a document with any
        evidence of being later cannot. See the module docstring.
        """
        return max(candidate.value for candidate in self.candidates)

    @property
    def disputed(self) -> bool:
        """Whether the candidates disagree by more than a timezone's worth."""
        values = [candidate.value for candidate in self.candidates]
        return (max(values) - min(values)).days > _TOLERATED_DISAGREEMENT_DAYS

    def explain(self) -> str:
        """One line a reviewer can read, naming every candidate and why the winner won."""
        parts = [
            f"{c.value.isoformat()} from {c.evidence.value} ({c.raw[:60]})" for c in self.candidates
        ]
        return (
            f"{self.chosen.value.isoformat()} (confidence {self.confidence:.2f}), chosen from "
            f"{self.chosen.evidence.value} as the most trustworthy of: {'; '.join(parts)}"
        )


def extract_publication_date(
    *,
    index_date: date | None = None,
    metadata: Mapping[str, str] | None = None,
    text: str | None = None,
    headers: Mapping[str, str] | None = None,
    not_after: date | None = None,
) -> PublicationDate | None:
    """Everything known about when a document was published.

    Args:
        index_date: The publisher's own filing date, where the adapter has one.
        not_after: Discard candidates later than this. For the retrieval timestamp: a document
            cannot have been published after it was fetched, so a "date" in the future is a
            misparse — a period end, a coupon date, a projection — and keeping it would
            quarantine the document for a reason that is not true.

    Returns:
        ``None`` when nothing yielded a date, which is the trigger for quarantine under
        point-in-time rules. Distinct from a low-confidence date: "undatable" and "probably
        July" need different responses from a reviewer.
    """
    candidates: list[DateCandidate] = []

    if index_date is not None:
        candidates.append(
            DateCandidate(
                value=index_date, evidence=DateEvidence.FILING_INDEX, raw=index_date.isoformat()
            )
        )
    if metadata:
        candidates.extend(from_metadata(metadata))
    if text:
        candidates.extend(from_text(text))
    if headers:
        candidates.extend(from_headers(headers))

    if not_after is not None:
        candidates = [c for c in candidates if c.value <= not_after]

    return choose(candidates)


def choose(candidates: Iterable[DateCandidate]) -> PublicationDate | None:
    """The most trustworthy candidate, with confidence adjusted for disagreement.

    Ties within one kind of evidence are broken by taking the **earliest**, which is the one a
    document is most likely to have been published on: metadata routinely carries both a
    creation and a modification date, and the modification is a later edit of the same document.
    The conservative direction for admissibility is handled separately, by
    :attr:`PublicationDate.latest`, so nothing is lost by being sensible here.
    """
    # Deduplicated on the date and the kind of evidence, not on the whole candidate. A page
    # carrying both `dcterms.date` and `dc.date` with the same value has said one thing twice,
    # and listing it twice pads the reviewer's list without adding anything to check. The first
    # `raw` is the one kept, so the explanation still names a real tag.
    seen: dict[tuple[date, DateEvidence], DateCandidate] = {}
    for candidate in candidates:
        seen.setdefault((candidate.value, candidate.evidence), candidate)
    found = tuple(seen.values())
    if not found:
        return None

    by_trust = {evidence: index for index, evidence in enumerate(_TRUST_ORDER)}
    chosen = min(found, key=lambda c: (by_trust[c.evidence], c.value))

    confidence = chosen.confidence
    values = [c.value for c in found]
    if (max(values) - min(values)).days > _TOLERATED_DISAGREEMENT_DAYS:
        confidence *= _DISAGREEMENT_PENALTY

    return PublicationDate(chosen=chosen, candidates=found, confidence=round(confidence, 3))


# -- Reading each kind of evidence ----------------------------------------------------------------


def from_metadata(metadata: Mapping[str, str]) -> list[DateCandidate]:
    """Dates from a document's own metadata: PDF document info, or HTML meta tags."""
    found: list[DateCandidate] = []
    for key, value in metadata.items():
        if key.strip().lower() not in _METADATA_KEYS:
            continue
        parsed = _parse_any(value)
        if parsed is not None:
            found.append(
                DateCandidate(
                    value=parsed, evidence=DateEvidence.DOCUMENT_METADATA, raw=f"{key}={value}"
                )
            )
    return found


def from_headers(headers: Mapping[str, str]) -> list[DateCandidate]:
    """Dates from HTTP response headers. The weakest evidence; see the module docstring."""
    lowered = {key.strip().lower(): value for key, value in headers.items()}
    found: list[DateCandidate] = []
    for key in _HEADER_KEYS:
        value = lowered.get(key)
        if value is None:
            continue
        parsed = _parse_any(value)
        if parsed is not None:
            found.append(
                DateCandidate(
                    value=parsed, evidence=DateEvidence.HTTP_HEADER, raw=f"{key}: {value}"
                )
            )
    return found


def from_text(text: str) -> list[DateCandidate]:
    """Dates printed in the document, from the first :data:`_TEXT_WINDOW` characters.

    Bounded on purpose. A publication date is on the cover or in the running header; reading a
    whole annual report would collect every period end, comparative and signature date in it and
    then have to choose between them, which is a harder problem answered worse.
    """
    window = text[:_TEXT_WINDOW]
    found: list[DateCandidate] = []

    for pattern, build in _TEXT_PATTERNS:
        for match in pattern.finditer(window):
            parsed = build(match)
            if parsed is not None:
                found.append(
                    DateCandidate(
                        value=parsed, evidence=DateEvidence.IN_DOCUMENT_TEXT, raw=match.group(0)
                    )
                )
            if len(found) >= _TEXT_CANDIDATES:
                return found
    return found


# -- Parsing -------------------------------------------------------------------------------------

_MONTHS: Final[dict[str, int]] = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_MONTH_NAMES: Final = "|".join(sorted(_MONTHS, key=len, reverse=True))


_Builder = Callable[[re.Match[str]], date | None]


def _iso(match: re.Match[str]) -> date | None:
    return _build(int(match["year"]), int(match["month"]), int(match["day"]))


def _day_month_year(match: re.Match[str]) -> date | None:
    month = _MONTHS.get(match["month"].lower())
    return None if month is None else _build(int(match["year"]), month, int(match["day"]))


def _month_day_year(match: re.Match[str]) -> date | None:
    month = _MONTHS.get(match["month"].lower())
    return None if month is None else _build(int(match["year"]), month, int(match["day"]))


# **No all-numeric `dd/mm/yyyy` pattern, deliberately.** `03/04/2022` is 3 April to a UK filing
# and 4 March to a US one, and this platform reads both. A date that could be either is not
# evidence, and guessing at it would put a silent one-month error into a look-ahead check.
_TEXT_PATTERNS: Final[tuple[tuple[re.Pattern[str], _Builder], ...]] = (
    (re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"), _iso),
    (
        re.compile(
            rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_NAMES})\.?,?\s+"
            rf"(?P<year>\d{{4}})\b",
            re.IGNORECASE,
        ),
        _day_month_year,
    ),
    (
        re.compile(
            rf"\b(?P<month>{_MONTH_NAMES})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+"
            rf"(?P<year>\d{{4}})\b",
            re.IGNORECASE,
        ),
        _month_day_year,
    ),
)


def _build(year: int, month: int, day: int) -> date | None:
    """A date, or ``None`` if the numbers do not make one.

    Out-of-range values are common in prose — a document number that looks like a date, a
    reference like `2022-13-01` — and a parser that raised on them would turn an unremarkable
    filing into a failed extraction.
    """
    if year < _EARLIEST_PLAUSIBLE_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_any(value: str) -> date | None:
    """Parse one string in whichever of the plausible formats it happens to be in.

    Three shapes appear in the wild and all three turn up in filings: ISO 8601 (metadata),
    RFC 2822 (HTTP headers) and PDF's own ``D:YYYYMMDD`` (document info dictionaries).
    """
    text = value.strip()
    if not text:
        return None

    if text.upper().startswith("D:"):
        return _parse_pdf_date(text)

    iso = _parse_iso(text)
    if iso is not None:
        return iso

    return _parse_rfc2822(text)


def _parse_iso(text: str) -> date | None:
    candidate = text.rstrip("Zz")
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(candidate[:10])
    except ValueError:
        return None


def _parse_rfc2822(text: str) -> date | None:
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        # HTTP dates are GMT by specification, and a naive one is an ill-formed header rather
        # than a local time. Reading it as UTC is the only interpretation that is ever right.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def _parse_pdf_date(text: str) -> date | None:
    """``D:20220728160500+01'00'`` — PDF's own format, from a document info dictionary."""
    digits = text[2:]
    if len(digits) < _PDF_DATE_DIGITS or not digits[:_PDF_DATE_DIGITS].isdigit():
        return None
    return _build(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))


_PDF_DATE_DIGITS: Final = 8
