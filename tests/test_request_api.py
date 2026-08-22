"""The research request API, against a real database.

The rules themselves are covered in ``test_request_validation.py`` and
``test_universe.py``. What is tested here is that they are actually *reached* — a
validation rule the endpoint never calls is a rule that does not exist — plus the things
only an integration test can see: what is persisted, what is returned, and who is allowed
to read it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import JobStatus, Provider, RequestStatus, SourceTier, UserRole
from aer.db.models import Artefact, AuditEvent, Job, ResearchRequest, SourceDocument, User
from aer.services import runs as run_service
from tests.api_fixtures import build_app, client_for

pytestmark = pytest.mark.integration

ENDPOINT = "/api/requests"


def payload(**overrides):
    body = {
        "company_name": "Microsoft Corporation",
        "ticker": "msft",
        "exchange": "nasdaq",
        "isin": "US5949181045",
        "as_of_date": "2026-07-01",
        "base_currency": "USD",
        "investment_horizon_months": 36,
        "max_cost_gbp": "2.00",
        "portfolio_context": {
            "current_weight": "0.025",
            "maximum_weight": "0.05",
            "benchmark": "MSCI World",
        },
        "focus_questions": ["How durable is the Azure gross margin?"],
        "excluded_sources": ["https://www.seekingalpha.com/article/1"],
    }
    body.update(overrides)
    return body


@pytest.fixture
async def clean_slate(db_engine):
    """Empty the request tables before each test.

    The application under test commits for real, so unlike ``db_session`` its writes
    survive the test that made them. Without this, a test's result would depend on which
    tests ran before it — the kind of failure you end up debugging in the wrong file.

    Truncated at setup rather than teardown: it is what the *next* test needs, and doing
    it here cannot contend with a transaction a finished test still has open. The
    statement timeout turns a lock conflict into a fast, readable failure instead of a
    suite that hangs.

    **``artefacts`` is on the list because nothing cascades to it.** Source documents go
    when their request does, but an artefact is content-addressed and belongs to no
    request, so `_leave_evidence_behind` used to commit a row here that outlived the whole
    file — and `tests/test_source_documents.py` asserts on the artefact table as a whole.
    Alphabetically this file runs first and that one runs later, with enough between them
    that something else truncated in the gap; a shuffled ordering removed the gap and both
    of its tests failed. Setup-time truncation cleans up after the *previous* test, so a
    table this file writes to and does not name is a table that leaks.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE research_requests, audit_events, users, artefacts "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def seeded_user(clean_slate, db_engine):
    """One user, committed, so the application's own session can see it."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email="api-tests@example.invalid",
            display_name="API tests",
            role=UserRole.OWNER,
        )
        session.add(user)
        await session.commit()
        return user


@pytest.fixture
async def api(api_settings, db_engine, fake_redis, seeded_user):
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def _start_a_run(engine, request_id: str) -> None:
    """Give a request a job, the way starting a run would.

    Written directly rather than through the run API because what is being tested is the
    consequence of a job existing, not how it came to.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        request = await session.get(ResearchRequest, uuid.UUID(request_id))
        assert request is not None
        await run_service.start_run(session, request=request)
        await session.commit()


async def _finish_the_run(engine, request_id: str) -> None:
    """Bring a request's run to a terminal state, the way a completed run would.

    A purge refuses while a worker might still be writing, so a test about *removing* a
    researched request has to research it and then let the run finish.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        job = await session.scalar(
            select(Job).where(Job.request_id == uuid.UUID(request_id)).order_by(Job.id.desc())
        )
        assert job is not None
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        await session.commit()


async def _leave_evidence(engine, request_id: str) -> None:
    """Give a request a gathered source document.

    What makes `delete_request` refuse is not that a run happened but that it left
    something behind, so a test contrasting the safe deletion with the destructive one has
    to leave something behind.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        job = await session.scalar(
            select(Job).where(Job.request_id == uuid.UUID(request_id)).order_by(Job.id.desc())
        )
        artefact = Artefact(
            sha256="e" * 64, media_type="text/html", size_bytes=11, storage_key="ee/e"
        )
        session.add(artefact)
        await session.flush()
        session.add(
            SourceDocument(
                work_order_id=uuid.UUID(request_id),
                request_id=uuid.UUID(request_id),
                job_id=job.id if job is not None else None,
                artefact_id=artefact.id,
                url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=datetime.now(UTC),
            )
        )
        await session.commit()


