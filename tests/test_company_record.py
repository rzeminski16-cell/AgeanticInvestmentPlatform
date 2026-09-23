"""What one account knows about each company, read from the record (page specification §4).

Three populations, every state read rather than stored, oldest-looked-at first. The scene
is one of each: a listing the book holds that nobody has researched, a listing the book
closed and still follows, and a company researched, declined with a reason, and never
owned — the population the page exists to keep.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aer.core.enums import DecisionAction, JobStatus, TransactionKind
from aer.db.models import Company, Job, PriceBar, Report, WatchlistEntry
from aer.services import company_record as record_service
from aer.services import decisions as decision_service
from aer.services import theses as thesis_service
from tests.portfolio_fixtures import AS_OF, book, funded, trade
from tests.request_fixtures import research_request

__all__ = ["book"]

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
FOLLOWED_ON = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
CLOSED_ON = date(2026, 6, 15)


@pytest.fixture
async def scene(db_session: Any, book: dict[str, Any]) -> dict[str, Any]:
    """One company per population, on the book fixture's user."""
    user = book["user"]
    microsoft = Company(name="MICROSOFT CORP", ticker="MSFT", exchange="NASDAQ", cik="0000789019")
    contoso = Company(name="Contoso plc", ticker="BARC", exchange="LSE", company_number="01234567")
    fabrikam = Company(name="Fabrikam Inc", ticker="FBRK", exchange="NYSE", cik="0000000009")
    stranger = Company(name="Wingtip Toys", ticker="WING", exchange="NASDAQ", cik="0000000010")
    db_session.add_all([microsoft, contoso, fabrikam, stranger])
    await db_session.flush()
    book["msft"].company_id = microsoft.id
    book["barc"].company_id = contoso.id
    # A prior close for the day's move: 400 the day before the fixture's 410.
    db_session.add(
        PriceBar(
            security_id=book["msft"].id,
            bar_date=AS_OF - timedelta(days=1),
            open=Decimal("398"),
            high=Decimal("402"),
            low=Decimal("397"),
            close=Decimal("400"),
        )
    )

    await funded(db_session, book)
    # Held, never researched: the neglected one.
    await trade(db_session, book, security=book["msft"], quantity="100", price="400")
    # Closed, and still followed monthly — overdue a look.
    await trade(
        db_session,
        book,
        security=book["barc"],
        quantity="100",
        price="250",
        currency="GBX",
        on=date(2026, 3, 2),
    )
    await trade(
        db_session,
        book,
        kind=TransactionKind.SELL,
        security=book["barc"],
        quantity="-100",
        price="300",
        currency="GBX",
        on=CLOSED_ON,
        at_hour=16,
    )
    watch = WatchlistEntry(
        user_id=user.id,
        company_name="Contoso plc",
        ticker="BARC",
        exchange="LSE",
        why="Cheap on cash, if the pension deficit is what they say.",
        cadence="monthly",
        followed_at=FOLLOWED_ON,
        last_checked_at=FOLLOWED_ON,
        next_check_at=FOLLOWED_ON + timedelta(days=31),
    )
    # Researched, declined with a reason, never owned.
    request = research_request(
        user_id=user.id,
        company_name="Fabrikam Inc",
        ticker="FBRK",
        exchange="NYSE",
        as_of_date=AS_OF,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    request.company_id = fabrikam.id
    db_session.add_all([watch, request])
    await db_session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="recordseed1234",
        status=JobStatus.SUCCEEDED,
        started_at=NOW - timedelta(days=2),
        finished_at=NOW - timedelta(days=2, hours=-1),
        total_cost_gbp=Decimal("3.20"),
    )
    db_session.add(job)
    await db_session.flush()
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=fabrikam.id,
        as_of_date=AS_OF,
        content={"markdown": "approved"},
        content_hash="c" * 64,
        approved_at=NOW - timedelta(days=1),
        immutable=True,
    )
    db_session.add(report)
    thesis = await thesis_service.write_thesis(
        db_session, user=user, company=fabrikam, title="Fabrikam is priced for perfection"
    )
    passed = await decision_service.record_decision(
        db_session,
        actor=user,
        thesis=thesis,
        action=DecisionAction.PASS,
        statement="Pass at this price.",
        basis="Priced for perfection.",
        decided_at=NOW - timedelta(hours=1),
    )
    await db_session.flush()
    return {
        "user": user,
        "microsoft": microsoft,
        "contoso": contoso,
        "fabrikam": fabrikam,
        "stranger": stranger,
        "watch": watch,
        "report": report,
        "thesis": thesis,
        "passed": passed,
    }


