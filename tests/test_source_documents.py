"""Provenance records, the point-in-time quarantine rule, and artefact immutability.

Three things are being protected:

1. **A source that cannot be dated cannot be cited under point-in-time rules.** This is
   the cheapest place to stop look-ahead bias, and the only place where the decision is
   still obvious — later on, nobody remembers why a document had no date.
2. **Artefact rows cannot be updated.** Enforced by the database, so it holds against a
   script and an ad-hoc ``psql`` session, not only against this application.
3. **The audit trail records what was refused.** "What did this run decline to use, and
   why?" is a question a reviewer will ask, and the answer has to outlive the process
   that decided it.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import IntegrityError as DbIntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import Provider, RequestStatus, SourceTier, UserRole
from aer.core.schemas.injection import Finding, InjectionSignal
from aer.db.models import Artefact, AuditEvent, ResearchRequest, SourceDocument, User
from aer.errors import IntegrityError, ValidationError
from aer.services.artefacts import store_artefact, store_artefact_stream, verify_artefact
from aer.services.injection import record_findings
from aer.services.sources import (
    NO_PUBLICATION_DATE,
    NOT_CITABLE,
    decide_quarantine,
    list_quarantined,
    record_source_document,
)
from aer.storage.local import LocalArtefactStore

pytestmark = pytest.mark.integration

FILING = b"<html>Annual Report 2026. Revenue was 1,234.</html>"
FILING_SHA256 = hashlib.sha256(FILING).hexdigest()


@pytest.fixture
def store(tmp_path) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=4096)


@pytest.fixture
async def request_row(db_session) -> ResearchRequest:
    user = User(email="sources@example.invalid", display_name="Sources", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    row = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2026, 6, 30),
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


@pytest.fixture
async def artefact(db_session, store) -> Artefact:
    record = await store_artefact(db_session, store, data=FILING, media_type="text/html")
    return record.artefact


class TestStoringArtefacts:
    async def test_it_creates_one_row_with_the_content_address(self, db_session, store):
        record = await store_artefact(db_session, store, data=FILING, media_type="text/html")

        assert record.was_new is True
        assert record.artefact.sha256 == FILING_SHA256
        assert record.artefact.size_bytes == len(FILING)
        assert record.artefact.media_type == "text/html"

    async def test_the_storage_key_is_relative(self, db_session, store):
        # Absolute would tie every row to this machine's directory layout.
        record = await store_artefact(db_session, store, data=FILING)

        assert not record.artefact.storage_key.startswith("/")
        assert (store.root / record.artefact.storage_key).is_file()

    async def test_storing_the_same_bytes_twice_reuses_the_row(self, db_session, store):
        first = await store_artefact(db_session, store, data=FILING)
        second = await store_artefact(db_session, store, data=FILING)

        assert second.was_new is False
        assert second.artefact.id == first.artefact.id

        # Counted for *this* digest rather than over the whole table. The claim is that
        # identical bytes deduplicate, which is a statement about one row; asking whether
        # the artefacts table holds exactly one row is a statement about every test that
        # ever committed, and a committed row left behind by another file used to fail it.
        rows = (
            await db_session.scalars(select(Artefact).where(Artefact.sha256 == FILING_SHA256))
        ).all()
        assert len(rows) == 1

    async def test_a_stream_stores_the_same_artefact(self, db_session, store):
        async def chunks():
            yield FILING[:20]
            yield FILING[20:]

        record = await store_artefact_stream(db_session, store, chunks=chunks())

        assert record.artefact.sha256 == FILING_SHA256

    async def test_an_oversized_payload_creates_no_row(self, db_session, store):
        # The count either side, not the table's emptiness: "this refusal wrote nothing" is
        # what the test means, and it stays true whatever else the database happens to hold.
        before = await db_session.scalar(select(func.count()).select_from(Artefact))

        with pytest.raises(ValidationError):
            await store_artefact(db_session, store, data=b"x" * (store.max_bytes + 1))

        assert await db_session.scalar(select(func.count()).select_from(Artefact)) == before


class TestConcurrentStores:
    """Ten writers, one row.

    Needs real committed transactions on separate connections, so these cannot use
    ``db_session`` — its whole design is a single connection inside one rolled-back
    transaction, which is the opposite of what a race needs.
    """

    @pytest.fixture
    async def committed_sessions(self, db_engine):
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with db_engine.begin() as connection:
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            await connection.execute(
                text("TRUNCATE source_documents, artefacts RESTART IDENTITY CASCADE")
            )
        yield factory
        async with db_engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE source_documents, artefacts RESTART IDENTITY CASCADE")
            )

    async def test_ten_simultaneous_stores_create_exactly_one_row(self, committed_sessions, store):
        # Two adapters fetching the same filing at once is ordinary, not exotic. This
        # asserts the *outcome*: one row, one file, and every caller holding the same
        # artefact — whichever way the interleaving happens to fall on the day. The
        # specific lost-race branch is forced deterministically in the test below,
        # because in practice these tasks usually serialise and never reach it.
        async def store_once():
            async with committed_sessions() as session:
                record = await store_artefact(session, store, data=FILING)
                await session.commit()
                return record.artefact.id, record.was_new

        results = await asyncio.gather(*(store_once() for _ in range(10)))

        identifiers = {artefact_id for artefact_id, _ in results}
        assert len(identifiers) == 1, "every writer must end up with the same artefact"
        assert sum(1 for _, was_new in results if was_new) == 1, "exactly one creates it"

        async with committed_sessions() as session:
            rows = (await session.scalars(select(Artefact))).all()
        assert len(rows) == 1

    @staticmethod
    def _with_stale_first_lookup(session, monkeypatch) -> None:
        """Make this session's first lookup believe the artefact is absent.

        The interleaving the savepoint exists for is: this session looked, found nothing,
        and *then* another writer committed the same digest. Reproducing that timing with
        sleeps is slow and unreliable — the tasks normally serialise and the branch is
        never reached. Forcing the one lookup that matters is exact, and everything after
        it (the insert, the constraint violation, the recovery) is the real code path.
        """
        original = session.scalar
        seen = {"count": 0}

        async def stale_first_lookup(statement, *args, **kwargs):
            seen["count"] += 1
            if seen["count"] == 1:
                return None
            return await original(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", stale_first_lookup)

    async def test_losing_the_insert_race_returns_the_winners_row(
        self, committed_sessions, store, monkeypatch
    ):
        async with committed_sessions() as winner:
            created = await store_artefact(winner, store, data=FILING)
            await winner.commit()
            winner_id = created.artefact.id

        async with committed_sessions() as loser:
            self._with_stale_first_lookup(loser, monkeypatch)

            record = await store_artefact(loser, store, data=FILING)

            assert record.was_new is False
            assert record.artefact.id == winner_id

    async def test_a_lost_race_leaves_the_caller_transaction_usable(
        self, committed_sessions, store, monkeypatch
    ):
        # The reason the insert is flushed inside a savepoint. Without one the failed
        # statement aborts the whole transaction, so the loser could not go on to write
        # the source document that was the entire point of storing the artefact.
        async with committed_sessions() as winner:
            await store_artefact(winner, store, data=FILING)
            await winner.commit()

        async with committed_sessions() as loser:
            self._with_stale_first_lookup(loser, monkeypatch)
            await store_artefact(loser, store, data=FILING)

            user = User(
                email="after-race@example.invalid", display_name="After", role=UserRole.OWNER
            )
            loser.add(user)
            await loser.commit()

            assert user.id is not None
            await loser.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
            await loser.commit()


class TestArtefactImmutability:
    async def test_the_database_refuses_an_update(self, db_session, artefact):
        # The rule that matters, enforced where it holds against every writer rather than
        # only against this application. A row whose sha256 could be edited to point at
        # different bytes would make every citation verifying against it a lie.
        with pytest.raises(DBAPIError, match="immutable"):
            await db_session.execute(
                text("UPDATE artefacts SET media_type = 'text/plain' WHERE id = :id"),
                {"id": artefact.id},
            )
        await db_session.rollback()

    async def test_even_changing_the_digest_is_refused(self, db_session, artefact):
        with pytest.raises(DBAPIError, match="immutable"):
            await db_session.execute(
                text("UPDATE artefacts SET sha256 = :new WHERE id = :id"),
                {"new": "0" * 64, "id": artefact.id},
            )
        await db_session.rollback()

    async def test_an_empty_artefact_is_refused(self, db_session):
        # Every empty file hashes to the same digest, so one would deduplicate against
        # every other, and a citation pointing at "the empty artefact" would verify
        # against nothing. It is almost always a failed fetch stored anyway.
        db_session.add(
            Artefact(
                sha256=hashlib.sha256(b"").hexdigest(),
                media_type="text/html",
                size_bytes=0,
                storage_backend="local",
                storage_key="e3/b0/empty",
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_an_uppercase_digest_is_refused(self, db_session):
        db_session.add(
            Artefact(
                sha256=FILING_SHA256.upper(),
                media_type="text/html",
                size_bytes=10,
                storage_backend="local",
                storage_key="AA/BB/CC",
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_the_same_digest_cannot_be_inserted_twice(self, db_session, artefact):
        db_session.add(
            Artefact(
                sha256=artefact.sha256,
                media_type="text/plain",
                size_bytes=99,
                storage_backend="local",
                storage_key="xx/yy/zz",
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestQuarantineRule:
    """The rule alone, with no database. Pure input to pure output."""

    def test_a_dated_source_passes_under_point_in_time(self):
        decision = decide_quarantine(
            publication_date=date(2026, 5, 1),
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
        )
        assert decision.quarantined is False
        assert decision.reason is None

    def test_an_undated_source_is_quarantined_under_point_in_time(self):
        decision = decide_quarantine(
            publication_date=None,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
        )
        assert decision.quarantined is True
        assert decision.reason == NO_PUBLICATION_DATE

    def test_an_undated_source_passes_when_point_in_time_is_off(self):
        # Turning point-in-time off is the operator saying they accept look-ahead risk.
        # The rule exists to enforce their choice, not to override it.
        decision = decide_quarantine(
            publication_date=None,
            point_in_time=False,
            source_tier=SourceTier.T2_ISSUER,
        )
        assert decision.quarantined is False

    def test_an_uncitable_tier_is_quarantined_whatever_its_date(self):
        for point_in_time in (True, False):
            decision = decide_quarantine(
                publication_date=date(2026, 1, 1),
                point_in_time=point_in_time,
                source_tier=SourceTier.T6_UNVERIFIED,
            )
            assert decision.quarantined is True
            assert decision.reason == NOT_CITABLE

    def test_the_actionable_reason_is_reported_first(self):
        # Both rules apply. "No publication date" is the one the operator can fix, so it
        # is the one worth telling them about.
        decision = decide_quarantine(
            publication_date=None,
            point_in_time=True,
            source_tier=SourceTier.T6_UNVERIFIED,
        )
        assert decision.reason == NO_PUBLICATION_DATE

    @pytest.mark.parametrize(
        "tier",
        [
            SourceTier.T1_REGULATORY,
            SourceTier.T2_ISSUER,
            SourceTier.T3_OFFICIAL_STATS,
            SourceTier.T4_LICENSED_MARKET,
            SourceTier.T5_SECONDARY,
        ],
    )
    def test_every_citable_tier_passes_when_dated(self, tier):
        decision = decide_quarantine(
            publication_date=date(2026, 1, 1), point_in_time=True, source_tier=tier
        )
        assert decision.quarantined is False


class TestSourceTierOrdering:
    def test_rank_follows_the_tier_number(self):
        # Conflict resolution prefers the lower number, so this ordering is load-bearing
        # rather than cosmetic.
        ranks = [tier.rank for tier in SourceTier]
        assert ranks == sorted(ranks)
        assert SourceTier.T1_REGULATORY.rank < SourceTier.T5_SECONDARY.rank

    def test_only_the_first_two_tiers_are_primary(self):
        primary = {tier for tier in SourceTier if tier.is_primary}
        assert primary == {SourceTier.T1_REGULATORY, SourceTier.T2_ISSUER}

    def test_only_the_last_tier_is_uncitable(self):
        uncitable = {tier for tier in SourceTier if not tier.is_citable}
        assert uncitable == {SourceTier.T6_UNVERIFIED}


class TestRecordingSources:
    async def test_it_records_the_full_provenance(self, db_session, request_row, artefact):
        document = await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/Archives/edgar/data/789019/000156459026000123/msft-10k.htm",
            canonical_url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
            title="Microsoft Corporation 10-K",
            publisher="U.S. Securities and Exchange Commission",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            # Before the request's as-of date of 2026-06-30. It was 2026-07-30 until task 15,
            # a month *after* it, and this test asserted the document was admissible — because
            # at the time nothing compared the two. That is the look-ahead hole the task closed,
            # and this fixture was sitting in it.
            publication_date=date(2026, 6, 12),
            publication_date_confidence=1.0,
            http_status=200,
            licence_note="US government work, public domain",
            robots_allowed=True,
        )

        assert document.provider is Provider.SEC_EDGAR
        assert document.source_tier is SourceTier.T1_REGULATORY
        assert document.artefact_id == artefact.id
        assert document.request_id == request_row.id
        assert document.quarantined is False
        assert document.robots_allowed is True
        assert document.licence_note

    async def test_the_same_artefact_recorded_twice_returns_the_first_row(
        self, db_session, request_row, artefact
    ):
        """Gap C4. The live run held two source rows for one digest of its own 10-Q: the
        A43 pre-read closed the sequential duplicate, but the parallel research nodes
        each hold their own session, and neither sees the other's uncommitted insert.
        The (request_id, artefact_id) constraint is the arbiter, and losing the insert
        means the row exists — so the answer is that row, not an error and not a twin."""
        first = await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/Archives/edgar/data/789019/msft-10q.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2026, 6, 12),
        )
        # A different URL form and a different tier: the race's realistic shape, one
        # worker fetching a URL variant of what the acquire step already recorded.
        second = await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/Archives/edgar/data/789019/msft-10q.htm?variant=1",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T5_SECONDARY,
            publication_date=None,
        )

        assert second.id == first.id
        assert second.source_tier is SourceTier.T1_REGULATORY
        held = await db_session.scalars(
            select(SourceDocument).where(
                SourceDocument.request_id == request_row.id,
                SourceDocument.artefact_id == artefact.id,
            )
        )
        assert len(list(held)) == 1

    async def test_an_undated_source_is_auto_quarantined(self, db_session, request_row, artefact):
        assert request_row.point_in_time is True

        document = await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://example.invalid/undated-note",
            provider=Provider.WEB_SEARCH,
            source_tier=SourceTier.T5_SECONDARY,
            publication_date=None,
        )

        assert document.quarantined is True
        assert document.quarantine_reason == NO_PUBLICATION_DATE

    async def test_the_quarantine_is_recorded_in_the_audit_log(
        self, db_session, request_row, artefact
    ):
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://example.invalid/undated-note",
            provider=Provider.WEB_SEARCH,
            source_tier=SourceTier.T5_SECONDARY,
        )

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "source.quarantined")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["reason"] == NO_PUBLICATION_DATE
        assert event.request_id == request_row.id
        assert event.this_hash

    async def test_an_admissible_source_writes_no_quarantine_event(
        self, db_session, request_row, artefact
    ):
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/filing.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2026, 5, 1),
        )

        events = (
            await db_session.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "source.quarantined")
            )
        ).all()
        assert events == []

    async def test_the_undated_source_is_kept_not_discarded(
        self, db_session, request_row, artefact
    ):
        # Losing it would erase the record of what the run looked at. "We saw this and
        # refused to use it" is a more useful trail than silence.
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://example.invalid/undated",
            provider=Provider.WEB_SEARCH,
            source_tier=SourceTier.T5_SECONDARY,
        )

        stored = (await db_session.scalars(select(SourceDocument))).all()
        assert len(stored) == 1

    async def test_quarantined_sources_can_be_listed(self, db_session, request_row, artefact):
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://example.invalid/a",
            provider=Provider.WEB_SEARCH,
            source_tier=SourceTier.T5_SECONDARY,
        )
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/b",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2026, 5, 1),
        )

        quarantined = await list_quarantined(db_session, request_id=request_row.id)

        assert [doc.url for doc in quarantined] == ["https://example.invalid/a"]

    async def test_a_naive_retrieved_at_is_refused(self, db_session, request_row, artefact):
        # A provenance timestamp without an offset is ambiguous by up to a day, which is
        # exactly the precision a point-in-time decision turns on.
        with pytest.raises(ValidationError, match="timezone-aware"):
            await record_source_document(
                db_session,
                request=request_row,
                artefact=artefact,
                url="https://example.invalid/naive",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=datetime(2026, 7, 1, 12, 0),  # noqa: DTZ001 -- the point of the test
            )

    async def test_the_same_url_with_changed_content_is_two_records(
        self, db_session, request_row, artefact, store
    ):
        # The rationale for re-acquisition was always that the content may have changed,
        # and that change is worth recording. Changed content is a different digest —
        # a different artefact — so both records stand. Identical bytes re-fetched later
        # are the same evidence and merge into one record (gap C4, the test below).
        changed = await store_artefact(
            db_session, store, data=FILING + b" amended", media_type="text/html"
        )
        for moment, art in (
            (datetime(2026, 7, 1, tzinfo=UTC), artefact),
            (datetime(2026, 7, 2, tzinfo=UTC), changed.artefact),
        ):
            await record_source_document(
                db_session,
                request=request_row,
                artefact=art,
                url="https://www.sec.gov/same.htm",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                publication_date=date(2026, 5, 1),
                retrieved_at=moment,
            )

        stored = (await db_session.scalars(select(SourceDocument))).all()
        assert len(stored) == 2

    async def test_the_identical_acquisition_recorded_twice_is_one_record(
        self, db_session, request_row, artefact
    ):
        # Same request, same URL, same bytes: one acquisition written down twice would
        # double-count the same evidence. Until gap C4 this raised; now the second write
        # is answered with the record the first one made, because "you already hold
        # this" is information a caller can use and an error is not.
        moment = datetime(2026, 7, 1, tzinfo=UTC)

        async def record() -> SourceDocument:
            return await record_source_document(
                db_session,
                request=request_row,
                artefact=artefact,
                url="https://www.sec.gov/same.htm",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                publication_date=date(2026, 5, 1),
                retrieved_at=moment,
            )

        first = await record()
        second = await record()

        assert second.id == first.id
        stored = (await db_session.scalars(select(SourceDocument))).all()
        assert len(stored) == 1


class TestSourceDocumentConstraints:
    async def test_a_quarantine_without_a_reason_is_refused(
        self, db_session, request_row, artefact
    ):
        # A flag nobody can act on. The database refuses the state rather than trusting
        # every writer to fill both fields.
        db_session.add(
            SourceDocument(
                request_id=request_row.id,
                artefact_id=artefact.id,
                url="https://example.invalid/x",
                provider=Provider.WEB_SEARCH,
                source_tier=SourceTier.T5_SECONDARY,
                retrieved_at=datetime.now(UTC),
                quarantined=True,
                quarantine_reason=None,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_a_reason_without_a_quarantine_is_refused(
        self, db_session, request_row, artefact
    ):
        db_session.add(
            SourceDocument(
                request_id=request_row.id,
                artefact_id=artefact.id,
                url="https://example.invalid/y",
                provider=Provider.WEB_SEARCH,
                source_tier=SourceTier.T5_SECONDARY,
                retrieved_at=datetime.now(UTC),
                quarantined=False,
                quarantine_reason="a note that does nothing",
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_a_confidence_outside_zero_to_one_is_refused(
        self, db_session, request_row, artefact
    ):
        db_session.add(
            SourceDocument(
                request_id=request_row.id,
                artefact_id=artefact.id,
                url="https://example.invalid/z",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=datetime.now(UTC),
                publication_date=date(2026, 1, 1),
                publication_date_confidence=1.5,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_an_artefact_still_cited_cannot_be_deleted(
        self, db_session, request_row, artefact
    ):
        # ON DELETE RESTRICT. Retention is a legitimate operation, but removing the bytes
        # a provenance record points at would leave that record describing nothing.
        await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/cited.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2026, 5, 1),
        )

        with pytest.raises(DbIntegrityError, match="violates foreign key constraint"):
            await db_session.execute(
                text("DELETE FROM artefacts WHERE id = :id"), {"id": artefact.id}
            )
        await db_session.rollback()


class TestVerifyingThroughTheService:
    async def test_an_intact_artefact_verifies(self, db_session, store, artefact):
        assert await verify_artefact(db_session, store, sha256=artefact.sha256) == len(FILING)

    async def test_a_failure_is_audited_before_it_is_raised(self, db_session, store, artefact):
        # An integrity failure is exactly the event a later investigation needs to find,
        # and an exception that only reaches a log line is lost on the next restart.
        store.path_for(artefact.sha256).write_bytes(b"tampered")

        with pytest.raises(IntegrityError):
            await verify_artefact(db_session, store, sha256=artefact.sha256, actor="nightly-check")

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "artefact.integrity_failed")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["sha256"] == artefact.sha256
        assert event.actor == "nightly-check"


class TestInformationalFindingsDoNotFlag:
    """Polish P9: the badge follows the findings that mean something.

    Inline XBRL's hidden facts are recorded for the reviewer but marked informational by
    the scanner; a document carrying only those must not light the injection badge — a
    badge on every clean filing is a badge nobody reads on the day one matters.
    """

    @staticmethod
    async def _document(db_session, request_row, artefact):
        return await record_source_document(
            db_session,
            request=request_row,
            artefact=artefact,
            url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=date(2026, 6, 12),
        )

    async def test_informational_only_findings_store_without_flagging(
        self, db_session, request_row, artefact
    ):
        document = await self._document(db_session, request_row, artefact)

        recorded = await record_findings(
            db_session,
            document=document,
            findings=(
                Finding.of(InjectionSignal.HIDDEN_TEXT, detail="inline XBRL header").model_copy(
                    update={"informational": True}
                ),
            ),
        )

        assert recorded.injection_flagged is False
        assert recorded.injection_findings, "the reviewer still sees the record"

    async def test_one_full_weight_finding_flags_as_before(self, db_session, request_row, artefact):
        document = await self._document(db_session, request_row, artefact)

        recorded = await record_findings(
            db_session,
            document=document,
            findings=(
                Finding.of(InjectionSignal.HIDDEN_TEXT, detail="inline XBRL header").model_copy(
                    update={"informational": True}
                ),
                Finding.of(InjectionSignal.INSTRUCTION_OVERRIDE, detail="asks to be obeyed"),
            ),
        )

        assert recorded.injection_flagged is True
