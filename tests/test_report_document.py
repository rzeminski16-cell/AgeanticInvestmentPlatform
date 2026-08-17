"""One assembly, three serialisations — held together by a golden document.

The scene below is a fixed report: every id, date and value pinned, every branch of the
contract walk exercised — paragraphs, scalars, bullets, described objects, tables with
contract-ordered columns, mismatched-shape lists, dual calculation-and-source markers,
within-section citation de-duplication, unresolved citations, status notes, warning
banners, the sector block, the withheld-comps disclosure and both appendix date shapes.

``golden.md`` was recorded from the renderer **as it stood before task 46's refactor**,
and the byte-identity test is what makes "the Markdown module becomes a serialiser of
``ReportDocument`` with byte-identical output" a held property rather than a claim. To
re-record after a deliberate change, run with ``UPDATE_GOLDEN=1`` (not ``AER_``-prefixed:
the hermetic conftest strips that prefix from every test's environment) — the test then
writes the file and fails, so an update can never pass silently.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.comps import WithheldComps
from aer.core.enums import JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    Evaluation,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    Skill,
    SourceDocument,
    User,
)
from aer.render.document import (
    CoverageNote,
    ReportDocument,
    _display_value,
    assemble_document,
)
from aer.render.html import _blocks, _emphasise, render_html
from aer.render.markdown import (
    RenderedReport,
    SectorNote,
    render_markdown,
    serialise_markdown,
)
from aer.sections.render import Banner, Heading, _unescaped
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fx_report"
GOLDEN_MD = FIXTURES / "golden.md"
GOLDEN_HTML = FIXTURES / "golden.html"

# The inline markers of each notation, in order of appearance. The Markdown pattern
# excludes the footnote definitions (``[^n]:``); the HTML pattern matches only the
# superscript markers, never the back-references.
_MD_MARKERS = re.compile(r"\[\^(\d+)\](?!:)")
_HTML_MARKERS = re.compile(r'<sup class="fn-ref"[^>]*><a[^>]*href="#fn-(\d+)">')

# Every id pinned, so the rendered document is stable byte for byte across runs. The
# job id is among them now that footnotes carry their drill-down path.
JOB_ID = uuid.UUID(int=0x1000)
CALC_ID = uuid.UUID(int=0x1001)
DOC_ONE_ID = uuid.UUID(int=0x2001)
DOC_TWO_ID = uuid.UUID(int=0x2002)
MISSING_CALC_ID = uuid.UUID(int=0x3001)
MISSING_DOC_ID = uuid.UUID(int=0x3002)

GENERATED_AT = datetime(2022, 7, 2, 9, 30, tzinfo=UTC)
RETRIEVED_AT = datetime(2022, 7, 1, 12, 0, tzinfo=UTC)

_OVERVIEW_CONTRACT: dict[str, Any] = {
    "type": "object",
    "title": "Golden Overview",
    "properties": {
        "thesis": {"type": "string", "title": "Thesis"},
        "headcount": {"type": "number", "title": "Headcount"},
        "key_points": {"type": "array", "title": "Key Points", "items": {"type": "string"}},
        "flagship": {"type": "object", "title": "Flagship Product"},
        "figures": {
            "type": "array",
            "title": "Figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
        "notes_mixed": {"type": "array", "title": "Mixed Notes"},
        "ignored_empty": {"type": "string", "title": "Ignored When Empty"},
    },
}

_PROSE_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {"commentary": {"type": "string", "title": "Commentary"}},
}

_UNRESOLVED_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "figures": {
            "type": "array",
            "title": "Figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
    },
}


async def _definition(
    session: AsyncSession,
    *,
    key: str,
    title: str,
    position: int,
    contract: dict[str, Any],
) -> SectionDefinition:
    definition = SectionDefinition(
        key=key,
        version=1,
        origin="builtin",
        title=title,
        position=Decimal(position),
        required=False,
        output_contract=contract,
        evidence_policy={"min_sources": 0, "requires_primary": False},
        token_budget=1000,
        allowed_tools=[],
        applicability={},
    )
    session.add(definition)
    await session.flush()
    return definition


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="golden@example.invalid", display_name="Golden", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

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
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        id=JOB_ID,
        request_id=request.id,
        workflow_version="golden_scene_v1",
        code_version="goldencode123456",
        status=JobStatus.RUNNING,
        started_at=GENERATED_AT,
    )
    db_session.add(job)
    await db_session.flush()

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    payload = b"golden artefact bytes\n"
    artefact = Artefact(
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        media_type="text/html",
        size_bytes=len(payload),
        storage_key="golden/e3b0",
    )
    # Its own bytes for the second document: one record per artefact per request
    # (gap C4), and two real documents never share a digest. Fixed sha so the golden
    # scene stays deterministic.
    artefact_two = Artefact(
        sha256="a3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b8aa",
        media_type="text/html",
        size_bytes=len(payload),
        storage_key="golden/a3b0",
    )
    db_session.add_all([artefact, artefact_two])
    await db_session.flush()

    dated = SourceDocument(
        id=DOC_ONE_ID,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        title="Form 10-K, fiscal 2022",
        publisher="US Securities and Exchange Commission",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=RETRIEVED_AT,
        publication_date=date(2022, 6, 15),
        quarantined=False,
    )
    undated = SourceDocument(
        id=DOC_TWO_ID,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact_two.id,
        url="https://www.microsoft.com/investor/segment-data.html",
        title="Segment data pack",
        publisher=None,
        provider=Provider.ISSUER_IR,
        source_tier=SourceTier.T2_ISSUER,
        retrieved_at=RETRIEVED_AT,
        publication_date=None,
        quarantined=False,
    )
    db_session.add_all([dated, undated])

    calculation = Calculation(
        id=CALC_ID,
        job_id=job.id,
        name="revenue_cagr",
        formula="cagr = (end / start) ** (1 / years) - 1",
        function_ref="aer.calc.basic:cagr",
        code_version="goldencode123456",
        inputs=[],
        output_value=Decimal("0.18"),
        output_unit="ratio",
    )
    db_session.add(calculation)
    await db_session.flush()

    overview = await _definition(
        db_session,
        key="golden_overview",
        title="Golden Overview",
        position=100,
        contract=_OVERVIEW_CONTRACT,
    )
    warned = await _definition(
        db_session,
        key="golden_warnings",
        title="Golden Warnings",
        position=200,
        contract=_PROSE_CONTRACT,
    )
    pending = await _definition(
        db_session,
        key="golden_pending",
        title="Golden Pending",
        position=300,
        contract=_PROSE_CONTRACT,
    )
    failed = await _definition(
        db_session,
        key="golden_failed",
        title="Golden Failed",
        position=400,
        contract=_PROSE_CONTRACT,
    )
    skipped = await _definition(
        db_session,
        key="golden_skipped",
        title="Golden Skipped",
        position=500,
        contract=_PROSE_CONTRACT,
    )
    unresolved = await _definition(
        db_session,
        key="golden_unresolved",
        title="Golden Unresolved",
        position=600,
        contract=_UNRESOLVED_CONTRACT,
    )

    rows = [
        ReportSection(
            job_id=job.id,
            section_definition_id=overview.id,
            section_key=overview.key,
            position=overview.position,
            status=SectionStatus.GENERATED,
            confidence=0.8,
            content={
                "thesis": "A fixed thesis sentence with no figures in it.",
                "headcount": 221000,
                "key_points": ["First fixed point.", "Second fixed point."],
                "flagship": {
                    "name": "Azure",
                    "role": "Growth engine",
                    "calculation_id": str(CALC_ID),
                    "source_document_id": str(DOC_ONE_ID),
                },
                "figures": [
                    {
                        "label": "Revenue CAGR",
                        "value": "0.18",
                        "unit": "ratio",
                        "calculation_id": str(CALC_ID),
                        "source_document_id": str(DOC_ONE_ID),
                    },
                    {
                        "label": "Revenue CAGR",
                        "value": "0.18",
                        "unit": "ratio",
                        "calculation_id": str(CALC_ID),
                        "source_document_id": str(DOC_TWO_ID),
                    },
                ],
                "notes_mixed": [
                    {"observation": "An object with its own shape."},
                    {
                        "label": "Odd item",
                        "detail": "A different shape, cited.",
                        "source_document_id": str(DOC_TWO_ID),
                    },
                    "A bare string in a mixed list.",
                ],
                "ignored_empty": "",
                "confidence": 0.8,
                "undeclared": "Never rendered: the contract does not declare this key.",
            },
        ),
        ReportSection(
            job_id=job.id,
            section_definition_id=warned.id,
            section_key=warned.key,
            position=warned.position,
            status=SectionStatus.GENERATED,
            confidence=0.2,
            low_confidence_reason="Insufficient evidence: the golden scene says so.",
            content={"commentary": "Written under a banner, deliberately."},
        ),
        ReportSection(
            job_id=job.id,
            section_definition_id=pending.id,
            section_key=pending.key,
            position=pending.position,
            status=SectionStatus.PENDING,
        ),
        ReportSection(
            job_id=job.id,
            section_definition_id=failed.id,
            section_key=failed.key,
            position=failed.position,
            status=SectionStatus.FAILED,
            low_confidence_reason="The golden scene fails this one on purpose.",
        ),
        ReportSection(
            job_id=job.id,
            section_definition_id=skipped.id,
            section_key=skipped.key,
            position=skipped.position,
            status=SectionStatus.SKIPPED_NOT_APPLICABLE,
        ),
        ReportSection(
            job_id=job.id,
            section_definition_id=unresolved.id,
            section_key=unresolved.key,
            position=unresolved.position,
            status=SectionStatus.GENERATED,
            content={
                "figures": [
                    {
                        "label": "Ghost figure",
                        "value": "1.23",
                        "unit": "x",
                        "calculation_id": str(MISSING_CALC_ID),
                        "source_document_id": str(MISSING_DOC_ID),
                    }
                ],
            },
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    return {
        "session": db_session,
        "job": job,
        "request": request,
        "company": company,
        "sector": SectorNote(
            label="Banks",
            warnings=("Deposit funding inverts the usual reading of leverage.",),
            blocked_models=("dcf",),
            metric_disclosure="CET1 was required for this sector and was not computed.",
        ),
        "comps": WithheldComps(peer_count=3, excluded_count=1, as_of=AS_OF_DATE),
    }


async def _render(scene: dict[str, Any]) -> RenderedReport:
    return await render_markdown(
        scene["session"],
        job=scene["job"],
        request=scene["request"],
        company=scene["company"],
        sector=scene["sector"],
        comps=scene["comps"],
        rating="Constructive (non-binding)",
        confidence=0.62,
        generated_at=GENERATED_AT,
    )


class TestTheReportFacesTheReader:
    """Gap A40. The live report opened with eight raw validator lines and UUIDs where the
    Executive Summary belongs, printed twelve-decimal ratios in its notes, and marked
    nothing in the contents for the reader who would find four sections missing. The
    document now says what it could not cover once, at the front, and keeps the
    diagnostics in the run where they belong.
    """

    async def test_the_coverage_notice_derives_from_recorded_state(
        self, scene: dict[str, Any]
    ) -> None:
        scene["session"].add(
            Evaluation(
                job_id=scene["job"].id,
                metric="source_coverage",
                value=Decimal("0.8462"),
                threshold=Decimal("0.90"),
                passed=False,
                details={},
            )
        )
        await scene["session"].flush()

        document = await assemble_document(
            scene["session"],
            job=scene["job"],
            request=scene["request"],
            generated_at=GENERATED_AT,
        )

        assert document.coverage is not None
        assert "Golden Failed" in document.coverage.sections_failed
        assert "Golden Pending" in document.coverage.sections_failed
        assert document.coverage.checks_failed == ("source_coverage",)
        assert "source_coverage" in document.coverage.sentence

    async def test_a_failed_sections_diagnostics_stay_out_of_the_document(
        self, scene: dict[str, Any]
    ) -> None:
        """The reader gets the status line; the validator's raw problems stay in the run."""
        document = await assemble_document(
            scene["session"],
            job=scene["job"],
            request=scene["request"],
            generated_at=GENERATED_AT,
        )

        failed = next(view for view in document.sections if view.key == "golden_failed")
        assert not failed.generated
        assert not any(isinstance(fragment, Banner) for fragment in failed.fragments)
        # A degraded-but-generated section keeps its banner: the suppression is for
        # diagnostics, not for honest warnings.
        warned = next(view for view in document.sections if view.key == "golden_warnings")
        assert any(isinstance(fragment, Banner) for fragment in warned.fragments)

    def test_a_full_report_carries_no_notice(self) -> None:
        assert CoverageNote(sections_failed=(), sections_total=18, checks_failed=()).sentence == ""

    def test_display_values_read_like_prose_not_storage(self) -> None:
        assert _display_value(Decimal("0.437565271053")) == (
            "0.4376 (rounded; full precision stored)"
        )
        assert _display_value(Decimal("15")) == "15"
        assert _display_value(Decimal("0.025")) == "0.025"

    def test_literal_escapes_are_decoded_at_render(self) -> None:
        assert _unescaped("no view \\u2014 favourable or unfavourable \\u2014 here") == (
            "no view — favourable or unfavourable — here"
        )
        assert _unescaped({"a": ["x \\u00e9"]}) == {"a": ["x é"]}
        assert _unescaped(42) == 42


