"""Phase 5's acceptance criteria, re-run end to end against a FakeProvider run.

`docs/PLAN.md` states them in one paragraph, and this module states them as five tests
against **one real run** driven through both gates to an approved, frozen report:

1. All required sections plus all enabled custom sections appear in the PDF.
2. Every figure has a resolvable provenance marker, regardless of which section it came
   from.
3. A custom section renders to institutional quality with no user-authored HTML.
4. The Obsidian vault opens cleanly, with working links.
5. Regenerating a company note preserves user content below the sentinel.

The point of gathering them here is that a criterion is a property of the *system*, not
of the task that happened to implement it. Each of these is covered in more detail by the
task-level suites; what this module adds is that they all hold together, at once, on a
single run that went through the workflow rather than through a fixture.

The run is a FakeProvider run: no network, no model spend, and a stub SEC client serving
recorded bytes. Everything else — the parsers, the verifier, the calculators, the
renderers, the PDF pass, the exporter — is the shipping code.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

import pikepdf
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, UserRole
from aer.db.models import (
    Report,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    User,
)
from aer.obsidian import SENTINEL, export_report
from aer.services.skills import save_skill, set_enabled
from tests.api_fixtures import build_app, client_for
from tests.run_fixtures import Driver, to_final_gate
from tests.test_skill_frontmatter import MOAT_DURABILITY
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

# The inline footnote markers of the HTML notation.
_MARKERS = re.compile(r'<sup class="fn-ref"[^>]*><a[^>]*href="#fn-(\d+)">')

# The §2.12 example skill, verbatim from the frontmatter fixture with its evidence floor
# lowered to one source: the slice run acquires one filing, and this module is about the
# phase's acceptance criteria rather than about the starved-evidence banner.
CUSTOM_SKILL = MOAT_DURABILITY.replace("min_sources: 3", "min_sources: 1")
CUSTOM_KEY = "custom.moat_durability"
CUSTOM_TITLE = "Competitive Moat Durability"


class EnqueueRecorder:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
def settings(api_settings: Settings, tmp_path: Path) -> Settings:
    """The application's settings plus a throwaway vault, for criteria 4 and 5."""
    return api_settings.model_copy(
        update={
            "obsidian_vault_root": tmp_path / "vault",
            "obsidian_personal_root": tmp_path / "personal",
        }
    )


