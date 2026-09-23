"""The price-move alert (F11): a move past the operator's threshold, beside what the record says.

Under ADRs 0078, 0079 and 0120. Everything here is arithmetic on stored bars and reads of
stored rows — no provider, no model, no network. The book fixture gives a US listing with one
close at AS_OF; `daily_bars` with no swing lays a flat run of closes before it, so the move
over the window is the fixture's close against that level and nothing else: 410 against 500
is down 18.0%, 410 against 300 is up 36.7%, 410 against 420 is inside any threshold a person
would set.

What the tests hold the feature to: a move is a finding of its own kind that carries no premise
status and opens no gate; it is never shipped without the sentence that says what the record
says; the threshold is the listing's own or the account's default, and the default is an
override the pass reads; one move is one alert, dismissed or not; a dismissal needs a reason;
and every read is scoped to the person whose watchlist it is.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import (
    FindingAction,
    FindingKind,
    PremiseComparator,
    PremiseStatus,
    Provider,
    SourceTier,
    UserRole,
)
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    Finding,
    Premise,
    PriceBar,
    Security,
    SourceDocument,
    Thesis,
    User,
)
from aer.errors import ValidationError
from aer.services import configuration, daily_pass, price_alerts, thesis_monitor
from aer.services import theses as thesis_service
from aer.services import watchlist as watchlist_service
from aer.services.theses import Predicate
from tests import portfolio_fixtures
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import AS_OF, daily_bars
from tests.price_fixtures import StubPrices

pytestmark = pytest.mark.integration

book = portfolio_fixtures.book


# -- The scene --------------------------------------------------------------------------------


async def _job_id(session: AsyncSession, user: User, settings: Any) -> uuid.UUID:
    """A pass to hang findings and calculations on.

    Run before anything is watched, so the pass itself raises nothing and each test's
    check is the first.
    """
    outcome = await daily_pass.run_daily_pass(
        session, None, user=user, settings=settings, as_of=AS_OF
    )
    return outcome.job.id


async def _level(session: AsyncSession, security: Security, close: str) -> None:
    """A flat run of closes at ``close`` on the ten weekdays before AS_OF."""
    await daily_bars(session, security, until=AS_OF, days=10, close=close, swing="0")


async def _watch(
    session: AsyncSession,
    user: User,
    ticker: str = "MSFT",
    exchange: str = "NASDAQ",
    *,
    threshold: Decimal | None = None,
    window: int = watchlist_service.DEFAULT_PRICE_WINDOW_DAYS,
) -> Any:
    return await watchlist_service.follow(
        session,
        user=user,
        company_name=ticker,
        ticker=ticker,
        exchange=exchange,
        why="Worth a look.",
        price_move_threshold_pct=threshold,
        price_move_window_days=window,
    )


async def _check(
    session: AsyncSession, book: dict[str, Any], settings: Any, job_id: uuid.UUID
) -> list[Finding]:
    return await price_alerts.check_watched_listings(
        session, user=book["user"], settings=settings, as_of=AS_OF, job_id=job_id
    )


async def _company_behind(session: AsyncSession, security: Security) -> Company:
    company = Company(
        name="Microsoft Corporation", ticker="MSFT", exchange="NASDAQ", cik="0000789019"
    )
    session.add(company)
    await session.flush()
    security.company_id = company.id
    await session.flush()
    return company


def _predicate() -> Predicate:
    return Predicate(
        metric="operating margin",
        comparator=PremiseComparator.AT_LEAST,
        threshold=Decimal("0.35"),
        unit="ratio",
    )


async def _thesis_with(
    session: AsyncSession, user: User, company: Company, rows: list[tuple[str, bool]]
) -> Thesis:
    """A thesis with one premise per row: the statement, and whether it has a predicate."""
    thesis = await thesis_service.write_thesis(
        session, user=user, company=company, title="Microsoft keeps its margin"
    )
    for statement, predicated in rows:
        await thesis_service.add_premise(
            session,
            thesis=thesis,
            actor=user,
            statement=statement,
            basis="The FY25 report.",
            predicate=_predicate() if predicated else None,
            review_by=None if predicated else date(2027, 3, 31),
        )
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    return loaded


async def _reading(
    session: AsyncSession, *, thesis: Thesis, premise: Premise, status: PremiseStatus, value: str
) -> Finding:
    """What the monitor last said about a premise, as the row it would have written."""
    finding = Finding(
        user_id=thesis.user_id,
        thesis_id=thesis.id,
        judgement_id=premise.judgement_id,
        kind=FindingKind.READING,
        status=status,
        justification="What the filing said.",
        source_document_ids=[],
        observed={"value": value, "unit": "ratio", "comparator": ">=", "threshold": "0.35"},
        opens_gate=status is PremiseStatus.CONTRADICTED,
    )
    session.add(finding)
    await session.flush()
    return finding


# -- What crosses the threshold ---------------------------------------------------------------


class TestWhatCrossesTheThreshold:
    async def test_a_fall_past_the_threshold_is_a_finding_beside_the_record(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        assert len(written) == 1
        finding = written[0]
        # A finding of its own kind: no premise status, no gate, no thesis — a listing.
        assert finding.kind is FindingKind.PRICE_MOVE
        assert finding.status is None
        assert finding.opens_gate is False
        assert finding.thesis_id is None
        assert finding.security_id == book["msft"].id
        assert finding.user_id == book["user"].id
        assert finding.job_id == job_id
        observed = finding.observed or {}
        assert observed["direction"] == "down"
        assert observed["move_pct"] == "-18.0%"
        assert observed["threshold_pct"] == "10"
        assert observed["window_days"] == 7
        # Never shipped alone: the sentence carries what the record says.
        assert finding.justification.startswith(
            "Down 18.0% over 7 days, from 500.00 USD to 410.00 USD."
        )
        assert "Nothing has been filed since your last check" in finding.justification
        assert "there is no thesis behind this listing" in finding.justification
        # NASDAQ has a proxy index; the platform holds no bars for it here, and says so
        # rather than calling anything a sector.
        assert "the S&P 500 could not be measured over the same period" in finding.justification
        assert price_alerts.headline_of(finding) == "MSFT moved -18.0% this week"

    async def test_a_rise_past_the_threshold_is_a_finding_too(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "300")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        assert len(written) == 1
        assert (written[0].observed or {})["direction"] == "up"
        assert (written[0].observed or {})["move_pct"] == "+36.7%"
        assert written[0].justification.startswith("Up 36.7% over 7 days")

    async def test_a_move_inside_the_threshold_raises_nothing(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "420")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        assert written == []
        rows = list(await db_session.scalars(select(Finding)))
        assert rows == []

    async def test_the_listings_own_threshold_beats_the_accounts(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """The account says 10%; the listing says 25%. Down 18% is not news on this one."""
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"], threshold=Decimal("25"))

        assert await _check(db_session, book, api_settings, job_id) == []

    async def test_a_tighter_threshold_of_its_own_fires_where_the_default_would_not(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "420")
        await _watch(db_session, book["user"], threshold=Decimal("2"))

        written = await _check(db_session, book, api_settings, job_id)

        assert len(written) == 1
        assert (written[0].observed or {})["threshold_pct"] == "2"

    async def test_the_window_is_the_listings_own(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"], window=3)

        written = await _check(db_session, book, api_settings, job_id)

        assert len(written) == 1
        assert (written[0].observed or {})["window_days"] == 3
        assert "over 3 days" in written[0].justification
        assert price_alerts.headline_of(written[0]) == "MSFT moved -18.0% over 3 days"


class TestTheAccountDefault:
    async def test_it_is_an_override_the_pass_reads(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """The settings page's number is the one the pass uses, not the one it started with."""
        await configuration.save_override(
            db_session, key="price_move_threshold_pct", raw="25", actor=book["user"]
        )
        effective = await configuration.effective_settings(db_session, api_settings)
        assert effective.price_move_threshold_pct == Decimal("25")
        job_id = await _job_id(db_session, book["user"], effective)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        assert await _check(db_session, book, effective, job_id) == []

    async def test_a_threshold_past_the_whole_price_is_refused(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError, match="at most 100"):
            await configuration.save_override(
                db_session, key="price_move_threshold_pct", raw="150", actor=book["user"]
            )

    async def test_a_listing_threshold_past_the_whole_price_is_refused(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError, match="at most 100"):
            await _watch(db_session, book["user"], threshold=Decimal("101"))
        with pytest.raises(ValidationError, match="at least a day"):
            await _watch(db_session, book["user"], window=0)