class TestTheGoldenMarkdown:
    async def test_the_serialisation_is_byte_identical_to_the_recorded_golden(
        self, scene: dict[str, Any]
    ) -> None:
        rendered = await _render(scene)

        if os.environ.get("UPDATE_GOLDEN") == "1":
            GOLDEN_MD.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN_MD.write_text(rendered.markdown, encoding="utf-8")
            pytest.fail("golden.md re-recorded; rerun without UPDATE_GOLDEN")

        assert rendered.markdown == GOLDEN_MD.read_text(encoding="utf-8")

    async def test_the_golden_scene_exercises_what_it_claims_to(
        self, scene: dict[str, Any]
    ) -> None:
        """Guards the golden itself: a scene that quietly lost a branch would keep
        passing byte-identity while covering less than this file's docstring claims."""
        rendered = await _render(scene)
        markdown = rendered.markdown

        assert "### Thesis" in markdown  # paragraph
        assert "### Headcount" in markdown  # scalar
        assert "- First fixed point." in markdown  # bullets
        assert "**Name:** Azure" in markdown  # described object
        assert "| Label | Value | Unit |" in markdown  # contract-ordered table
        assert "A bare string in a mixed list." in markdown  # mixed list
        assert "> **Insufficient evidence" in markdown  # warning banner
        assert "*This section was not generated.*" in markdown  # pending
        assert "*This section could not be generated.*" in markdown  # failed
        assert "*This section does not apply to this company.*" in markdown  # skipped
        assert "Unresolved citation" in markdown  # missing targets
        assert "## Sector: Banks" in markdown  # sector block
        assert "figures are withheld" in markdown  # comps disclosure
        assert "## Sources" in markdown  # appendix
        assert markdown.count("[^1]") >= 2  # markers and their definitions

        # Within-section de-duplication, counted exactly: the flagship object (2) and
        # the first figure row (2) cite under different labels so all four are distinct;
        # the second figure row reuses the labelled calculation and adds one source (1);
        # the odd mixed note adds one (1); the unresolved section adds two (2).
        assert rendered.footnote_count == 8


