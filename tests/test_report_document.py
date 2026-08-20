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
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import aer.render.glance as glance_module
from aer.calc.comps import WithheldComps
from aer.config import HouseStyle
from aer.core.enums import JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    Evaluation,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    Skill,
    SourceDocument,
    User,
)
from aer.render import display
from aer.render.document import (
    DISCLAIMER,
    UNDATED_MARKER,
    UNDATED_NOTE,
    CalculationFootnote,
    CoverageNote,
    ReportDocument,
    _display_value,
    assemble_document,
)
from aer.render.html import _blocks, _emphasise, _joint, _marks, render_html
from aer.render.html import _footnote as html_footnote
from aer.render.markdown import (
    RenderedReport,
    SectorNote,
    _footnote_text,
    render_markdown,
    serialise_markdown,
)
from aer.render.summary import summary_document
from aer.sections.render import Banner, Heading, _unescaped, render_section
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
    # The subject, as `acquire` records it (ADR 0061). Without it the request names no
    # company and every fact query scoped to the subject returns nothing.
    request.company_id = company.id
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

    async def test_a_section_resting_on_an_undated_source_carries_the_marker(
        self, scene: dict[str, Any]
    ) -> None:
        """The C3 marker: point-in-time is a soft constraint, and the reader sees where.

        A source with no stated publication date is used rather than excluded; the
        section resting on it carries a small symbol by its heading, and the legend
        explains the symbol exactly once.
        """
        document = await _document(scene)
        marked = next(view for view in document.sections if view.key == "golden_overview")
        clean = next(view for view in document.sections if view.key == "golden_warnings")
        assert marked.undated
        assert not clean.undated
        assert document.undated_note == UNDATED_NOTE

        markdown = serialise_markdown(document)
        assert f"## Golden Overview {UNDATED_MARKER}" in markdown
        assert f"## Golden Warnings {UNDATED_MARKER}" not in markdown
        assert markdown.count(UNDATED_NOTE) == 1

        html = render_html(document)
        assert f"Golden Overview {UNDATED_MARKER}</h2>" in html
        assert html.count(UNDATED_NOTE) == 1

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
        # Contract-ordered table; the declared unit column is folded into the value by
        # the display formatter (gap R1) rather than shown as machine bookkeeping.
        assert "| Label | Value |" in markdown
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
        # Gap R8: the contents annotation's gap is structural — a margin, not a text
        # node a PDF text layer can collapse into "Business Overview(not generated)".
        assert re.search(r"\.not-generated \{[^}]*margin-left", html)

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
                    # The *last* content column, on a row that also cites: the value cell
                    # takes the marker-appending branch (the unit column is folded away by
                    # the display formatter), which escapes separately from the plain
                    # cells and must be planted separately or a regression there hides.
                    "label": "Fig <script>cell()</script>",
                    "value": "<i>1</i>",
                    # Dropped from the page by gap R1; planted anyway, because a dropped
                    # column that leaked would still have to leak escaped.
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
        assert "&lt;i&gt;1&lt;/i&gt;" in html  # the marker-carrying value cell
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

    def test_adjacent_markers_are_separated_never_a_number(self) -> None:
        """Gap R7: markers 2 and 3 set flush read as twenty-three in the PDF's text."""
        rendered = str(_marks((2, 3), seen=set(), titles={}))
        assert '</sup><sup class="fn-sep">,</sup><sup class="fn-ref"' in rendered
        # The visible text a PDF's text layer carries: separated digits, never "23".
        assert re.sub(r"<[^>]+>", "", rendered) == "2,3"

    def test_a_marker_after_a_word_gets_a_space_and_after_punctuation_does_not(self) -> None:
        """Gap R7's other half: "share76" in a live table cell; "supports.76" is fine."""
        assert str(_joint("share", (7,))) == "\N{NO-BREAK SPACE}"
        assert str(_joint("the evidence supports.", (7,))) == ""
        assert str(_joint("18%", (7,))) == ""
        assert str(_joint("share", ())) == ""


