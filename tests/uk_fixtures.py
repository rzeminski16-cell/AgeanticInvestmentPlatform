"""A Companies House register that answers exactly as the real one did, for the harnesses.

**Recorded, not invented.** The three JSON documents under `tests/fixtures/uk/` were captured
from the live register on 18 September 2026, and the 406 below is what it answered when asked
for Tesco's newest accounts as inline XBRL — the measurement that produced ADR 0128. A stub
that made up its own answers would let the smoke harness prove a refusal the register does not
actually give.

Used by the availability check's callers rather than by the acquisition path: this is the
surface :func:`aer.services.availability.check_availability` asks for, and nothing more.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from aer.errors import ValidationError
from aer.fetch.client import FetchResult
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.uk.companies_house import (
    NOT_TAGGED_STATUS,
    FilingHistory,
    parse_filing_history,
)

__all__ = ["TESCO", "RecordedCompaniesHouse"]

FIXTURES: Final = Path(__file__).parent / "fixtures" / "uk"

TESCO: Final = ResolvedEntity(
    identifier="00445790", name="TESCO PLC", ticker="TSCO", exchange="LSE"
)


class RecordedCompaniesHouse:
    """The register, answering from what it said on 18 September 2026.

    ``tagged`` decides the one thing the recording cannot cover both ways: whether the newest
    accounts come back as inline XBRL or as the 406 that means "no tagged copy of this filing".
    Tesco's answer was the 406, and that is the default.
    """

    def __init__(self, *, tagged: bool = False, known: bool = True) -> None:
        self._tagged = tagged
        self._known = known
        self.resolved: list[str] = []
        self.fetched: list[str] = []

    async def resolve_entity(
        self, ticker: str, *, exchange: str | None = None, name: str | None = None
    ) -> ResolvedEntity:
        self.resolved.append(name or ticker)
        if not self._known:
            message = (
                f"No company on the UK register matches {name or ticker}. Companies House "
                "registers companies and knows nothing about listings, so the company's "
                "registered name finds it where a ticker does not."
            )
            raise ValidationError(message, context={"ticker": ticker})
        return TESCO

    async def fetch_filing_history(self, company_number: str, **_: Any) -> FilingHistory:
        payload = (FIXTURES / "ch_filing_history_tesco.json").read_bytes()
        return parse_filing_history(payload, company_number=company_number)

    async def fetch_document(self, ref: DocumentRef, *, tagged: bool = True) -> FetchResult:
        self.fetched.append(ref.url)
        status = 200 if self._tagged else NOT_TAGGED_STATUS
        return FetchResult(
            url=ref.url,
            final_url=ref.url,
            status_code=status,
            sha256="",
            size_bytes=0,
            media_type="application/xhtml+xml" if self._tagged else "",
            declared_media_type="application/xhtml+xml" if self._tagged else "",
            headers={},
            redirect_chain=(),
            elapsed_ms=1.0,
            attempts=1,
            licence_note="Crown copyright, Open Government Licence.",
            robots_allowed=True,
        )


def recorded(name: str) -> Any:
    """One of the recorded responses, parsed. For a test that asserts against the register."""
    return json.loads((FIXTURES / name).read_bytes())