# -- One move, one alert -----------------------------------------------------------------------


class TestOneAlertPerMovePerWindow:
    async def test_the_same_move_is_not_a_second_alert(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        first = await _check(db_session, book, api_settings, job_id)
        again = await _check(db_session, book, api_settings, job_id)

        assert len(first) == 1
        assert again == []

    async def test_a_dismissed_move_is_not_news_again(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """Counting it twice would also double the dismissals the threshold band reads."""
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])
        first = await _check(db_session, book, api_settings, job_id)
        finding = await thesis_monitor.finding_of(db_session, first[0].id, user_id=book["user"].id)
        assert finding is not None

        await thesis_monitor.resolve_finding(
            db_session,
            finding=finding,
            actor=book["user"],
            action=FindingAction.DISMISSED,
            reason="Read it; it changes nothing I hold.",
        )

        assert await _check(db_session, book, api_settings, job_id) == []
        counted = await price_alerts.moves_in_last_six_months(
            db_session,
            user_id=book["user"].id,
            security_id=book["msft"].id,
            now=datetime.now(UTC),
        )
        assert counted == (1, 1)

    async def test_a_dismissal_needs_a_reason(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])
        first = await _check(db_session, book, api_settings, job_id)
        finding = await thesis_monitor.finding_of(db_session, first[0].id, user_id=book["user"].id)
        assert finding is not None

        with pytest.raises(ValidationError, match="needs a reason"):
            await thesis_monitor.resolve_finding(
                db_session,
                finding=finding,
                actor=book["user"],
                action=FindingAction.DISMISSED,
                reason="   ",
            )


