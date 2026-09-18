"""Acquiring a UK company's own accounts (ADR 0121).

The register publishes a filing history and, per filing, a document. There is no aggregate,
so a UK acquisition is *n* fetches and *n* records — and each document is both halves of the
evidence at once: the prose a section cites and, where it is tagged, the figures.

**These tests use documents recorded from the live register on 18 September 2026**, and one
of them records something the plan did not expect: a listed company's filed accounts are a
PDF. See ADR 0121's appended section and `ROADMAP.md` §3.19 items 21 and 22.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import Provider, SourceTier, UserRole
from aer.db.models import Company, SourceDocument, User
from aer.errors import ExternalServiceError
from aer.fetch.client import FetchResult
from aer.services.filings import acquire_accounts
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.uk.companies_house import parse_filing_history
from aer.storage.local import LocalArtefactStore
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

FIXTURES: Final = Path(__file__).parent / "fixtures" / "uk"
TESCO: Final = ResolvedEntity(
    identifier="00445790", name="TESCO PLC", ticker="TSCO", exchange="LSE"
)

# Enough of an annual report to excerpt: the paragraphs are what a section cites.
ACCOUNTS_HTML: Final = b"""<html><body>
<p>The group traded profitably through the year to 26 February 2026, with like-for-like sales
ahead of the prior year in every market in which it operates, and the board is recommending a
final dividend in line with its stated policy of progressive returns to shareholders.</p>
<p>Operating costs rose in the period, reflecting the national living wage settlement and the
continuing investment in the group's distribution network, both of which the board regards as
necessary to sustain the competitive position described in the strategic report above.</p>
<p>These accounts were delivered to the registrar under transaction {accession}, and the
directors confirm that they were approved by the board on the date shown in the statement of
responsibilities set out on the preceding page of this report.</p>
</body></html>"""


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class StubCompaniesHouse:
    """The client's surface, served from the recorded filing history through the real store."""

    def __init__(self, store: LocalArtefactStore, *, payload: bytes = ACCOUNTS_HTML) -> None:
        self._store = store
        self._payload = payload
        self.fetched: list[str] = []
        self.refuse: Exception | None = None

    async def discover_documents(self, entity: ResolvedEntity) -> tuple[DocumentRef, ...]:
        if self.refuse is not None:
            raise self.refuse
        history = parse_filing_history(
            fixture("ch_filing_history_tesco.json"), company_number=entity.identifier
        )
        return tuple(
            filing.to_ref(company_name=entity.name)
            for filing in history.accounts()
            if filing.is_fetchable
        )

    async def fetch_document(self, ref: DocumentRef) -> FetchResult:
        self.fetched.append(ref.url)
        # Each filing's own bytes. Two years of accounts are not byte-identical, and a stub
        # that made them so would collapse into one artefact and one source document.
        stored = await self._store.put_bytes(
            self._payload.replace(b"{accession}", (ref.accession or "").encode())
        )
        return FetchResult(
            url=ref.url,
            final_url=ref.url,
            status_code=200,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type="text/html",
            declared_media_type="text/html",
            headers={"content-type": "text/html"},
            redirect_chain=(),
            elapsed_ms=1.0,
            attempts=1,
            licence_note="Crown copyright, Open Government Licence.",
            robots_allowed=True,
        )


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = User(email="uk@example.invalid", display_name="U", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Tesco PLC",
        ticker="TSCO",
        exchange="LSE",
        as_of_date=date(2026, 9, 1),
        base_currency="GBP",
        reporting_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    db_session.add(request)
    await db_session.flush()

    company = Company(
        name=TESCO.name, company_number=TESCO.identifier, ticker="TSCO", exchange="LSE"
    )
    db_session.add(company)
    await db_session.flush()
    request.company_id = company.id
    await db_session.flush()

    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    return {
        "session": db_session,
        "request": request,
        "company": company,
        "settings": settings,
        "store": store,
        "client": StubCompaniesHouse(store),
    }


async def _acquire(scene: dict[str, Any], **kwargs: Any) -> Any:
    return await acquire_accounts(
        scene["session"],
        scene["store"],
        client=kwargs.pop("client", scene["client"]),
        request=scene["request"],
        entity=TESCO,
        company=scene["company"],
        settings=scene["settings"],
        **kwargs,
    )


class TestTheAccountsBecomeEvidence:
    async def test_the_newest_accounts_are_acquired_newest_first(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _acquire(scene, depth=3)

        dates = [item.document.publication_date for item in outcome.filings]
        assert dates == sorted(dates, reverse=True)
        assert dates[0] == date(2026, 7, 25)

    async def test_the_depth_is_a_stated_number_rather_than_a_window(
        self, scene: dict[str, Any]
    ) -> None:
        """Companies House publishes no aggregate, so the cost is linear in this.

        Left unbounded it would be a function of how long the company has existed: the
        recorded history says Tesco has filed accounts 55 times.
        """
        history = json.loads(fixture("ch_filing_history_tesco.json"))
        assert history["total_count"] > 50

        outcome = await _acquire(scene, depth=2)

        assert len(outcome.filings) == 2

    async def test_each_document_is_a_citable_source_at_the_regulatory_tier(
        self, scene: dict[str, Any]
    ) -> None:
        await _acquire(scene, depth=2)

        rows = list(
            await scene["session"].scalars(
                select(SourceDocument).where(
                    SourceDocument.work_order_id == scene["request"].id,
                    SourceDocument.provider == Provider.COMPANIES_HOUSE,
                )
            )
        )
        assert len(rows) == 2
        assert all(row.source_tier is SourceTier.T1_REGULATORY for row in rows)
        assert all(row.publisher == "Companies House" for row in rows)
        assert all(row.company_id == scene["company"].id for row in rows)

    async def test_a_document_is_dated_by_the_day_the_register_accepted_it(
        self, scene: dict[str, Any]
    ) -> None:
        """Not by the period it covers. The 2026 accounts are made up to 26 February and were
        filed on 25 July, and July is when anybody could first have read them."""
        outcome = await _acquire(scene, depth=1)

        [only] = outcome.filings
        assert only.document.publication_date == date(2026, 7, 25)
        assert only.document.quarantined is False

    async def test_the_paragraphs_are_recorded_so_a_claim_can_cite_one(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _acquire(scene, depth=1)

        assert outcome.excerpts >= 2

    async def test_the_transaction_id_travels_with_the_record(self, scene: dict[str, Any]) -> None:
        """It is the register's own name for the filing, and the only thing tying a fact read
        out of the document back to the filing that carried it."""
        outcome = await _acquire(scene, depth=1)

        [only] = outcome.filings
        recorded = json.loads(json.dumps(outcome.as_dict()))["filings"][0]
        assert only.accession == "MzUzNDMwNDQ5M2FkaXF6a2N4"
        assert recorded["accession"] == only.accession
        assert recorded["artefact_sha256"] == only.sha256


class TestWhatCannotBeHad:
    async def test_an_unreadable_history_costs_the_sweep_and_not_the_run(
        self, scene: dict[str, Any]
    ) -> None:
        scene["client"].refuse = ExternalServiceError(
            "The register is unavailable.", provider="companies_house"
        )

        outcome = await _acquire(scene)

        assert outcome.filings == ()
        assert "could not be read" in outcome.skipped[0]

    async def test_a_company_that_has_filed_nothing_fetchable_says_so(
        self, scene: dict[str, Any]
    ) -> None:
        """An older filing is an index record with no document behind it. Saying so beats a
        silent empty sweep three steps from where it matters."""

        class Empty(StubCompaniesHouse):
            async def discover_documents(self, entity: ResolvedEntity) -> tuple[DocumentRef, ...]:
                return ()

        outcome = await _acquire(scene, client=Empty(scene["store"]))

        assert outcome.filings == ()
        assert "no accounts this platform can fetch" in outcome.skipped[0]

    async def test_one_refused_document_leaves_the_others_standing(
        self, scene: dict[str, Any]
    ) -> None:
        """A UK company's history is exactly the case where one bad year must not take the
        other three with it."""

        class OneBad(StubCompaniesHouse):
            async def fetch_document(self, ref: DocumentRef) -> FetchResult:
                if len(self.fetched) == 1:
                    self.fetched.append(ref.url)
                    raise ExternalServiceError(
                        "That document could not be fetched.", provider="companies_house"
                    )
                return await super().fetch_document(ref)

        outcome = await _acquire(scene, depth=3, client=OneBad(scene["store"]))

        assert len(outcome.filings) == 2
        assert len(outcome.skipped) == 1
        assert "could not be fetched" in outcome.skipped[0]
