"""Companies House: the UK register, and the accounts filed with it.

The UK counterpart to SEC EDGAR, and the same discipline applies. **URLs are built here from
identifiers Companies House issued** — a company number, a transaction ID — and no method takes
a URL. That is what carries the "no agent-callable tool takes a URL" property into this adapter:
a filing whose text says *"fetch https://attacker.test/"* produces no method call that could act
on it, because no such method exists.

**Authentication is HTTP Basic with the key as the username and an empty password**, which is
the scheme Companies House documents. The credential is handed to
:class:`~aer.fetch.client.SafeFetcher` once at construction and attached per provider, so it
never travels through this module's call sites and never reaches the policy table.

**A company number is not a ticker.** Companies House knows nothing about listings: it registers
companies. Resolving `BP` to `00102498` means searching by name and then *confirming* the match,
which is why :meth:`CompaniesHouseClient.resolve_entity` refuses an ambiguous search rather than
taking the first hit. Picking the wrong company here would put another business's accounts under
this company's name, and nothing downstream would notice — every figure would be internally
consistent and about the wrong firm.

**Only accounts are worth acquiring.** The filing history is mostly officer appointments,
registered-office changes and confirmation statements. `ACCOUNTS_CATEGORIES` is what a research
run wants; everything else is noise that costs a fetch and a hash.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from urllib.parse import quote, urlencode

import structlog

from aer.core.enums import Provider, SourceTier
from aer.errors import ValidationError
from aer.fetch.client import FetchResult, SafeFetcher
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.storage.protocol import ArtefactStore

__all__ = [
    "ACCOUNTS_CATEGORIES",
    "CompaniesHouseClient",
    "CompanyProfile",
    "FilingHistory",
    "FilingRecord",
    "basic_auth_header",
    "parse_company_profile",
    "parse_filing_history",
    "parse_search_results",
]

_log = structlog.get_logger("aer.sources.uk.companies_house")

API_ROOT: Final = "https://api.company-information.service.gov.uk"

# Documents live behind a different host from the rest of the API. Both are covered by the
# `.company-information.service.gov.uk` allowlist entry.
DOCUMENT_ROOT: Final = "https://document-api.company-information.service.gov.uk"

# Filing categories worth fetching. The rest of a filing history is officer appointments,
# registered-office changes and confirmation statements — real records, and not ones a research
# report cites.
ACCOUNTS_CATEGORIES: Final[frozenset[str]] = frozenset({"accounts"})

# A company number is eight characters: digits, or two letters and six digits for the Scottish
# and Northern Irish registers. Validated because it goes into a URL path.
_NUMBER_LENGTH: Final = 8

_SEARCH_LIMIT: Final = 20
_HISTORY_LIMIT: Final = 100


def basic_auth_header(api_key: str) -> str:
    """The ``Authorization`` value for a Companies House key.

    HTTP Basic with the key as the username and an empty password, which is what the API
    documents. Built here rather than at the call site so there is one place that knows the
    scheme, and so the credential is turned into a header exactly once.
    """
    import base64  # noqa: PLC0415 -- used only here, and only when a key is configured

    encoded = base64.b64encode(f"{api_key}:".encode()).decode("ascii")
    return f"Basic {encoded}"


def normalise_company_number(value: str) -> str:
    """A company number in the form the API expects.

    Zero-padded to eight characters, uppercased. `102498` and `00102498` are the same company,
    and the register only answers to the padded form — a detail that is a 404 the first time it
    is missed.

    Raises:
        ValidationError: The value is not a company number at all. It goes into a URL path, so
            it is checked rather than trusted.
    """
    cleaned = value.strip().upper().replace(" ", "")
    if not cleaned:
        message = "A company number is required."
        raise ValidationError(message, context={"value": value})

    if cleaned.isdigit():
        cleaned = cleaned.zfill(_NUMBER_LENGTH)

    if len(cleaned) != _NUMBER_LENGTH or not cleaned.isalnum():
        message = (
            f"{value!r} is not a Companies House company number. Expected eight characters: "
            "digits, or a two-letter prefix and six digits for the Scottish and Northern "
            "Irish registers."
        )
        raise ValidationError(message, context={"value": value})
    return cleaned


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """What the register holds about a company."""

    company_number: str
    name: str
    status: str | None = None
    incorporated_on: date | None = None
    accounts_reference_date: str | None = None

    @property
    def is_active(self) -> bool:
        return (self.status or "").lower() == "active"


@dataclass(frozen=True, slots=True)
class FilingRecord:
    """One entry in a company's filing history."""

    transaction_id: str
    category: str
    description: str
    filed_on: date
    document_id: str | None = None
    made_up_to: date | None = None

    @property
    def is_accounts(self) -> bool:
        return self.category.strip().lower() in ACCOUNTS_CATEGORIES

    @property
    def is_fetchable(self) -> bool:
        """Whether a document can actually be retrieved for this filing.

        Older entries are index records with no document behind them. Saying so beats
        constructing a URL that 404s and recording the failure as provenance.
        """
        return self.document_id is not None

    def to_ref(self, *, company_name: str) -> DocumentRef:
        if self.document_id is None:
            message = "This filing has no document to reference."
            raise ValidationError(message, context={"transaction_id": self.transaction_id})
        return DocumentRef(
            url=document_url(self.document_id),
            title=f"{self.description} — {company_name}",
            publication_date=self.filed_on,
            form=self.category,
            accession=self.transaction_id,
        )


