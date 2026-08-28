"""The Overview screen: composed from registries, and honest when one of them cannot answer.

Two claims are under test and they pull in opposite directions. The screen must be
composable — no query of its own, every entry contributed by the tool it belongs to — and
it must never answer "nothing is waiting for you" unless that is true. A registry makes the
first easy and the second easy to get wrong, because the natural way to survive a broken
provider is to skip it, and skipping it is how an empty page becomes a lie.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import GateKind, JobStatus, RequestStatus, UserRole
from aer.db.models import Cost, Job, ResearchRequest, User
from aer.errors import IntegrityError
from aer.services import overview as overview_service
from aer.web.overview import attention as attention_module
from aer.web.overview.attention import (
    Attention,
    AttentionProvider,
    AttentionProviderError,
    Severity,
    _build,
    items_for,
    registered_providers,
)
from aer.web.overview.nav import OVERVIEW
from aer.web.overview.pages import _pounds
from aer.web.overview.research import GATE_ASKS
from aer.web.shell import NAV, flat_items
from tests.api_fixtures import build_app, client_for

ADR_DIR = Path(__file__).parent.parent / "docs" / "adr"


def _provider(**overrides: Any) -> AttentionProvider:
    fields: dict[str, Any] = {
        "key": "probe",
        "tool": "research",
        "items_ref": "aer.web.overview.platform:items",
        "adr": "0071",
    }
    return AttentionProvider(**{**fields, **overrides})


def _item(**overrides: Any) -> Attention:
    fields: dict[str, Any] = {
        "key": "probe.1",
        "tool": "research",
        "severity": Severity.BLOCKED,
        "title": "Something is waiting",
        "detail": "It has been waiting a while.",
        "href": "/requests",
    }
    return Attention(**{**fields, **overrides})


class TestAnItemIsAPieceOfWork:
    def test_an_item_that_leads_nowhere_is_refused(self) -> None:
        # The same rule a provenance badge carries, for the same reason: a feed of things
        # you cannot act on is a notification tray, and this one is a work list.
        with pytest.raises(ValueError, match="no href"):
            _item(href="")

    def test_severity_is_three_decisions_rather_than_a_scale(self) -> None:
        assert {member.value for member in Severity} == {"blocked", "broken", "idle"}


class TestAProviderHasAnOwnerAndARecord:
    def test_a_provider_without_an_adr_is_refused(self) -> None:
        with pytest.raises(AttentionProviderError, match="no ADR reference"):
            _build([_provider(adr=" ")])

    def test_two_providers_cannot_claim_one_key(self) -> None:
        with pytest.raises(AttentionProviderError, match="Two providers"):
            _build([_provider(), _provider(tool="portfolio")])

    def test_every_shipped_provider_names_an_adr_that_exists(self) -> None:
        missing = [
            provider.key
            for provider in registered_providers()
            if not list(ADR_DIR.glob(f"{provider.adr}-*.md"))
        ]

        assert not missing, f"attention providers naming an ADR with no file: {missing}"

    def test_a_builder_the_code_lost_fails_loudly(self) -> None:
        with pytest.raises(AttentionProviderError, match="does not resolve"):
            _provider(items_ref="aer.web.overview.platform:no_such_builder").items_fn()

    def test_a_reference_to_something_uncallable_is_refused(self) -> None:
        with pytest.raises(AttentionProviderError, match="cannot be called"):
            _provider(items_ref="aer.web.overview.platform:TOOL").items_fn()

    def test_every_provider_belongs_to_a_tool_the_nav_knows(self) -> None:
        tools = {section.tool for section in NAV}
        strangers = sorted(p.key for p in registered_providers() if p.tool not in tools)

        assert not strangers, f"attention from a tool with no section: {strangers}"


class TestAFeedThatCannotAskDoesNotSaySoIsFine:
    """The difference between this registry and the badge one, and the reason for both.

    A badge is a hint, so a failed provider drops its number and nobody is misled. This
    feed is the answer to "is anything waiting for me", and an empty one is a claim. A
    provider that failed must therefore become an item rather than a silence.
    """

    async def test_a_provider_that_raises_becomes_an_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def explode(_session: Any, *, user_id: uuid.UUID) -> tuple[Attention, ...]:
            raise IntegrityError("the jobs table is not there", context={})

        monkeypatch.setattr(attention_module, "_REGISTRY", {"research": _provider(key="research")})
        monkeypatch.setattr(AttentionProvider, "items_fn", lambda _row: explode)

        found = await items_for(object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert len(found) == 1
        assert found[0].severity is Severity.BROKEN
        assert "could not be asked" in found[0].title
        assert "the jobs table is not there" in found[0].detail
        # And it says the rest of the feed is incomplete, which is the part that stops a
        # short list being read as a short day.
        assert "incomplete" in found[0].detail

    async def test_one_broken_provider_does_not_hide_anothers_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def explode(_session: Any, *, user_id: uuid.UUID) -> tuple[Attention, ...]:
            raise IntegrityError("gone", context={})

        async def fine(_session: Any, *, user_id: uuid.UUID) -> tuple[Attention, ...]:
            return (_item(key="fine.1"),)

        monkeypatch.setattr(
            attention_module,
            "_REGISTRY",
            {"a_broken": _provider(key="a_broken"), "b_fine": _provider(key="b_fine")},
        )
        monkeypatch.setattr(
            AttentionProvider,
            "items_fn",
            lambda row: explode if row.key == "a_broken" else fine,
        )

        found = await items_for(object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert {item.key for item in found} == {"a_broken.unavailable", "fine.1"}

    async def test_the_worst_comes_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def mixed(_session: Any, *, user_id: uuid.UUID) -> tuple[Attention, ...]:
            return (
                _item(key="i", severity=Severity.IDLE),
                _item(key="b", severity=Severity.BROKEN),
                _item(key="x", severity=Severity.BLOCKED),
            )

        monkeypatch.setattr(attention_module, "_REGISTRY", {"one": _provider(key="one")})
        monkeypatch.setattr(AttentionProvider, "items_fn", lambda _row: mixed)

        found = await items_for(object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert [item.key for item in found] == ["x", "b", "i"]


class TestEveryGateCanSayWhatItWants:
    def test_no_gate_ships_without_a_phrase(self) -> None:
        """Otherwise a new gate renders as "the run stopped and will not continue until you
        decide" — true of every gate, and useless about this one."""
        missing = sorted(gate.value for gate in GateKind if gate not in GATE_ASKS)

        assert not missing, f"gates with no phrase in GATE_ASKS: {missing}"

    def test_each_phrase_completes_the_sentence_it_is_dropped_into(self) -> None:
        # Rendered as "The run stopped so you could {phrase}." — a phrase starting with a
        # capital or ending in a full stop would read as two sentences spliced together.
        for gate, phrase in GATE_ASKS.items():
            assert phrase == phrase.lstrip(), gate
            assert not phrase.endswith("."), gate
            assert phrase[0].islower(), f"{gate}: {phrase!r}"