async def _document(scene: dict[str, Any]) -> ReportDocument:
    return await assemble_document(
        scene["session"],
        job=scene["job"],
        request=scene["request"],
        company=scene["company"],
        sector=scene["sector"],
        comps=scene["comps"],
        rating="Constructive (non-binding)",
        confidence=0.62,
        generated_at=GENERATED_AT,
    )


class TestTheGoldenHtml:
    """The HTML notation, held against the same scene as the Markdown one.

    ``golden.html`` is a recording of this notation's own output, so unlike ``golden.md``
    it cannot prove the notation correct — the structural assertions below do that. What
    the recording holds is *stability*: task 48 hashes and archives these bytes, and a
    serialiser that drifted between approving a preview and rendering the PDF would break
    the "what is approved is what exists" property silently.
    """

    async def test_the_html_is_byte_identical_to_the_recorded_golden(
        self, scene: dict[str, Any]
    ) -> None:
        html = render_html(await _document(scene))

        if os.environ.get("UPDATE_GOLDEN") == "1":
            GOLDEN_HTML.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN_HTML.write_text(html, encoding="utf-8")
            pytest.fail("golden.html re-recorded; rerun without UPDATE_GOLDEN")

        assert html == GOLDEN_HTML.read_text(encoding="utf-8")

    async def test_the_page_carries_the_whole_document(self, scene: dict[str, Any]) -> None:
        html = render_html(await _document(scene))

        assert html.startswith("<!DOCTYPE html>")
        assert "<title>MICROSOFT CORP — Research Note</title>" in html
        assert "Constructive (non-binding)" in html
        assert "not</strong> regulated investment advice" in html  # emphasis, converted
        assert 'id="contents"' in html
        assert 'href="#section-golden_overview"' in html
        assert "Sector: Banks" in html
        assert "does not run dcf" in html
        assert '<div class="banner" role="note">' in html  # the low-confidence warning
        assert '<p class="status-note">This section was not generated.</p>' in html
        assert "<strong>Name:</strong> Azure" in html  # described object
        assert '<th scope="col">Label</th>' in html  # contract-ordered table
        assert "figures are withheld" in html  # comps disclosure
        assert "<strong>Unresolved citation</strong>" in html
        assert 'id="sources"' in html
        assert "e3b0c4429" in html  # the artefact digest prefix, checkable

    async def test_both_notations_agree_on_every_marker(self, scene: dict[str, Any]) -> None:
        """One assembly, two serialisations: the marker sequences are the same sequence.

        Compared as sequences rather than sets, because within-section de-duplication
        means a number can mark twice — and a notation that dropped or reordered a repeat
        would still pass a set comparison.
        """
        document = await _document(scene)
        markdown = serialise_markdown(document)
        html = render_html(document)

        md_sequence = _MD_MARKERS.findall(markdown)
        html_sequence = _HTML_MARKERS.findall(html)

        assert md_sequence == html_sequence
        assert len(md_sequence) > document.footnote_count  # a repeat exists and survived

        # First occurrences run 1..n in document order — the assembler's numbering,
        # visible in both notations.
        first_seen = list(dict.fromkeys(html_sequence))
        assert first_seen == [str(n) for n in range(1, document.footnote_count + 1)]

        # Exactly one marker per number carries the back-reference anchor, and every
        # footnote links back to it.
        for number in range(1, document.footnote_count + 1):
            assert html.count(f'id="fnref-{number}"') == 1
            assert f'id="fn-{number}"' in html

    async def test_model_output_renders_as_text_never_as_markup(
        self, scene: dict[str, Any]
    ) -> None:
        """A drafted sentence cannot become script in the operator's browser.

        Markup is planted in every path that reaches a tag: paragraphs, bullets, table
        cells, described objects, the degradation banner, and a fetched document's title
        (which flows into footnotes and the appendix). One assertion covers them all:
        no ``<script>`` survives anywhere.
        """
        session: AsyncSession = scene["session"]
        row = await session.scalar(
            select(ReportSection).where(ReportSection.section_key == "golden_overview")
        )
        assert row is not None
        row.content = {
            **row.content,
            "thesis": 'Before <script>alert("x")</script> & <b>after</b>.',
            "key_points": ["A point with <img src=x onerror=alert(1)> in it."],
            "flagship": {
                "name": "<script>flag()</script>",
                "role": "Growth engine",
                "calculation_id": str(CALC_ID),
            },
            "figures": [
                {
                    "label": "Fig <script>cell()</script>",
                    "value": "<i>1</i>",
                    # The *last* content column, on a row that also cites: this cell takes
                    # the marker-appending branch, which escapes separately from the plain
                    # cells and must be planted separately or a regression there hides.
                    "unit": "x <script>unit()</script>",
                    "calculation_id": str(CALC_ID),
                    "source_document_id": str(DOC_ONE_ID),
                }
            ],
        }
        warned = await session.scalar(
            select(ReportSection).where(ReportSection.section_key == "golden_warnings")
        )
        assert warned is not None
        warned.low_confidence_reason = "Reason with <script>warn()</script> inside."
        dated = await session.get(SourceDocument, DOC_ONE_ID)
        assert dated is not None
        dated.title = "Form <script>title()</script> 10-K"
        await session.flush()

        html = render_html(await _document(scene))

        assert "<script>" not in html
        assert "<b>" not in html
        assert "<img" not in html
        assert "<i>" not in html
        assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
        assert "&lt;script&gt;cell()&lt;/script&gt;" in html  # a table cell
        assert "&lt;script&gt;unit()&lt;/script&gt;" in html  # the marker-carrying cell
        assert "&lt;script&gt;warn()&lt;/script&gt;" in html  # the banner
        assert "&lt;script&gt;title()&lt;/script&gt;" in html  # a footnote title