class TestCreate:
    async def test_the_service_creates_the_work_order_the_request_hangs_off(self, api, db_engine):
        """The production path builds the run root itself.

        `tests/conftest.py` gives hand-built request rows a work order, because seventy
        fixtures construct one directly and each was written to test what happens after a
        request exists. That helper must not be able to stand in for this: if
        `create_request` stopped writing the work order, every one of those fixtures would
        go on passing and only production would be broken.

        So this asserts the row is there *and* that it carries the mandate's own values —
        a work order the helper minted from a half-built request would not (ADR 0068).
        """
        created = (await api.post(ENDPOINT, json=payload(max_cost_gbp="1.25"))).json()

        async with db_engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.text(
                        "SELECT tool, subject_kind, as_of_date, point_in_time, max_cost_gbp "
                        "FROM work_orders WHERE id = :id"
                    ),
                    {"id": created["id"]},
                )
            ).one()

        assert row.tool == "research"
        assert row.subject_kind == "company"
        assert row.point_in_time is True
        assert str(row.max_cost_gbp) == "1.25"
        assert row.as_of_date.isoformat() == created["as_of_date"]

    async def test_returns_201_with_a_location_header(self, api):
        response = await api.post(ENDPOINT, json=payload())

        assert response.status_code == 201
        body = response.json()
        assert response.headers["location"] == f"{ENDPOINT}/{body['id']}"

    async def test_the_created_record_is_readable_at_that_location(self, api):
        created = await api.post(ENDPOINT, json=payload())
        fetched = await api.get(created.headers["location"])

        assert fetched.status_code == 200
        assert fetched.json() == created.json()

    async def test_it_starts_as_an_unresolved_draft(self, api):
        body = (await api.post(ENDPOINT, json=payload())).json()

        assert body["status"] == RequestStatus.DRAFT.value
        # No outbound call is made while a request is written, so the identity is
        # unverified by construction. Anything downstream needs to be able to tell.
        assert body["resolved"] is False

    async def test_input_is_normalised_on_the_way_in(self, api):
        body = (await api.post(ENDPOINT, json=payload())).json()

        assert body["ticker"] == "MSFT"
        assert body["exchange"] == "NASDAQ"
        assert body["excluded_sources"] == ["seekingalpha.com"]

    async def test_weights_survive_the_round_trip_exactly(self, api):
        # Through JSONB and back. A weight that changes in the third decimal place because
        # it passed through a float is a number that cannot be reconciled.
        body = (await api.post(ENDPOINT, json=payload())).json()

        assert body["portfolio_context"]["current_weight"] == "0.025"
        assert body["portfolio_context"]["maximum_weight"] == "0.05"

    async def test_creation_is_recorded_in_the_audit_log(self, api, db_session):
        body = (await api.post(ENDPOINT, json=payload())).json()

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.created")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["request_id"] == body["id"]
        assert event.this_hash

    async def test_no_job_is_started(self, api, db_session):
        # A request is a draft. Nothing may be spent until a plan has been approved, and
        # the cheapest way to guarantee that now is for nothing to be queued at all.
        before = await db_session.scalar(select(Job).limit(1))
        await api.post(ENDPOINT, json=payload())
        after = await db_session.scalar(select(Job).limit(1))

        assert before is None
        assert after is None


