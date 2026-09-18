"""Whether a subject can be researched, asked before a run starts (ADR 0128).

**The point is what it costs to say no.** The same refusal used to arrive at `acquire`,
after the planner had spent a model call and after the operator had read and approved a
plan. Here it costs one or two free requests to a register, and the request stays editable
so the operator can change the ticker and try again.

The UK case is the one that made this necessary, and it is measured rather than assumed:
nine London-listed companies were checked against the live register on 18 September 2026 and
not one files tagged accounts. A scan has nothing in it to trace a number to.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aer.config import Settings
from aer.core.enums import Provider
from aer.db.models import Job, ResearchRequest, User, WorkOrder
from aer.errors import ExternalServiceError, ValidationError
from aer.fetch.client import FetchResult
from aer.runtime import Registers
from aer.services.availability import Availability, check_availability
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.uk.companies_house import NOT_TAGGED_STATUS, FilingHistory, FilingRecord
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request

pytestmark = pytest.mark.usefixtures("no_real_sockets")

TESCO = ResolvedEntity(identifier="00445790", name="TESCO PLC", ticker="TSCO", exchange="LSE")


def _request(**kwargs: Any) -> Any:
    defaults = {
        "user_id": None,
        "company_name": "Tesco PLC",
        "ticker": "TSCO",
        "exchange": "LSE",
        "as_of_date": date(2026, 9, 1),
        "base_currency": "GBP",
        "investment_horizon_months": 12,
        "max_cost_gbp": "2.50",
    }
    return research_request(**{**defaults, **kwargs})


class _Register:
    """A register that answers what the test told it to, and touches no network."""

    def __init__(
        self,
        *,
        entity: ResolvedEntity | None = TESCO,
        refusal: Exception | None = None,
        filings: tuple[FilingRecord, ...] | None = None,
        status: int = 200,
        history_error: Exception | None = None,
    ) -> None:
        self._entity = entity
        self._refusal = refusal
        self._status = status
        self._history_error = history_error
        self._filings = (
            filings
            if filings is not None
            else (
                FilingRecord(
                    transaction_id="MzUzNDMwNDQ5M2FkaXF6a2N4",
                    category="accounts",
                    description="Accounts",
                    filed_on=date(2026, 7, 25),
                    document_id="a-document",
                ),
            )
        )

    async def resolve_entity(
        self, ticker: str, *, exchange: str | None = None, name: str | None = None
    ) -> ResolvedEntity:
        if self._refusal is not None:
            raise self._refusal
        assert self._entity is not None
        return self._entity

    async def fetch_filing_history(self, company_number: str, **_: object) -> FilingHistory:
        if self._history_error is not None:
            raise self._history_error
        return FilingHistory(
            company_number=company_number, filings=self._filings, total=len(self._filings)
        )

    async def fetch_document(self, ref: DocumentRef, *, tagged: bool = True) -> FetchResult:
        return FetchResult(
            url=ref.url,
            final_url=ref.url,
            status_code=self._status,
            sha256="0" * 64,
            size_bytes=1,
            media_type="application/xhtml+xml",
            declared_media_type=None,
            headers={},
            redirect_chain=(),
            elapsed_ms=0.0,
            attempts=1,
        )


class TestTheAnswerIsAYesOrAReasonedNo:
    def test_a_yes_carries_no_reason_and_a_no_must(self) -> None:
        """The pair is the whole type: a refusal with no sentence is a dead end on a page."""
        with pytest.raises(ValueError, match="yes with no reason"):
            Availability(researchable=False, register=Provider.SEC_EDGAR)
        with pytest.raises(ValueError, match="yes with no reason"):
            Availability(researchable=True, register=Provider.SEC_EDGAR, reason="because")


class TestAUsListing:
    async def test_a_ticker_edgar_knows_is_researchable(self) -> None:
        answer = await check_availability(
            _request(ticker="MSFT", exchange="NASDAQ", company_name="Microsoft Corporation"),
            sec_client=_Register(
                entity=ResolvedEntity(identifier="0000789019", name="MICROSOFT CORP")
            ),
        )

        assert answer.researchable
        assert answer.register is Provider.SEC_EDGAR
        assert answer.identifier == "0000789019"

    async def test_edgar_s_own_refusal_is_the_one_shown(self) -> None:
        """Rewriting it here would be a second answer to a question already answered well.

        `resolve_ticker` distinguishes a missing symbol, a symbol listed elsewhere and a
        symbol belonging to another company altogether — the readiness audit's TSCO case.
        """
        message = "TSCO is not in the SEC's ticker file. A UK-listed company with no US listing…"
        answer = await check_availability(
            _request(ticker="TSCO", exchange="NASDAQ"),
            sec_client=_Register(refusal=ValidationError(message)),
        )

        assert not answer.researchable
        assert answer.reason == message

    async def test_no_client_admits_rather_than_refuses(self) -> None:
        """A missing client is this machine's state, not a fact about the company."""
        answer = await check_availability(_request(ticker="MSFT", exchange="NASDAQ"))

        assert answer.researchable


