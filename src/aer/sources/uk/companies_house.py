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
from aer.core.schemas.facts import RawFact
from aer.errors import AerError, ValidationError
from aer.extract.ixbrl import extract_ixbrl
from aer.fetch.client import FetchResult, SafeFetcher
from aer.sources.base import DocumentRef, ResolvedEntity, raw_fact_from_ixbrl
from aer.storage.protocol import ArtefactStore

__all__ = [
    "ACCOUNTS_CATEGORIES",
    "FACT_DEPTH",
    "IXBRL_MEDIA_TYPE",
    "NOT_TAGGED_STATUS",
    "CompaniesHouseClient",
    "CompanyProfile",
    "FilingHistory",
    "FilingRecord",
    "basic_auth_header",
    "looks_like_company_number",
    "normalise_company_number",
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

# What a tagged filing is, and what to ask the document endpoint for. A filing that has no
# such copy answers 406 rather than serving the scan, which is how this platform tells a
# tagged filing from an untagged one without downloading either.
IXBRL_MEDIA_TYPE: Final = "application/xhtml+xml"
NOT_TAGGED_STATUS: Final = 406

# A company number is eight characters: digits, or two letters and six digits for the Scottish
# and Northern Irish registers. Validated because it goes into a URL path.
_NUMBER_LENGTH: Final = 8

_SEARCH_LIMIT: Final = 20
_HISTORY_LIMIT: Final = 100

# How many accounts filings a fact fetch opens, newest first. On an annual filer that is four
# years — enough for a growth series and a margin trend — and it is a **stated number** rather
# than "everything", because Companies House publishes no aggregate: a UK fact base is one
# fetch and one arelle parse per year of history, so its cost is linear in this and would
# otherwise be a function of how long the company has existed. See ADR 0121.
FACT_DEPTH: Final = 4


def basic_auth_header(api_key: str) -> str:
    """The ``Authorization`` value for a Companies House key.

    HTTP Basic with the key as the username and an empty password, which is what the API
    documents. Built here rather than at the call site so there is one place that knows the
    scheme, and so the credential is turned into a header exactly once.
    """
    import base64  # noqa: PLC0415 -- used only here, and only when a key is configured

    encoded = base64.b64encode(f"{api_key}:".encode()).decode("ascii")
    return f"Basic {encoded}"


def looks_like_company_number(value: str) -> bool:
    """Whether this string is already a company number, rather than something to search for.

    Deliberately stricter than :func:`normalise_company_number`, which zero-pads a short
    number because `102498` and `00102498` are the same company. That padding is right when
    somebody has said "this is a company number" and wrong when they have not: `1234` would
    become a company number and a ticker would be looked up as an entity that has nothing to
    do with the subject. So this asks only whether the value is *already* one — eight
    characters, all digits or a two-letter register prefix and six digits.
    """
    cleaned = value.strip().upper().replace(" ", "")
    if len(cleaned) != _NUMBER_LENGTH or not cleaned.isalnum():
        return False
    return cleaned.isdigit() or (cleaned[:2].isalpha() and cleaned[2:].isdigit())


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

    # What the company says it does, in UK SIC 2007 (ADR 0121). A company may declare up to
    # four, and the register returns them as filed rather than ranked. Empty from a search
    # result, which carries no classification: only the profile endpoint does.
    sic_codes: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        return (self.status or "").lower() == "active"

    @property
    def fiscal_year_end(self) -> str | None:
        """The accounting reference date as ``MMDD``, or ``None``.

        The register states it as a day and a month, which is the same fact in the other
        order and without the padding that makes it sortable — and ``fiscal_year_of`` reads
        the padded form. A company with no reference date on file is one whose first accounts
        are not due yet.
        """
        if not self.accounts_reference_date:
            return None
        day, _, month = self.accounts_reference_date.partition("/")
        if not day.isdigit() or not month.isdigit():
            return None
        return f"{int(month):02d}{int(day):02d}"


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
        sic_codes=_sic_codes(document.get("sic_codes")),
    )


def _same_name(candidate: str, query: str) -> bool:
    """Whether the register calls this company exactly what the operator called it.

    Compared on the letters and digits alone: the register writes `TESCO PLC` and an operator
    may write `Tesco plc.` or `Tesco P.L.C.`, and none of the difference is about identity. No
    stemming and no dropping of the legal suffix — `SHELL PLC` and `SHELL TRANSPORT` are
    different companies, and a match that tolerated the difference would be the guess this
    function exists to avoid making.
    """
    return _letters(candidate) == _letters(query) and bool(_letters(query))


