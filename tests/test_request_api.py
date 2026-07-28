"""The research request API, against a real database.

The rules themselves are covered in ``test_request_validation.py`` and
``test_universe.py``. What is tested here is that they are actually *reached* — a
validation rule the endpoint never calls is a rule that does not exist — plus the things
only an integration test can see: what is persisted, what is returned, and who is allowed
to read it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import RequestStatus, UserRole
from aer.db.models import AuditEvent, Job, ResearchRequest, User
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
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text("TRUNCATE research_requests, audit_events, users RESTART IDENTITY CASCADE")
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


class TestCreate:
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
