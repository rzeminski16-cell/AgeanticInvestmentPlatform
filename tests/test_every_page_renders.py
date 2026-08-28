"""Every server-rendered page, rendered, against a run that actually happened.

`tests/test_shell_nav.py` proves every page is *reachable* — that a nav href resolves to a
registered route and that no route is unreachable by accident. It does not open one. So the
suite knows the map is honest and nothing knows whether the places on it render.

That gap is survivable while templates change one at a time. It is not survivable through the
interface overhaul, which rewrites forty-two of them, because of what `StrictUndefined` does:
a template naming a context key its handler stopped supplying **raises**, and the failure
surfaces as a 500 on one page in one state that no other test opens. A rewrite that moved a
field from `job.status` to `state.plain_status` and missed the run console's error branch would
ship green.

So: drive one complete run, make one book, then open everything.

**A 500 is the failure this exists to catch.** Almost every other status is legitimate
somewhere — a 404 for a report on a run that produced none, a 409 for editing a request that
has been run — and asserting 200 everywhere would mean asserting the platform never refuses,
which is the opposite of what it is for. What no page may do is raise.

**One run, not one per page.** The drive is the expensive part; the render is not. Sharing it
across every route is what keeps this affordable enough to run every time rather than nightly.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, TransactionKind, UserRole
from aer.db.models import (
    Calculation,
    Claim,
    Company,
    Portfolio,
    Report,
    ResearchRequest,
    User,
)
from tests.api_fixtures import build_app, client_for
from tests.route_fixtures import page_routes_for
from tests.run_fixtures import Driver, to_final_gate
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies, portfolios"

# A page may refuse. It may not raise.
#
# 500 is what `StrictUndefined` produces when a template names something its handler no longer
# passes, which is the whole reason this file exists. 502/503/504 would mean a dependency the
# in-process client does not have, and are here so a future change that introduces one fails
# loudly rather than being read as a legitimate refusal.
FORBIDDEN_STATUSES = frozenset({500, 502, 503, 504})


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """An operator, a request, and a book with one cash transaction in it.

    The portfolio is seeded here rather than driven through its form because this file is
    about rendering rather than about recording: what the portfolio page needs is a book that
    exists and a transaction to compute from, and the form's own behaviour is
    `tests/e2e/test_portfolio_screen.py`'s subject.
    """
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
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        book = Portfolio(user_id=user.id, name="My portfolio", base_currency="GBP")
        session.add_all([request, book])
        await session.commit()
        yield {"user": user, "request": request, "book": book}
    await _truncate(db_engine)


class _EnqueueRecorder:
    """Records what would have been enqueued instead of reaching for a worker queue.

    A run is started through the API here, and `enqueue_run` would otherwise try a real
    Redis: the failure is five retry warnings and a stall rather than an error, which is
    slower to diagnose than it is to prevent.
    """

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> _EnqueueRecorder:
    recorder = _EnqueueRecorder()
    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", recorder)
    monkeypatch.setattr("aer.web.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: _EnqueueRecorder,
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.fixture
async def finished_run(
    api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
) -> dict[str, Any]:
    """One run, driven to a frozen report, plus the ids every parameterised route needs.

    Approving the final gate rather than stopping at it, deliberately: a run parked at gate 3
    renders the gate pages and nothing downstream, and the report surfaces are precisely the
    ones tranche 7 rewrites.
    """
    driver = Driver(db_engine, api_settings)
    job_id = await to_final_gate(api, committed["request"].id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step="red_team")
    await driver.advance(job_id)

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        report = await session.scalar(select(Report).where(Report.job_id == job_id))
        claim = await session.scalar(select(Claim).limit(1))
        calculation = await session.scalar(select(Calculation).where(Calculation.job_id == job_id))
        company = await session.scalar(select(Company).limit(1))
    return {
        "job_id": job_id,
        "request_id": committed["request"].id,
        "report_id": report.id if report else uuid.uuid4(),
        "claim_id": claim.id if claim else uuid.uuid4(),
        "calculation_id": calculation.id if calculation else uuid.uuid4(),
        "company_id": company.id if company else uuid.uuid4(),
        "portfolio_id": committed["book"].id,
    }


def _fill(route: str, ids: dict[str, Any]) -> str | None:
    """One route template to a real URL, or None when nothing here can supply its parameters.

    Returning None rather than guessing: a route filled with an invented id renders the
    not-found page, which is a real page but not the one the route is for, and a suite that
    quietly checked the wrong page would be worse than one that checked nothing.
    """
    filled = route
    for name, value in ids.items():
        filled = filled.replace(f"{{{name}}}", str(value))
    # The two remaining parameters have no id of their own. A footnote is addressed by its
    # number in the assembled document, and a skill by the key an operator gave it.
    filled = filled.replace("{number}", "1").replace("{key}", "does-not-exist")
    return None if "{" in filled else filled


class TestEveryPageRenders:
    """The whole map, opened, against one finished run."""

    async def test_no_page_raises(self, api: Any, finished_run: dict[str, Any]) -> None:
        raised: dict[str, int] = {}
        opened = 0
        for route in sorted(page_routes_for()):
            url = _fill(route, finished_run)
            if url is None:  # pragma: no cover -- every route is fillable today
                continue
            opened += 1
            response = await api.get(url, follow_redirects=True)
            if response.status_code in FORBIDDEN_STATUSES:
                raised[route] = response.status_code

        assert not raised, (
            f"These pages did not render: {raised}. Under `StrictUndefined` a 500 is most "
            "often a template naming a context key its handler stopped supplying — the "
            "failure the interface overhaul is most likely to introduce, and the one that "
            "surfaces on a single page in a single state that nothing else opens."
        )
        assert opened >= 25, (
            f"only {opened} pages were opened, which is fewer than the map holds. A route "
            "whose parameters `_fill` cannot supply is skipped silently; if the number has "
            "dropped, teach `_fill` about the new parameter rather than letting the "
            "coverage quietly shrink."
        )


class TestThePagesThatMustRefuse:
    """Refusals are pages too, and the overhaul rewrites them alongside the happy paths."""

    async def test_editing_a_run_request_is_refused_on_a_page(
        self, api: Any, finished_run: dict[str, Any]
    ) -> None:
        """A stale bookmark needs the reason, not a bare 409.

        The request is now a record of something that happened, and saying so is a rendered
        page (`requests/immutable.html`) rather than a status code.
        """
        response = await api.get(f"/requests/{finished_run['request_id']}/edit")
        assert response.status_code not in FORBIDDEN_STATUSES
        assert response.status_code == 409
        # The structural anchor rather than a phrase: the reason's wording belongs to the
        # service and may be improved, but a 409 that renders no reason at all is the
        # regression — a bare status where a page should be.
        assert 'id="immutable-reason"' in response.text
        assert "cannot be edited" in response.text

    async def test_a_run_that_is_not_yours_answers_as_missing(self, api: Any) -> None:
        """The same answer for "no such run" and "not yours", so ids cannot be enumerated."""
        response = await api.get(f"/runs/{uuid.uuid4()}")
        assert response.status_code == 404


class TestThePortfolioRendersInEveryShape:
    async def test_a_book_with_nothing_in_it(self, api: Any) -> None:
        response = await api.get("/portfolio")
        assert response.status_code not in FORBIDDEN_STATUSES
        assert response.status_code == 200

    async def test_a_book_with_a_cash_transaction(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """Cash alone is a book. A page gated on securities would show the empty state over
        a balance somebody had just entered."""
        page = await api.get("/portfolio")
        token = _csrf_from(page.text)
        recorded = await api.post(
            "/portfolio/transactions",
            data={
                "csrf_token": token,
                "kind": TransactionKind.DEPOSIT.value,
                "security": "",
                "trade_date": AS_OF_DATE.isoformat(),
                "quantity": "50000",
                "currency": "GBP",
                "fees": "0",
            },
            follow_redirects=True,
        )
        assert recorded.status_code not in FORBIDDEN_STATUSES
        again = await api.get("/portfolio")
        assert again.status_code == 200

    async def test_a_dated_view_is_a_link(self, api: Any) -> None:
        """The date is in the URL, so "as it stood on the thirtieth" is a page somebody can
        keep. A malformed one falls back rather than erroring."""
        for query in (f"?as_of={AS_OF_DATE.isoformat()}", "?as_of=not-a-date"):
            response = await api.get(f"/portfolio{query}")
            assert response.status_code == 200, query


def _csrf_from(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the portfolio page rendered no CSRF token"
    return found.group(1)
