"""Badge counts: registered by a tool, fetched afterwards, and announced when they land.

Three things can go wrong here and none of them shows up in a test of the count itself.
A slot no provider fills stays empty for ever; a provider no slot names computes a number
nobody sees; and a swap that replaces a live region rather than its contents announces
nothing at all — silently, to exactly the readers who depend on it. Each has a test below.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import fakeredis.aioredis as fake_aioredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from aer.core.enums import JobStatus
from aer.db.models import Job
from aer.errors import IntegrityError
from aer.services.runs import awaiting_approval_count
from aer.web import routes as routes_module
from aer.web.shell import NAV, UNLISTED, flat_items, shell_for
from aer.web.shell import badges as badge_module
from aer.web.shell.badges import (
    Badge,
    BadgeProvider,
    BadgeProviderError,
    _build,
    badges_from,
    cached_counts_for,
    counts_for,
    registered_badges,
    resolve_badge,
)
from aer.web.templating import TEMPLATES_DIR, templates
from tests.api_fixtures import build_app, client_for

FRAGMENT = TEMPLATES_DIR / "_shell" / "badges.html"
NAV_TEMPLATE = TEMPLATES_DIR / "_nav.html"
ADR_DIR = Path(__file__).parent.parent / "docs" / "adr"


def _provider(**overrides: Any) -> BadgeProvider:
    fields: dict[str, Any] = {
        "key": "probe",
        "tool": "research",
        "title": "Probe",
        "label": "things",
        "count_ref": "aer.services.runs:awaiting_approval_count",
        "adr": "0071",
    }
    return BadgeProvider(**{**fields, **overrides})


async def _two(_session: Any, *, user_id: uuid.UUID) -> int:
    return 2


class TestARegisteredCountHasAnOwnerAndARecord:
    def test_a_provider_without_an_adr_is_refused(self) -> None:
        with pytest.raises(BadgeProviderError, match="no ADR reference"):
            _build([_provider(adr="  ")])

    def test_two_providers_cannot_claim_one_slot(self) -> None:
        with pytest.raises(BadgeProviderError, match="Two providers"):
            _build([_provider(), _provider(tool="portfolio")])

    def test_every_shipped_provider_names_an_adr_that_exists(self) -> None:
        missing = [
            provider.key
            for provider in registered_badges()
            if not list(ADR_DIR.glob(f"{provider.adr}-*.md"))
        ]

        assert not missing, f"badges naming an ADR with no file: {missing}"

    def test_an_unregistered_key_is_refused_rather_than_defaulted(self) -> None:
        # No default. A slot with no provider behind it would be a number the sidebar
        # invented, which is the one thing a count in a research platform may never be.
        with pytest.raises(BadgeProviderError, match="No badge provider"):
            resolve_badge("nothing_counts_this")

    def test_a_counter_the_code_lost_fails_loudly(self) -> None:
        with pytest.raises(BadgeProviderError, match="does not resolve"):
            _provider(count_ref="aer.services.runs:no_such_counter").count_fn()

    def test_a_reference_to_something_uncallable_is_refused(self) -> None:
        with pytest.raises(BadgeProviderError, match="cannot be called"):
            _provider(count_ref="aer.services.runs:__all__").count_fn()


class TestSlotsAndProvidersAgree:
    """The join that is a test rather than an import.

    `registry.py` imports `nav.py`; making it import `badges.py` as well to check this at
    construction would put a cycle in the shell and pay for it on every page. So the two
    halves are declared independently and reconciled here.
    """

    def test_every_badge_key_in_the_nav_has_a_provider(self) -> None:
        registered = {provider.key for provider in registered_badges()}
        orphans = sorted(
            item.badge_key
            for item in flat_items()
            if item.badge_key and item.badge_key not in registered
        )

        assert not orphans, (
            f"nav items naming a badge nothing provides: {orphans}. The slot renders and "
            "never fills."
        )

    def test_every_provider_has_a_slot_to_render_into(self) -> None:
        claimed = {item.badge_key for item in flat_items() if item.badge_key}
        unused = sorted(p.key for p in registered_badges() if p.key not in claimed)

        assert not unused, (
            f"badge providers no nav item names: {unused}. The count runs on every page "
            "load and is rendered nowhere."
        )

    def test_at_least_one_item_actually_carries_one(self) -> None:
        # Otherwise both tests above pass by agreeing that nothing exists.
        assert any(item.badge_key for item in flat_items())

    def test_a_badge_belongs_to_a_tool_the_nav_knows(self) -> None:
        tools = {section.tool for section in NAV}
        strangers = sorted(p.key for p in registered_badges() if p.tool not in tools)

        assert not strangers, f"badges owned by a tool with no section: {strangers}"


class TestTheSwapKeepsTheRegion:
    def test_the_fragment_swaps_contents_and_not_the_element(self) -> None:
        """ADR 0077's second gap, asserted rather than remembered.

        `hx-swap-oob="true"` replaces the whole element, which for an `aria-live` region
        means the node a screen reader is watching is thrown away and the replacement is
        never announced. The failure is invisible to everyone who does not depend on it.
        """
        body = FRAGMENT.read_text(encoding="utf-8")

        assert 'hx-swap-oob="innerHTML"' in body
        assert 'hx-swap-oob="true"' not in body

    def test_the_slot_is_a_live_region(self) -> None:
        assert 'aria-live="polite"' in NAV_TEMPLATE.read_text(encoding="utf-8")

    def test_the_slot_and_the_swap_agree_on_the_id(self) -> None:
        """Rendered rather than grepped, because the two ids are built by two templates
        from two different variables — and htmx says nothing at all when an out-of-band
        target is missing; the swap simply does not happen."""
        provider = registered_badges()[0]
        nav = templates.env.get_template("_nav.html").render(
            shell=shell_for("/requests"),
            disclaimer="x",
            csrf_field="csrf_token",
            csrf_token="test-token",
        )
        fragment = templates.env.get_template("_shell/badges.html").render(
            badges=(Badge(key=provider.key, count=1, label=provider.label),)
        )

        expected = f'id="aer-badge-{provider.key}"'
        assert expected in nav
        assert expected in fragment

    def test_the_first_paint_carries_no_number(self) -> None:
        # The whole point of the fetch. A nav that rendered a count would have counted it
        # while the page was being built, which is what this design refuses — and the
        # emptiness is also what `empty:hidden` keys on, so a slot with stray whitespace
        # would paint an empty pill on every page.
        nav = templates.env.get_template("_nav.html").render(
            shell=shell_for("/requests"),
            disclaimer="x",
            csrf_field="csrf_token",
            csrf_token="test-token",
        )

        slot = re.search(r'id="aer-badge-approvals"(.*?)>(.*?)</span>', nav, re.DOTALL)
        assert slot is not None, "the Requests item rendered no badge slot"
        assert slot.group(2) == "", f"the slot is not empty on first paint: {slot.group(2)!r}"

    def test_the_loader_asks_for_the_route_the_registry_excuses(self) -> None:
        body = NAV_TEMPLATE.read_text(encoding="utf-8")

        assert 'hx-get="/_shell/badges"' in body
        assert "/_shell/badges" in UNLISTED

    def test_the_fragment_renders_the_count_and_says_what_it_counts(self) -> None:
        markup = templates.env.get_template("_shell/badges.html").render(
            badges=(Badge(key="approvals", count=3, label="runs waiting for your approval"),)
        )

        assert 'id="aer-badge-approvals"' in markup
        assert ">3<" in markup
        # A bare numeral beside a word is read as "Requests 3" and means nothing.
        assert "3 runs waiting for your approval" in markup

    def test_a_zero_is_swapped_but_renders_as_nothing(self) -> None:
        """A pill reading "0" on every page an operator has nothing waiting on is noise.

        The swap still happens, and that is the part worth keeping: it is what clears a
        number the operator has just acted on. Omitting the badge entirely would leave the
        old one on screen the next time the fragment is fetched.
        """
        markup = templates.env.get_template("_shell/badges.html").render(
            badges=(Badge(key="approvals", count=0, label="runs waiting for your approval"),)
        )

        assert 'hx-swap-oob="innerHTML"' in markup
        assert re.search(r'id="aer-badge-approvals"[^>]*>\s*</span>', markup), (
            f"a zero rendered content: {markup!r}"
        )


class TestTheFragmentSurvivesTheDatabaseBeingDown:
    """The landing page is designed to render with Postgres down, and the nav now fetches
    its counts on every page — including that one.

    asyncpg raises the operating system's error directly when it cannot reach the server,
    so a bare `ConnectionRefusedError` came back past a handler catching `SQLAlchemyError`
    and the fragment answered 500. Nothing visible broke: htmx swallows a failed
    out-of-band fetch and the page renders. What it produced was an unhandled exception in
    the log on every load of the one page whose whole point is degrading gracefully.
    """

    @pytest.fixture
    async def broken_client(self, api_settings, broken_engine, fake_redis):
        async for made in client_for(
            build_app(api_settings, engine=broken_engine, redis=fake_redis)
        ):
            yield made

    async def test_it_answers_with_nothing_rather_than_an_error(self, broken_client) -> None:
        response = await broken_client.get("/_shell/badges")

        assert response.status_code == 200
        assert response.text.strip() == ""

    async def test_the_landing_page_still_renders(self, broken_client) -> None:
        # The property that made this worth fixing rather than logging.
        assert (await broken_client.get("/")).status_code == 200

    async def test_a_database_two_migrations_behind_is_the_same_answer(
        self, api_settings, db_engine, fake_redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second way to be unable to ask, and the one that was still a 500.

        A database nothing is listening on raises the operating system's error; one that
        *is* listening with a schema behind the models raises `ProgrammingError` from the
        same statement. Catching only the first left the fragment answering 500 on exactly
        the machine the front page exists to help — one that has not run the migrations.
        """

        async def behind_the_schema(_session: Any) -> None:
            raise ProgrammingError("SELECT users", {}, Exception('relation "users" does not exist'))

        monkeypatch.setattr(routes_module, "current_user_or_none", behind_the_schema)

        async for made in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
            response = await made.get("/_shell/badges")

            assert response.status_code == 200
            assert response.text.strip() == ""