class TestValidationIsReached:
    async def test_a_future_as_of_date_is_rejected(self, api):
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        response = await api.post(ENDPOINT, json=payload(as_of_date=tomorrow))

        assert response.status_code == 422
        problems = response.json()["context"]["problems"]
        assert [p["field"] for p in problems] == ["as_of_date"]

    async def test_a_cost_above_the_per_run_budget_is_rejected(self, api):
        response = await api.post(ENDPOINT, json=payload(max_cost_gbp="999.00"))

        assert response.status_code == 422
        assert response.json()["context"]["problems"][0]["field"] == "max_cost_gbp"

    async def test_an_etf_is_rejected_by_rule(self, api):
        response = await api.post(
            ENDPOINT,
            json=payload(ticker="SPY", company_name="SPDR S&P 500 ETF Trust", exchange="NYSE"),
        )

        assert response.status_code == 422
        problem = response.json()["context"]["problems"][0]
        assert problem["code"] == "exchange_traded_fund"
        assert problem["field"] == "ticker"

    async def test_an_unsupported_exchange_is_rejected_by_rule(self, api):
        response = await api.post(ENDPOINT, json=payload(exchange="TSX"))

        problem = response.json()["context"]["problems"][0]
        assert problem["code"] == "unsupported_exchange"
        assert problem["field"] == "exchange"

    async def test_an_otc_venue_is_rejected_by_its_own_rule(self, api):
        response = await api.post(ENDPOINT, json=payload(exchange="OTCQB"))

        assert response.json()["context"]["problems"][0]["code"] == "otc_venue"

    async def test_every_problem_is_reported_at_once(self, api):
        response = await api.post(
            ENDPOINT,
            json=payload(
                ticker="SPY",
                company_name="SPDR S&P 500 ETF Trust",
                exchange="NYSE",
                as_of_date="2099-01-01",
                max_cost_gbp="999.00",
            ),
        )

        fields = {p["field"] for p in response.json()["context"]["problems"]}
        assert fields == {"as_of_date", "max_cost_gbp", "ticker"}

    async def test_a_malformed_ticker_is_a_schema_error(self, api):
        response = await api.post(ENDPOINT, json=payload(ticker="NOT A TICKER!"))

        assert response.status_code == 422
        assert response.json()["code"] == "request_validation_error"

    async def test_nothing_is_persisted_when_validation_fails(self, api, db_session):
        await api.post(ENDPOINT, json=payload(ticker="SPY", company_name="SPDR ETF Trust"))

        found = await db_session.scalar(
            select(ResearchRequest).where(ResearchRequest.ticker == "SPY")
        )
        assert found is None

    async def test_an_unknown_field_is_refused(self, api):
        # extra="forbid". A client sending `rating: "BUY"` must fail rather than have it
        # silently dropped and assume the rating took effect.
        response = await api.post(ENDPOINT, json=payload(rating="BUY"))
        assert response.status_code == 422