class TestTheWalkStripsNotation:
    """Gap R6: markdown in model prose is stripped at render, never printed as asterisks."""

    CONTRACT: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "commentary": {"type": "string", "title": "Commentary"},
            "blocks": {
                "type": "array",
                "title": "Commentary",
                "items": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "lead_in": {"type": "string"},
                        "text": {"type": "string"},
                    },
                },
            },
        },
    }

    def test_paired_emphasis_is_stripped_from_prose(self) -> None:
        rendered = render_section(
            key="probe",
            title="Probe",
            contract=self.CONTRACT,
            content={"commentary": "**Base case.** Growth holds while **mix** improves."},
        )
        assert "Base case. Growth holds while mix improves." in rendered.markdown
        assert "**" not in rendered.markdown.replace("## Probe", "")

    def test_an_unpaired_marker_is_left_exactly_as_written(self) -> None:
        rendered = render_section(
            key="probe",
            title="Probe",
            contract=self.CONTRACT,
            content={"commentary": "The result of 3 ** 2 is nine."},
        )
        assert "3 ** 2" in rendered.markdown

    def test_prose_blocks_render_as_paragraphs_with_the_lead_in_emphasised(self) -> None:
        """The structured home for the bold opener: paragraphs, never a two-column table."""
        rendered = render_section(
            key="probe",
            title="Probe",
            contract=self.CONTRACT,
            content={
                "blocks": [
                    {"lead_in": "Base case", "text": "Revenue compounds at the stated rate."},
                    {"text": "A block with no lead-in reads as an ordinary paragraph."},
                ]
            },
        )
        assert "**Base case:** Revenue compounds at the stated rate." in rendered.markdown
        assert "A block with no lead-in reads as an ordinary paragraph." in rendered.markdown
        assert "| Lead In | Text |" not in rendered.markdown

    def test_a_prose_block_still_cites_through_the_metadata_keys(self) -> None:
        source_id = "0b6c0d8e-58a1-4a67-9418-000000000001"
        rendered = render_section(
            key="probe",
            title="Probe",
            contract=self.CONTRACT,
            content={
                "blocks": [
                    {
                        "lead_in": "What the filing shows",
                        "text": "Margins are disclosed by segment.",
                        "source_document_id": source_id,
                    }
                ]
            },
        )
        assert "[^1]" in rendered.markdown
        assert [str(ref) for ref in rendered.citations] == [f"source_document:{source_id}"]