def _letters(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _sic_codes(raw: Any) -> tuple[str, ...]:
    """The declared SIC codes, in the order the register returned them.

    Order is kept because it is the only thing distinguishing them: the API ranks nothing, so
    re-sorting would replace the register's answer with this platform's opinion of it.
    """
    if not isinstance(raw, list):
        return ()
    codes = [str(code).strip() for code in raw if str(code).strip().isdigit()]
    return tuple(codes)


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

    def __init__(
        self, fetcher: SafeFetcher, *, store: ArtefactStore, depth: int = FACT_DEPTH
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._depth = max(1, depth)

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

    async def fetch_document(self, ref: DocumentRef, *, tagged: bool = True) -> FetchResult:
        """Fetch a filed document referenced by a history this client produced.

        The URL comes from a :class:`~aer.sources.base.DocumentRef`, only ever built from a
        document identifier the register issued. It is still validated against the allowlist by
        the fetch layer, because a chain of trusted construction is only as strong as its
        weakest link and this one crosses a module boundary.

        **The tagged copy has to be asked for**, and this was measured rather than assumed. The
        register serves one filing at two representations and hands over the PDF unless the
        request says otherwise: a small company's accounts came back as an untagged 20 KB PDF
        by default and as 19.6 KB of inline XBRL carrying eight facts when asked for by type.
        Without the header, the fact extractor was being handed the wrong document and
        reporting the filing as untagged.

        ``tagged=False`` asks for whatever the register serves, which is the scanned PDF. The
        default is the other way round because the caller that wants figures is the one that
        would otherwise be silently wrong.

        A filing with no tagged copy answers **406**, which is a better answer than the 14 MB
        scan behind the other representation: it says "this filing is not tagged" in one round
        trip and no megabytes. The caller decides what to do with it; this returns the result
        rather than raising, exactly as it does for any other status.
        """
        accept = IXBRL_MEDIA_TYPE if tagged else None
        return await self._fetcher.fetch(ref.url, provider=self.provider, accept=accept)

    # -- Adapter surface -------------------------------------------------------------------

    async def resolve_entity(
        self, ticker: str, *, exchange: str | None = None, name: str | None = None
    ) -> ResolvedEntity:
        """Find a company number for a name.

        **Refuses an ambiguous match rather than taking the first hit.** Companies House
        registers companies and knows nothing about listings, so a search for a ticker or a
        short name routinely returns a dozen businesses with similar names — dormant
        subsidiaries, pension trustees, a holding company and its operating arm. Picking one by
        rank would put another business's accounts under this company's name, and every figure
        downstream would be internally consistent and about the wrong firm.

        Args:
            ticker: The listing's symbol, kept on the resolved entity whatever answered.
            name: What to search for, when it is not the ticker — which on this register it
                usually is not. `TSCO` is Tesco's symbol and no part of `TESCO PLC`, so a
                search for the symbol finds the company by luck or not at all. The run's own
                company name is the better query, and it is what the ambiguity refusal below
                already tells an operator to supply.
            exchange: Recorded on the entity, not used to search. The register knows nothing
                about listings.

        **A query that is already a company number is looked up rather than searched.** That
        is the escape hatch the ambiguity refusal names, and without it an operator told
        "three active companies match" has nowhere to go.

        Raises:
            ValidationError: Nothing matched, or more than one active company did.
        """
        query = (name or ticker).strip()
        if looks_like_company_number(query):
            profile = await self.fetch_profile(query)
            return self._resolved(profile, ticker=ticker, exchange=exchange, by="company_number")

        candidates = await self.search_companies(query)
        active = [c for c in candidates if c.is_active]

        # **An exact name is not an ambiguity.** A search for a listed company's registered
        # name returns its subsidiaries too — "TESCO PLC" matches nine active companies on the
        # real register, among them TESCO ATRATO (GP) LIMITED — and refusing all of them would
        # make every large UK group unresearchable by name. Exactly one of those nine is
        # *called* TESCO PLC, and taking it is answering the operator rather than guessing
        # between businesses, which is what the refusal below exists to prevent.
        named = [c for c in active if _same_name(c.name, query)]
        if len(named) == 1:
            return self._resolved(named[0], ticker=ticker, exchange=exchange, by="exact_name")

        if not active:
            message = (
                f"No active company on the Companies House register matches {query!r}. "
                "The register lists companies rather than securities, so a ticker is often "
                "not the registered name — try the full company name."
            )
            raise ValidationError(message, context={"query": query, "candidates": len(candidates)})

        if len(active) > 1:
            names = [f"{c.name} ({c.company_number})" for c in active[:5]]
            message = (
                f"{query!r} matches {len(active)} active companies on the register, and "
                "choosing between them by search rank would risk attributing another "
                f"business's accounts to this one. Candidates: {'; '.join(names)}. "
                "Supply the company number instead."
            )
            raise ValidationError(message, context={"query": query, "matches": names})

        return self._resolved(active[0], ticker=ticker, exchange=exchange, by="search")

    def _resolved(
        self, profile: CompanyProfile, *, ticker: str, exchange: str | None, by: str
    ) -> ResolvedEntity:
        """One resolution, however it was reached — and the log says which way."""
        _log.info(
            "companies_house.entity_resolved",
            company_number=profile.company_number,
            name=profile.name,
            by=by,
        )
        return ResolvedEntity(
            identifier=profile.company_number,
            name=profile.name,
            ticker=ticker.strip().upper() or None,
            exchange=exchange,
        )

    async def discover_documents(
        self,
        entity: ResolvedEntity,
        *,
        forms: frozenset[str] | None = None,
    ) -> tuple[DocumentRef, ...]:
        """A company's accounts, newest first, as they stand on the register."""
        categories = forms if forms is not None else ACCOUNTS_CATEGORIES
        history = await self.fetch_filing_history(entity.identifier, categories=categories)

        wanted = [f for f in history.filings if f.is_fetchable and f.category in categories]

        return tuple(filing.to_ref(company_name=entity.name) for filing in wanted)

    async def fetch_facts(self, entity: ResolvedEntity) -> tuple[RawFact, ...]:
        """Every fact the company tagged, read out of its own accounts (ADR 0121).

        **This is not the SEC shape with a different client behind it.** EDGAR publishes one
        JSON document holding every figure a registrant ever tagged, and `acquire` is built
        around that single fetch. Companies House publishes a filing history and, per filing,
        a document; there is no aggregate. So a UK fact base is *n* fetches and *n* parses,
        one per accounting period, and every fact is the output of this platform's own
        extractor rather than of the registry's aggregation — which is why
        :mod:`aer.calc.plausibility` matters more here than anywhere else: a parsing error is
        a wrong number with a perfect audit trail.

        **Unfiltered, as the protocol requires.** Unmapped tags come back carrying the tag as
        their concept, exactly as the EDGAR parser returns them, so the confirmation gate sees
        what a UK filer extended the taxonomy with instead of this adapter silently dropping
        it. Two things are refused: a cross-tab cell, which no row could state, and a fact
        identical to one already collected — a total tagged both in the primary statement and
        in the note that analyses it is one observation stated twice.

        **A figure two filings both state is two observations**, and deliberately so: each
        names its own filing, and the platform's whole selection story is the latest filing's
        word on a period with the rest recorded as superseded (ADR 0113). An adapter that
        collapsed them would leave nothing to select between and would silently pick the
        older restatement as often as the newer one.

        A document that will not parse costs its own facts and not the fetch: the others are
        already hashed and stored, and a UK company's history is exactly the case where one
        bad year must not take the other three with it.
        """
        history = await self.fetch_filing_history(entity.identifier)
        wanted = history.accounts()[: self._depth]
        if not wanted:
            _log.info(
                "companies_house.no_accounts",
                company_number=entity.identifier,
                filings=len(history.filings),
            )
            return ()

        facts: list[RawFact] = []
        seen: set[RawFact] = set()
        for filing in wanted:
            for fact in await self._facts_in(filing, entity=entity):
                if fact in seen:
                    continue
                seen.add(fact)
                facts.append(fact)

        _log.info(
            "companies_house.facts_extracted",
            company_number=entity.identifier,
            documents=len(wanted),
            facts=len(facts),
        )
        return tuple(facts)

    async def _facts_in(
        self, filing: FilingRecord, *, entity: ResolvedEntity
    ) -> tuple[RawFact, ...]:
        """One accounts document's facts, joined to the filing that carried them."""
        ref = filing.to_ref(company_name=entity.name)
        result = await self.fetch_document(ref)
        if result.status_code == NOT_TAGGED_STATUS:
            # The register has no tagged copy of this filing. Said plainly rather than
            # reported as an extraction failure, because it is a fact about the filing: a
            # listed company's accounts are uploaded as a scan, and every one of them would
            # otherwise be logged as a document this platform could not read.
            _log.info(
                "companies_house.filing_not_tagged",
                company_number=entity.identifier,
                transaction_id=filing.transaction_id,
                filed_on=filing.filed_on.isoformat(),
            )
            return ()
        try:
            extraction = extract_ixbrl(await self._body(result))
        except AerError as unreadable:
            _log.warning(
                "companies_house.document_unreadable",
                company_number=entity.identifier,
                transaction_id=filing.transaction_id,
                reason=unreadable.message,
            )
            return ()

        joined = (
            raw_fact_from_ixbrl(
                fact,
                form=filing.category,
                accession=filing.transaction_id,
                filed_date=filing.filed_on,
            )
            for fact in extraction.facts
        )
        return tuple(fact for fact in joined if fact is not None)

    # -- Internals -------------------------------------------------------------------------

    async def _get(self, url: str) -> FetchResult:
        return await self._fetcher.fetch(url, provider=self.provider)

    async def _body(self, result: FetchResult) -> bytes:
        """Read the archived bytes back by hash.

        Read from the store rather than held from the response, so what is parsed is provably
        what was archived — the same rule the SEC client follows.
        """
        return await self._store.read(result.sha256)
