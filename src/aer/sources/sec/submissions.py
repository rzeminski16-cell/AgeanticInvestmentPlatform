"""The filing index: what an entity has filed, and when.

``data.sec.gov/submissions/CIK##########.json`` returns the entity's filing history. It is
what turns "the latest annual report" into a specific accession number and a specific URL,
and its ``filingDate`` is the anchor every point-in-time decision about a *document* rests
on.

**The columnar shape is the hazard in this module.** ``filings.recent`` is not a list of
filings. It is a set of parallel arrays::

    {
        "accessionNumber": ["0000789019-24-000078", ...],
        "filingDate": ["2024-07-30", ...],
        "form": ["10-K", ...],
    }

Row *i* of a filing is element *i* of every array. If one array is shorter than another —
a truncated response, a change at the SEC's end — then zipping them silently shifts every
subsequent row, and a filing gets attributed to the wrong date. Nothing about the result
would look wrong. So the lengths are checked before anything is zipped, and a mismatch
raises rather than producing a plausible index.

**Older filings live elsewhere.** ``recent`` holds roughly the last thousand filings; the
rest are in additional files listed under ``filings.files``. Those references are parsed
and exposed so a caller that needs deeper history knows it exists, but fetching them is
the client's decision, not the parser's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from aer.core.schemas.facts import format_accession
from aer.errors import ExternalServiceError
from aer.sources.base import DocumentRef
from aer.sources.sec.tickers import format_cik, normalise_exchange

__all__ = [
    "ANNUAL_FORMS",
    "PERIODIC_FORMS",
    "QUARTERLY_FORMS",
    "ArchiveFile",
    "Filing",
    "SubmissionsIndex",
    "parse_submissions",
]

ANNUAL_FORMS: Final[frozenset[str]] = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F"})
QUARTERLY_FORMS: Final[frozenset[str]] = frozenset({"10-Q", "10-Q/A"})
PERIODIC_FORMS: Final[frozenset[str]] = ANNUAL_FORMS | QUARTERLY_FORMS

_ARCHIVE_BASE: Final = "https://www.sec.gov/Archives/edgar/data"

# The columns a row is built from. Only the first three are required: a filing with no
# accession, form or date is not a filing anyone can do anything with, whereas a missing
# document name only costs a link.
_REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("accessionNumber", "filingDate", "form")
_OPTIONAL_COLUMNS: Final[tuple[str, ...]] = (
    "reportDate",
    "primaryDocument",
    "primaryDocDescription",
    "isXBRL",
    "items",
)


@dataclass(frozen=True, slots=True)
class Filing:
    """One filing from the index."""

    accession: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    description: str
    is_xbrl: bool

    def url(self, cik: str) -> str:
        """The URL of this filing's primary document.

        The archive path uses the CIK with leading zeros stripped and the accession with
        its dashes removed — a different form from the one every other endpoint uses,
        which is why it is built here rather than by string-formatting at a call site.
        """
        bare_cik = str(int(cik))
        folder = self.accession.replace("-", "")
        return f"{_ARCHIVE_BASE}/{bare_cik}/{folder}/{self.primary_document}"

    def to_ref(self, cik: str, *, entity_name: str = "") -> DocumentRef:
        """As a :class:`~aer.sources.base.DocumentRef` for the acquisition layer."""
        label = self.description or self.form
        title = f"{entity_name} {label} {self.filing_date:%Y-%m-%d}".strip()
        return DocumentRef(
            url=self.url(cik),
            title=title,
            # The date the filing was *accepted*, not the period it covers. That is the
            # date the information became public, which is the only one a point-in-time
            # rule can honestly use.
            publication_date=self.filing_date,
            form=self.form,
            accession=self.accession,
        )


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """A reference to an older slice of the filing history, not yet fetched."""

    name: str
    first_filing: date | None
    last_filing: date | None
    count: int

    @property
    def url(self) -> str:
        return f"https://data.sec.gov/submissions/{self.name}"


@dataclass(frozen=True, slots=True)
class SubmissionsIndex:
    """An entity's identity and its filing history."""

    cik: str
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str | None, ...]
    filings: tuple[Filing, ...]
    older_files: tuple[ArchiveFile, ...] = ()
    fiscal_year_end: str | None = None
    sic: str | None = None
    sic_description: str | None = None

    def filed_on_or_before(self, as_of_date: date) -> tuple[Filing, ...]:
        """Filings public as at a date.

        The point-in-time gate for documents, applied at acquisition. A filing accepted
        after the as-of date did not exist as far as the research is concerned, and this
        is where it stops being a candidate rather than somewhere downstream that might
        forget to ask.
        """
        return tuple(f for f in self.filings if f.filing_date <= as_of_date)

    def of_form(self, forms: frozenset[str]) -> tuple[Filing, ...]:
        """Filings of particular form types, e.g. :data:`ANNUAL_FORMS`."""
        return tuple(f for f in self.filings if f.form in forms)

    def latest(self, forms: frozenset[str], *, as_of_date: date | None = None) -> Filing | None:
        """The most recent filing of a given form type, respecting an as-of date."""
        candidates = self.filings if as_of_date is None else self.filed_on_or_before(as_of_date)
        matching = [f for f in candidates if f.form in forms]
        if not matching:
            return None
        return max(matching, key=lambda f: (f.filing_date, f.accession))


