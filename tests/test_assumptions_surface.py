"""The assumptions surface: what it shows, what it hashes, and who may touch it.

The page and the JSON API are two renderings of one payload. That is not tidiness — the
confirm hash is taken over that payload, so a page showing one thing and hashing another
would make every confirmation meaningless in exactly the way the run gates already refuse.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import UserRole
from aer.db.models import Assumption, ResearchRequest, User
from aer.services import assumptions as assumption_service
from aer.services import scenarios as scenario_service
from aer.web.csrf import CSRF_FIELD_NAME
from tests.api_fixtures import build_app, client_for
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

DISCOUNT_RATE = "discount_rate"
PROPOSED_JUSTIFICATION = "CAPM with a 4.2% equity risk premium and a beta of 1.1."


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """A request with one proposed assumption, committed so the app's session sees it."""
    await _truncate(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        request = ResearchRequest(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
        )
        session.add(request)
        await session.flush()

        await assumption_service.propose(
            session,
            request_id=request.id,
            name=DISCOUNT_RATE,
            value=Decimal("0.09"),
            unit="pure",
            justification=PROPOSED_JUSTIFICATION,
            proposed_by="valuation_interpretation",
            confidence=0.7,
        )
        await session.commit()
        yield {"user": user, "request": request}
    await _truncate(db_engine)


@pytest.fixture
async def api(
    api_settings: Settings, db_engine: Any, fake_redis: Any, committed: dict[str, Any]
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def assumption_id(engine: Any, request_id: uuid.UUID) -> uuid.UUID:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        found = await session.scalar(select(Assumption).where(Assumption.request_id == request_id))
        assert found is not None
        return found.id


class TestTheJsonSurface:
    async def test_it_lists_the_assumption_with_its_reasoning(self, api, committed):
        body = (await api.get(f"/api/requests/{committed['request'].id}/assumptions")).json()

        assert len(body["assumptions"]) == 1
        row = body["assumptions"][0]
        assert row["name"] == DISCOUNT_RATE
        assert row["value"] == "0.090000000000"
        assert row["justification"] == PROPOSED_JUSTIFICATION

    async def test_it_says_how_many_are_still_waiting(self, api, committed):
        """The number that blocks a valuation, surfaced rather than left to be counted."""
        body = (await api.get(f"/api/requests/{committed['request'].id}/assumptions")).json()
        assert body["unconfirmed"] == 1

    async def test_a_value_is_a_string_so_the_hash_covers_what_was_shown(self, api, committed):
        """A JSON number would round a Decimal, and a hash over a rounded figure is a hash
        over something nobody displayed."""
        body = (await api.get(f"/api/requests/{committed['request'].id}/assumptions")).json()
        assert isinstance(body["assumptions"][0]["value"], str)

    async def test_confirming_makes_it_usable(self, api, committed, db_engine):
        request_id = committed["request"].id
        body = (await api.get(f"/api/requests/{request_id}/assumptions")).json()
        target = await assumption_id(db_engine, request_id)

        response = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/confirm",
            json={"payload_hash": body["payload_hash"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["unconfirmed"] == 0
        assert response.json()["assumptions"][0]["approved"] is True

    async def test_confirming_a_stale_page_is_refused(self, api, committed, db_engine):
        """The gate rule, applied to assumptions: agreeing to something else is not agreeing."""
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)

        response = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/confirm",
            json={"payload_hash": "0" * 64},
        )
        assert response.status_code == 422, response.text
        assert "changed after this page was rendered" in response.text

    async def test_amending_keeps_the_proposal_and_un_confirms(self, api, committed, db_engine):
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)
        body = (await api.get(f"/api/requests/{request_id}/assumptions")).json()

        await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/confirm",
            json={"payload_hash": body["payload_hash"]},
        )
        amended = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/amend",
            json={
                "value": "0.11",
                "justification": "The beta ignores the pending disposal of the US division.",
            },
        )

        assert amended.status_code == 200, amended.text
        assert amended.json()["assumptions"][0]["value"] == "0.110000000000"
        assert amended.json()["assumptions"][0]["approved"] is False
        assert amended.json()["unconfirmed"] == 1

    async def test_the_hash_after_an_amendment_matches_a_fresh_read(
        self, api, committed, db_engine
    ):
        """The defect this pins: a value assigned in Python is not the value the database
        stores. `NUMERIC(38,12)` returns twelve places, so an unrefreshed row hashes
        differently from the same row read back — and confirming what the page showed would
        be refused for a reason nobody could see."""
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)

        amended = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/amend",
            json={"value": "0.11", "justification": "Disposal not reflected in the beta."},
        )
        fresh = await api.get(f"/api/requests/{request_id}/assumptions")

        assert amended.json()["payload_hash"] == fresh.json()["payload_hash"]

    async def test_confirming_straight_after_amending_works(self, api, committed, db_engine):
        """The behaviour that hash mismatch would have broken, end to end."""
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)

        amended = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/amend",
            json={"value": "0.11", "justification": "Disposal not reflected in the beta."},
        )
        confirmed = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/confirm",
            json={"payload_hash": amended.json()["payload_hash"]},
        )

        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["unconfirmed"] == 0

    async def test_an_amendment_without_a_reason_is_refused(self, api, committed, db_engine):
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)

        response = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/amend",
            json={"value": "0.11", "justification": ""},
        )
        assert response.status_code == 422

    async def test_another_operators_assumptions_are_not_readable(self, api, committed, db_engine):
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stranger = User(
                email="stranger@example.invalid", display_name="Stranger", role=UserRole.ANALYST
            )
            session.add(stranger)
            await session.flush()
            request = await session.get(ResearchRequest, committed["request"].id)
            assert request is not None
            request.user_id = stranger.id
            await session.commit()

        response = await api.get(f"/api/requests/{committed['request'].id}/assumptions")
        assert response.status_code == 404

    async def test_another_operator_cannot_confirm(self, api, committed, db_engine):
        """Reading is closed; the write must be too, and by the same check."""
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)
        body = (await api.get(f"/api/requests/{request_id}/assumptions")).json()

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stranger = User(
                email="stranger@example.invalid", display_name="Stranger", role=UserRole.ANALYST
            )
            session.add(stranger)
            await session.flush()
            request = await session.get(ResearchRequest, request_id)
            assert request is not None
            request.user_id = stranger.id
            await session.commit()

        response = await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/confirm",
            json={"payload_hash": body["payload_hash"]},
        )
        assert response.status_code == 404