class TestALondonListing:
    async def test_a_company_whose_accounts_are_a_scan_is_refused_by_name(self) -> None:
        """The measured case, and the one an operator meets on any FTSE ticker.

        Asked for inline XBRL, the register answers 406 where the filing is a scan — so the
        refusal costs one request and says what is wrong with the *filing* rather than
        implying something is wrong with the company.
        """
        answer = await check_availability(
            _request(), companies_house_client=_Register(status=NOT_TAGGED_STATUS)
        )

        assert not answer.researchable
        assert answer.register is Provider.COMPANIES_HOUSE
        assert answer.identifier == "00445790"
        assert "TESCO PLC files its accounts" in answer.reason
        assert "scanned document" in answer.reason
        assert "tagged" in answer.reason

    async def test_a_company_that_files_tagged_accounts_is_researchable(self) -> None:
        """The control. The rule is about the filing, not about the country."""
        answer = await check_availability(_request(), companies_house_client=_Register())

        assert answer.researchable
        assert answer.identifier == "00445790"

    async def test_with_no_credential_a_uk_subject_is_refused_and_says_so(self) -> None:
        answer = await check_availability(_request())

        assert not answer.researchable
        assert "no Companies House credential" in answer.reason
        assert "A US listing is unaffected" in answer.reason

    async def test_an_ambiguous_company_keeps_the_register_s_own_refusal(self) -> None:
        message = "'TESCO' matches 9 active companies on the register…"
        answer = await check_availability(
            _request(), companies_house_client=_Register(refusal=ValidationError(message))
        )

        assert not answer.researchable
        assert answer.reason == message
        # No identifier: nothing resolved, which is a different refusal from "files scans"
        # and leads the operator to do a different thing.
        assert answer.identifier == ""

    async def test_a_company_with_no_fetchable_accounts_says_which_problem_it_is(self) -> None:
        answer = await check_availability(_request(), companies_house_client=_Register(filings=()))

        assert not answer.researchable
        assert "no accounts this platform can fetch" in answer.reason

    async def test_a_register_having_a_bad_moment_says_to_try_again(self) -> None:
        """Distinguished from the refusals above on purpose: this one is not about the
        company at all, and telling an operator their company is unresearchable because a
        server was briefly down would be false."""
        answer = await check_availability(
            _request(),
            companies_house_client=_Register(
                history_error=ExternalServiceError("Gateway timeout.", provider="companies_house")
            ),
        )

        assert not answer.researchable
        assert "could not be read" in answer.reason

    async def test_the_venue_decides_which_register_is_asked(self) -> None:
        """No trial resolution: `TSCO` is Tesco in London and Tractor Supply on NASDAQ."""
        uk = await check_availability(_request(exchange="LSE"), companies_house_client=_Register())
        us = await check_availability(
            _request(exchange="NASDAQ"),
            sec_client=_Register(entity=ResolvedEntity(identifier="0000916365", name="TSC")),
        )

        assert uk.register is Provider.COMPANIES_HOUSE
        assert us.register is Provider.SEC_EDGAR


class TestTheRunIsNeverCreated:
    """The point of asking early: a refused subject costs nothing and leaves no wreckage."""

    async def test_the_api_refuses_before_any_job_exists(
        self, api_settings: Settings, db_engine: AsyncEngine, fake_redis: Redis
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as seeding:
            user = User(email="owner@example.invalid", display_name="Owner")
            seeding.add(user)
            await seeding.flush()
            request = _request(user_id=user.id, exchange="LSE")
            seeding.add(request)
            await seeding.commit()
            request_id = request.id

        refusing = Registers(
            sec_client=_Register(),  # type: ignore[arg-type]
            companies_house_client=_Register(status=NOT_TAGGED_STATUS),  # type: ignore[arg-type]
        )
        try:
            app = build_app(api_settings, engine=db_engine, redis=fake_redis, registers=refusing)
            async for client in client_for(app):
                response = await client.post("/api/runs", json={"request_id": str(request_id)})

            assert response.status_code == 422
            assert "scanned document" in response.text

            async with factory() as reading:
                assert await reading.scalar(select(func.count()).select_from(Job)) == 0
        finally:
            async with factory() as cleanup:
                await cleanup.execute(delete(Job))
                await cleanup.execute(delete(ResearchRequest))
                await cleanup.execute(delete(WorkOrder))
                await cleanup.execute(delete(User))
                await cleanup.commit()

    async def test_the_audit_driver_refuses_on_the_same_terms(
        self, db_session: Any, api_settings: Settings
    ) -> None:
        """**A third caller is how a check quietly stops applying.** The two the ADR named
        are the API route and the web page; the audit driver commissions runs too, and a
        harness able to start a run the product refuses at its own front door proves the
        opposite of what it exists to prove. Its request survives the refusal, as it does at
        the route: a refusal is a sentence beside an editable request, not a lost form."""
        from audit.driver.commission import commission  # noqa: PLC0415
        from audit.subjects import Subject  # noqa: PLC0415

        user = User(email="driver@example.invalid", display_name="Driver")
        db_session.add(user)
        await db_session.flush()

        subject = Subject(
            key="tsco",
            company_name="Tesco PLC",
            ticker="TSCO",
            exchange="LSE",
            base_currency="GBP",
            reporting_currency="GBP",
            horizon_months=12,
            focus_questions=(),
            use_case="the refusal a London listing meets",
        )
        refusing = Registers(
            sec_client=_Register(),  # type: ignore[arg-type]
            companies_house_client=_Register(status=NOT_TAGGED_STATUS),  # type: ignore[arg-type]
        )

        with pytest.raises(ValidationError) as refused:
            await commission(
                db_session,
                subject=subject,
                actor=user,
                settings=api_settings,
                cap_gbp=Decimal("2.50"),
                registers=refusing,
            )

        assert "scanned document" in refused.value.message
        assert await db_session.scalar(select(func.count()).select_from(Job)) == 0
        assert await db_session.scalar(select(func.count()).select_from(ResearchRequest)) == 1