class TestTheHtmlNotationEdges:
    """The defensive paths the golden scene cannot pin, held directly."""

    def test_a_dangling_emphasis_marker_stays_plain(self) -> None:
        assert str(_emphasise("is **not** advice")) == "is <strong>not</strong> advice"
        # An unpaired ** must not silently emphasise the rest of the sentence.
        assert str(_emphasise("a ** b")) == "a  b"
        assert str(_emphasise("**a** and **b")) == "<strong>a</strong> and b"

    def test_heading_depth_is_capped_at_what_html_has(self) -> None:
        rendered = _blocks((Heading(level=9, text="Deep"),), seen=set(), titles={})
        assert str(rendered) == "<h6>Deep</h6>"


class TestCustomSectionsInTheDocument:
    """Skill-origin sections: attributed in the contents, in place in the body."""

    @pytest.fixture
    async def custom_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        """Two skill-origin sections slotted *between* built-ins, both citing."""
        session: AsyncSession = scene["session"]
        job: Job = scene["job"]

        skill = Skill(key="golden_custom", kind="custom_section", enabled=True)
        session.add(skill)
        await session.flush()

        contract: dict[str, Any] = {
            "type": "object",
            "properties": {
                "figures": {
                    "type": "array",
                    "title": "Figures",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "unit": {"type": "string"},
                            "calculation_id": {"type": "string"},
                            "source_document_id": {"type": "string"},
                        },
                    },
                },
            },
        }
        placements = (
            ("custom_early", "Custom Early", 250, {"source_document_id": str(DOC_ONE_ID)}),
            ("custom_late", "Custom Late", 450, {"calculation_id": str(CALC_ID)}),
        )
        for key, title, position, citation in placements:
            definition = SectionDefinition(
                key=key,
                version=1,
                origin="skill",
                skill_id=skill.id,
                title=title,
                position=Decimal(position),
                required=False,
                output_contract=contract,
                evidence_policy={"min_sources": 0, "requires_primary": False},
                token_budget=1000,
                allowed_tools=[],
                applicability={},
            )
            session.add(definition)
            await session.flush()
            session.add(
                ReportSection(
                    job_id=job.id,
                    section_definition_id=definition.id,
                    section_key=key,
                    position=definition.position,
                    status=SectionStatus.GENERATED,
                    content={
                        "figures": [
                            {"label": f"{title} figure", "value": "1.0", "unit": "x", **citation}
                        ],
                    },
                )
            )
        await session.flush()
        return scene

    async def test_the_body_keeps_position_order_and_the_contents_group(
        self, custom_scene: dict[str, Any]
    ) -> None:
        html = render_html(await _document(custom_scene))

        # Body: position order, custom sections interleaved where their positions put
        # them, each wearing the attribution chip.
        body_order = [
            html.index(f'id="section-{key}"')
            for key in (
                "golden_overview",  # 100
                "golden_warnings",  # 200
                "custom_early",  # 250
                "golden_pending",  # 300
                "golden_failed",  # 400
                "custom_late",  # 450
                "golden_skipped",  # 500
                "golden_unresolved",  # 600
            )
        ]
        assert body_order == sorted(body_order)
        assert html.count('<div class="custom-chip">Custom analysis</div>') == 2

        # Contents: the custom entries sit under their own heading with the attribution
        # note, not mixed into the platform's list.
        contents = html[html.index('id="contents"') : html.index("</nav>")]
        custom_block = contents[contents.index("Custom analysis") :]
        builtin_block = contents[: contents.index("Custom analysis")]
        assert "operator's analysis rather than platform output" in custom_block
        for key in ("custom_early", "custom_late"):
            assert f'href="#section-{key}"' in custom_block
            assert f'href="#section-{key}"' not in builtin_block

    async def test_footnote_numbering_runs_unbroken_across_the_boundary(
        self, custom_scene: dict[str, Any]
    ) -> None:
        """A custom section is numbered where it stands, not appended after the spine."""
        document = await _document(custom_scene)
        html = render_html(document)

        # The base scene carries 8 citations; each custom section adds one, and their
        # positions put both between the overview's six and the unresolved pair.
        assert document.footnote_count == 10
        early = html[html.index('id="section-custom_early"') : html.index('id="section-golden_pen')]
        assert 'href="#fn-7">7</a>' in early
        unresolved = html[html.index('id="section-golden_unresolved"') :]
        assert 'href="#fn-9">9</a>' in unresolved

        first_seen = list(dict.fromkeys(_HTML_MARKERS.findall(html)))
        assert first_seen == [str(n) for n in range(1, 11)]