class TestThePage:
    async def test_it_shows_the_value_and_the_reasoning(self, api, committed):
        page = await api.get(f"/requests/{committed['request'].id}/assumptions")

        assert page.status_code == 200
        assert DISCOUNT_RATE in page.text
        assert PROPOSED_JUSTIFICATION in page.text

    async def test_it_carries_the_hash_of_what_it_displayed(self, api, committed):
        page = await api.get(f"/requests/{committed['request'].id}/assumptions")
        expected = (await api.get(f"/api/requests/{committed['request'].id}/assumptions")).json()[
            "payload_hash"
        ]

        assert expected in page.text

    async def test_it_warns_about_what_is_still_unconfirmed(self, api, committed):
        page = await api.get(f"/requests/{committed['request'].id}/assumptions")
        assert 'id="unconfirmed-banner"' in page.text

    async def test_confirming_through_the_form_uses_the_same_service(
        self, api, committed, db_engine
    ):
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)
        page = await api.get(f"/requests/{request_id}/assumptions")
        token = page.cookies.get("aer_csrf") or ""
        hashed = (await api.get(f"/api/requests/{request_id}/assumptions")).json()["payload_hash"]

        response = await api.post(
            f"/requests/{request_id}/assumptions/{target}/confirm",
            data={CSRF_FIELD_NAME: token, "payload_hash": hashed},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text

        body = (await api.get(f"/api/requests/{request_id}/assumptions")).json()
        assert body["unconfirmed"] == 0
        assert body["assumptions"][0]["approved_by"] == "owner@example.invalid"

    async def test_a_form_without_a_token_confirms_nothing(self, api, committed, db_engine):
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)
        hashed = (await api.get(f"/api/requests/{request_id}/assumptions")).json()["payload_hash"]

        response = await api.post(
            f"/requests/{request_id}/assumptions/{target}/confirm",
            data={"payload_hash": hashed},
            follow_redirects=False,
        )
        assert response.status_code == 403

        body = (await api.get(f"/api/requests/{request_id}/assumptions")).json()
        assert body["unconfirmed"] == 1

    async def test_the_history_page_shows_the_proposal_that_was_replaced(
        self, api, committed, db_engine
    ):
        """The point of keeping proposals: a reader can see what a number was chosen over."""
        request_id = committed["request"].id
        target = await assumption_id(db_engine, request_id)

        await api.post(
            f"/api/requests/{request_id}/assumptions/{target}/amend",
            json={
                "value": "0.11",
                "justification": "The beta ignores the pending disposal of the US division.",
            },
        )

        page = await api.get(f"/requests/{request_id}/assumptions/{target}")
        assert page.status_code == 200
        assert PROPOSED_JUSTIFICATION in page.text
        assert "pending disposal" in page.text
        assert "0.090000000000" in page.text
        assert "0.110000000000" in page.text

    async def test_the_request_page_links_to_it(self, api, committed):
        page = await api.get(f"/requests/{committed['request'].id}")
        assert 'id="open-assumptions"' in page.text

    async def test_a_request_with_no_assumptions_says_so(self, api, committed, db_engine):
        """Silence would read as "nothing is assumed", which is never true of a valuation."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            found = await session.scalar(
                select(Assumption).where(Assumption.request_id == committed["request"].id)
            )
            await session.delete(found)
            await session.commit()

        page = await api.get(f"/requests/{committed['request'].id}/assumptions")
        assert 'id="no-assumptions"' in page.text


class TestTheScenarioSurface:
    async def test_a_case_is_resolved_rather_than_stored(self, api, committed, db_engine):
        """The surface shows the base case as it is now, not as it was when the case was set."""
        request_id = committed["request"].id
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

        async with factory() as session:
            found = await session.scalar(
                select(Assumption).where(Assumption.request_id == request_id)
            )
            user = await session.get(User, committed["user"].id)
            await assumption_service.confirm(session, assumption=found, actor=user)

            bear = await scenario_service.create_scenario(
                session,
                request_id=request_id,
                key="bear",
                label="Bear case",
                description="The two largest contracts expire and neither renewal is signed.",
            )
            await scenario_service.set_override(
                session,
                scenario=bear,
                assumption_name=DISCOUNT_RATE,
                value=Decimal("0.13"),
                unit="pure",
                justification="A higher required return in the downside case.",
            )
            await session.commit()

        body = (await api.get(f"/api/requests/{request_id}/scenarios")).json()
        case = body["scenarios"][0]

        assert case["key"] == "bear"
        assert case["overridden"] == [DISCOUNT_RATE]
        assert case["values"][DISCOUNT_RATE]["value"] == "0.130000000000"

    async def test_the_page_says_what_a_case_differs_in(self, api, committed, db_engine):
        request_id = committed["request"].id
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

        async with factory() as session:
            found = await session.scalar(
                select(Assumption).where(Assumption.request_id == request_id)
            )
            user = await session.get(User, committed["user"].id)
            await assumption_service.confirm(session, assumption=found, actor=user)
            bear = await scenario_service.create_scenario(
                session,
                request_id=request_id,
                key="bear",
                label="Bear case",
                description="Contracts expire unsigned.",
            )
            await scenario_service.set_override(
                session,
                scenario=bear,
                assumption_name=DISCOUNT_RATE,
                value=Decimal("0.13"),
                unit="pure",
                justification="A higher required return in the downside case.",
            )
            await session.commit()

        page = await api.get(f"/requests/{request_id}/assumptions")
        assert 'id="scenarios"' in page.text
        assert "Bear case" in page.text
        assert "Contracts expire unsigned." in page.text


class TestSupplyingAnAssumptionTheRunCouldNotPropose:
    """Gap B2c. Without this route the assumptions gate is unreachable.

    A discounted cash flow needs a risk-free rate, a beta and an equity risk premium. The
    workflow acquires no macroeconomic series and no price history, and the premium has no
    series behind it at all — so a run proposes eight of eleven, and amend and confirm only
    operate on rows that already exist. The gate fires on a complete set; without a way to
    add the other three, it never fires and the valuation never runs.
    """

    async def test_a_supplied_value_is_recorded_against_the_person_who_typed_it(
        self, api, committed
    ):
        response = await api.post(
            f"/api/requests/{committed['request'].id}/assumptions",
            json={
                "name": "equity_risk_premium",
                "value": "0.055",
                "unit": "pure",
                "justification": "Damodaran's implied US premium at the as-of date.",
            },
        )

        assert response.status_code == 200
        row = next(
            item for item in response.json()["assumptions"] if item["name"] == "equity_risk_premium"
        )
        assert row["proposed_by"] == "owner@example.invalid"

    async def test_it_is_still_unconfirmed(self, api, committed):
        """Typing a number and agreeing to it are two acts, exactly as for a model's."""
        response = await api.post(
            f"/api/requests/{committed['request'].id}/assumptions",
            json={
                "name": "beta",
                "value": "1.15",
                "unit": "pure",
                "justification": "Five-year monthly beta against the S&P 500.",
            },
        )

        row = next(item for item in response.json()["assumptions"] if item["name"] == "beta")
        assert row["approved"] is False

    async def test_a_name_no_valuation_reads_is_refused(self, api, committed):
        """Otherwise it is stored, listed, confirmed — and then silently ignored by
        `inputs_from`, which looks assumptions up by name."""
        response = await api.post(
            f"/api/requests/{committed['request'].id}/assumptions",
            json={
                "name": "terminal_growth_rate",
                "value": "0.02",
                "unit": "pure",
                "justification": "A plausible long-run rate.",
            },
        )

        assert response.status_code == 422

    async def test_proposing_the_same_name_twice_supersedes_rather_than_duplicates(
        self, api, committed
    ):
        url = f"/api/requests/{committed['request'].id}/assumptions"
        body = {
            "name": "risk_free_rate",
            "value": "0.042",
            "unit": "pure",
            "justification": "Ten-year Treasury at the as-of date.",
        }
        await api.post(url, json=body)
        response = await api.post(url, json={**body, "value": "0.044"})

        matching = [
            item for item in response.json()["assumptions"] if item["name"] == "risk_free_rate"
        ]
        assert len(matching) == 1
        assert matching[0]["value"].startswith("0.044")

    async def test_somebody_elses_request_is_not_reachable(self, api, committed, db_engine):
        """An assumption is a judgement about somebody else's analysis; neither half is
        public, and the create route must check ownership like every other route here."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stranger = User(email="stranger@example.invalid", display_name="S", role=UserRole.OWNER)
            session.add(stranger)
            await session.flush()
            theirs = ResearchRequest(
                user_id=stranger.id,
                company_name="Somebody Else Ltd",
                ticker="SEL",
                exchange="LSE",
                as_of_date=AS_OF_DATE,
                point_in_time=True,
                base_currency="GBP",
                reporting_currency="GBP",
                investment_horizon_months=12,
                max_cost_gbp="2.50",
            )
            session.add(theirs)
            await session.commit()
            other_id = theirs.id

        response = await api.post(
            f"/api/requests/{other_id}/assumptions",
            json={
                "name": "beta",
                "value": "1.0",
                "unit": "pure",
                "justification": "Reaching into another operator's request.",
            },
        )

        assert response.status_code == 404
