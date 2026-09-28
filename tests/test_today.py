"""Today, as the drawn hub (page specification §1, corrected 28 September 2026).

The verdict first — the book in a sentence, its figures and the conviction strip — then what
happened since the operator last looked, beside *Needs you*: one list ranked by the
specification's six kinds over every tool's rows, numbered, one action each. *Worth doing* is
last, each suggestion earned by a condition in the record and the band absent when nothing
qualifies. The scene holds one of each kind the fake record can hold: a run at a gate, a
listing held with no thesis, a report older than its window, a listing followed and never
researched, and a refresh whose change summary nobody has read.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import FindingKind, JobStatus, TransactionKind, UserRole
from aer.db.models import (
    Company,
    Finding,
    Job,
    Portfolio,
    PriceBar,
    Report,
    Security,
    User,
    WatchlistEntry,
    WorkOrder,
)
from aer.services import daily_pass
from aer.services import refresh as refresh_service
from aer.services import theses as thesis_service
from aer.web.overview import attention as attention_module
from aer.web.overview.attention import Attention, AttentionProvider, Severity, kind_of
from aer.web.overview.hub import book_verdict
from aer.web.overview.suggestions import suggestions_for
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import trade
from tests.request_fixtures import research_request

AS_OF = date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


# -- The rank -------------------------------------------------------------------------------


class TestTheRankIsFixed:
    def test_the_six_kinds_take_the_specifications_order(self) -> None:
        ranked = [
            ("research.gate.1", Severity.BLOCKED),
            ("monitor.gate.1", Severity.BLOCKED),
            ("companies.no_thesis.1", Severity.IDLE),
            ("monitor.moved.1", Severity.IDLE),
            ("review.unreviewed.1", Severity.IDLE),
            ("companies.stale.1", Severity.IDLE),
        ]
        assert [kind_of(key, severity).rank for key, severity in ranked] == [1, 2, 3, 4, 5, 6]

    def test_every_other_kind_comes_after_them_worst_first(self) -> None:
        assert kind_of("research.failed.1", Severity.BROKEN).rank == 8
        assert kind_of("research.budget.1", Severity.BLOCKED).rank == 7
        assert kind_of("decisions.undone.1", Severity.IDLE).rank == 9
        assert kind_of("research.budget.1", Severity.BLOCKED).label == "Waiting for you"

    def test_a_price_move_outranks_a_failed_run_whatever_its_severity(self) -> None:
        # The specification's fourth row is an ordinary notice; a failed run needs
        # diagnosis. The rank is the page's, not the severity's.
        moved = kind_of("monitor.moved.1", Severity.IDLE)
        failed = kind_of("research.failed.1", Severity.BROKEN)
        assert moved.rank < failed.rank
        assert (moved.tone, moved.label) == ("info", "A price moved")

    async def test_the_feed_sorts_by_rank_then_severity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def items(session: Any, *, user_id: uuid.UUID) -> list[Attention]:
            return [
                _item("research.failed.1", Severity.BROKEN),
                _item("companies.stale.1", Severity.IDLE),
                _item("monitor.moved.1", Severity.IDLE),
                _item("research.gate.1", Severity.BLOCKED),
                _item("decisions.undone.1", Severity.IDLE),
            ]

        provider = AttentionProvider(
            key="probe", tool="research", items_ref="aer.web.overview.platform:items", adr="0071"
        )
        monkeypatch.setattr(attention_module, "_REGISTRY", {"probe": provider})
        monkeypatch.setattr(AttentionProvider, "items_fn", lambda _row: items)

        ordered = await attention_module.items_for(object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert [row.key for row in ordered] == [
            "research.gate.1",
            "monitor.moved.1",
            "companies.stale.1",
            "research.failed.1",
            "decisions.undone.1",
        ]


def _item(key: str, severity: Severity) -> Attention:
    return Attention(
        key=key,
        tool="research",
        severity=severity,
        title=key,
        detail="A reason, in one sentence.",
        href="/requests",
    )


# -- The scene ------------------------------------------------------------------------------


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    """One of each kind the record can hold, on a session of its own."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="today@example.invalid", display_name="Today", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        book = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        contoso = Company(
            name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
        )
        fabrikam = Company(name="Fabrikam Inc", ticker="FBRK", exchange="NYSE", cik="0000000009")
        session.add_all([book, contoso, fabrikam])
        await session.flush()
        security = Security(
            company_id=contoso.id,
            ticker="CTSO",
            exchange="LSE",
            provider_symbol="CTSO.LSE",
            name="Contoso plc",
            quote_currency="GBP",
        )
        session.add(security)
        await session.flush()
        session.add_all(
            [
                PriceBar(
                    security_id=security.id,
                    bar_date=AS_OF - timedelta(days=1),
                    open=Decimal("12"),
                    high=Decimal("12"),
                    low=Decimal("12"),
                    close=Decimal("12"),
                ),
                PriceBar(
                    security_id=security.id,
                    bar_date=AS_OF,
                    open=Decimal("12.4"),
                    high=Decimal("12.6"),
                    low=Decimal("12.3"),
                    close=Decimal("12.5"),
                ),
            ]
        )
        holdings = {"portfolio": book, "document": None}
        await trade(
            session,
            holdings,
            kind=TransactionKind.DEPOSIT,
            quantity="5000",
            price=None,
            currency="GBP",
            on=date(2026, 6, 1),
        )
        # Held with no thesis behind it: rank 3, and a *write a thesis* suggestion.
        await trade(
            session,
            holdings,
            security=security,
            quantity="100",
            price="10",
            currency="GBP",
            on=date(2026, 6, 15),
        )
        # A run at a gate: rank 1.
        gated = research_request(
            user_id=user.id,
            company_name="Northwind Traders",
            ticker="NWND",
            exchange="NASDAQ",
            as_of_date=AS_OF,
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
            portfolio_context={},
        )
        session.add(gated)
        await session.flush()
        gate_job = Job(
            work_order_id=gated.id,
            workflow_version="vertical_slice_v1",
            code_version="todayseed12345",
            status=JobStatus.AWAITING_APPROVAL,
            started_at=NOW - timedelta(hours=2),
        )
        session.add(gate_job)
        # Researched a hundred days ago, with a thesis that holds: rank 6, stale.
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
        session.add(request)
        await session.flush()
        job = Job(
            work_order_id=request.id,
            workflow_version="vertical_slice_v1",
            code_version="todayseed12345",
            status=JobStatus.SUCCEEDED,
            started_at=NOW - timedelta(days=101),
            finished_at=NOW - timedelta(days=100, hours=23),
            total_cost_gbp=Decimal("3.20"),
        )
        session.add(job)
        await session.flush()
        report = Report(
            job_id=job.id,
            request_id=request.id,
            company_id=fabrikam.id,
            as_of_date=AS_OF - timedelta(days=100),
            content={"markdown": "approved"},
            content_hash="c" * 64,
            approved_at=NOW - timedelta(days=100),
            immutable=True,
        )
        session.add(report)
        await thesis_service.write_thesis(
            session, user=user, company=fabrikam, title="Fabrikam keeps its pricing power"
        )
        # Followed forty days ago and never researched: a *research* suggestion.
        wingtip = WatchlistEntry(
            user_id=user.id,
            company_name="Wingtip Toys",
            ticker="WING",
            exchange="NASDAQ",
            why="A toy maker with a moat, maybe.",
            cadence="quarterly",
            followed_at=NOW - timedelta(days=40),
            next_check_at=NOW + timedelta(days=52),
        )
        session.add(wingtip)
        await session.commit()
        yield {
            "factory": factory,
            "user": user,
            "contoso": contoso,
            "fabrikam": fabrikam,
            "security": security,
            "report": report,
            "gate_job": gate_job,
            "request": request,
            "job": job,
        }
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _order_of(body: str, *keys: str) -> list[int]:
    return [body.index(f'data-attention="{key}"') for key in keys]


