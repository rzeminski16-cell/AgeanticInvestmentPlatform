"""Who a report is about, by the name the filings use — gap A67.

A live note's Prior Research Comparison opened "This is the first research run for M&T
Banking Corporation (MTB)": the operator's typo, preserved verbatim, three lines under a
front matter that had the SEC-resolved "M&T BANK CORP" right.

The rule these pin is one sentence — the resolved name where one exists, the typed one
where none does — and the reason it needs a named home is that
``ResearchRequest.company_name`` is the easiest string in the codebase to reach and looks
authoritative. It is an input field. Nothing checks it.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import UserRole
from aer.db.models import Company, User
from aer.services.subject import name_of, subject_name
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

TYPED = "M&T Banking Corporation"
FILED = "M&T BANK CORP"


async def _request(session: AsyncSession, *, user_id: uuid.UUID, ticker: str = "MTB"):
    request = research_request(
        user_id=user_id,
        company_name=TYPED,
        ticker=ticker,
        exchange="NYSE",
        as_of_date=date(2026, 8, 16),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="5.00",
    )
    session.add(request)
    await session.flush()
    return request


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="subject@example.invalid", display_name="S", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    company = Company(name=FILED, cik="0000036270", ticker="MTB", exchange="NYSE")
    db_session.add(company)
    await db_session.flush()
    return {"session": db_session, "user": user, "company": company}


class TestTheRuleItself:
    def test_the_filed_name_wins(self, scene: dict[str, Any]) -> None:
        request = research_request(company_name=TYPED, ticker="MTB", exchange="NYSE")

        assert name_of(request, scene["company"]) == FILED

    def test_no_company_keeps_what_was_typed(self) -> None:
        request = research_request(company_name=TYPED, ticker="MTB", exchange="NYSE")

        assert name_of(request, None) == TYPED

    def test_a_blank_filed_name_falls_back_rather_than_rendering_nothing(self) -> None:
        """An empty heading is worse than an unverified one."""
        request = research_request(company_name=TYPED, ticker="MTB", exchange="NYSE")
        blank = Company(name="   ", cik="0", ticker="MTB", exchange="NYSE")

        assert name_of(request, blank) == TYPED


class TestResolvingFromTheRun:
    async def test_the_link_written_by_acquire_is_preferred(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id)
        request.company_id = scene["company"].id
        await session.flush()

        assert await subject_name(session, request) == FILED

    async def test_the_ticker_finds_it_before_that_link_exists(self, scene: dict[str, Any]) -> None:
        """A caller running before ``acquire`` sets ``company_id`` still gets the filed
        name where the company row is already there."""
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id)

        assert request.company_id is None
        assert await subject_name(session, request) == FILED

    async def test_an_unresolved_request_keeps_what_was_typed(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, ticker="ZZZZ")

        assert await subject_name(session, request) == TYPED

    async def test_a_different_exchange_is_a_different_company(self, scene: dict[str, Any]) -> None:
        """The ticker alone is not an identity — the same letters list elsewhere — so a
        mismatched exchange must fall back rather than name the wrong filer."""
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id)
        request.exchange = "LSE"
        await session.flush()

        assert await subject_name(session, request) == TYPED