@dataclass(frozen=True, slots=True)
class FilingHistory:
    """A company's filings, newest first."""

    company_number: str
    filings: tuple[FilingRecord, ...] = ()
    total: int = 0

    def accounts(self) -> tuple[FilingRecord, ...]:
        return tuple(f for f in self.filings if f.is_accounts and f.is_fetchable)

    def filed_on_or_before(self, as_of_date: date) -> tuple[FilingRecord, ...]:
        """Filings this platform may look at, as at a date.

        Filtered here rather than downstream, so a filing accepted after the as-of date is
        never turned into a reference and no later code path can fetch it by forgetting.
        """
        return tuple(f for f in self.filings if f.filed_on <= as_of_date)


def document_url(document_id: str) -> str:
    """The content URL for a filed document, built from its identifier."""
    return f"{DOCUMENT_ROOT}/document/{quote(document_id, safe='')}/content"


# -- Parsing ---------------------------------------------------------------------------------


def parse_company_profile(payload: bytes) -> CompanyProfile:
    """Parse a company profile response.

    Raises:
        ValidationError: The payload is not a profile. A 404 body, a rate-limit page or a
            changed API should not read as a company with no name.
    """
    document = _object(payload, what="company profile")

    number = str(document.get("company_number") or "").strip()
    name = str(document.get("company_name") or "").strip()
    if not number or not name:
        message = (
            "The company profile response has no company number or name. That is what an "
            "error body looks like, and it is not a company."
        )
        raise ValidationError(message, context={"keys": sorted(document)[:10]})

    accounts = document.get("accounts")
    reference = None
    if isinstance(accounts, dict):
        made_up = accounts.get("accounting_reference_date")
        if isinstance(made_up, dict):
            day, month = made_up.get("day"), made_up.get("month")
            reference = f"{day}/{month}" if day and month else None

    return CompanyProfile(
        company_number=number,
        name=name,
        status=str(document.get("company_status") or "").strip() or None,
        incorporated_on=_parse_date(document.get("date_of_creation")),
        accounts_reference_date=reference,
    )