@pytest.fixture
async def clean_slate(db_engine: Any) -> Any:
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        # The custom section this module enables is a skill, and skills outlive the run
        # tables; leaving one behind would add a section to every later run in the suite.
        await connection.execute(
            text("DELETE FROM report_sections WHERE section_key LIKE 'custom.%'")
        )
        await connection.execute(text("DELETE FROM section_definitions WHERE origin = 'skill'"))
        await connection.execute(text("DELETE FROM skill_versions"))
        await connection.execute(text("DELETE FROM skills"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any, settings: Settings) -> dict[str, Any]:
    """A user, a request, and one enabled custom section authored as a skill."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="phase5@example.invalid", display_name="Phase 5", role=UserRole.OWNER)
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
            # The platform's own per-run default, read rather than restated. A hard-coded
            # £2.50 here would have gone on admitting a run the real ceiling refuses.
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.flush()

        saved = await save_skill(session, source=CUSTOM_SKILL, actor=user)
        await set_enabled(session, key="moat_durability", enabled=True, actor=user)
        await session.commit()
        return {"user": user, "request": request, "skill": saved}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> EnqueueRecorder:
    recorder = EnqueueRecorder()
    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", recorder)
    monkeypatch.setattr("aer.web.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def api(
    settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: EnqueueRecorder,
) -> Any:
    async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.fixture
def driver(db_engine: Any, settings: Settings) -> Driver:
    return Driver(db_engine, settings)


@pytest.fixture
async def approved(
    api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
) -> dict[str, Any]:
    """One run, driven through both gates to an approved and frozen report."""
    job_id = await to_final_gate(api, committed["request"].id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step="red_team")
    await driver.advance(job_id)

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        report = await session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None, "the approved run must have produced a report"
        assert report.immutable, "approval freezes the report"
    return {"job_id": job_id, "report": report}


def _pdf_text(pdf_bytes: bytes) -> str:
    """Every bookmark title in the PDF, flattened into one searchable string."""
    titles: list[str] = []

    def walk(items: Any) -> None:
        for item in items:
            titles.append(str(item.title))
            walk(item.children)

    with pikepdf.open(BytesIO(pdf_bytes)) as pdf, pdf.open_outline() as outline:
        walk(outline.root)
    return "\n".join(titles)


class TestCriterionOneEverySectionReachesThePdf:
    """ "All required sections plus all enabled custom sections appear in the PDF.\""""

    async def test_the_pdf_bookmarks_every_section_the_run_produced(
        self, api: Any, approved: dict[str, Any], db_engine: Any
    ) -> None:
        report = approved["report"]
        pdf = await api.get(f"/api/reports/{report.id}/download/pdf")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        # The served bytes are the archived bytes, provably.
        assert pdf.headers["X-Artefact-SHA256"] == hashlib.sha256(pdf.content).hexdigest()

        bookmarks = _pdf_text(pdf.content)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            rows = list(
                await session.execute(
                    select(SectionDefinition.title, SectionDefinition.origin)
                    .join(
                        ReportSection,
                        ReportSection.section_definition_id == SectionDefinition.id,
                    )
                    .where(ReportSection.job_id == approved["job_id"])
                )
            )

        titles = [title for title, _ in rows]
        assert len(titles) >= 18, "the spine is eighteen sections plus whatever is enabled"
        for title in titles:
            assert title in bookmarks, f"the PDF has no bookmark for {title!r}"

        # And the custom one is among them, which is the half of the criterion a
        # built-in-only report would still satisfy.
        custom = [title for title, origin in rows if origin == "skill"]
        assert custom == [CUSTOM_TITLE]
        assert CUSTOM_TITLE in bookmarks


class TestCriterionTwoEveryFigureResolves:
    """ "Every figure has a resolvable provenance marker regardless of which section it
    came from.\""""

    async def test_every_marker_in_the_document_answers_with_its_evidence(
        self, api: Any, approved: dict[str, Any]
    ) -> None:
        job_id = approved["job_id"]
        preview = await api.get(f"/reports/{approved['report'].id}/preview")
        assert preview.status_code == 200

        markers = sorted({int(number) for number in _MARKERS.findall(preview.text)})
        assert markers, "an approved report with figures must carry markers"
        assert markers == list(range(1, len(markers) + 1))

        for number in markers:
            answer = await api.get(f"/runs/{job_id}/footnotes/{number}")
            assert answer.status_code in (200, 303), f"note {number} did not resolve"
            if answer.status_code == 303:
                walk = await api.get(answer.headers["location"])
                assert walk.status_code == 200
                assert 'id="formula"' in walk.text
            else:
                # A source marker answers with the bytes: the full artefact digest.
                digest = re.search(r'id="source-sha256"[^>]*>\s*([0-9a-f]{64})', answer.text)
                unresolved = 'id="unresolved-note"' in answer.text
                assert digest or unresolved, f"note {number} answered with neither"

    async def test_the_report_page_offers_the_walk(
        self, api: Any, approved: dict[str, Any]
    ) -> None:
        """The document is where a reader doubts a number, so the path starts there."""
        page = await api.get(f"/reports/{approved['report'].id}")

        assert 'id="report-preview"' in page.text
        assert 'id="report-claims-link"' in page.text
        assert 'id="report-sources-link"' in page.text


class TestCriterionThreeTheCustomSectionIsInstitutional:
    """ "A custom section renders to institutional quality with no user-authored HTML.\""""

    async def test_it_renders_through_the_generic_walk_and_is_attributed(
        self, api: Any, approved: dict[str, Any]
    ) -> None:
        preview = await api.get(f"/reports/{approved['report'].id}/preview")

        # Rendered from its contract alone: headings, and a table or list of its items.
        assert f'id="section-{CUSTOM_KEY}"' in preview.text
        assert f"<h2>{CUSTOM_TITLE}</h2>" in preview.text

        # Attributed as the operator's own methodology rather than platform output, in
        # both places a reader meets it: the contents page, and the section itself. The
        # chip is asserted by its class — the contents note carries the same words, so a
        # bare text search would pass with the per-section attribution removed.
        assert "the operator's analysis rather than platform output" in preview.text
        section = preview.text[preview.text.index(f'id="section-{CUSTOM_KEY}"') :]
        chip = re.search(r'<div class="custom-chip">([^<]+)</div>', section)
        assert chip is not None, "the custom section must carry its attribution chip"
        assert chip.group(1).strip() == "Custom analysis"

    async def test_no_user_authored_markup_reaches_the_page(
        self, api: Any, approved: dict[str, Any], db_engine: Any
    ) -> None:
        """Markup planted in the section's own content renders as text, not as tags.

        The skill author writes prose and a schema; they never write HTML, and content
        that arrives looking like HTML is data that happens to contain angle brackets.
        """
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            section = await session.scalar(
                select(ReportSection).where(
                    ReportSection.job_id == approved["job_id"],
                    ReportSection.section_key == CUSTOM_KEY,
                )
            )
            assert section is not None
            content = dict(section.content or {})
            content["summary"] = "<script>alert('x')</script> Switching costs are high."
            section.content = content
            await session.commit()

        preview = await api.get(f"/reports/{approved['report'].id}/preview")

        assert "<script>alert" not in preview.text
        assert "&lt;script&gt;" in preview.text


class TestCriterionFourTheVaultOpensCleanly:
    """ "Obsidian vault opens cleanly with working links.\""""

    async def test_every_link_the_export_writes_resolves_to_a_file(
        self, approved: dict[str, Any], settings: Settings, db_engine: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            record = await export_report(
                session, settings=settings, report_id=approved["report"].id
            )
            await session.commit()

        vault = settings.obsidian_vault_root
        assert vault is not None
        files = list(vault.rglob("*.md"))
        assert len(files) == len(record.files)

        stems = {path.stem for path in files}
        links = 0
        for path in files:
            for target in re.findall(r"\[\[([^\]]+?)\]\]", path.read_text(encoding="utf-8")):
                name = target.split("|")[0].split("#")[0].strip()
                assert name in stems, f"{path.name} links [[{name}]], which no file resolves"
                links += 1
        assert links, "an exported run must link its notes to each other"

    async def test_the_run_note_pins_the_skill_version_its_custom_section_used(
        self, approved: dict[str, Any], settings: Settings, db_engine: Any
    ) -> None:
        """Methodology drift is visible: the note records *which* version wrote it."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            await export_report(session, settings=settings, report_id=approved["report"].id)
            await session.commit()

        vault = settings.obsidian_vault_root
        assert vault is not None
        run_note = next((vault / "20-Runs").iterdir())
        text_content = run_note.read_text(encoding="utf-8")

        assert f"{CUSTOM_KEY}@1" in text_content
        assert "aer/custom-section" in text_content


class TestCriterionFiveTheSentinelHolds:
    """ "Regenerating a company note preserves user content below the sentinel.\""""

    async def test_a_second_export_keeps_the_persons_own_half_byte_for_byte(
        self, approved: dict[str, Any], settings: Settings, db_engine: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            await export_report(session, settings=settings, report_id=approved["report"].id)
            await session.commit()

        vault = settings.obsidian_vault_root
        assert vault is not None
        company_note = next((vault / "10-Companies").iterdir())
        mine = "\n\n## My own notes\n\nHand-written, and mine. <-- with an arrow\n"
        company_note.write_text(company_note.read_text(encoding="utf-8") + mine, encoding="utf-8")

        async with factory() as session:
            await export_report(session, settings=settings, report_id=approved["report"].id)
            await session.commit()

        regenerated = company_note.read_text(encoding="utf-8")
        assert regenerated.endswith(mine)
        assert regenerated.count(SENTINEL) == 1
