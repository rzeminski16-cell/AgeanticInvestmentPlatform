"""A limit on the book is the operator's, stated and never proposed, and it blocks nothing.

ADR 0136. What is pinned here: a limit is a row the operator states and nothing else writes;
a changed limit supersedes the old one rather than editing it, and the history keeps both; a
withdrawal needs a reason; and the comparison a page makes is a word beside a figure, never a
new figure. The schema's own checks are exercised too, because the migration is the
enforcement and autogenerate does not compare them.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import aer.agents
from aer.core.enums import LimitKind, UserRole
from aer.db.models import AuditEvent, BookLimit, User
from aer.errors import ConflictError, ValidationError
from aer.services import limits

pytestmark = pytest.mark.integration


async def _state(
    session: AsyncSession,
    book: dict[str, Any],
    kind: LimitKind,
    fraction: str,
    sector: str | None = None,
) -> BookLimit:
    return await limits.state_limit(
        session,
        portfolio=book["portfolio"],
        actor=book["user"],
        kind=kind,
        fraction=Decimal(fraction),
        sector=sector,
    )


class TestALimitIsStated:
    async def test_it_is_the_operators_row_with_its_book_and_its_owner(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        stated = await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")

        assert stated.user_id == book["user"].id, "ADR 0120: every new table carries its owner"
        assert stated.portfolio_id == book["portfolio"].id
        assert stated.fraction == Decimal("0.10")
        assert stated.stated_by == book["user"].email
        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])
        assert in_force.single_position is not None
        assert in_force.single_position.id == stated.id
        chained = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "limit.stated")
        )
        assert chained is not None
        assert chained.subject_id == book["portfolio"].id

    async def test_until_one_is_stated_there_is_none(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """No default and no suggestion (ADR 0136 §1)."""
        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])

        assert not in_force.stated
        assert in_force.single_position is None
        assert in_force.five_largest is None
        assert in_force.sectors == {}

    async def test_a_sector_limit_names_its_sector_and_the_others_name_none(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError, match="names the sector"):
            await _state(db_session, book, LimitKind.SECTOR, "0.2")
        with pytest.raises(ValidationError, match="names no sector"):
            await _state(db_session, book, LimitKind.FIVE_LARGEST, "0.5", sector="Banks")

        semis = await _state(db_session, book, LimitKind.SECTOR, "0.2", sector="Semiconductors")

        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])
        assert in_force.for_sector("Semiconductors") == semis
        assert in_force.for_sector("Banks") is None

    @pytest.mark.parametrize("fraction", ["0", "-0.1", "1.5", "NaN"])
    async def test_a_limit_is_a_share_of_the_book(
        self, db_session: AsyncSession, book: dict[str, Any], fraction: str
    ) -> None:
        with pytest.raises(ValidationError, match="share of the book"):
            await _state(db_session, book, LimitKind.SINGLE_POSITION, fraction)

    async def test_only_the_books_owner_states_one(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        stranger = User(email="stranger@example.invalid", display_name="S", role=UserRole.OWNER)
        db_session.add(stranger)
        await db_session.flush()

        with pytest.raises(ConflictError, match="whose book it limits"):
            await limits.state_limit(
                db_session,
                portfolio=book["portfolio"],
                actor=stranger,
                kind=LimitKind.SINGLE_POSITION,
                fraction=Decimal("0.1"),
            )


class TestALimitIsSupersededNeverEdited:
    async def test_a_changed_limit_is_a_new_row_and_the_old_one_stays(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        first = await _state(db_session, book, LimitKind.FIVE_LARGEST, "0.5")

        second = await _state(db_session, book, LimitKind.FIVE_LARGEST, "0.45")

        assert second.supersedes_id == first.id
        assert first.fraction == Decimal("0.5"), "the old row is untouched"
        assert not first.is_withdrawn, "superseded is not withdrawn"
        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])
        assert in_force.five_largest == second
        history = await limits.history_of(db_session, portfolio=book["portfolio"])
        assert {row.id for row in history} == {first.id, second.id}

    async def test_restating_the_limit_in_force_writes_nothing(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")

        with pytest.raises(ConflictError, match="already the single position ceiling"):
            await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.1")

    async def test_a_sectors_limit_supersedes_only_its_own_sector(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        banks = await _state(db_session, book, LimitKind.SECTOR, "0.2", sector="Banks")
        semis = await _state(db_session, book, LimitKind.SECTOR, "0.3", sector="Semiconductors")

        assert semis.supersedes_id is None
        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])
        assert in_force.sectors == {"Banks": banks, "Semiconductors": semis}

    async def test_a_limit_is_superseded_at_most_once(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """The unique supersession is the schema's: a history that forked would be two
        answers to what the operator allowed themselves."""
        first = await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")
        await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.12")

        db_session.add(
            BookLimit(
                portfolio_id=book["portfolio"].id,
                user_id=book["user"].id,
                kind=LimitKind.SINGLE_POSITION,
                fraction=Decimal("0.15"),
                stated_by=book["user"].email,
                supersedes_id=first.id,
            )
        )
        with pytest.raises(IntegrityError, match="uq_book_limits_supersedes_once"):
            await db_session.flush()


class TestALimitIsWithdrawnWithAReason:
    async def test_withdrawn_it_leaves_force_and_keeps_its_reason(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        stated = await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")

        await limits.withdraw_limit(
            db_session,
            portfolio=book["portfolio"],
            limit=stated,
            actor=book["user"],
            reason="A concentrated book is the strategy now.",
        )

        assert stated.withdrawn_reason == "A concentrated book is the strategy now."
        in_force = await limits.limits_of(db_session, portfolio=book["portfolio"])
        assert in_force.single_position is None

    async def test_a_withdrawal_needs_a_reason(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        stated = await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")

        with pytest.raises(ValidationError, match="needs a reason"):
            await limits.withdraw_limit(
                db_session,
                portfolio=book["portfolio"],
                limit=stated,
                actor=book["user"],
                reason=" ",
            )

    async def test_a_replaced_limit_cannot_be_withdrawn(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        first = await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.10")
        await _state(db_session, book, LimitKind.SINGLE_POSITION, "0.12")

        with pytest.raises(ConflictError, match="not in force"):
            await limits.withdraw_limit(
                db_session,
                portfolio=book["portfolio"],
                limit=first,
                actor=book["user"],
                reason="r",
            )


class TestTheSchemaRefusesWhatTheServiceDoes:
    async def test_a_sector_kind_with_no_sector_is_refused_by_the_database(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        db_session.add(
            BookLimit(
                portfolio_id=book["portfolio"].id,
                user_id=book["user"].id,
                kind=LimitKind.SECTOR,
                fraction=Decimal("0.2"),
                stated_by=book["user"].email,
            )
        )
        with pytest.raises(IntegrityError, match="book_limit_sector_iff_sector_kind"):
            await db_session.flush()

    async def test_a_share_above_the_whole_book_is_refused_by_the_database(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        db_session.add(
            BookLimit(
                portfolio_id=book["portfolio"].id,
                user_id=book["user"].id,
                kind=LimitKind.SINGLE_POSITION,
                fraction=Decimal("1.5"),
                stated_by=book["user"].email,
            )
        )
        with pytest.raises(IntegrityError, match="book_limit_is_a_share"):
            await db_session.flush()


class TestTheComparisonIsAWordNotAFigure:
    def _limit(self, fraction: str) -> BookLimit:
        return BookLimit(kind=LimitKind.SINGLE_POSITION, fraction=Decimal(fraction))

    def test_over_is_strictly_above(self) -> None:
        limit = self._limit("0.10")

        assert limits.is_over(Decimal("0.114"), limit)
        assert not limits.is_over(Decimal("0.10"), limit), "at the ceiling is not over it"
        assert not limits.is_over(Decimal("0.114"), None), "no limit, nothing to be over"
        assert not limits.is_over(None, limit)

    def test_near_is_within_two_points_and_not_over(self) -> None:
        limit = self._limit("0.50")

        assert limits.is_near(Decimal("0.485"), limit)
        assert limits.is_near(Decimal("0.48"), limit), "two points is within two points"
        assert not limits.is_near(Decimal("0.472"), limit), "2.8 points is not"
        assert not limits.is_near(Decimal("0.47"), limit)
        assert not limits.is_near(Decimal("0.51"), limit), "over is not near"
        assert not limits.is_near(Decimal("0.49"), None)

    def test_the_words_are_the_operators_own_value(self) -> None:
        assert limits.ceiling_words(self._limit("0.100000")) == "the 10% ceiling you set"
        assert limits.limit_percent(self._limit("0.125000")) == "12.5%"

    @pytest.mark.parametrize(
        ("typed", "fraction"), [("10", "0.1"), ("12.5%", "0.125"), (" 50 % ", "0.5"), ("100", "1")]
    )
    def test_a_typed_percentage_is_the_fraction_stored(self, typed: str, fraction: str) -> None:
        assert limits.parse_percent(typed) == Decimal(fraction)

    @pytest.mark.parametrize("typed", ["", "ten", "0", "-5", "101"])
    def test_a_typed_percentage_that_is_not_a_share_is_refused(self, typed: str) -> None:
        with pytest.raises(ValidationError):
            limits.parse_percent(typed)


class TestNothingButTheOperatorWritesOne:
    def test_no_agent_can_return_a_limit(self) -> None:
        """ADR 0136 §1: no agent's schema has a limit's fields, so no model output can be
        turned into one by accident or by injection. Every model class in every module of
        the agents package is read, and the walk is asserted to have found some."""
        words = {"limit", "limits", "ceiling", "ceilings"}
        models = []
        for found in pkgutil.walk_packages(aer.agents.__path__, prefix="aer.agents."):
            module = importlib.import_module(found.name)
            models.extend(
                member
                for _, member in inspect.getmembers(module, inspect.isclass)
                if issubclass(member, BaseModel) and member.__module__ == module.__name__
            )
        offenders = [
            f"{model.__name__}.{field}"
            for model in models
            for field in model.model_fields
            if words & set(field.split("_"))
        ]

        assert len(models) > 20, "the walk found the agents' schemas"
        assert offenders == []