def parse_filing_history(payload: bytes, *, company_number: str) -> FilingHistory:
    """Parse a filing-history response.

    Entries that cannot be turned into a record are skipped rather than raised on: a history
    runs to hundreds of items and one malformed row is not a reason to lose the rest.
    """
    document = _object(payload, what="filing history")
    items = document.get("items")
    if not isinstance(items, list):
        message = "The filing history response has no 'items' list."
        raise ValidationError(message, context={"keys": sorted(document)[:10]})

    records = [parsed for raw in items if (parsed := _one_filing(raw)) is not None]
    return FilingHistory(
        company_number=company_number,
        filings=tuple(records),
        total=int(document.get("total_count") or len(records)),
    )


def parse_search_results(payload: bytes) -> tuple[CompanyProfile, ...]:
    """Parse a company-search response into candidate companies."""
    document = _object(payload, what="company search")
    items = document.get("items")
    if not isinstance(items, list):
        return ()

    found: list[CompanyProfile] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        number = str(raw.get("company_number") or "").strip()
        title = str(raw.get("title") or raw.get("company_name") or "").strip()
        if not number or not title:
            continue
        found.append(
            CompanyProfile(
                company_number=number,
                name=title,
                status=str(raw.get("company_status") or "").strip() or None,
                incorporated_on=_parse_date(raw.get("date_of_creation")),
            )
        )
    return tuple(found)


def _one_filing(raw: Any) -> FilingRecord | None:
    if not isinstance(raw, dict):
        return None

    transaction = str(raw.get("transaction_id") or "").strip()
    category = str(raw.get("category") or "").strip()
    filed_on = _parse_date(raw.get("date"))
    if not transaction or not category or filed_on is None:
        return None

    # The document identifier is buried in a link, and it is the *only* thing that identifies
    # a retrievable document. Taken apart rather than used as a URL: see the module docstring.
    document_id = None
    links = raw.get("links")
    if isinstance(links, dict):
        metadata = links.get("document_metadata")
        if isinstance(metadata, str) and metadata.strip():
            document_id = metadata.rstrip("/").rsplit("/", 1)[-1].strip() or None

    return FilingRecord(
        transaction_id=transaction,
        category=category,
        description=str(raw.get("description") or "").strip() or category,
        filed_on=filed_on,
        document_id=document_id,
        made_up_to=_parse_date(raw.get("action_date") or raw.get("made_up_date")),
    )


def _object(payload: bytes, *, what: str) -> dict[str, Any]:
    try:
        document: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        message = f"The {what} response is not JSON."
        raise ValidationError(message, context={"bytes": len(payload)}) from exc
    if not isinstance(document, dict):
        message = f"The {what} response is not an object."
        raise ValidationError(message, context={"type": type(document).__name__})
    return document


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# -- The client ------------------------------------------------------------------------------


