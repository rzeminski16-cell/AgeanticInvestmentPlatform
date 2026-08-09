"""Emptying the database without an exclusive lock, and without defeating the schema.

Gap A17. The cleanup helper is test infrastructure, which is exactly why it needs tests of
its own: a cleanup that silently deletes nothing leaves the *next* test failing for reasons
that have nothing to do with it, and a cleanup that needs ``CASCADE`` is one that tears
down states the application is forbidden to reach.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import Provider, SourceTier, UserRole
from aer.db.models import Artefact, ResearchRequest, SourceDocument, User
from tests.db_cleanup import delete_all, deletion_order

pytestmark = pytest.mark.integration


class TestTheOrderIsSafeByConstruction:
    def test_it_covers_the_whole_schema(self) -> None:
        # Derived from the metadata rather than from a list somebody maintains, so a table
        # added tomorrow is cleaned up without anybody remembering to say so.
        order = deletion_order()
        assert len(order) > 30
        assert "research_requests" in order

    def test_children_come_before_their_parents(self) -> None:
        # The whole basis of not needing CASCADE. `citations` references `claims`, which
        # references `research_requests`; deleting in that order violates nothing.
        order = deletion_order()
        assert order.index("citations") < order.index("claims")
        assert order.index("claims") < order.index("research_requests")
        assert order.index("research_requests") < order.index("users")

    def test_a_subset_keeps_the_safe_order(self) -> None:
        subset = deletion_order(["users", "research_requests"])
        assert subset == ("research_requests", "users")

    def test_a_name_no_table_carries_is_refused(self) -> None:
        # A typo would otherwise leave the table full, and the test that depended on it
        # empty would fail somewhere else entirely.
        with pytest.raises(LookupError, match="reserch_requests"):
            deletion_order(["reserch_requests"])

    def test_reference_data_the_migrations_install_is_left_alone(self) -> None:
        # A cleanup is not the same as a fresh database: a fresh one *has* the eighteen
        # section definitions and the sector profiles, because migrations put them there.
        # Deleting them leaves a state no deployment has ever been in, and the next test to
        # resolve a section fails somewhere nowhere near its own code — which is precisely
        # what the first version of this helper did.
        order = deletion_order()
        assert "section_definitions" not in order
        assert "sector_profiles" not in order

    def test_naming_a_seeded_table_still_empties_it(self) -> None:
        # For the rare test that wants to prove what happens when the spine is absent.
        assert deletion_order(["section_definitions"]) == ("section_definitions",)

    def test_the_metadata_is_actually_populated(self) -> None:
        # `Base.metadata` is empty until the model modules are imported. Without that
        # import the helper would return nothing, delete nothing, and report success —
        # the worst of the three possible outcomes.
        assert deletion_order() != ()


class TestItEmptiesWhatItSays:
    """Against real committed rows, not the transactional fixture.

    `db_session` wraps each test in a transaction it rolls back, so rows written through it
    are invisible to any other connection — including the one `delete_all` opens. Testing
    the cleanup through that fixture would assert nothing at all: the delete would find an
    empty schema and the fixture would still be holding its own rows. Which is A17's shape
    exactly, in miniature.
    """

    async def test_a_populated_schema_comes_back_empty(self, db_engine: Any) -> None:
        await _seed_committed(db_engine)

        await delete_all(db_engine)

        for model in (SourceDocument, ResearchRequest, User, Artefact):
            assert await _count(db_engine, model) == 0, f"{model.__tablename__} still has rows"

    async def test_it_honours_the_declared_foreign_keys(self, db_engine: Any) -> None:
        # The point of the ordering. `source_documents` holds a RESTRICT reference to
        # `artefacts`; deleting artefacts first would raise, and CASCADE would delete the
        # document without the schema's permission. Neither happens — the delete succeeds
        # and nothing had to override a rule to make it.
        await _seed_committed(db_engine)

        await delete_all(db_engine)

        assert await _count(db_engine, Artefact) == 0

    async def test_naming_a_subset_leaves_the_rest_alone(self, db_engine: Any) -> None:
        await _seed_committed(db_engine)
        try:
            # Children first, or the request's own foreign key would refuse.
            await delete_all(db_engine, ["source_documents", "research_requests"])

            assert await _count(db_engine, ResearchRequest) == 0
            assert await _count(db_engine, User) == 1
        finally:
            await delete_all(db_engine)


async def _count(engine: Any, model: Any) -> int:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _seed_committed(engine: Any) -> None:
    """Rows that really exist, so a separate connection can see and delete them."""
    await delete_all(engine)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        await _seed(session)
        await session.commit()


async def _seed(session: AsyncSession) -> None:
    user = User(email="cleanup@example.invalid", display_name="C", role=UserRole.OWNER)
    artefact = Artefact(
        sha256="c" * 64, size_bytes=10, media_type="application/json", storage_key="cc/c"
    )
    session.add_all([user, artefact])
    await session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=datetime.now(UTC).date(),
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()

    session.add(
        SourceDocument(
            request_id=request.id,
            artefact_id=artefact.id,
            url="https://example.invalid/doc",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            retrieved_at=datetime.now(UTC),
        )
    )
    await session.flush()
