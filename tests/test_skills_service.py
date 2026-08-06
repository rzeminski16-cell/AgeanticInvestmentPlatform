"""Saving skills: versions accrete, invalid files never land, and identity holds.

Task 35. The acceptance criterion under test is the write path itself: a skill row cannot
exist with invalid frontmatter, because validation happens before anything is constructed.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Skill, SkillVersion, User
from aer.errors import ValidationError
from aer.services.skills import (
    current_version,
    list_skills,
    save_skill,
    set_enabled,
    versions_of,
)
from aer.skills.frontmatter import SkillFileError
from tests.test_skill_frontmatter import MOAT_DURABILITY

pytestmark = pytest.mark.integration


@pytest.fixture
async def author(db_session: AsyncSession) -> User:
    user = User(email="skills@example.invalid", display_name="Skill Author")
    db_session.add(user)
    await db_session.flush()
    return user


async def _row_counts(session: AsyncSession) -> tuple[int, int]:
    skills = await session.scalar(select(func.count(Skill.id)))
    versions = await session.scalar(select(func.count(SkillVersion.id)))
    return int(skills or 0), int(versions or 0)


class TestSavingCreatesVersions:
    async def test_the_first_save_is_version_one_and_disabled(
        self, db_session: AsyncSession, author: User
    ) -> None:
        row = await save_skill(db_session, source=MOAT_DURABILITY, actor=author)

        assert row.version == 1
        assert row.title == "Competitive Moat Durability"
        assert row.min_sources == 3
        assert row.token_budget == 12000
        assert row.created_by == author.id

        [skill] = await list_skills(db_session)
        assert skill.key == "moat_durability"
        assert skill.enabled is False

    async def test_the_source_round_trips_byte_for_byte(
        self, db_session: AsyncSession, author: User
    ) -> None:
        row = await save_skill(db_session, source=MOAT_DURABILITY, actor=author)

        assert row.source == MOAT_DURABILITY

    async def test_an_edit_is_a_new_version_and_the_old_one_is_untouched(
        self, db_session: AsyncSession, author: User
    ) -> None:
        first = await save_skill(db_session, source=MOAT_DURABILITY, actor=author)
        edited = MOAT_DURABILITY.replace("min_sources: 3", "min_sources: 4")
        second = await save_skill(db_session, source=edited, actor=author)

        assert second.version == 2
        assert second.content_hash != first.content_hash
        assert first.min_sources == 3
        assert second.min_sources == 4

        history = await versions_of(db_session, key="moat_durability")
        assert [v.version for v in history] == [1, 2]

        latest = await current_version(db_session, key="moat_durability")
        assert latest is not None
        assert latest.version == 2

    async def test_saving_identical_bytes_is_refused(
        self, db_session: AsyncSession, author: User
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=author)

        with pytest.raises(ValidationError, match="byte-for-byte"):
            await save_skill(db_session, source=MOAT_DURABILITY, actor=author)


class TestInvalidFilesNeverLand:
    async def test_a_refused_file_writes_nothing(
        self, db_session: AsyncSession, author: User
    ) -> None:
        broken = MOAT_DURABILITY.replace("scope: global", "scope: everywhere")

        with pytest.raises(SkillFileError):
            await save_skill(db_session, source=broken, actor=author)

        assert await _row_counts(db_session) == (0, 0)

    async def test_a_reserved_output_field_never_reaches_the_database(
        self, db_session: AsyncSession, author: User
    ) -> None:
        broken = MOAT_DURABILITY.replace("  summary: string", "  rating: string")

        with pytest.raises(SkillFileError):
            await save_skill(db_session, source=broken, actor=author)

        assert await _row_counts(db_session) == (0, 0)


class TestIdentityHolds:
    async def test_a_key_keeps_its_kind(self, db_session: AsyncSession, author: User) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=author)
        # The same key re-declared as prose-only methodology: a different thing entirely.
        as_methodology = (
            "---\n"
            "aer_skill: 1\n"
            "key: moat_durability\n"
            "kind: methodology\n"
            'title: "Moat lens"\n'
            "version: 1\n"
            "---\n"
            "Weight moats heavily.\n"
        )

        with pytest.raises(ValidationError, match="keeps its kind"):
            await save_skill(db_session, source=as_methodology, actor=author)

    async def test_enabling_is_separate_from_saving(
        self, db_session: AsyncSession, author: User
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=author)

        enabled = await set_enabled(db_session, key="moat_durability", enabled=True, actor=author)
        assert enabled.enabled is True

        disabled = await set_enabled(db_session, key="moat_durability", enabled=False, actor=author)
        assert disabled.enabled is False

    async def test_enabling_an_unknown_key_is_refused(
        self, db_session: AsyncSession, author: User
    ) -> None:
        with pytest.raises(ValidationError, match="No skill is named"):
            await set_enabled(db_session, key="a_typo", enabled=True, actor=author)


class TestTheStoredColumnsAreTheValidatedOnes:
    async def test_nested_structures_round_trip_through_jsonb(
        self, db_session: AsyncSession, author: User
    ) -> None:
        row = await save_skill(db_session, source=MOAT_DURABILITY, actor=author)

        output: dict[str, Any] = row.output_contract or {}
        assert set(output) == {"summary", "durability_years"}
        assert row.allowed_tools == ["search_facts", "search_sources"]
        assert row.applicability["markets"] == ["US", "UK"]