def parse_submissions(payload: bytes) -> SubmissionsIndex:
    """Parse a submissions response into an index, newest filing first.

    Raises:
        ExternalServiceError: If the payload is not JSON, is missing the fields that make
            it a submissions document, or has parallel arrays of differing lengths.
    """
    document = _load(payload)

    cik_value = document.get("cik")
    if cik_value is None:
        message = (
            "The submissions response has no cik field. That is an error page or a "
            "different endpoint, not a filing index."
        )
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=False, context={"keys": sorted(document)[:10]}
        )

    recent = document.get("filings", {}).get("recent", {})
    filings = _parse_recent(recent) if recent else ()

    return SubmissionsIndex(
        cik=format_cik(cik_value),
        name=str(document.get("name", "")).strip(),
        tickers=tuple(str(t).strip().upper() for t in document.get("tickers", []) if t),
        exchanges=tuple(normalise_exchange(str(e)) for e in document.get("exchanges", []) if e),
        filings=tuple(sorted(filings, key=lambda f: (f.filing_date, f.accession), reverse=True)),
        older_files=_parse_older(document.get("filings", {}).get("files", [])),
        fiscal_year_end=_optional_str(document.get("fiscalYearEnd")),
        sic=_optional_str(document.get("sic")),
        sic_description=_optional_str(document.get("sicDescription")),
    )


def _load(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = f"The SEC submissions response is not valid JSON ({exc})."
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=True, context={"bytes": len(payload)}
        ) from exc

    if not isinstance(document, dict):
        message = "The SEC submissions response is not a JSON object."
        raise ExternalServiceError(
            message,
            provider="sec_edgar",
            retryable=False,
            context={"type": type(document).__name__},
        )
    return document


def _parse_recent(recent: dict[str, Any]) -> tuple[Filing, ...]:
    columns = _validated_columns(recent)
    rows = len(columns["accessionNumber"])

    filings: list[Filing] = []
    for index in range(rows):
        filing_date = _parse_date(columns["filingDate"][index])
        accession_raw = str(columns["accessionNumber"][index])
        if filing_date is None or not accession_raw:
            # Both are load-bearing: without a date the filing cannot be point-in-time
            # filtered, and without an accession it cannot be cited. A row missing either
            # is dropped rather than given a substitute.
            continue
        try:
            accession = format_accession(accession_raw)
        except ValueError:
            continue

        filings.append(
            Filing(
                accession=accession,
                form=str(columns["form"][index]).strip(),
                filing_date=filing_date,
                report_date=_parse_date(_at(columns, "reportDate", index)),
                primary_document=str(_at(columns, "primaryDocument", index) or "").strip(),
                description=str(_at(columns, "primaryDocDescription", index) or "").strip(),
                is_xbrl=bool(_at(columns, "isXBRL", index)),
            )
        )
    return tuple(filings)


def _validated_columns(recent: dict[str, Any]) -> dict[str, list[Any]]:
    """Extract the parallel arrays, having confirmed they are parallel.

    This is the check the module exists to make. Arrays of differing lengths zipped
    together produce a filing index in which every row after the short array is attributed
    to the wrong date — a wrong answer that looks exactly like a right one.
    """
    missing = [name for name in _REQUIRED_COLUMNS if not isinstance(recent.get(name), list)]
    if missing:
        message = (
            f"The submissions index is missing the {', '.join(missing)} column(s). Every "
            "filing needs an accession, a form and a date to be usable."
        )
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=False, context={"missing": missing}
        )

    columns = {name: list(recent[name]) for name in _REQUIRED_COLUMNS}
    for name in _OPTIONAL_COLUMNS:
        value = recent.get(name)
        if isinstance(value, list):
            columns[name] = list(value)

    lengths = {name: len(values) for name, values in columns.items()}
    if len(set(lengths.values())) > 1:
        message = (
            "The submissions index has parallel arrays of differing lengths "
            f"({lengths}). Zipping them would attribute filings to the wrong dates, so "
            "the response is refused rather than parsed into a plausible index."
        )
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=True, context={"lengths": lengths}
        )
    return columns


def _parse_older(files: Any) -> tuple[ArchiveFile, ...]:
    if not isinstance(files, list):
        return ()
    parsed: list[ArchiveFile] = []
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        parsed.append(
            ArchiveFile(
                name=str(entry["name"]).strip(),
                first_filing=_parse_date(entry.get("filingFrom")),
                last_filing=_parse_date(entry.get("filingTo")),
                count=int(entry.get("filingCount") or 0),
            )
        )
    return tuple(parsed)


def _at(columns: dict[str, list[Any]], name: str, index: int) -> Any:
    values = columns.get(name)
    return values[index] if values is not None else None


def _parse_date(value: Any) -> date | None:
    """Parse an EDGAR ``YYYY-MM-DD`` date, treating anything else as absent.

    Returns ``None`` rather than raising. EDGAR uses an empty string for "no report date",
    which is common and entirely legitimate on a form that covers no period.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