# -- What the record says beside it ------------------------------------------------------------


class TestWhatTheRecordSaysBesideIt:
    async def test_the_market_is_measured_over_the_same_window(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        index = Security(
            ticker="GSPC",
            exchange="INDX",
            provider_symbol="GSPC.INDX",
            name="S&P 500",
            quote_currency="USD",
        )
        db_session.add(index)
        await db_session.flush()
        await _level(db_session, index, "5000")
        db_session.add(
            PriceBar(
                security_id=index.id,
                bar_date=AS_OF,
                open=Decimal("4700"),
                high=Decimal("4750"),
                low=Decimal("4690"),
                close=Decimal("4700"),
            )
        )
        await db_session.flush()
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        observed = written[0].observed or {}
        assert observed["market_label"] == "S&P 500"
        assert observed["market_move_pct"] == "-6.0%"
        assert "the S&P 500 moved -6.0% over the same period" in written[0].justification

    async def test_the_premises_are_read_as_the_monitor_left_them(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """Read, never re-measured: the states beside the move are the monitor's latest
        readings, and a premise it has not read yet says so."""
        company = await _company_behind(db_session, book["msft"])
        thesis = await _thesis_with(
            db_session,
            book["user"],
            company,
            [
                ("Operating margin holds above 35%.", True),
                ("Azure keeps taking share.", True),
                ("Capital spending stays disciplined.", True),
                ("Management allocates capital well.", False),
            ],
        )
        holds, broken, _unread, _reviewed = sorted(thesis.premises, key=lambda row: row.position)
        await _reading(
            db_session, thesis=thesis, premise=holds, status=PremiseStatus.UNCHANGED, value="0.42"
        )
        await _reading(
            db_session,
            thesis=thesis,
            premise=broken,
            status=PremiseStatus.CONTRADICTED,
            value="0.31",
        )
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        premises = (written[0].observed or {})["premises"]
        assert [row["state"] for row in premises] == [
            "holds",
            "contradicted",
            "not yet read",
            "reviewed by a person",
        ]
        assert premises[0]["value"] == "0.42 ratio"
        assert premises[0]["threshold"] == ">= 0.35 ratio"
        assert "1 of 4 premises is contradicted" in written[0].justification
        figures = price_alerts.figures_of(written[0])
        assert figures is not None
        assert figures["headline"] == "The price moved, and so did a premise."
        assert figures["any_contradicted"] is True

    async def test_a_thesis_that_still_holds_says_so(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        company = await _company_behind(db_session, book["msft"])
        thesis = await _thesis_with(
            db_session,
            book["user"],
            company,
            [("Operating margin holds above 35%.", True), ("Azure keeps taking share.", True)],
        )
        for premise in thesis.premises:
            await _reading(
                db_session,
                thesis=thesis,
                premise=premise,
                status=PremiseStatus.UNCHANGED,
                value="0.42",
            )
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        assert "all 2 premises still hold" in written[0].justification
        figures = price_alerts.figures_of(written[0])
        assert figures is not None
        assert figures["headline"] == "The price moved. Your thesis did not."

    async def test_filings_since_the_last_check_are_counted(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        company = await _company_behind(db_session, book["msft"])
        artefact = Artefact(
            sha256="d" * 64, size_bytes=32, media_type="text/html", storage_key="dd/d"
        )
        db_session.add(artefact)
        await db_session.flush()
        db_session.add(
            SourceDocument(
                work_order_id=book["document"].work_order_id,
                company_id=company.id,
                artefact_id=artefact.id,
                url="https://www.sec.gov/Archives/edgar/data/789019/000078901926000010.htm",
                provider=Provider.ECB,
                source_tier=SourceTier.T3_OFFICIAL_STATS,
                title="Form 8-K",
                retrieved_at=datetime.now(UTC),
            )
        )
        await db_session.flush()
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        assert (written[0].observed or {})["filed_since"] == 1
        assert "1 document has been filed since your last check" in written[0].justification

    async def test_the_calculation_behind_the_move_is_on_record(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])

        written = await _check(db_session, book, api_settings, job_id)

        recorded = (written[0].observed or {})["calculation_id"]
        assert recorded is not None
        assert await db_session.get(Calculation, uuid.UUID(recorded)) is not None
        figures = price_alerts.figures_of(written[0])
        assert figures is not None
        assert figures["calculation_href"] == f"/calculations/{recorded}"


# -- Scope, and what it never does ------------------------------------------------------------


class TestScope:
    async def test_another_persons_watchlist_is_not_read(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        other = User(email="other@example.invalid", display_name="O", role=UserRole.OWNER)
        db_session.add(other)
        await db_session.flush()
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])
        written = await _check(db_session, book, api_settings, job_id)

        theirs = await price_alerts.check_watched_listings(
            db_session, user=other, settings=api_settings, as_of=AS_OF, job_id=job_id
        )

        assert theirs == []
        assert await thesis_monitor.finding_of(db_session, written[0].id, user_id=other.id) is None
        counted = await price_alerts.moves_in_last_six_months(
            db_session, user_id=other.id, security_id=book["msft"].id, now=datetime.now(UTC)
        )
        assert counted == (0, 0)

    async def test_a_listing_with_no_window_yet_is_skipped_and_stamped(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """The London listing has one close. Nothing to measure, and the check is recorded."""
        job_id = await _job_id(db_session, book["user"], api_settings)
        await _watch(db_session, book["user"], "BARC", "LSE")

        written = await _check(db_session, book, api_settings, job_id)

        assert written == []
        entries = await watchlist_service.entries_for(db_session, user_id=book["user"].id)
        assert entries[0].last_checked_at is not None


class TestTheDailyPassCarriesIt:
    async def test_the_pass_writes_the_alert_after_the_closes(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        await _level(db_session, book["msft"], "500")
        await _watch(db_session, book["user"])
        client = StubPrices({"MSFT.US": [(AS_OF, "410")]})

        outcome = await daily_pass.run_daily_pass(
            db_session, client, user=book["user"], settings=api_settings, as_of=AS_OF
        )

        assert outcome.alerts == 1
        rows = list(
            await db_session.scalars(select(Finding).where(Finding.kind == FindingKind.PRICE_MOVE))
        )
        assert len(rows) == 1
        assert rows[0].job_id == outcome.job.id


# -- The pages ---------------------------------------------------------------------------------


@pytest.fixture
async def scene(db_engine: Any, api_settings: Any) -> Any:
    """A price move on record, committed, so the application's own sessions can read it."""
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        msft = Security(
            ticker="MSFT",
            exchange="NASDAQ",
            provider_symbol="MSFT.US",
            name="Microsoft",
            quote_currency="USD",
        )
        session.add_all([user, msft])
        await session.flush()
        session.add(
            PriceBar(
                security_id=msft.id,
                bar_date=AS_OF,
                open=Decimal("400"),
                high=Decimal("420"),
                low=Decimal("399"),
                close=Decimal("410"),
            )
        )
        await _level(session, msft, "500")
        job_id = await _job_id(session, user, api_settings)
        await _watch(session, user)
        written = await price_alerts.check_watched_listings(
            session, user=user, settings=api_settings, as_of=AS_OF, job_id=job_id
        )
        await session.commit()
        yield {"user": user, "finding_id": written[0].id}
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


class TestThePages:
    async def test_the_finding_page_shows_the_move_beside_the_record(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        response = await api.get(f"/monitor/findings/{scene['finding_id']}")

        assert response.status_code == 200, response.text
        body = response.text
        assert "A PRICE MOVED" in body
        assert "The price moved. There is no thesis behind this listing." in body
        assert "-18.0%" in body
        assert "500.00 USD" in body
        assert 'id="closes"' in body
        assert "Nothing has been filed since your last check" in body
        assert "Too many dismissals mean the threshold is wrong, not the market." in body
        assert "Alert past" in body
        assert 'href="/decisions?security=MSFT.NASDAQ"' in body
        assert "Read, and doing nothing about it" in body
        # The premise gate never appears: a price is an outcome, not evidence.
        assert 'id="thesis-gate"' not in body

    async def test_the_monitor_lists_it_under_the_listing_with_no_thesis_to_link(
        self, api: Any
    ) -> None:
        body = (await api.get("/monitor")).text

        assert 'data-listing="MSFT.NASDAQ"' in body
        assert "/theses/None" not in body
        assert "Price moved" in body

    async def test_the_main_menu_names_the_move(self, api: Any) -> None:
        body = (await api.get("/")).text

        assert "Microsoft moved -18.0% this week" in body

    async def test_dismissing_it_from_the_page_needs_and_records_a_reason(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/monitor/findings/{scene['finding_id']}")

        refused = await api.post(
            f"/monitor/findings/{scene['finding_id']}/resolve",
            data={"csrf_token": _csrf(page.text), "action": "dismissed", "reason": " "},
        )
        assert refused.status_code >= 400
        done = await api.post(
            f"/monitor/findings/{scene['finding_id']}/resolve",
            data={
                "csrf_token": _csrf(page.text),
                "action": "dismissed",
                "reason": "Read it; it changes nothing I hold.",
            },
        )
        assert done.status_code == 303, done.text

        body = (await api.get(f"/monitor/findings/{scene['finding_id']}")).text
        assert "Dismissed by owner@example.invalid" in body
        assert 'id="reopen"' in body
        assert "1 dismissed." in body

    async def test_the_watchlist_carries_the_threshold(self, api: Any) -> None:
        listed = (await api.get("/watchlist")).text
        # The apostrophe reaches the page HTML-escaped.
        assert "Alert past 10% over 7 days, the account&#39;s default" in listed

        followed = await api.post(
            "/watchlist",
            data={
                "csrf_token": _csrf(listed),
                "company_name": "Contoso plc",
                "ticker": "CTSO",
                "exchange": "LSE",
                "why": "The FY25 margin bridge looks too good.",
                "price_move_threshold_pct": "12.5",
                "price_move_window_days": "10",
            },
        )
        assert followed.status_code == 303, followed.text
        assert "Alert past 12.5% over 10 days" in (await api.get("/watchlist")).text

    async def test_the_watchlist_refuses_a_threshold_that_is_not_one(self, api: Any) -> None:
        listed = (await api.get("/watchlist")).text

        refused = await api.post(
            "/watchlist",
            data={
                "csrf_token": _csrf(listed),
                "company_name": "Contoso plc",
                "ticker": "CTSO",
                "exchange": "LSE",
                "price_move_threshold_pct": "lots",
            },
        )

        assert refused.status_code >= 400
        assert "is a percentage" in refused.text

    async def test_the_settings_page_offers_the_account_default(self, api: Any) -> None:
        body = (await api.get("/settings")).text

        assert "Price move worth telling you about" in body