class TestOneFailingCountDoesNotSilenceTheRest:
    async def test_a_provider_that_raises_is_omitted_rather_than_zeroed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero is a claim — "nothing is waiting" — and it is the wrong one to invent."""

        async def explode(_session: Any, *, user_id: uuid.UUID) -> int:
            raise IntegrityError("the counting table is not there", context={})

        monkeypatch.setattr(
            badge_module,
            "_REGISTRY",
            {"broken": _provider(key="broken"), "fine": _provider(key="fine")},
        )
        monkeypatch.setattr(
            BadgeProvider,
            "count_fn",
            lambda row: explode if row.key == "broken" else _two,
        )

        filled = await counts_for(object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert [badge.key for badge in filled] == ["fine"]
        assert filled[0].count == 2


class TestTheCacheIsBestEffortInBothDirections:
    async def test_a_cache_that_is_down_still_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis being unavailable already shows on the pages that queue work. It must not
        also take out the sidebar, which is chrome."""
        monkeypatch.setattr(badge_module, "_REGISTRY", {"fine": _provider(key="fine")})
        monkeypatch.setattr(BadgeProvider, "count_fn", lambda row: _two)  # noqa: ARG005

        filled = await cached_counts_for(_BrokenRedis(), object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert [(badge.key, badge.count) for badge in filled] == [("fine", 2)]

    async def test_a_second_ask_within_the_window_does_not_count_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        async def counted(_session: Any, *, user_id: uuid.UUID) -> int:
            calls.append(1)
            return 7

        monkeypatch.setattr(badge_module, "_REGISTRY", {"fine": _provider(key="fine")})
        monkeypatch.setattr(BadgeProvider, "count_fn", lambda row: counted)  # noqa: ARG005
        redis = _redis()
        who = uuid.uuid4()

        first = await cached_counts_for(redis, object(), user_id=who)  # type: ignore[arg-type]
        second = await cached_counts_for(redis, object(), user_id=who)  # type: ignore[arg-type]

        assert first == second
        assert len(calls) == 1

    async def test_two_operators_do_not_share_a_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Keyed per user, because the count is "your runs" and one operator seeing
        # another's would be a claim about somebody else's work.
        seen: list[uuid.UUID] = []

        async def counted(_session: Any, *, user_id: uuid.UUID) -> int:
            seen.append(user_id)
            return len(seen)

        monkeypatch.setattr(badge_module, "_REGISTRY", {"fine": _provider(key="fine")})
        monkeypatch.setattr(BadgeProvider, "count_fn", lambda row: counted)  # noqa: ARG005
        redis = _redis()

        await cached_counts_for(redis, object(), user_id=uuid.uuid4())  # type: ignore[arg-type]
        await cached_counts_for(redis, object(), user_id=uuid.uuid4())  # type: ignore[arg-type]

        assert len(seen) == 2

    async def test_a_payload_this_code_did_not_write_is_recomputed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(badge_module, "_REGISTRY", {"fine": _provider(key="fine")})
        monkeypatch.setattr(BadgeProvider, "count_fn", lambda row: _two)  # noqa: ARG005
        redis = _redis()
        who = uuid.uuid4()
        await redis.set(f"aer:shell:badges:{who}", "not json at all")

        filled = await cached_counts_for(redis, object(), user_id=who)  # type: ignore[arg-type]

        assert [(badge.key, badge.count) for badge in filled] == [("fine", 2)]

    def test_a_cached_key_the_registry_no_longer_knows_is_dropped(self) -> None:
        rebuilt = badges_from({"approvals": 4, "a_badge_that_was_removed": 9})

        assert [badge.key for badge in rebuilt] == ["approvals"]

    def test_a_cached_value_that_is_not_a_count_is_dropped(self) -> None:
        # `True` is an `int` in Python, and a badge reading "True" would be a number the
        # cache invented.
        assert badges_from({"approvals": "lots"}) == ()
        assert badges_from({"approvals": True}) == ()


def _redis() -> Any:
    """An in-process Redis, for the reason `api_fixtures.fake_redis` gives for using one.

    A real implementation of the protocol rather than a dictionary with two methods: the
    `ex=` argument and the round trip through bytes are exactly the parts a hand-rolled
    double gets right by construction and the real client might not.
    """
    return fake_aioredis.FakeRedis(decode_responses=True)


class _BrokenRedis:
    async def get(self, key: str) -> str | None:
        raise RedisConnectionError("no route to host")

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        raise RedisConnectionError("no route to host")


class TestTheCountItself:
    """Against a real database, because the join is the part that can be wrong."""

    pytestmark = pytest.mark.integration

    async def test_it_counts_only_this_operators_stopped_runs(self, scene: dict[str, Any]) -> None:
        session = scene["session"]
        job = scene["job"]
        owner = scene["request"].user_id
        before = await awaiting_approval_count(session, user_id=owner)

        job.status = JobStatus.AWAITING_APPROVAL
        await session.flush()

        assert await awaiting_approval_count(session, user_id=owner) == before + 1
        assert await awaiting_approval_count(session, user_id=uuid.uuid4()) == 0
        assert (
            await session.scalar(select(Job.status).where(Job.id == job.id))
            is JobStatus.AWAITING_APPROVAL
        )