class TestEdit:
    """A draft is a note to self. It stops being one the moment a run exists."""

    async def test_a_draft_can_be_replaced(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()

        response = await api.put(
            f"{ENDPOINT}/{created['id']}",
            json=payload(ticker="AAPL", company_name="Apple Inc."),
        )

        assert response.status_code == 200
        assert response.json()["ticker"] == "AAPL"
        assert (await api.get(f"{ENDPOINT}/{created['id']}")).json()["ticker"] == "AAPL"

    async def test_the_same_rules_apply_as_at_creation(self, api):
        # The edit path calls the same validator. A rule that only creation enforces is a
        # rule anyone can get around by creating a valid request and then editing it.
        created = (await api.post(ENDPOINT, json=payload())).json()

        response = await api.put(f"{ENDPOINT}/{created['id']}", json=payload(exchange="TSX"))

        assert response.status_code == 422
        assert response.json()["context"]["problems"][0]["code"] == "unsupported_exchange"

    async def test_a_rejected_edit_changes_nothing(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()

        await api.put(f"{ENDPOINT}/{created['id']}", json=payload(max_cost_gbp="999.00"))

        assert (await api.get(f"{ENDPOINT}/{created['id']}")).json() == created

    async def test_editing_a_request_with_a_run_is_a_409(self, api, db_engine):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])

        response = await api.put(f"{ENDPOINT}/{created['id']}", json=payload(ticker="AAPL"))

        assert response.status_code == 409
        assert response.json()["code"] == "conflict"
        # Not 422: the body was never the problem, so resubmitting it would not help.
        assert (await api.get(f"{ENDPOINT}/{created['id']}")).json()["ticker"] == "MSFT"

    async def test_the_edit_is_recorded_with_before_and_after(self, api, db_session):
        created = (await api.post(ENDPOINT, json=payload())).json()

        await api.put(f"{ENDPOINT}/{created['id']}", json=payload(ticker="AAPL"))

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.edited")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        # Before and after, not just the field names: the row itself only remembers the
        # after, so a log entry saying "ticker changed" answers nothing months later.
        assert event.payload["changes"]["ticker"] == ["MSFT", "AAPL"]

    async def test_only_the_fields_that_changed_are_recorded(self, api, db_session):
        created = (await api.post(ENDPOINT, json=payload())).json()

        await api.put(f"{ENDPOINT}/{created['id']}", json=payload(max_cost_gbp="1.50"))

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.edited")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert list(event.payload["changes"]) == ["max_cost_gbp"]
        # A string, never a number. JSON has no decimal type, and a cost ceiling that comes
        # back as 1.4999999999999998 in the audit trail is worse than useless.
        assert event.payload["changes"]["max_cost_gbp"] == ["2.00", "1.50"]

    async def test_an_unknown_field_is_refused(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()
        response = await api.put(f"{ENDPOINT}/{created['id']}", json=payload(rating="BUY"))
        assert response.status_code == 422

    async def test_editing_an_unknown_request_is_a_404(self, api):
        response = await api.put(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000", json=payload())
        assert response.status_code == 404


class TestDelete:
    async def test_a_draft_can_be_deleted(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()

        response = await api.delete(f"{ENDPOINT}/{created['id']}")

        assert response.status_code == 204
        assert (await api.get(f"{ENDPOINT}/{created['id']}")).status_code == 404

    async def test_deleting_a_request_with_a_run_is_refused(self, api, db_engine, db_session):
        # The guard, not a convenience. `research_requests` cascades to jobs, plans, sources
        # and reports, so a delete allowed here would take the evidence with it.
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])

        response = await api.delete(f"{ENDPOINT}/{created['id']}")

        assert response.status_code == 409
        still_there = await db_session.get(ResearchRequest, uuid.UUID(created["id"]))
        assert still_there is not None

    async def test_the_deletion_outlives_the_row(self, api, db_session):
        created = (await api.post(ENDPOINT, json=payload())).json()

        await api.delete(f"{ENDPOINT}/{created['id']}")

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.deleted")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.request_id == uuid.UUID(created["id"])
        # Enough to say what was removed. `audit_events.request_id` is deliberately not a
        # foreign key, so the record survives the thing it describes.
        assert event.payload["ticker"] == "MSFT"

    async def test_deleting_an_unknown_request_is_a_404(self, api):
        response = await api.delete(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


class TestRead:
    async def test_listing_returns_the_most_recent_first(self, api):
        first = (await api.post(ENDPOINT, json=payload(ticker="AAA"))).json()
        second = (await api.post(ENDPOINT, json=payload(ticker="BBB"))).json()

        listed = (await api.get(ENDPOINT)).json()
        ids = [row["id"] for row in listed]

        assert ids.index(second["id"]) < ids.index(first["id"])

    async def test_a_missing_id_is_404_with_a_stable_code(self, api):
        response = await api.get(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404
        assert response.json()["code"] == "request_not_found"

    async def test_a_malformed_id_is_422_not_500(self, api):
        assert (await api.get(f"{ENDPOINT}/not-a-uuid")).status_code == 422

    async def test_another_users_request_is_not_readable(self, api, db_engine, seeded_user):
        # Scoped by owner even though the MVP has one user. An unscoped lookup is a
        # horizontal access-control bug the day a second user exists, and invisible until
        # then.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            other = User(
                email="someone-else@example.invalid", display_name="Other", role=UserRole.OWNER
            )
            session.add(other)
            await session.flush()
            theirs = ResearchRequest(
                user_id=other.id,
                company_name="Private Co",
                ticker="PRIV",
                exchange="LSE",
                as_of_date=datetime.now(UTC).date(),
                base_currency="GBP",
                investment_horizon_months=12,
                max_cost_gbp="1.00",
                portfolio_context={},
            )
            session.add(theirs)
            await session.commit()
            other_id = theirs.id

        try:
            response = await api.get(f"{ENDPOINT}/{other_id}")
            assert response.status_code == 404
        finally:
            async with factory() as session:
                await session.delete(await session.get(ResearchRequest, other_id))
                await session.delete(await session.get(User, other.id))
                await session.commit()

    async def test_pagination_bounds_are_enforced(self, api):
        assert (await api.get(f"{ENDPOINT}?limit=0")).status_code == 422
        assert (await api.get(f"{ENDPOINT}?limit=500")).status_code == 422
        assert (await api.get(f"{ENDPOINT}?offset=-1")).status_code == 422


class TestArchiveAndRestore:
    """The reversible removal, and the one thing `DELETE` could never offer: it works on a
    request that has been researched, because it destroys nothing."""

    async def test_a_request_with_a_run_can_be_archived(self, api, db_engine):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])

        response = await api.post(f"{ENDPOINT}/{created['id']}/archive")

        assert response.status_code == 200
        assert response.json()["archived_at"] is not None

    async def test_an_archived_request_leaves_the_default_listing(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await api.post(f"{ENDPOINT}/{created['id']}/archive")

        live = (await api.get(ENDPOINT)).json()
        archived = (await api.get(ENDPOINT, params={"archived": True})).json()

        assert created["id"] not in [row["id"] for row in live]
        assert created["id"] in [row["id"] for row in archived]

    async def test_it_is_still_readable_by_id(self, api):
        """Archived is out of the way, not gone. A bookmark still resolves."""
        created = (await api.post(ENDPOINT, json=payload())).json()
        await api.post(f"{ENDPOINT}/{created['id']}/archive")

        assert (await api.get(f"{ENDPOINT}/{created['id']}")).status_code == 200

    async def test_restoring_puts_it_back_on_the_list(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await api.post(f"{ENDPOINT}/{created['id']}/archive")

        response = await api.post(f"{ENDPOINT}/{created['id']}/restore")

        assert response.status_code == 200
        assert response.json()["archived_at"] is None
        assert created["id"] in [row["id"] for row in (await api.get(ENDPOINT)).json()]

    async def test_archiving_twice_is_a_409(self, api):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await api.post(f"{ENDPOINT}/{created['id']}/archive")

        response = await api.post(f"{ENDPOINT}/{created['id']}/archive")

        assert response.status_code == 409

    async def test_archiving_an_unknown_request_is_a_404(self, api):
        response = await api.post(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000/archive")
        assert response.status_code == 404


class TestPurge:
    """The destructive endpoint. Its own path rather than a flag on `DELETE`, so a caller
    that wanted the safe deletion cannot get this one by setting a query parameter."""

    async def test_it_removes_a_request_a_delete_would_refuse(self, api, db_engine, db_session):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])
        await _leave_evidence(db_engine, created["id"])
        await _finish_the_run(db_engine, created["id"])
        assert (await api.delete(f"{ENDPOINT}/{created['id']}")).status_code == 409

        response = await api.post(f"{ENDPOINT}/{created['id']}/purge")

        assert response.status_code == 200
        assert (await api.get(f"{ENDPOINT}/{created['id']}")).status_code == 404

    async def test_it_reports_what_it_removed(self, api, db_engine):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])
        await _finish_the_run(db_engine, created["id"])

        removed = (await api.post(f"{ENDPOINT}/{created['id']}/purge")).json()

        assert removed["jobs"] == 1

    async def test_the_preview_matches_and_removes_nothing(self, api, db_engine):
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])
        await _finish_the_run(db_engine, created["id"])

        preview = (await api.get(f"{ENDPOINT}/{created['id']}/removal-preview")).json()

        assert preview["jobs"] == 1
        assert (await api.get(f"{ENDPOINT}/{created['id']}")).status_code == 200
        assert (await api.post(f"{ENDPOINT}/{created['id']}/purge")).json() == preview

    async def test_a_live_run_is_a_409(self, api, db_engine, db_session):
        """Deleting rows a worker is writing to is a crash in the worker and a half-deleted
        request here. `_start_a_run` leaves the job queued, which is exactly that state."""
        created = (await api.post(ENDPOINT, json=payload())).json()
        await _start_a_run(db_engine, created["id"])

        response = await api.post(f"{ENDPOINT}/{created['id']}/purge")

        assert response.status_code == 409
        assert await db_session.get(ResearchRequest, uuid.UUID(created["id"])) is not None

    async def test_purging_an_unknown_request_is_a_404(self, api):
        response = await api.post(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000/purge")
        assert response.status_code == 404