class TestThePeriodSeries:
    """Gap R9: a period series renders as a financial table — periods across the top,
    line items down the side, a footnote per cell — never a key-value dump."""

    CONTRACT: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "financials": {
                "type": "array",
                "title": "Financial History",
                "items": {
                    "type": "object",
                    "required": ["label", "values"],
                    "properties": {
                        "label": {"type": "string"},
                        "values": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["period", "value"],
                                "properties": {
                                    "period": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                    "financial_fact_id": {"type": "string"},
                                    "calculation_id": {"type": "string"},
                                    "source_document_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    SOURCE = "43a1c0de-0000-4000-9000-000000000001"
    CALC = "43a1c0de-0000-4000-9000-000000000002"

    def _rendered(self) -> Any:
        content = {
            "financials": [
                {
                    "label": "Revenue",
                    "values": [
                        {
                            "period": "FY2021",
                            "value": "168088000000",
                            "unit": "USD",
                            "financial_fact_id": str(uuid.uuid4()),
                            "source_document_id": self.SOURCE,
                        },
                        {
                            "period": "FY2022",
                            "value": "198270000000",
                            "unit": "USD",
                            "financial_fact_id": str(uuid.uuid4()),
                            "source_document_id": self.SOURCE,
                        },
                    ],
                },
                {
                    "label": "Operating margin",
                    "values": [
                        {
                            "period": "FY2022",
                            "value": "0.42",
                            "unit": "ratio",
                            "calculation_id": self.CALC,
                        }
                    ],
                },
            ]
        }
        return render_section(key="probe", title="Probe", contract=self.CONTRACT, content=content)

    def test_periods_run_across_and_line_items_down(self) -> None:
        rendered = self._rendered()
        assert "|  | FY2021 | FY2022 |" in rendered.markdown
        # Values in the house style, each cell citing its own figure; the same source
        # cited twice keeps one marker, exactly as in prose.
        assert "| Revenue | $168,088m[^1] | $198,270m[^1] |" in rendered.markdown
        # A period the row does not carry is an em dash, never a blank that reads as
        # zero — and the margin ratio reads as a percentage off the row's own label.
        assert "| Operating margin | \N{EM DASH} | 42%[^2] |" in rendered.markdown

    def test_every_cell_resolves_to_its_own_citation(self) -> None:
        rendered = self._rendered()
        assert [str(ref) for ref in rendered.citations] == [
            f"source_document:{self.SOURCE}",
            f"calculation:{self.CALC}",
        ]

    def test_the_period_never_bakes_into_the_label(self) -> None:
        """The live failure, held from the other side: no row label carries a period."""
        rendered = self._rendered()
        table = next(f for f in rendered.fragments if f.__class__.__name__ == "Table")
        assert [row.cells[0] for row in table.rows] == ["Revenue", "Operating margin"]

    def test_a_value_that_never_resolved_is_an_em_dash_with_no_footnote(self) -> None:
        """Gap A66: the MTB report printed a retained earnings row as its own footnote
        marker, twice — an empty value cell whose citation still registered."""
        content = {
            "financials": [
                {
                    "label": "Retained earnings",
                    "values": [
                        {
                            "period": "FY2025",
                            "value": "",
                            "unit": "USD",
                            "financial_fact_id": str(uuid.uuid4()),
                            "source_document_id": self.SOURCE,
                        }
                    ],
                }
            ]
        }
        rendered = render_section(
            key="probe", title="Probe", contract=self.CONTRACT, content=content
        )

        assert "| Retained earnings | \N{EM DASH} |" in rendered.markdown
        assert "[^1]" not in rendered.markdown, "a footnote on an absent figure"


class TestAStatedUnitIsNeverDropped:
    """Gap A66: two adjacent tables at two scales with no unit anywhere.

    The MTB balance sheet read "Total assets 219.3" while the cash flow read "2,280" —
    both rows carried units, and the formatter dropped any unit it did not recognise,
    leaving the two scales a page apart with nothing saying which was which.
    """

    def test_an_unrecognised_money_unit_is_shown_beside_the_value(self) -> None:
        shown = display.scalar(
            "219.3", style=HouseStyle(), unit="USD billions", label="Total assets", in_table=True
        )
        assert shown == "219.3 USD billions"

    def test_the_platforms_own_type_words_stay_out_of_print(self) -> None:
        """"pure" and "x" are for the formatter, not the reader — printing them would
        trade the missing-unit leak for a jargon leak, which the golden documents hold."""
        assert (
            display.scalar("0.42", style=HouseStyle(), unit="pure", label="Odd", in_table=True)
            == "0.42"
        )
        assert (
            display.scalar("1.23", style=HouseStyle(), unit="x", label="Ghost", in_table=True)
            == "1.23"
        )

    def test_a_recognised_unit_still_formats_as_before(self) -> None:
        shown = display.scalar(
            "219300000000", style=HouseStyle(), unit="USD", label="Total assets", in_table=True
        )
        assert shown == "$219,300m"

    def test_an_absent_value_is_an_em_dash_never_none(self) -> None:
        assert display.scalar(None, style=HouseStyle(), unit="USD", in_table=True) == "\u2014"
        assert display.scalar("  ", style=HouseStyle(), unit="USD", in_table=True) == "\u2014"

    def test_longhand_symbol_money_in_prose_reads_in_the_house_style(self) -> None:
        """ "$442,000,000" mid-sentence, two pages from the front page's "$442m"."""
        assert (
            display.prose("net income of $442,000,000 in the quarter", style=HouseStyle())
            == "net income of $442m in the quarter"
        )

    def test_a_five_figure_amount_is_left_as_written(self) -> None:
        assert (
            display.prose("a fee of $45,000 was paid", style=HouseStyle())
            == "a fee of $45,000 was paid"
        )
        # Cents and all: a rewrite through the money formatter would round them away,
        # and an amount a writer stated to the penny is theirs to state.
        assert (
            display.prose("a fee of $45,000.50 was paid", style=HouseStyle())
            == "a fee of $45,000.50 was paid"
        )


class TestTheFrontPageNumbers:
    """Gap R10: an at-a-glance block from stored rows, first in reading order — and
    silence, not apology, when the run holds nothing to show."""

    async def _with_figures(self, scene: dict[str, Any]) -> dict[str, Any]:
        session: AsyncSession = scene["session"]
        # Dated after the filings this scene carries. The figures are fiscal years
        # ending 30 June, filed the following month, so a run as at 30 June could not
        # have seen the latest of them — and under ADR 0061 the evidence pack now says
        # so rather than showing a fact filed after the as-of date.
        scene["request"].as_of_date = date(2022, 9, 30)
        for year, value in ((2020, "143015000000"), (2021, "168088000000"), (2022, "198270000000")):
            session.add(
                FinancialFact(
                    company_id=scene["company"].id,
                    source_document_id=DOC_ONE_ID,
                    concept="revenue",
                    unit="USD",
                    value=Decimal(value),
                    period_end=date(year, 6, 30),
                    fiscal_year=year,
                    fiscal_period="FY",
                    filed_date=date(year, 7, 28),
                )
            )
        session.add(
            FinancialFact(
                company_id=scene["company"].id,
                source_document_id=DOC_ONE_ID,
                concept="earnings_per_share_diluted",
                unit="USD/share",
                value=Decimal("9.65"),
                period_end=date(2022, 6, 30),
                fiscal_year=2022,
                fiscal_period="FY",
                filed_date=date(2022, 7, 28),
            )
        )
        session.add(
            Calculation(
                job_id=scene["job"].id,
                name="net_margin",
                formula="net_margin = net_income / revenue",
                function_ref="aer.calc.basic:net_margin",
                code_version="goldencode123456",
                inputs=[],
                output_value=Decimal("0.367"),
                output_unit="ratio",
                sequence=5,
            )
        )
        await session.flush()
        return scene

    async def test_the_block_leads_the_document_with_the_first_markers(
        self, scene: dict[str, Any]
    ) -> None:
        document = await _document(await self._with_figures(scene))
        assert document.glance

        markdown = serialise_markdown(document)
        assert markdown.index("## At a glance") < markdown.index("## Golden Overview")
        assert "### Latest reported figures" in markdown
        # House-style values, one marker per figure — and no raw id column: the fact id
        # is metadata the numeral rule reads, never text a reader sees.
        assert "| Revenue | FY2022 | $198,270m[^1] |" in markdown
        assert "| EPS (diluted) | FY2022 | $9.65[^2] |" in markdown
        # The annual strip is the R9 series shape: periods across the top.
        assert "|  | FY2020 | FY2021 | FY2022 |" in markdown
        assert "| Net margin | \N{EM DASH} | 36.7%[^4] |" in markdown

        html = render_html(document)
        assert html.index('id="at-a-glance"') < html.index('id="contents"')

    async def test_a_run_with_nothing_to_show_shows_nothing(self, scene: dict[str, Any]) -> None:
        """The golden scene holds no facts and no curated calculation: no block, no
        apology — the coverage notice owns the honest account."""
        document = await _document(scene)
        assert document.glance == ()
        assert "At a glance" not in serialise_markdown(document)

    async def test_a_glance_that_would_mix_issuers_is_withheld_and_the_reader_is_told(
        self, scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Task P2 (ADR 0061), end to end: the guard's reason reaches the printed page.

        The query cannot produce a mixed set any more, so the scene defeats it — the
        future this guard exists for — and the assertion is on the serialised Markdown,
        because the live failure was only visible in a signed PDF: three issuers' figures
        presented as one company's quarter, with nothing anywhere saying so.
        """
        scene = await self._with_figures(scene)
        foreign = FinancialFact(company_id=uuid.uuid4())
        genuine = glance_module._consolidated_facts

        async def defeated(session: AsyncSession, *, request: Any) -> list[Any]:
            return [*await genuine(session, request=request), foreign]

        monkeypatch.setattr(glance_module, "_consolidated_facts", defeated)
        document = await _document(scene)

        assert document.glance == (), "a block that would mix issuers must not render"
        assert document.coverage is not None
        assert "withheld" in document.coverage.sentence

        markdown = serialise_markdown(document)
        assert "At a glance" not in markdown
        assert "Coverage notice" in markdown
        assert "front page must not mix issuers" in markdown


class TestTheOnePageSummary:
    """Gap O8: a second renderer over the same document — the view, the numbers, the
    risks — with footnote numbers that match the full note."""

    async def _summary(self, scene: dict[str, Any]) -> tuple[ReportDocument, ReportDocument]:
        session: AsyncSession = scene["session"]
        flagged = await session.scalars(
            select(SectionDefinition).where(
                SectionDefinition.key.in_(["golden_overview", "golden_warnings"])
            )
        )
        for definition in flagged:
            definition.evidence_policy = {
                **(definition.evidence_policy or {}),
                "one_pager": True,
            }
        await session.flush()
        document = await _document(scene)
        return document, summary_document(document)

    async def test_only_the_claiming_sections_survive(self, scene: dict[str, Any]) -> None:
        document, summary = await self._summary(scene)
        assert [view.key for view in summary.sections] == ["golden_overview", "golden_warnings"]
        assert summary.charts == ()
        assert summary.comps_paragraph is None
        # The claim is data on the definition row, read into the view at assembly.
        assert [view.key for view in document.sections if view.one_pager] == [
            "golden_overview",
            "golden_warnings",
        ]

    async def test_footnote_numbers_match_the_full_note(self, scene: dict[str, Any]) -> None:
        """The one-pager is an entry point into the reference document: kept markers
        keep their numbers, and notes for dropped sections are dropped with them."""
        _, summary = await self._summary(scene)
        markdown = serialise_markdown(summary)

        assert "## Golden Overview \N{DAGGER}" in markdown
        assert "## Golden Unresolved" not in markdown
        kept = {note.number for note in summary.footnotes}
        assert kept == {1, 2, 3, 4, 5, 6}  # the overview's markers; the ghost 7/8 gone
        assert DISCLAIMER in markdown  # the footer travels with every edition
        assert "[^7]" not in markdown

    async def test_the_summary_page_drops_the_contents(self, scene: dict[str, Any]) -> None:
        _, summary = await self._summary(scene)
        html = render_html(summary, contents=False)
        assert 'id="contents"' not in html
        assert 'id="at-a-glance"' not in html  # this scene holds no front-page numbers
        assert "Golden Overview" in html


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


class TestACalculationFootnoteDatesItsFigure:
    """Gap A54. The live report anchored its structural reading on FY2021 ratios while
    its front page carried FY2025 margins, and nothing the reader could see dated
    either: the period was stored on the calculation row (gap C1) and never rendered.
    The footnote — where every figure resolves — now prints it.
    """

    @staticmethod
    def _footnote(period_label: str | None) -> CalculationFootnote:
        return CalculationFootnote(
            number=1,
            formula="net_margin = net_income / revenue",
            value="0.4376",
            unit="",
            function_ref="aer.calc.ratios:net_margin",
            code_version_prefix="abc123",
            period_label=period_label,
        )

    def test_a_stamped_calculation_prints_its_period_in_markdown(self) -> None:
        text = _footnote_text(self._footnote("FY2025"), style=HouseStyle())

        assert "= 0.4376 for FY2025 " in text

    def test_an_unstamped_calculation_stays_honestly_undated(self) -> None:
        """A discount rate is not a statement-period figure; inventing a period for it
        would be worse than the blank."""
        text = _footnote_text(self._footnote(None), style=HouseStyle())

        assert " for " not in text
        assert "= 0.4376 " in text

    def test_the_html_note_carries_the_same_period(self) -> None:
        row = html_footnote(self._footnote("FY2025"), style=HouseStyle())

        assert "for FY2025" in str(row["text"])