class TestTheRecord:
    async def test_it_keeps_the_three_populations_apart_and_the_neglected_first(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        records = await record_service.records_for(db_session, user=scene["user"], now=NOW)

        assert [record.name for record in records] == [
            "MICROSOFT CORP",  # never looked at sorts first
            "Contoso plc",  # followed in May
            "Fabrikam Inc",  # decided on an hour ago
        ]
        held, closed, researched = records
        assert held.population == record_service.HELD
        assert held.holding is not None
        assert held.last_looked_at is None
        assert closed.population == record_service.CLOSED_WATCHING
        assert closed.closed_on == CLOSED_ON
        assert closed.watch is scene["watch"]
        assert closed.cadence == "monthly"
        assert researched.population == record_service.RESEARCHED
        assert researched.holding is None
        assert researched.closed_on is None

    async def test_the_states_are_read_from_the_record(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        held, closed, researched = await record_service.records_for(
            db_session, user=scene["user"], now=NOW
        )

        assert (held.thesis_state, held.report_state) == ("none", "none")
        assert held.price is not None
        assert held.price.close == Decimal("410")
        assert held.price.currency == "USD"
        assert held.price.move_pct == Decimal("0.025")
        assert (closed.thesis_state, closed.report_state) == ("none", "none")
        assert closed.is_overdue(NOW)
        assert researched.thesis_state == "holds"
        assert researched.broken_premises == 0
        assert researched.report_state == "current"
        assert researched.report is not None
        assert researched.report.id == scene["report"].id
        assert researched.run_state == ""
        assert researched.passed is not None
        assert researched.passed.judgement_id == scene["passed"].judgement_id
        assert researched.decisions == 1

    async def test_a_report_older_than_the_window_is_stale(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        later = NOW + timedelta(days=100)
        record = await record_service.record_of(
            db_session, user=scene["user"], company=scene["fabrikam"], now=later
        )
        assert record.report_state == "stale"

    async def test_the_filters_narrow_and_never_hide_the_declined(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        records = await record_service.records_for(db_session, user=scene["user"], now=NOW)

        def names(show: str) -> list[str]:
            return [row.name for row in record_service.filtered(records, show, now=NOW)]

        assert names("held") == ["MICROSOFT CORP"]
        assert names("researched") == ["Fabrikam Inc"]
        assert names("closed") == ["Contoso plc"]
        assert names("no_report") == ["MICROSOFT CORP", "Contoso plc"]
        assert names("overdue") == ["Contoso plc"]
        assert names("all") == names("nonsense") == [row.name for row in records]

    async def test_changing_the_cadence_moves_the_next_check_from_now(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        entry = await record_service.set_cadence(
            db_session, entry=scene["watch"], cadence="quarterly", now=NOW
        )
        assert entry.cadence == "quarterly"
        assert entry.next_check_at == FOLLOWED_ON + timedelta(days=92)

        with pytest.raises(ValueError, match="weekly"):
            await record_service.set_cadence(db_session, entry=entry, cadence="weekly", now=NOW)

    async def test_a_company_is_in_the_record_by_any_of_its_doors(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        user = scene["user"]
        for key in ("microsoft", "contoso", "fabrikam"):
            found = await record_service.company_of(db_session, user=user, company_id=scene[key].id)
            assert found is not None, key
            assert found.id == scene[key].id, key
        assert (
            await record_service.company_of(db_session, user=user, company_id=scene["stranger"].id)
            is None
        )
        assert (
            await record_service.company_of(db_session, user=user, company_id=uuid.uuid4()) is None
        )