# -- Needs you --------------------------------------------------------------------------------


class TestNeedsYou:
    async def test_the_list_is_one_ranked_list_numbered_with_one_action_each(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        gate = f"research.gate.{scene['gate_job'].id}"
        no_thesis = f"companies.no_thesis.{scene['contoso'].id}"
        stale = f"companies.stale.{scene['fabrikam'].id}"
        first, second, third = _order_of(body, gate, no_thesis, stale)
        assert first < second < third
        assert 'data-rank="1"' in body
        assert 'data-rank="3"' in body
        assert 'data-rank="6"' in body
        # The specification's row wording (§1.1).
        assert "A gate is waiting on Northwind Traders" in body
        assert "Contoso plc is held with no thesis behind it" in body
        assert re.search(r"Fabrikam Inc was last researched \d+ days ago", body)
        # Every row still says why it is there, and ends in one word.
        assert body.count('data-field="reason"') >= 3
        assert re.search(r'data-field="action"\s*>Open<', body)
        assert re.search(r'data-field="action"\s*>Write<', body)

    async def test_the_no_thesis_row_leads_to_the_form_with_the_company_chosen(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text
        assert f'href="/theses?company={scene["contoso"].id}"' in body

    async def test_a_stopped_run_previews_from_its_title_and_opens_from_its_word(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        # ADR 0006: the drawer is an enhancement over a link, so the title is a link to the
        # run that htmx turns into the preview, and the word always goes.
        body = (await api.get("/")).text
        job = scene["gate_job"].id

        assert f'hx-get="/research/runs/{job}/preview"' in body
        assert body.count(f'href="/runs/{job}"') >= 2


# -- Worth doing -------------------------------------------------------------------------------


class TestWorthDoing:
    async def test_each_card_is_earned_by_a_condition(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        assert 'id="worth-doing"' in body
        assert 'data-suggestion="thesis"' in body
        assert "Write a thesis for Contoso plc" in body
        assert 'data-suggestion="research"' in body
        assert "Research Wingtip Toys" in body
        assert "nothing commissioned in the last thirty days" in body
        assert 'data-suggestion="review"' not in body  # nothing closed
        assert 'data-suggestion="refresh"' not in body  # nothing refreshed

    async def test_a_finished_refresh_is_worth_reviewing_until_its_summary_is_read(
        self, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            refresh = Job(
                work_order_id=scene["request"].id,
                workflow_version=refresh_service.WORKFLOW_VERSION,
                code_version="todayseed12345",
                status=JobStatus.SUCCEEDED,
                refresh_kind=refresh_service.REFRESH,
                refreshes_report_id=scene["report"].id,
                started_at=NOW - timedelta(days=1),
                finished_at=NOW - timedelta(hours=20),
            )
            session.add(refresh)
            await session.commit()
            user = await session.get(User, scene["user"].id)
            assert user is not None

            earned = await suggestions_for(session, user=user, now=NOW)
            refreshes = [row for row in earned if row.kind == "refresh"]
            assert len(refreshes) == 1
            assert refreshes[0].title == "Review the Fabrikam Inc refresh"
            assert refreshes[0].action_href == f"/runs/{refresh.id}"  # no new report: quiet

            await refresh_service.mark_changes_read(session, job=refresh, now=NOW)
            await session.commit()
            earned = await suggestions_for(session, user=user, now=NOW)
            assert not [row for row in earned if row.kind == "refresh"]

    async def test_the_band_is_absent_when_nothing_qualifies(
        self, api_settings: Any, db_engine: Any, fake_redis: Any
    ) -> None:
        # A returning operator with one finished run and nothing else: no card, no heading.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            user = User(email="quiet@example.invalid", display_name="Q", role=UserRole.OWNER)
            session.add(user)
            await session.flush()
            request = research_request(
                user_id=user.id,
                company_name="Contoso Corporation",
                ticker="CTSO",
                exchange="NASDAQ",
                as_of_date=AS_OF,
                base_currency="USD",
                investment_horizon_months=12,
                max_cost_gbp="2.50",
                portfolio_context={},
            )
            session.add(request)
            await session.flush()
            session.add(
                Job(
                    work_order_id=request.id,
                    workflow_version="vertical_slice_v1",
                    code_version="todayseed12345",
                    status=JobStatus.SUCCEEDED,
                    started_at=NOW,
                    finished_at=NOW,
                )
            )
            await session.commit()
        try:
            async for client in client_for(
                build_app(api_settings, engine=db_engine, redis=fake_redis)
            ):
                body = (await client.get("/")).text
                assert 'id="worth-doing"' not in body
                assert 'id="nothing-needs-you"' in body
                assert "Nothing needs you today." in body
                assert 'id="verdict"' in body
        finally:
            await delete_all(db_engine)


# -- The verdict -------------------------------------------------------------------------------


class TestTheVerdict:
    async def test_it_leads_with_the_book_in_a_sentence(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        assert "Your book needs attention." in body
        # One position, held with nothing written down: named as a state, not counted.
        assert "Nothing is written down about why you hold it — 23.8% of the book." in body

    async def test_its_figures_are_read_from_the_record(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        assert 'data-state="book"' in body
        assert "£5,250.00" in body  # 4,000 cash and 100 shares at 12.50
        assert "+1.0% on the day" in body  # from 5,200 at the close before
        assert 'data-state="reasoning"' in body
        assert "0 of 1" in body
        assert 'data-state="next_check"' in body
        assert 'data-state="spent"' in body
        assert "Spent this month" in body
        # And the footer says when those figures were struck, as drawn.
        assert re.search(r'id="valued-at">Valued at the close of \d+ \w+ 2026<', body)

    async def test_the_strip_is_every_position_by_weight_named_for_a_reader(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        assert 'id="conviction-strip"' in body
        assert body.count('data-state="doubt"') == 1
        assert "Contoso plc, 23.8% of the book, under review or no thesis" in body
        assert f'href="/portfolio/positions/{scene["security"].id}"' in body
        # Each colour is named beside it, and the cash the strip leaves out is said.
        legend = body[body.index('aria-label="What the colours mean"') :]
        assert "under review or no thesis" in legend[: legend.index("</ul>")]
        assert "76.2% cash is not shown" in body

    async def test_the_share_it_states_is_struck_rather_than_added_up(
        self, scene: dict[str, Any], api_settings: Any
    ) -> None:
        async with scene["factory"]() as session:
            user = await session.get(User, scene["user"].id)
            assert user is not None
            verdict = await book_verdict(session, user=user, settings=api_settings, now=NOW)

        assert verdict.needs_attention
        assert [figure.key for figure in verdict.figures] == [
            "book",
            "reasoning",
            "next_check",
            "spent",
        ]
        assert [segment.state for segment in verdict.strip] == ["doubt"]
        assert verdict.strip[0].width == "100.00%"
        assert verdict.largest == "Contoso plc 23.8%"


# -- Since the last look -------------------------------------------------------------------------


class TestTheBriefing:
    async def test_a_first_look_covers_the_last_seven_days(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/")).text

        assert "The last seven days" in body
        assert "your first look" in body
        assert "Welcome, Today" in body

    async def test_a_reload_keeps_the_window_and_claims_no_look(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        await api.get("/")
        body = (await api.get("/")).text

        assert "The last seven days" in body
        assert "you last looked" not in body
        async with scene["factory"]() as session:
            user = await session.get(User, scene["user"].id)
            assert user is not None
            assert user.today_seen_at is not None
            assert user.looked_before is None

    async def test_the_next_morning_starts_from_the_last_look(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        await _seen(scene, ago=timedelta(days=1))

        body = (await api.get("/")).text

        assert "Since yesterday" in body
        assert "you last looked yesterday" in body
        assert "Welcome back, Today" in body

    async def test_it_says_what_happened_and_offers_nothing_to_press(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            session.add(
                Finding(
                    user_id=scene["user"].id,
                    security_id=scene["security"].id,
                    kind=FindingKind.PRICE_MOVE,
                    justification="The close fell past the threshold you set. Nothing was filed.",
                    observed={"company": "Contoso plc", "move_pct": "-12.4%", "window_days": 5},
                )
            )
            session.add(
                Job(
                    work_order_id=scene["request"].id,
                    workflow_version=refresh_service.WORKFLOW_VERSION,
                    code_version="todayseed12345",
                    status=JobStatus.SUCCEEDED,
                    refresh_kind=refresh_service.REFRESH,
                    refreshes_report_id=scene["report"].id,
                    started_at=datetime.now(UTC) - timedelta(hours=2),
                    finished_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            order = WorkOrder(user_id=scene["user"].id, tool=daily_pass.TOOL, as_of_date=AS_OF)
            session.add(order)
            await session.flush()
            session.add(
                Job(
                    work_order_id=order.id,
                    workflow_version=daily_pass.WORKFLOW_VERSION,
                    code_version="todayseed12345",
                    status=JobStatus.SUCCEEDED,
                    started_at=datetime.now(UTC) - timedelta(minutes=20),
                    finished_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await session.commit()

        body = (await api.get("/")).text
        since = body[body.index('id="since"') : body.index('id="needs-you"')]

        assert "Contoso plc moved -12.4%" in since
        assert "Fabrikam Inc&#39;s report was refreshed" in since
        assert "The summary of what changed has not been read." in since
        assert "The daily pass ran" in since
        assert "<button" not in since
        assert "<form" not in since

    async def test_a_quiet_week_says_so(self, api: Any, scene: dict[str, Any]) -> None:
        body = (await api.get("/")).text

        assert 'id="nothing-happened"' in body
        assert "Nothing happened in the last seven days." in body


async def _seen(scene: dict[str, Any], *, ago: timedelta) -> None:
    """The operator last opened Today ``ago``, having looked the week before that too."""
    async with scene["factory"]() as session:
        user = await session.get(User, scene["user"].id)
        assert user is not None
        user.today_seen_at = datetime.now(UTC) - ago
        user.looked_before = datetime.now(UTC) - ago - timedelta(days=7)
        await session.commit()


# -- The first run ----------------------------------------------------------------------------


class TestTheFirstRun:
    async def test_it_says_nothing_is_recorded_and_offers_the_first_request(
        self, api_settings: Any, db_engine: Any, fake_redis: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="new@example.invalid", display_name="N", role=UserRole.OWNER))
            await session.commit()
        try:
            async for client in client_for(
                build_app(api_settings, engine=db_engine, redis=fake_redis)
            ):
                body = (await client.get("/")).text
                assert "Nothing is recorded yet." in body
                assert "Research your first company" in body
                assert "Start with two things" in body
                assert 'id="needs-you"' not in body
                assert 'id="worth-doing"' not in body
                assert 'href="/requests/new"' in body
                assert 'href="/settings"' in body
        finally:
            await delete_all(db_engine)


async def _report_count(factory: Any) -> int:
    async with factory() as session:
        return len(list(await session.scalars(select(Report))))
