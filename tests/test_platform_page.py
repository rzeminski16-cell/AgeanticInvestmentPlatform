"""Platform as one page (page specification §19): settings and state, read from the record.

What is pinned here is the drawing's contract rather than its layout: six sheets, each linking
to the page where its lines change, so this page is never the only route to anything; a key
shown as there or not there and never as itself; the discarded line summed from the replies
the schema refused; and the backups saying plainly that nothing records one, rather than a
reassuring line the platform cannot support.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings, load_settings
from aer.core.enums import JobStatus, LimitKind, RequestStatus, TransactionKind, UserRole
from aer.db.models import (
    AgentRun,
    Artefact,
    Cost,
    Job,
    JobStep,
    Portfolio,
    PriceBar,
    Security,
    User,
)
from aer.providers.protocol import SCHEMA_REJECTED
from aer.services import limits as limit_service
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import AS_OF, daily_bars, funded, trade
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

# A value that must never reach the page: the key is there, and that is all anyone reads.
_KEY = "never-shown-4c1a9e"


@pytest.fixture
def keyed_settings(settings_env: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """A platform with a Companies House key and no price feed."""
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_SECRET_KEY", "test-signing-key-not-a-real-one")
    settings_env.setenv("AER_COMPANIES_HOUSE_API_KEY", _KEY)
    return load_settings()


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """A sterling book, a quarter of it in Barclays, and one run that spent money."""
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        artefact = Artefact(sha256="e" * 64, size_bytes=32, media_type="text/csv", storage_key="ee")
        session.add_all([user, artefact])
        await session.flush()
        portfolio = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        barc = Security(
            ticker="BARC",
            exchange="LSE",
            provider_symbol="BARC.LSE",
            name="Barclays",
            quote_currency="GBX",
        )
        session.add_all([portfolio, barc])
        await session.flush()
        session.add(
            PriceBar(
                security_id=barc.id,
                bar_date=AS_OF,
                open=Decimal(248),
                high=Decimal(252),
                low=Decimal(247),
                close=Decimal(250),
            )
        )
        scene = {"user": user, "portfolio": portfolio, "barc": barc, "document": None}
        await funded(session, scene)
        await trade(
            session,
            scene,
            kind=TransactionKind.BUY,
            security=barc,
            quantity="10000",
            price="250",
            currency="GBX",
        )
        await daily_bars(session, barc, until=AS_OF, days=10)
        await _a_run_that_spent(session, user)
        await session.commit()
        yield {**scene, "factory": factory}
    await delete_all(db_engine)


async def _a_run_that_spent(session: Any, user: User) -> None:
    """A research run with two calls: one used, one the schema refused and nobody could use."""
    request = research_request(
        user_id=user.id,
        company_name="Barclays PLC",
        ticker="BARC",
        exchange="LSE",
        as_of_date=datetime.now(UTC).date(),
        base_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="8.00",
        portfolio_context={},
        status=RequestStatus.DRAFT,
    )
    session.add(request)
    await session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="test-1",
        code_version="abc",
        status=JobStatus.SUCCEEDED,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    for sequence, (amount, stop) in enumerate((("2.40", "end_turn"), ("0.30", SCHEMA_REJECTED))):
        step = JobStep(
            job_id=job.id,
            step_key=f"step-{sequence}",
            sequence=sequence,
            status=JobStatus.SUCCEEDED,
            attempt=0,
            idempotency_key=f"{job.id}:step-{sequence}",
            input_hash="0" * 64,
            started_at=datetime.now(UTC),
        )
        session.add(step)
        await session.flush()
        run = AgentRun(
            job_step_id=step.id,
            agent_role="report_writer",
            provider="anthropic",
            model="claude-opus-5",
            input_tokens=100,
            output_tokens=50,
            stop_reason=stop,
        )
        session.add(run)
        await session.flush()
        session.add(
            Cost(
                job_id=job.id,
                agent_run_id=run.id,
                category="llm_output",
                provider="anthropic",
                units=Decimal(1000),
                unit_type="tokens",
                amount_usd=Decimal(amount),
                amount_gbp=Decimal(amount),
                fx_rate=Decimal(1),
            )
        )
    await session.flush()


@pytest.fixture
async def api(keyed_settings: Settings, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    async for client in client_for(build_app(keyed_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _line(page: str, key: str) -> str:
    """One line of a sheet, by its hook, as the page rendered it."""
    found = re.search(rf'<li [^>]*data-line="{re.escape(key)}"[^>]*>(.*?)</li>', page, re.S)
    assert found is not None, f"no line {key!r} on the page"
    return found.group(0)


class TestOnePage:
    async def test_the_six_sheets_each_link_to_where_they_change(self, api: Any) -> None:
        response = await api.get("/platform")

        assert response.status_code == 200
        page = response.text
        for sheet in ("costs", "limits", "monitoring", "data", "backups", "health"):
            assert f'id="{sheet}"' in page, sheet
        # Never the only route to anything (§19's must-not): each editor keeps its own page.
        for editor in ('href="/costs"', 'href="/platform/book"', 'href="/settings"'):
            assert editor in page, editor

    async def test_it_is_where_the_platform_destination_opens(self, api: Any) -> None:
        page = (await api.get("/")).text

        assert re.search(r'<a\s[^>]*href="/platform"', page) is not None
        assert 'href="/healthz"' not in (await api.get("/platform")).text


class TestWhatItSays:
    async def test_a_key_is_there_or_not_and_never_itself(self, api: Any) -> None:
        page = (await api.get("/platform")).text

        assert _KEY not in page
        assert "Ready" in _line(page, "companies-house")
        prices = _line(page, "prices")
        assert "No key" in prices
        assert "text-warning-ink" in prices

    async def test_the_discarded_line_is_the_spend_the_schema_refused(self, api: Any) -> None:
        page = (await api.get("/platform")).text

        discarded = _line(page, "discarded")
        assert "£0.30" in discarded
        assert "text-warning-ink" in discarded
        assert "£2.70" in _line(page, "spent")
        assert "1 run" in _line(page, "research")

    async def test_a_stated_limit_reads_as_the_book_page_states_it(
        self, api: Any, committed: Any
    ) -> None:
        async with committed["factory"]() as session:
            user = await session.get(User, committed["user"].id)
            book = await session.get(Portfolio, committed["portfolio"].id)
            await limit_service.state_limit(
                session,
                portfolio=book,
                actor=user,
                kind=LimitKind.SINGLE_POSITION,
                fraction=Decimal("0.20"),
            )
            await session.commit()

        page = (await api.get("/platform")).text

        line = _line(page, "single_position")
        assert "20%" in line
        assert "over it today" in line
        assert "BARC" in line
        # A limit never stated is said to be unset, never given a value the page chose.
        assert "not set" in _line(page, "five_largest")

    async def test_the_backups_say_nothing_records_one(self, api: Any) -> None:
        page = (await api.get("/platform")).text

        assert "Not recorded" in _line(page, "last-backup")
        assert "Never recorded" in _line(page, "last-proved")

    async def test_health_names_each_part_of_the_machinery(self, api: Any) -> None:
        page = (await api.get("/platform")).text

        for part in ("worker", "daily-pass", "queue", "last-run", "database"):
            _line(page, part)
        # No worker has reported to the in-process Redis, and the page says so.
        assert "Not running" in _line(page, "worker")
        assert "Barclays PLC (BARC)" in _line(page, "last-run")
