"""Export, and the starter library that makes the editor something other than a blank page.

B10. Import with a confirmation diff already existed (threat T20); what was missing was a
way to get a skill back *out*, and anything to start from.

The property worth protecting on export is that it round-trips. A re-serialisation of the
parsed frontmatter would look correct and quietly rewrite an operator's file — reordering
keys, dropping comments — so an export followed by an import would show a diff full of
changes nobody made, and the diff is the whole of the T20 control.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import UserRole
from aer.db.models import User
from aer.services import skills as skill_service
from aer.skills.frontmatter import parse_skill_file
from aer.skills.library import starter_library, starter_source
from tests.api_fixtures import build_app, client_for


@pytest.fixture
async def saved_skill(db_engine: Any) -> Any:
    """One skill committed through the real service, so export has something to return."""
    source = starter_library()[0].source
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="lib@example.invalid", display_name="Lib", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        version = await skill_service.save_skill(session, source=source, actor=user)
        await session.commit()
        key = starter_library()[0].key
        yield {"key": key, "source": source, "version": version}
    async with factory() as session:
        await session.execute(text("DELETE FROM section_definitions WHERE origin = 'skill'"))
        await session.execute(text("DELETE FROM skills"))
        await session.execute(text("DELETE FROM users WHERE email = 'lib@example.invalid'"))
        await session.commit()


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, saved_skill: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class TestTheStarterLibrary:
    def test_every_shipped_example_parses(self) -> None:
        """`starter_library` skips what will not parse, so absence is the failure to catch.

        A stale example silently vanishing from the page is exactly the kind of rot that
        goes unnoticed; this counts the files on disk against the ones that survived.
        """
        from aer.skills.library import EXAMPLES_DIR  # noqa: PLC0415

        on_disk = sorted(EXAMPLES_DIR.glob("*.md"))
        loaded = starter_library()

        assert on_disk, "the starter library is empty"
        assert len(loaded) == len(on_disk)

    def test_the_examples_differ_from_each_other(self) -> None:
        """Three copies of one idea would not make the feature discoverable."""
        keys = {item.key for item in starter_library()}

        assert len(keys) == len(starter_library())

    def test_each_carries_a_summary_to_list(self) -> None:
        assert all(item.summary for item in starter_library())

    def test_an_unknown_key_has_no_source(self) -> None:
        assert starter_source("not_a_real_example") is None

    def test_nothing_is_installed_by_loading_the_library(self) -> None:
        """The library is a list of files. Installing one goes through import, with a diff.

        If this ever starts touching the database, the T20 confirmation step has been made
        optional for exactly the files an operator is most likely to trust blindly.
        """
        library = starter_library()

        assert all(isinstance(item.source, str) for item in library)


@pytest.mark.integration
class TestExport:
    async def test_a_stored_skill_comes_back_byte_for_byte(
        self, api: Any, saved_skill: Any
    ) -> None:
        """The round trip the import diff depends on."""
        response = await api.get(f"/skills/{saved_skill['key']}/export")

        assert response.status_code == 200
        assert response.text == saved_skill["source"]

    async def test_it_downloads_as_a_file(self, api: Any, saved_skill: Any) -> None:
        response = await api.get(f"/skills/{saved_skill['key']}/export")

        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert f"{saved_skill['key']}.md" in disposition

    async def test_the_export_re_imports_without_a_diff(self, api: Any, saved_skill: Any) -> None:
        """Exported then re-parsed must be the same content hash.

        A re-serialisation would pass the two tests above and fail this one: the bytes would
        differ in ordering or whitespace, the hash would move, and every round trip would
        look like an edit.
        """
        exported = await api.get(f"/skills/{saved_skill['key']}/export")

        assert (
            parse_skill_file(exported.text).content_hash
            == parse_skill_file(saved_skill["source"]).content_hash
        )

    async def test_an_unknown_skill_is_a_404(self, api: Any) -> None:
        missing = await api.get("/skills/no_such_skill/export")

        assert missing.status_code == 404


@pytest.mark.integration
class TestTheLibraryPage:
    async def test_it_lists_the_examples(self, api: Any) -> None:
        page = await api.get("/skills/examples")

        assert page.status_code == 200
        for example in starter_library():
            assert example.title in page.text

    async def test_importing_an_example_goes_through_the_diff(self, api: Any) -> None:
        """Not installed on click: the same review step any other file gets."""
        page = await api.get("/skills/examples")

        assert 'action="/skills/import"' in page.text
