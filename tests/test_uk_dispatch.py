"""A London listing is researched against the register its venue names (ADR 0121).

Two steps change here and they change together. `acquire` dispatches on the venue, so a
London listing resolves at Companies House and a New York one at EDGAR; and `extract` reads
what that register publishes — one aggregate for EDGAR, and for Companies House the accounts
filings themselves, one parse each.

**The pre-run check (ADR 0128) is why the dispatch has to exist.** The check asks Companies
House whether a UK subject's newest accounts are tagged, and admits the run when they are. A
run admitted on that answer and then resolved against EDGAR would be exactly the defect the
check's own docstring forbids: a check that passes runs which then fail.

The tagged documents here are built by hand (`tests/ixbrl_fixtures.py`) rather than recorded,
because a truth set is the point: a real 200-page annual report gives one document whose
correct extraction nobody can write down.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Final

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import JobStatus, Provider, UserRole
from aer.core.sectors import SicScheme
from aer.db.models import (
    Company,
    Extraction,
    FinancialFact,
    JobStep,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.errors import ExternalServiceError
from aer.extract import extract_text
from aer.fetch.client import FetchResult
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.uk.companies_house import CompanyProfile
from aer.storage.local import LocalArtefactStore
from aer.workflow.engine import StepContext
from aer.workflow.workflows.vertical_slice_v1 import _acquire, _extract
from tests.ixbrl_fixtures import (
    CLEAN_IFRS,
    CLEAN_IFRS_TRUTH,
    EXTENSION_TAG,
    NOT_TAGGED,
    PERIOD_END,
    SEGMENT_AXIS,
    SEGMENT_TRUTH,
    WITH_EXTENSION,
    WITH_SEGMENTS,
    accounts_stating,
)
from tests.request_fixtures import research_request
from tests.workflow_fixtures import StubSecClient, seed_job

pytestmark = pytest.mark.integration

ACME: Final = ResolvedEntity(
    identifier="01234567", name="ACME HOLDINGS PLC", ticker="ACME", exchange="LSE"
)

# 62012 is "business and domestic software development" in UK SIC 2007. It reaches no
# specialist profile, which is the ordinary case and the one that must not fire a gate.
SOFTWARE_SIC: Final = "62012"

# 64191 is a UK bank. Declared second here on purpose: `principal_sic` must prefer the code
# that fires the sector gate over the code the register happened to list first.
BANK_SIC: Final = "64191"


class StubCompaniesHouse:
    """The client's surface, serving tagged accounts through the real artefact store.

    ``documents`` is what the register holds, newest first: each entry is the bytes of one
    filing and the day it was accepted. Distinct bytes per filing, because two artefacts
    that are byte-identical are one artefact and would collapse into one source document.
    """

    def __init__(
        self,
        store: LocalArtefactStore,
        *,
        documents: tuple[tuple[bytes, date], ...] = ((CLEAN_IFRS, date(2022, 10, 3)),),
        sic_codes: tuple[str, ...] = (SOFTWARE_SIC,),
    ) -> None:
        self._store = store
        self._documents = documents
        self._sic_codes = sic_codes
        self.resolved: list[str] = []
        self.profiles: list[str] = []
        self.fetched: list[str] = []

    async def resolve_entity(
        self, ticker: str, *, exchange: str | None = None, name: str | None = None
    ) -> ResolvedEntity:
        self.resolved.append(name or ticker)
        return ACME

    async def fetch_profile(self, company_number: str) -> CompanyProfile:
        self.profiles.append(company_number)
        return CompanyProfile(
            company_number=company_number,
            name=ACME.name,
            status="active",
            accounts_reference_date="30/6",
            sic_codes=self._sic_codes,
        )

    async def discover_documents(self, entity: ResolvedEntity) -> tuple[DocumentRef, ...]:
        return tuple(
            DocumentRef(
                url=f"https://find-and-update.company-information.service.gov.uk/document/{index}",
                title=f"{entity.name} accounts to {filed.isoformat()}",
                publication_date=filed,
                form="accounts",
                accession=f"TRANSACTION-{index}",
            )
            for index, (_, filed) in enumerate(self._documents)
        )

    async def fetch_document(self, ref: DocumentRef, *, tagged: bool = True) -> FetchResult:
        self.fetched.append(ref.url)
        index = int(ref.url.rsplit("/", 1)[1])
        stored = await self._store.put_bytes(self._documents[index][0])
        return FetchResult(
            url=ref.url,
            final_url=ref.url,
            status_code=200,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type="application/xhtml+xml",
            declared_media_type="application/xhtml+xml",
            headers={"content-type": "application/xhtml+xml"},
            redirect_chain=(),
            elapsed_ms=1.0,
            attempts=1,
            licence_note="Crown copyright, Open Government Licence.",
            robots_allowed=True,
        )


async def _scene(
    session: AsyncSession,
    tmp_path: Any,
    *,
    exchange: str = "LSE",
    ticker: str = "ACME",
    documents: tuple[tuple[bytes, date], ...] = ((CLEAN_IFRS, date(2022, 10, 3)),),
    sic_codes: tuple[str, ...] = (SOFTWARE_SIC,),
    companies_house: bool = True,
) -> dict[str, Any]:
    user = User(
        email=f"uk-{uuid.uuid4().hex[:8]}@example.invalid", display_name="U", role=UserRole.OWNER
    )
    session.add(user)
    await session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Acme Holdings plc",
        ticker=ticker,
        exchange=exchange,
        as_of_date=date(2026, 9, 1),
        base_currency="GBP",
        reporting_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    session.add(request)
    await session.flush()

    job = await seed_job(session, request=request)
    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    client = (
        StubCompaniesHouse(store, documents=documents, sic_codes=sic_codes)
        if companies_house
        else None
    )
    sec = StubSecClient(store)

    def context(step_key: str, outputs: dict[str, dict[str, Any]] | None = None) -> StepContext:
        return StepContext(
            session=session,
            job=job,
            step=JobStep(
                job_id=job.id,
                step_key=step_key,
                sequence=2,
                status=JobStatus.RUNNING,
                idempotency_key=f"{job.id}:{step_key}",
                input_hash="0" * 64,
            ),
            services={
                "store": store,
                "settings": settings,
                "sec_client": sec,
                "companies_house_client": client,
            },
            outputs=outputs or {},
        )

    return {
        "session": session,
        "request": request,
        "job": job,
        "store": store,
        "settings": settings,
        "client": client,
        "sec": sec,
        "context": context,
    }


async def _acquired(scene: dict[str, Any]) -> dict[str, Any]:
    return (await _acquire(scene["context"]("acquire"))).output


async def _extracted(scene: dict[str, Any], acquired: dict[str, Any]) -> dict[str, Any]:
    return (await _extract(scene["context"]("extract", {"acquire": acquired}))).output


class TestTheVenueDecidesTheRegister:
    async def test_a_london_listing_is_resolved_at_companies_house(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        scene = await _scene(db_session, tmp_path)

        output = await _acquired(scene)

        assert output["register"] == Provider.COMPANIES_HOUSE.value
        assert output["company_number"] == ACME.identifier
        assert "cik" not in output
        # And EDGAR was never asked. A dispatch that resolved both would cost a request to
        # a register that has never heard of the company, and would resolve it as somebody.
        assert scene["sec"].entity_calls == []

    async def test_a_new_york_listing_is_resolved_at_edgar(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        scene = await _scene(db_session, tmp_path, exchange="NASDAQ", ticker="MSFT")

        output = await _acquired(scene)

        assert output["register"] == Provider.SEC_EDGAR.value
        assert output["cik"]
        assert scene["client"].resolved == []

    async def test_the_run_records_which_register_answered(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """Both halves write it, so "which register?" is answered by the row rather than
        re-derived from the venue by every reader that needs it."""
        scene = await _scene(db_session, tmp_path)

        await _acquired(scene)

        request = await db_session.get(ResearchRequest, scene["request"].id)
        assert request is not None
        assert request.register is Provider.COMPANIES_HOUSE
        assert request.resolved is True

    async def test_the_identifier_is_a_company_number_and_not_a_cik(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """Both are identifiers and they are not interchangeable: a company number in `cik`
        would be looked up at EDGAR, where it is another registrant's key or nobody's."""
        scene = await _scene(db_session, tmp_path)

        output = await _acquired(scene)

        company = await db_session.get(Company, uuid.UUID(output["company_id"]))
        assert company is not None
        assert company.company_number == ACME.identifier
        assert company.cik is None

    async def test_the_profile_classifies_the_company_in_uk_sic_2007(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """The scheme travels with the code. `631` is fire and marine insurance on the US
        register and data processing on this one, so a code labelled with the wrong scheme
        classifies the company as the wrong kind of business."""
        scene = await _scene(db_session, tmp_path)

        output = await _acquired(scene)

        company = await db_session.get(Company, uuid.UUID(output["company_id"]))
        assert company is not None
        assert company.sic == SOFTWARE_SIC
        assert company.sic_scheme is SicScheme.UK_SIC_2007
        # 30 June, as the register states it, in the padded order `fiscal_year_of` reads.
        assert company.fiscal_year_end == "0630"

    async def test_the_declared_code_that_fires_the_gate_is_the_one_kept(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """A company declares up to four and the register ranks none of them. Firing the
        sector gate asks a person; not firing takes the standard model in silence."""
        scene = await _scene(db_session, tmp_path, sic_codes=(SOFTWARE_SIC, BANK_SIC))

        output = await _acquired(scene)

        company = await db_session.get(Company, uuid.UUID(output["company_id"]))
        assert company is not None
        assert company.sic == BANK_SIC

    async def test_without_a_credential_the_step_names_it(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """The pre-run check refuses this first, so this is a guard rather than the path an
        operator meets — and a guard that named a 401 at the publisher would not be one."""
        scene = await _scene(db_session, tmp_path, companies_house=False)

        with pytest.raises(ExternalServiceError) as refused:
            await _acquired(scene)

        assert "Companies House credential" in refused.value.message


class TestTheAccountsAreTheWholeFactBase:
    async def test_the_figures_come_out_of_the_filing_itself(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """No aggregate, no second party: every figure is this platform's own extractor
        reading the document the company filed."""
        scene = await _scene(db_session, tmp_path)
        acquired = await _acquired(scene)

        output = await _extracted(scene, acquired)

        assert output["facts_written"] == len(CLEAN_IFRS_TRUTH)
        stored = {
            row.concept: row.value
            for row in await db_session.scalars(
                select(FinancialFact).where(FinancialFact.dimension_axis.is_(None))
            )
        }
        assert stored == {concept: Decimal(value) for concept, value in CLEAN_IFRS_TRUTH.items()}

    async def test_each_fact_traces_to_the_filing_that_stated_it(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        scene = await _scene(db_session, tmp_path)
        acquired = await _acquired(scene)

        await _extracted(scene, acquired)

        rows = list(await db_session.scalars(select(FinancialFact)))
        documents = {row.id: row.url for row in await db_session.scalars(select(SourceDocument))}
        assert rows
        assert all(documents[row.source_document_id].endswith("/0") for row in rows)
        assert {row.accession for row in rows} == {"TRANSACTION-0"}

    async def test_a_period_two_filings_state_is_selected_latest_first(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """ADR 0113: the latest filing's word on a period, the rest recorded as superseded.

        Every UK company's accounts restate the year before, so this is the ordinary case
        here rather than an edge one — and it is why the selection runs over the union of
        four documents rather than per document.
        """
        scene = await _scene(
            db_session,
            tmp_path,
            documents=(
                (accounts_stating("210000"), date(2023, 9, 20)),
                (accounts_stating("198270"), date(2022, 10, 3)),
            ),
        )
        acquired = await _acquired(scene)

        output = await _extracted(scene, acquired)

        assert output["facts_chosen"] == 1
        assert output["facts_rejected"] == 1
        fact = await db_session.scalar(select(FinancialFact))
        assert fact is not None
        assert fact.value == Decimal(210_000_000)
        assert fact.accession == "TRANSACTION-0"
        assert fact.period_end == PERIOD_END

    async def test_a_segment_breakdown_is_kept_apart_from_the_consolidated_figure(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """Two segments' revenue and the group's are three observations, and the two that
        name an axis must never compete with the one that does not."""
        scene = await _scene(db_session, tmp_path, documents=((WITH_SEGMENTS, date(2022, 10, 3)),))
        acquired = await _acquired(scene)

        output = await _extracted(scene, acquired)

        assert output["segment_facts_written"] == len(SEGMENT_TRUTH)
        assert output["segment_facts_seen"] == len(SEGMENT_TRUTH)
        segments = {
            row.dimension_member: row.value
            for row in await db_session.scalars(
                select(FinancialFact).where(FinancialFact.dimension_axis == SEGMENT_AXIS)
            )
        }
        assert segments == {member: Decimal(value) for member, value in SEGMENT_TRUTH.items()}

        consolidated = await db_session.scalar(
            select(FinancialFact).where(
                FinancialFact.concept == "revenue", FinancialFact.dimension_axis.is_(None)
            )
        )
        assert consolidated is not None
        assert consolidated.value == Decimal(CLEAN_IFRS_TRUTH["revenue"])

    async def test_an_invented_element_reaches_the_confirmation_gate(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """UK filers extend the taxonomy routinely, and an extension carrying the company's
        headline profit measure is not something to map by guessing."""
        scene = await _scene(db_session, tmp_path, documents=((WITH_EXTENSION, date(2022, 10, 3)),))
        acquired = await _acquired(scene)

        output = await _extracted(scene, acquired)

        assert EXTENSION_TAG in output["unmapped_tags"]
        row = next(r for r in output["unmapped_concepts"] if r["tag"] == EXTENSION_TAG)
        assert row["observations"] == 1
        assert row["value"] == "91204000"
        # Sized against a line that mapped, which is what makes the gate answerable.
        assert row["share"]
        assert output["reference_concept"] == "revenue"

    async def test_a_document_that_will_not_parse_costs_its_own_facts_only(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """A UK company's history is exactly the case where one bad year must not take the
        other three with it."""
        scene = await _scene(
            db_session,
            tmp_path,
            documents=(
                (NOT_TAGGED, date(2023, 9, 20)),
                (CLEAN_IFRS, date(2022, 10, 3)),
            ),
        )
        acquired = await _acquired(scene)

        output = await _extracted(scene, acquired)

        assert output["facts_written"] == len(CLEAN_IFRS_TRUTH)
        # The year that would not parse is named, not silently absent: it cost every figure
        # in it, which is more than a missing segment breakdown.
        assert any("TRANSACTION-0" in note for note in output["accounts_notes"])
        assert output["segment_notes"] == []

    async def test_the_figure_in_the_prose_is_the_presented_one_not_the_stored_one(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """Why this path records no fact-level excerpt, stated as a measurement.

        The aggregate path locates each persisted value in the JSON it came from, because
        an aggregate has no prose for a numeric claim to cite. An inline document presents
        its figures *scaled* — `198270` in the text, tagged with a scale of three, stored as
        198,270,000 — so the same locator would find nothing on every UK filing there is.
        What a claim cites here is the paragraph, recorded when the document was acquired.
        """
        scene = await _scene(
            db_session,
            tmp_path,
            documents=((accounts_stating("198270"), date(2022, 10, 3)),),
        )
        acquired = await _acquired(scene)

        await _extracted(scene, acquired)

        fact = await db_session.scalar(select(FinancialFact))
        assert fact is not None
        assert fact.value == Decimal(198_270_000)

        extracted = await extract_text(
            scene["store"],
            sha256=acquired["filings"][0]["artefact_sha256"],
            extractor="html",
            settings=scene["settings"],
        )
        assert "198270" in extracted.text.text
        assert str(fact.value) not in extracted.text.text

        # And the paragraph containing it was recorded at acquisition, so the figure a
        # section quotes is in a row the citation verifier can re-read.
        excerpts = list(
            await db_session.scalars(
                select(Extraction).where(
                    Extraction.source_document_id
                    == uuid.UUID(acquired["filings"][0]["source_document_id"])
                )
            )
        )
        assert excerpts
        assert any("198270" in row.excerpt for row in excerpts)
