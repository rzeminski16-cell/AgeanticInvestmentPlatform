"""From a fetch to rows: acquisition, company identity, and stored facts.

The acceptance criterion this file exists for: **each fetch creates exactly one artefact
and exactly one source document**, and every persisted fact traces through that source
document to a hashed artefact.

Also covers the two idempotence properties that make re-running research safe. Re-running
is normal — an as-of date moves, a run is repeated after a fix — and a pipeline that
duplicated its output each time would make every count downstream wrong.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError as DbIntegrityError

from aer.core.enums import FactBasis, Provider, RequestStatus, SourceTier, UserRole
from aer.db.models import Artefact, Company, FinancialFact, ResearchRequest, SourceDocument, User
from aer.errors import IntegrityError
from aer.fetch.client import FetchResult
from aer.services.acquisition import record_acquisition
from aer.services.facts import persist_facts, upsert_company
from aer.services.sources import NO_PUBLICATION_DATE
from aer.sources.base import ResolvedEntity
from aer.sources.sec.companyfacts import parse_company_facts
from aer.sources.sec.pit import select_point_in_time
from aer.storage.local import LocalArtefactStore
from tests.sec_fixtures import MSFT_CIK, fixture_bytes, make_fact

pytestmark = pytest.mark.integration

COMPANYFACTS = fixture_bytes("companyfacts_msft.json")
FILING = b"<html>Microsoft Corporation Form 10-K for the year ended 30 June 2020.</html>"


@pytest.fixture
def store(tmp_path) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=1_000_000)


@pytest.fixture
async def request_row(db_session) -> ResearchRequest:
    user = User(email="sec@example.invalid", display_name="SEC", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    row = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2021, 3, 31),
        base_currency="USD",
        investment_horizon_months=36,
        max_cost_gbp="2.00",
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def fetched(store: LocalArtefactStore, body: bytes, **overrides) -> FetchResult:
    """A FetchResult for bytes already in the store, as the fetch layer would leave them."""
    stored = await store.put_bytes(body)
    defaults = {
        "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
        "final_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
        "status_code": 200,
        "sha256": stored.sha256,
        "size_bytes": stored.size_bytes,
        "media_type": "application/json",
        "declared_media_type": "application/json",
        "headers": {},
        "redirect_chain": (),
        "elapsed_ms": 12.0,
        "attempts": 1,
        "licence_note": "US government work; not subject to copyright in the United States.",
        "robots_allowed": None,
    }
    return FetchResult(**{**defaults, **overrides})


class TestAcquisition:
    async def test_one_fetch_creates_one_artefact_and_one_source_document(
        self, db_session, store, request_row
    ):
        result = await fetched(store, COMPANYFACTS)

        await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        artefacts = await db_session.scalar(select(func.count()).select_from(Artefact))
        documents = await db_session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.request_id == request_row.id)
        )
        assert (artefacts, documents) == (1, 1)

    async def test_the_artefact_row_carries_the_digest_the_fetcher_computed(
        self, db_session, store, request_row
    ):
        result = await fetched(store, COMPANYFACTS)

        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert acquisition.sha256 == hashlib.sha256(COMPANYFACTS).hexdigest()
        assert acquisition.artefact.size_bytes == len(COMPANYFACTS)

    async def test_the_bytes_are_not_stored_a_second_time(self, db_session, store, request_row):
        # The fetcher already archived them, and the store is content-addressed. Handing
        # them to store_artefact again would cost a full re-hash of a document that can be
        # tens of megabytes.
        result = await fetched(store, COMPANYFACTS)
        before = await store.verify(result.sha256)

        await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert await store.verify(result.sha256) == before

    async def test_recording_an_artefact_not_in_the_store_is_refused(
        self, db_session, store, request_row
    ):
        # A provenance row claiming bytes nobody holds is the one thing this table must
        # never contain.
        missing = await fetched(store, COMPANYFACTS, sha256="0" * 64)

        with pytest.raises(IntegrityError, match="nothing for a provenance record"):
            await record_acquisition(
                db_session,
                store,
                request=request_row,
                result=missing,
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
            )

    async def test_a_filing_carries_its_publication_date_and_licence(
        self, db_session, store, request_row
    ):
        # The acceptance criterion: source_documents rows carry publication_date and
        # licence_note.
        result = await fetched(
            store,
            FILING,
            url="https://www.sec.gov/Archives/edgar/data/789019/msft-20200630.htm",
            final_url="https://www.sec.gov/Archives/edgar/data/789019/msft-20200630.htm",
            media_type="text/html",
        )

        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2020, 7, 30),
            title="MICROSOFT CORP 10-K 2020-07-30",
        )

        document = acquisition.source_document
        assert document.publication_date == date(2020, 7, 30)
        assert document.licence_note is not None
        assert document.licence_note.startswith("US government work")
        assert document.quarantined is False

    async def test_a_generated_aggregate_is_quarantined_for_having_no_date(
        self, db_session, store, request_row
    ):
        # companyfacts is generated on request from whatever filings exist at that moment.
        # It has no publication date of its own, so under point-in-time rules it is
        # quarantined -- which is correct, because the citable thing is the filing, not
        # the endpoint that aggregates it.
        result = await fetched(store, COMPANYFACTS)

        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert acquisition.quarantined is True
        assert acquisition.source_document.quarantine_reason == NO_PUBLICATION_DATE

    async def test_a_failed_fetch_still_leaves_a_record(self, db_session, store, request_row):
        # A run that fetched nothing and a run whose every fetch returned 403 are very
        # different situations, and only one of them is a network problem.
        result = await fetched(store, b"<html>404 Not Found</html>", status_code=404)

        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert acquisition.source_document.http_status == 404

    async def test_a_redirect_records_the_destination_separately(
        self, db_session, store, request_row
    ):
        result = await fetched(
            store,
            FILING,
            url="https://www.sec.gov/Archives/edgar/data/789019/old.htm",
            final_url="https://www.sec.gov/Archives/edgar/data/789019/new.htm",
        )

        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2020, 7, 30),
        )

        document = acquisition.source_document
        assert document.url.endswith("old.htm")
        assert document.canonical_url is not None
        assert document.canonical_url.endswith("new.htm")


class TestCompanyIdentity:
    async def test_a_resolved_entity_becomes_a_company_row(self, db_session):
        entity = ResolvedEntity(
            identifier=MSFT_CIK, name="MICROSOFT CORP", ticker="MSFT", exchange="NASDAQ"
        )

        company = await upsert_company(
            db_session, entity=entity, ticker="MSFT", exchange="NASDAQ", sic="7372"
        )

        assert company.cik == MSFT_CIK
        assert company.sic == "7372"

    async def test_resolving_twice_reuses_the_row(self, db_session):
        entity = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")

        first = await upsert_company(db_session, entity=entity, ticker="MSFT", exchange="NASDAQ")
        second = await upsert_company(db_session, entity=entity, ticker="MSFT", exchange="NASDAQ")

        assert first.id == second.id
        assert await db_session.scalar(select(func.count()).select_from(Company)) == 1

    async def test_the_registry_identifier_matches_before_the_listing(self, db_session):
        # A company can change ticker or move exchange. Matching on the listing alone
        # would create a second row for the same company the first time it did.
        original = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")
        await upsert_company(db_session, entity=original, ticker="MSFT", exchange="NASDAQ")

        renamed = ResolvedEntity(identifier=MSFT_CIK, name="Microsoft Corporation")
        again = await upsert_company(db_session, entity=renamed, ticker="MSFT.NEW", exchange="NYSE")

        assert await db_session.scalar(select(func.count()).select_from(Company)) == 1
        assert again.name == "Microsoft Corporation"

    async def test_a_company_with_no_registry_identifier_is_refused(self, db_session):
        # An entity resolvable against no authority is one nothing can be verified about.
        db_session.add(Company(name="Nowhere Ltd", ticker="NWH", exchange="LSE"))

        with pytest.raises(DbIntegrityError, match="registry_identifier"):
            await db_session.flush()


class TestPersistingFacts:
    @pytest.fixture
    async def company(self, db_session) -> Company:
        return await upsert_company(
            db_session,
            entity=ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP"),
            ticker="MSFT",
            exchange="NASDAQ",
        )

    @pytest.fixture
    async def source(self, db_session, store, request_row) -> SourceDocument:
        result = await fetched(store, COMPANYFACTS)
        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )
        return acquisition.source_document

    async def test_selected_facts_are_written(self, db_session, company, source):
        selection = select_point_in_time(
            parse_company_facts(COMPANYFACTS).facts, as_of_date=date(2021, 3, 31)
        )

        written = await persist_facts(
            db_session, company=company, source_document=source, facts=selection.chosen
        )

        assert written == len(selection.chosen)

    async def test_every_fact_traces_to_a_hashed_artefact(self, db_session, company, source):
        # The invariant, end to end: fact -> source document -> artefact -> SHA-256.
        await persist_facts(
            db_session, company=company, source_document=source, facts=[make_fact()]
        )

        digest = await db_session.scalar(
            select(Artefact.sha256)
            .join(SourceDocument, SourceDocument.artefact_id == Artefact.id)
            .join(FinancialFact, FinancialFact.source_document_id == SourceDocument.id)
        )

        assert digest == hashlib.sha256(COMPANYFACTS).hexdigest()

    async def test_re_running_writes_nothing_new(self, db_session, company, source):
        facts = select_point_in_time(
            parse_company_facts(COMPANYFACTS).facts, as_of_date=date(2021, 3, 31)
        ).chosen

        first = await persist_facts(
            db_session, company=company, source_document=source, facts=facts
        )
        second = await persist_facts(
            db_session, company=company, source_document=source, facts=facts
        )

        assert first > 0
        assert second == 0

    async def test_a_restatement_is_stored_alongside_the_original(
        self, db_session, company, source
    ):
        # Both are true statements about the same period, made two years apart. Collapsing
        # them would destroy the point-in-time record, which is what filed_date being part
        # of the uniqueness key prevents.
        original = make_fact(value=143015000000, filed="2020-07-30")
        restatement = make_fact(
            value=142000000000, filed="2022-07-28", accession="0000789019-22-000010"
        )

        written = await persist_facts(
            db_session,
            company=company,
            source_document=source,
            facts=[original, restatement],
        )

        assert written == 2

    async def test_a_fact_with_no_fiscal_period_cannot_be_duplicated(
        self, db_session, company, source
    ):
        # The NULLS NOT DISTINCT case. Under the SQL default two NULLs never compare
        # equal, so without the modifier this row could be inserted any number of times.
        undated = make_fact(fiscal_period=None)

        first = await persist_facts(
            db_session, company=company, source_document=source, facts=[undated]
        )
        second = await persist_facts(
            db_session, company=company, source_document=source, facts=[undated]
        )

        assert (first, second) == (1, 0)

    async def test_the_basis_is_recorded_on_every_row(self, db_session, company, source):
        await persist_facts(
            db_session, company=company, source_document=source, facts=[make_fact()]
        )

        basis = await db_session.scalar(select(FinancialFact.basis))

        assert basis is FactBasis.AS_REPORTED

    async def test_the_unit_and_filed_date_survive_the_round_trip(
        self, db_session, company, source
    ):
        await persist_facts(
            db_session,
            company=company,
            source_document=source,
            facts=[
                make_fact(
                    value="5.76",
                    unit="USD/shares",
                    concept="earnings_per_share_diluted",
                )
            ],
        )

        row = await db_session.scalar(select(FinancialFact))

        assert row is not None
        assert row.unit == "USD/shares"
        # Exact, not approximate. The column is NUMERIC and the value is a Decimal all the
        # way through; comparing against a float here would be testing the opposite of
        # what the schema promises.
        assert row.value == Decimal("5.76")
        assert row.filed_date == date(2021, 1, 1)

    async def test_a_large_integer_survives_the_round_trip(self, db_session, company, source):
        # 143,015,000,000 exceeds 2^53. A float column would round it.
        await persist_facts(
            db_session,
            company=company,
            source_document=source,
            facts=[make_fact(value=143015000000)],
        )
        db_session.expunge_all()

        row = await db_session.scalar(select(FinancialFact))

        assert row is not None
        assert int(row.value) == 143015000000

    async def test_persisting_nothing_is_not_an_error(self, db_session, company, source):
        written = await persist_facts(db_session, company=company, source_document=source, facts=[])

        assert written == 0

    async def test_a_full_filing_history_does_not_exceed_the_parameter_limit(
        self, db_session, company, source
    ):
        """Postgres binds each value as a parameter and stops at 32,767.

        Sixteen columns means 2,047 rows per statement, and this failed on the first real
        company: Microsoft's companyfacts, point-in-time selected, is 13,702 facts — 219,232
        parameters. The extract step died with ``the number of query arguments cannot exceed
        32767`` after the planner call had been paid for.

        Every fixture in this file until now held a handful of facts, which is why nothing
        caught it. The count below is deliberately just over one batch rather than a realistic
        13,702: the boundary is what breaks, and a test that takes a second is a test that
        keeps being run.
        """
        over_one_batch = 2_100
        facts = [
            make_fact(concept=f"concept_{index}", value=index, fiscal_period="FY")
            for index in range(over_one_batch)
        ]

        written = await persist_facts(
            db_session, company=company, source_document=source, facts=facts
        )

        assert written == over_one_batch
        stored = await db_session.scalar(select(func.count()).select_from(FinancialFact))
        assert stored == over_one_batch

    async def test_a_batched_insert_still_skips_what_is_already_stored(
        self, db_session, company, source
    ):
        """The idempotency the docstring promises has to hold across batch boundaries too.

        A naive chunking that reported ``len(rows)`` rather than summing what each statement
        returned would pass the test above and quietly lie here — and "how many facts did this
        run add?" is a number that appears on the run console.
        """
        facts = [
            make_fact(concept=f"concept_{index}", value=index, fiscal_period="FY")
            for index in range(2_100)
        ]
        await persist_facts(db_session, company=company, source_document=source, facts=facts)

        again = await persist_facts(
            db_session, company=company, source_document=source, facts=facts
        )

        assert again == 0

    async def test_a_fact_cannot_outlive_the_document_it_came_from(
        self, db_session, company, source
    ):
        # ON DELETE RESTRICT. Removing evidence has to mean removing what rests on it,
        # not leaving the fact standing with nothing behind it.
        await persist_facts(
            db_session, company=company, source_document=source, facts=[make_fact()]
        )

        await db_session.delete(source)

        with pytest.raises(DbIntegrityError, match="violates foreign key constraint"):
            await db_session.flush()


class TestTheFullSlice:
    async def test_parse_select_and_persist_produces_the_point_in_time_answer(
        self, db_session, store, request_row
    ):
        # The whole task in one test: fetch a companyfacts document, record its
        # provenance, select as at March 2021, persist, and read back the revenue figure
        # that was public on that date -- not the one restated in 2022.
        result = await fetched(store, COMPANYFACTS)
        acquisition = await record_acquisition(
            db_session,
            store,
            request=request_row,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )
        company = await upsert_company(
            db_session,
            entity=ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORPORATION"),
            ticker="MSFT",
            exchange="NASDAQ",
        )

        parsed = parse_company_facts(COMPANYFACTS)
        selection = select_point_in_time(parsed.facts, as_of_date=request_row.as_of_date)
        await persist_facts(
            db_session,
            company=company,
            source_document=acquisition.source_document,
            facts=selection.chosen,
        )

        stored = (
            await db_session.scalars(
                select(FinancialFact).where(
                    FinancialFact.concept == "revenue",
                    FinancialFact.period_end == date(2020, 6, 30),
                    FinancialFact.unit == "USD",
                )
            )
        ).all()

        # Exactly one. The fixture tags FY2020 revenue under two names in the same filing,
        # and both mean the same number -- point-in-time selection resolves that to a
        # single answer rather than passing the ambiguity downstream.
        assert len(stored) == 1
        assert int(stored[0].value) == 143015000000
        assert stored[0].filed_date == date(2020, 7, 30)
        assert stored[0].accession == "0000789019-20-000039"