class CompaniesHouseClient:
    """Fetches and parses the Companies House endpoints this platform uses.

    Args:
        fetcher: The single door to the network, already carrying the API credential for
            :attr:`~aer.core.enums.Provider.COMPANIES_HOUSE`.
    """

    provider: Final = Provider.COMPANIES_HOUSE
    source_tier: Final = SourceTier.T1_REGULATORY

    def __init__(self, fetcher: SafeFetcher, *, store: ArtefactStore) -> None:
        self._fetcher = fetcher
        self._store = store

    async def fetch_profile(self, company_number: str) -> CompanyProfile:
        number = normalise_company_number(company_number)
        result = await self._get(f"{API_ROOT}/company/{number}")
        return parse_company_profile(await self._body(result))

    async def fetch_filing_history(
        self, company_number: str, *, categories: Iterable[str] = ACCOUNTS_CATEGORIES
    ) -> FilingHistory:
        """A company's filing history, narrowed to the categories worth acquiring.

        Narrowed in the query rather than afterwards: a long-lived company's history runs to
        hundreds of entries, and asking the register to filter costs one request instead of
        several pages of officer appointments.
        """
        number = normalise_company_number(company_number)
        params: dict[str, str] = {"items_per_page": str(_HISTORY_LIMIT)}
        wanted = [c.strip() for c in categories if c.strip()]
        if wanted:
            params["category"] = ",".join(sorted(wanted))

        result = await self._get(f"{API_ROOT}/company/{number}/filing-history?{urlencode(params)}")
        return parse_filing_history(await self._body(result), company_number=number)

    async def search_companies(self, query: str) -> tuple[CompanyProfile, ...]:
        cleaned = query.strip()
        if not cleaned:
            message = "A company search needs a query."
            raise ValidationError(message, context={"query": query})

        params = urlencode({"q": cleaned, "items_per_page": str(_SEARCH_LIMIT)})
        result = await self._get(f"{API_ROOT}/search/companies?{params}")
        return parse_search_results(await self._body(result))

    async def fetch_document(self, ref: DocumentRef) -> FetchResult:
        """Fetch a filed document referenced by a history this client produced.

        The URL comes from a :class:`~aer.sources.base.DocumentRef`, only ever built from a
        document identifier the register issued. It is still validated against the allowlist by
        the fetch layer, because a chain of trusted construction is only as strong as its
        weakest link and this one crosses a module boundary.
        """
        return await self._fetcher.fetch(ref.url, provider=self.provider)

    # -- Adapter surface -------------------------------------------------------------------

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        """Find a company number for a name.

        **Refuses an ambiguous match rather than taking the first hit.** Companies House
        registers companies and knows nothing about listings, so a search for a ticker or a
        short name routinely returns a dozen businesses with similar names — dormant
        subsidiaries, pension trustees, a holding company and its operating arm. Picking one by
        rank would put another business's accounts under this company's name, and every figure
        downstream would be internally consistent and about the wrong firm.

        Raises:
            ValidationError: Nothing matched, or more than one active company did.
        """
        candidates = await self.search_companies(ticker)
        active = [c for c in candidates if c.is_active]

        if not active:
            message = (
                f"No active company on the Companies House register matches {ticker!r}. "
                "The register lists companies rather than securities, so a ticker is often "
                "not the registered name — try the full company name."
            )
            raise ValidationError(message, context={"query": ticker, "candidates": len(candidates)})

        if len(active) > 1:
            names = [f"{c.name} ({c.company_number})" for c in active[:5]]
            message = (
                f"{ticker!r} matches {len(active)} active companies on the register, and "
                "choosing between them by search rank would risk attributing another "
                f"business's accounts to this one. Candidates: {'; '.join(names)}. "
                "Supply the company number instead."
            )
            raise ValidationError(message, context={"query": ticker, "matches": names})

        found = active[0]
        _log.info(
            "companies_house.entity_resolved",
            query=ticker,
            company_number=found.company_number,
            name=found.name,
        )
        return ResolvedEntity(
            identifier=found.company_number,
            name=found.name,
            ticker=ticker.strip().upper() or None,
            exchange=exchange,
        )

    async def discover_documents(
        self,
        entity: ResolvedEntity,
        *,
        as_of_date: date | None = None,
        forms: frozenset[str] | None = None,
    ) -> tuple[DocumentRef, ...]:
        """A company's accounts, newest first, filtered at acquisition."""
        categories = forms if forms is not None else ACCOUNTS_CATEGORIES
        history = await self.fetch_filing_history(entity.identifier, categories=categories)

        filings = history.filed_on_or_before(as_of_date) if as_of_date else history.filings
        wanted = [f for f in filings if f.is_fetchable and f.category in categories]

        return tuple(filing.to_ref(company_name=entity.name) for filing in wanted)

    # -- Internals -------------------------------------------------------------------------

    async def _get(self, url: str) -> FetchResult:
        return await self._fetcher.fetch(url, provider=self.provider)

    async def _body(self, result: FetchResult) -> bytes:
        """Read the archived bytes back by hash.

        Read from the store rather than held from the response, so what is parsed is provably
        what was archived — the same rule the SEC client follows.
        """
        return await self._store.read(result.sha256)