class TestSpendReadsAsMoney:
    def test_nothing_spent_is_a_zero_rather_than_a_dash(self) -> None:
        assert _pounds(Decimal("0")) == "£0.00"

    def test_a_third_of_a_penny_is_not_nothing(self) -> None:
        # "£0.00" and "we have spent nothing" are different answers, and rounding turns
        # the second into the first on the one page that is supposed to say what is going on.
        assert _pounds(Decimal("0.0034")) == "under £0.01"

    def test_pounds_are_grouped(self) -> None:
        assert _pounds(Decimal("1234.5")) == "£1,234.50"


class TestOverviewIsInTheNav:
    def test_it_leads_the_sections(self) -> None:
        assert next(section.key for section in NAV) == "overview"

    def test_the_section_comes_from_its_own_module(self) -> None:
        # The claim the nav-as-data slice made: a tool contributes a section rather than
        # editing the shell's own file. `registry.py` imports it; it declares nothing.
        assert OVERVIEW in NAV

    def test_the_item_resolves(self) -> None:
        assert "/" in {item.href for item in flat_items()}


# ---------------------------------------------------------------------------------------
# Against a real database. The queries are the part that can be wrong.


pytestmark_integration = pytest.mark.integration


@pytest.fixture
async def clean_slate(db_engine):
    """Empty what this file writes, before each test.

    Setup rather than teardown, for the reason `test_request_api.py` gives: it is what the
    *next* test needs, and it cannot contend with a transaction a finished test still holds.
    `work_orders` is the root every run hangs off (ADR 0072), so cascading from `users`
    takes the requests, jobs and costs with it.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text("TRUNCATE users, work_orders, costs RESTART IDENTITY CASCADE")
        )


@pytest.fixture
async def owner(clean_slate, db_engine) -> User:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="overview@example.invalid", display_name="Overview", role=UserRole.OWNER)
        session.add(user)
        await session.commit()
        return user


@pytest.fixture
async def client(api_settings, db_engine, fake_redis, owner):
    async for made in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield made


class _Seeder:
    """Requests and runs in the committed database, built directly.

    Directly rather than through the workflow: what is under test is what a status means to
    the feed, and driving a real run to each of four states would test the workflow four
    times over.
    """

    def __init__(self, engine: Any, user: User) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._user = user
        self._n = 0

    async def stranger(self) -> uuid.UUID:
        """A second operator, so "not yours" can be told apart from "does not exist"."""
        self._n += 1
        async with self._factory() as session:
            other = User(
                email=f"stranger{self._n}@example.invalid",
                display_name="Somebody else",
                role=UserRole.OWNER,
            )
            session.add(other)
            await session.commit()
            return other.id

    async def request(self, *, status: RequestStatus = RequestStatus.DRAFT, **kw: Any) -> uuid.UUID:
        self._n += 1
        async with self._factory() as session:
            row = ResearchRequest(
                user_id=kw.pop("user_id", self._user.id),
                company_name=kw.pop("company_name", f"Contoso {self._n}"),
                ticker=f"CTS{self._n}",
                exchange="NASDAQ",
                as_of_date=datetime.now(UTC).date(),
                base_currency="USD",
                investment_horizon_months=12,
                max_cost_gbp=Decimal("2.50"),
                status=status,
                **kw,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def run(self, request_id: uuid.UUID, status: JobStatus, **kw: Any) -> uuid.UUID:
        async with self._factory() as session:
            job = Job(
                work_order_id=request_id,
                request_id=request_id,
                workflow_version="test",
                code_version="abc",
                status=status,
                started_at=datetime.now(UTC),
                **kw,
            )
            session.add(job)
            await session.commit()
            return job.id

    async def cost(self, job_id: uuid.UUID | None, amount: str, when: datetime) -> None:
        async with self._factory() as session:
            session.add(
                Cost(
                    job_id=job_id,
                    category="model",
                    provider="anthropic",
                    units=Decimal("1"),
                    unit_type="call",
                    amount_usd=Decimal(amount),
                    amount_gbp=Decimal(amount),
                    fx_rate=Decimal("1"),
                    occurred_at=when,
                )
            )
            await session.commit()


@pytest.fixture
def seed(db_engine, owner) -> _Seeder:
    return _Seeder(db_engine, owner)


@pytest.mark.integration
class TestTheQueriesBound:
    async def test_a_listing_says_how_many_it_left_out(
        self, db_engine, owner, seed: _Seeder
    ) -> None:
        for _ in range(3):
            await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            bounded = await overview_service.stopped_runs(session, user_id=owner.id, limit=2)

        assert len(bounded.rows) == 2
        assert bounded.remaining == 1

    async def test_a_listing_that_fits_leaves_nothing_out(
        self, db_engine, owner, seed: _Seeder
    ) -> None:
        await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            bounded = await overview_service.stopped_runs(session, user_id=owner.id)

        assert len(bounded.rows) == 1
        assert bounded.remaining == 0

    async def test_a_capped_run_is_not_a_failure(self, db_engine, owner, seed: _Seeder) -> None:
        """`JobStatus` says why: it is resumable after a decision, so filing it under
        failure sends an operator to debug a run that is waiting to be told to carry on."""
        await seed.run(await seed.request(), JobStatus.BUDGET_EXCEEDED)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            assert (await overview_service.failed_runs(session, user_id=owner.id)).is_empty
            assert not (await overview_service.capped_runs(session, user_id=owner.id)).is_empty

    async def test_a_draft_with_a_run_is_not_idle(self, db_engine, owner, seed: _Seeder) -> None:
        started = await seed.request()
        await seed.run(started, JobStatus.RUNNING)
        await seed.request()

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            idle = await overview_service.unstarted_requests(session, user_id=owner.id)

        assert [row.id for row in idle.rows] != [started]
        assert len(idle.rows) == 1

    async def test_a_draft_with_several_runs_is_counted_once(
        self, db_engine, owner, seed: _Seeder
    ) -> None:
        # The reason for `NOT EXISTS` rather than an outer join: a request that had been
        # retried twice would otherwise arrive twice and spend two of the eight slots.
        busy = await seed.request()
        await seed.run(busy, JobStatus.FAILED)
        await seed.run(busy, JobStatus.RUNNING)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            failed = await overview_service.failed_runs(session, user_id=owner.id)
            idle = await overview_service.unstarted_requests(session, user_id=owner.id)

        assert len(failed.rows) == 1
        assert idle.is_empty

    async def test_another_operators_work_is_not_yours(
        self, db_engine, owner, seed: _Seeder
    ) -> None:
        await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            mine = await overview_service.stopped_runs(session, user_id=owner.id)
            theirs = await overview_service.stopped_runs(session, user_id=uuid.uuid4())

        assert not mine.is_empty
        assert theirs.is_empty

    async def test_spend_counts_a_charge_whose_run_was_purged(
        self, db_engine, seed: _Seeder
    ) -> None:
        """`costs.job_id` is nullable with ``ON DELETE SET NULL`` so spend outlives the run
        that incurred it. A total joined through `jobs` would shrink when a request was
        purged — the one number here that must not move for an invisible reason."""
        now = datetime.now(UTC)
        await seed.cost(None, "1.50", now)
        await seed.cost(None, "0.25", overview_service.start_of_month(now) - timedelta(days=1))

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            total = await overview_service.spend_since(
                session, since=overview_service.start_of_month(now)
            )

        assert total == Decimal("1.50")


@pytest.mark.integration
class TestThePage:
    async def test_an_operator_who_has_never_started_is_told_how_to(self, client) -> None:
        """An empty work list means two opposite things.

        To somebody who has been using the platform it means caught up. To somebody who
        installed it ten minutes ago it means nothing here works yet — and congratulating the
        second reader on being up to date is the front door failing at its only job.
        """
        response = await client.get("/")

        assert response.status_code == 200
        assert "Start with two things" in response.text
        assert "/requests/new" in response.text
        assert "/settings" in response.text

    async def test_an_operator_who_is_caught_up_is_not_told_to_start(
        self, client, seed: _Seeder
    ) -> None:
        """The complement. Once a request exists the reader is under way, and the empty list
        is an achievement rather than an instruction."""
        request_id = await seed.request()
        await seed.run(request_id, JobStatus.SUCCEEDED)

        response = await client.get("/")

        assert "Nothing is waiting" in response.text
        assert "Start with two things" not in response.text

    async def test_a_stopped_run_appears_with_the_gate_it_wants(
        self, client, seed: _Seeder
    ) -> None:
        request_id = await seed.request(company_name="Contoso Corporation")
        job_id = await seed.run(request_id, JobStatus.AWAITING_APPROVAL)

        body = (await client.get("/")).text

        assert f'data-attention="research.gate.{job_id}"' in body
        assert "Contoso Corporation is waiting for you" in body
        assert f"/runs/{job_id}" in body
        # The run has no paused step and no recorded approval, so `pending_gate` falls back
        # to the gate order and answers PLAN — which is what a run in this state is in fact
        # waiting at. The item says so rather than saying "a gate".
        assert "The run stopped so you could approve its research plan." in body

    async def test_a_failed_run_shows_the_reason_it_recorded(self, client, seed: _Seeder) -> None:
        request_id = await seed.request()
        await seed.run(
            request_id, JobStatus.FAILED, error={"message": "The SEC index was unreachable."}
        )

        body = (await client.get("/")).text

        assert "The SEC index was unreachable." in body

    async def test_the_tiles_are_the_registered_badges(self, client, seed: _Seeder) -> None:
        # The sidebar and the dashboard read the same registry, so they cannot disagree
        # about a number.
        await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        body = (await client.get("/")).text

        assert "Waiting for you" in body
        assert "Spent this month" in body

    async def test_the_page_is_reachable_from_the_nav(self, client) -> None:
        body = (await client.get("/")).text

        assert 'href="/"' in body

    async def test_a_row_that_can_be_previewed_offers_it_as_a_link(
        self, client, seed: _Seeder
    ) -> None:
        job_id = await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        body = (await client.get("/")).text

        assert f'hx-get="/research/runs/{job_id}/preview"' in body
        # And the same anchor is still a link to the page, so the click works with
        # scripting off (ADR 0006).
        assert f'href="/runs/{job_id}"' in body

    async def test_a_draft_nobody_ran_offers_no_preview(self, client, seed: _Seeder) -> None:
        # `preview_href` is optional on purpose: an unrun request has nothing to show that
        # the row does not already say, and a panel repeating it would be a click for
        # nothing.
        request_id = await seed.request()

        body = (await client.get("/")).text

        assert f'data-attention="research.idle.{request_id}"' in body
        assert "/preview" not in body


@pytest.mark.integration
class TestTheDrawerFragment:
    async def test_it_shows_the_run_close_up(self, client, seed: _Seeder) -> None:
        request_id = await seed.request(company_name="Contoso Corporation")
        job_id = await seed.run(request_id, JobStatus.AWAITING_APPROVAL)

        body = (await client.get(f"/research/runs/{job_id}/preview")).text

        assert "Contoso Corporation" in body
        assert "AWAITING_APPROVAL" in body
        assert "approve its research plan" in body
        assert "£0.00" in body
        # The way out. A preview with no next step is a click that costs an operator a
        # decision they still have to go elsewhere to make.
        assert f'href="/runs/{job_id}"' in body

    async def test_it_is_a_fragment_rather_than_a_page(self, client, seed: _Seeder) -> None:
        # It is swapped into a drawer on a page that is already rendered. A whole page here
        # would put a second navigation and a second disclaimer inside the first.
        job_id = await seed.run(await seed.request(), JobStatus.AWAITING_APPROVAL)

        body = (await client.get(f"/research/runs/{job_id}/preview")).text

        assert "<!doctype html>" not in body.lower()
        assert "<nav" not in body

    async def test_another_operators_run_is_a_404(self, client, seed: _Seeder) -> None:
        request_id = await seed.request(user_id=await seed.stranger())
        job_id = await seed.run(request_id, JobStatus.AWAITING_APPROVAL)

        response = await client.get(f"/research/runs/{job_id}/preview")

        assert response.status_code == 404

    async def test_a_run_that_does_not_exist_answers_the_same_way(self, client) -> None:
        # One answer for "no such run" and "not yours", which is the rule every other
        # surface here follows: two would let a caller enumerate ids by watching which
        # ones answer differently.
        response = await client.get(f"/research/runs/{uuid.uuid4()}/preview")

        assert response.status_code == 404
