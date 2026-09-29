"""The operator's limits on the pages that draw them (ADR 0136).

The service's own rules are `test_limits.py`'s. What is pinned here is the drawing: the limits
are stated on Platform, under *Book*, and nowhere else; every page about the book says *over
the {n}% ceiling you set* beside the recorded figure, in the warning ink, where one is stated
and past; and where none is stated, each says that none is set — no page draws a default.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import LimitKind, TransactionKind, UserRole
from aer.db.models import Artefact, BookLimit, Company, Portfolio, PriceBar, Security, User
from aer.services import limits as limit_service
from aer.services import theses as thesis_service
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import AS_OF, daily_bars, funded, trade

pytestmark = pytest.mark.integration

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "aer"


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """A sterling book, a quarter of it in Barclays, seen by the application."""
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
        # Ten thousand at 250p is £25,000 of a £100,000 book: a quarter, past any ceiling
        # below 25% and inside any above it.
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
        await session.commit()
        yield {**scene, "factory": factory}
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return found.group(1)


async def _state(api: Any, kind: LimitKind, percent: str, **extra: str) -> Any:
    page = (await api.get("/platform/book")).text
    return await api.post(
        "/platform/book/limits",
        data={"csrf_token": _csrf(page), "kind": kind.value, "percent": percent, **extra},
    )


class TestTheBookPage:
    async def test_each_limit_is_blank_until_stated(self, api: Any) -> None:
        page = (await api.get("/platform/book")).text

        assert page.count('data-set="no"') == 2
        assert "You have stated none, so none is drawn on any page." in page
        # No default and no example value anywhere a limit is typed (ADR 0136 §1).
        assert 'name="percent" value=' not in page
        assert "placeholder" not in page.split('id="limits"')[1].split("</section>")[0]

    async def test_a_stated_limit_says_what_it_means_today(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        stated = await _state(api, LimitKind.SINGLE_POSITION, "10")

        assert stated.status_code == 303
        page = (await api.get("/platform/book")).text
        row = page.split('data-limit="single_position"')[1].split("</li>")[0]
        assert 'data-field="value">10%<' in row
        assert "1 position is over it today: BARC" in row
        async with committed["factory"]() as session:
            limit = await session.scalar(select(BookLimit))
            assert limit is not None
            assert limit.user_id == committed["user"].id
            assert limit.fraction == Decimal("0.1")

    async def test_a_changed_limit_keeps_the_old_one_below(self, api: Any) -> None:
        await _state(api, LimitKind.FIVE_LARGEST, "50")
        await _state(api, LimitKind.FIVE_LARGEST, "40")

        page = (await api.get("/platform/book")).text

        assert 'id="limit-history"' in page
        assert "replaced on" in page
        assert "by 40%" in page

    async def test_a_limit_that_is_not_a_share_is_refused_with_the_form_again(
        self, api: Any
    ) -> None:
        refused = await _state(api, LimitKind.SINGLE_POSITION, "120")

        assert refused.status_code == 422
        assert 'id="limit-problem"' in refused.text
        assert "share of the book" in refused.text

    async def test_withdrawing_needs_a_reason_and_keeps_it(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await _state(api, LimitKind.SINGLE_POSITION, "10")
        async with committed["factory"]() as session:
            limit = await session.scalar(select(BookLimit))
            assert limit is not None
        page = (await api.get("/platform/book")).text

        refused = await api.post(
            f"/platform/book/limits/{limit.id}/withdraw",
            data={"csrf_token": _csrf(page), "reason": ""},
        )
        # The refusal is the page again, with a fresh token; the next attempt is made from it.
        withdrawn = await api.post(
            f"/platform/book/limits/{limit.id}/withdraw",
            data={"csrf_token": _csrf(refused.text), "reason": "A concentrated book is the plan."},
        )

        assert refused.status_code == 422
        assert withdrawn.status_code == 303
        after = (await api.get("/platform/book")).text
        assert "A concentrated book is the plan." in after
        assert after.count('data-set="no"') == 2

    async def test_a_missing_token_states_nothing(self, api: Any) -> None:
        response = await api.post(
            "/platform/book/limits", data={"kind": "single_position", "percent": "10"}
        )

        assert response.status_code == 403
        assert "No limit was stated" in response.text


class TestTheRiskPage:
    async def test_each_figure_stands_beside_its_ceiling_or_the_lack_of_one(self, api: Any) -> None:
        before = (await api.get("/risk")).text
        assert "no ceiling set" in before
        assert 'id="no-limits"' in before

        await _state(api, LimitKind.SINGLE_POSITION, "10")
        page = (await api.get("/risk")).text

        largest = page.split('data-concentration="largest-position"')[1].split("</div>")[0]
        assert largest.startswith(' data-over="yes"')
        assert "ceiling 10%" in largest
        assert "over the ceiling you set" in largest
        assert 'id="no-limits"' not in page


class TestThePortfolio:
    async def test_a_holding_past_its_ceiling_is_flagged_counted_and_filtered(
        self, api: Any
    ) -> None:
        before = (await api.get("/portfolio")).text
        assert "none set" in before
        assert (await api.get("/portfolio?show=over_ceiling")).text.count('id="no-ceiling"') == 1

        await _state(api, LimitKind.SINGLE_POSITION, "10")
        page = (await api.get("/portfolio")).text
        filtered = (await api.get("/portfolio?show=over_ceiling")).text

        assert "over ceiling" in page
        assert "data-ceiling-line" in page
        assert "1 position" in page
        assert 'data-holding="BARC.LSE"' in filtered
        assert 'id="no-ceiling"' not in filtered

    async def test_within_the_ceiling_nothing_is_flagged(self, api: Any) -> None:
        await _state(api, LimitKind.SINGLE_POSITION, "30")

        filtered = (await api.get("/portfolio?show=over_ceiling")).text

        assert 'data-holding="BARC.LSE"' not in filtered
        assert 'id="nothing-matches"' in filtered


class TestThePosition:
    async def test_the_weight_reads_against_the_ceiling(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await _state(api, LimitKind.SINGLE_POSITION, "10")

        page = (await api.get(f"/portfolio/positions/{committed['barc'].id}")).text

        assert "/ 10%" in page
        assert 'id="over-ceiling"' in page
        assert "Over the 10% ceiling you set." in page


class TestTheDecisionCheck:
    async def test_a_what_if_past_a_ceiling_is_said_and_blocks_nothing(self, api: Any) -> None:
        await _state(api, LimitKind.SINGLE_POSITION, "10")

        past = await api.get("/decisions/check", params={"security": "BARC.LSE", "what_if": "40"})
        within = await api.get("/decisions/check", params={"security": "BARC.LSE", "what_if": "5"})

        assert 'id="limit-crossed"' in past.text
        assert "BARC would be past the 10% ceiling you set." in past.text
        # The record control's words travel with the panel, out of band.
        assert 'id="record-label" hx-swap-oob="true">Record it anyway<' in past.text
        assert 'id="limit-crossed"' not in within.text
        assert 'id="record-label" hx-swap-oob="true">Record it<' in within.text

    async def test_the_page_itself_says_record_it_anyway(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        # The record form is drawn only once a thesis is open for a decision to act on.
        async with committed["factory"]() as session:
            company = Company(
                name="Barclays PLC", ticker="BARC", exchange="LSE", company_number="00048839"
            )
            session.add(company)
            await session.flush()
            user = await session.get(User, committed["user"].id)
            assert user is not None
            await thesis_service.write_thesis(
                session, user=user, company=company, title="The bank re-rates"
            )
            await session.commit()
        await _state(api, LimitKind.SINGLE_POSITION, "10")

        page = await api.get("/decisions/new", params={"security": "BARC.LSE", "what_if": "40"})

        assert '<span id="record-label">Record it anyway</span>' in page.text
        assert "disabled" not in page.text.split('id="record"')[1].split(">")[0]


class TestToday:
    async def test_the_concentration_suggestion_is_earned_by_the_operators_ceiling(
        self, api: Any
    ) -> None:
        before = (await api.get("/")).text
        assert "Look at your concentration" not in before

        await _state(api, LimitKind.FIVE_LARGEST, "20")
        after = (await api.get("/")).text

        assert "Look at your concentration" in after
        assert "past the 20% ceiling you set" in after


class TestALimitNeverLeavesTheMachine:
    def test_nothing_that_renders_a_report_reads_a_limit(self) -> None:
        """ADR 0136 §5: a limit is on the operator's own pages and never in a report or an
        export. Nothing under the report's sections, its renderers or its exports imports
        the limits, so none can print one."""
        offenders = [
            str(path.relative_to(_SOURCE))
            for folder in ("sections", "render", "export", "obsidian")
            if (_SOURCE / folder).is_dir()
            for path in (_SOURCE / folder).rglob("*.py")
            if "services.limits" in path.read_text(encoding="utf-8")
            or "book_limits" in path.read_text(encoding="utf-8")
        ]

        assert offenders == []

    def test_the_limits_service_is_the_only_writer(self) -> None:
        # A constructor call, not the model's own `class BookLimit(Base)` line.
        constructed = re.compile(r"(?<!class )\bBookLimit\(")
        writers = [
            str(path.relative_to(_SOURCE))
            for path in _SOURCE.rglob("*.py")
            if constructed.search(path.read_text(encoding="utf-8"))
        ]

        assert writers == ["services/limits.py"]
        assert limit_service.state_limit is not None
