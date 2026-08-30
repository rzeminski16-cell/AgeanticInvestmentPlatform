"""The printed page, measured. Roadmap §2.4's layout check as geometry assertions.

Two defects in a live document motivated this file, and neither was visible to any test
that reads strings: a two-hundred-word red-team challenge sat in a narrow table column,
so one row spanned three pages and neither position could be read; and key-figure pairs
rendered as separate stacked blocks, so a reader reassembled the pairing by counting.
Every other render test asserts on notation — Markdown lines, HTML strings, PDF metadata
— which is exactly why both shipped: the strings were right and the page was wrong.

So this file renders real documents through WeasyPrint and walks the box tree the paged
engine actually produced. The rules are the ones the two defects broke: nothing paints
past the page's right edge, no table row outgrows a page, a row's cells sit on one line,
and a label shares its line with its value. The first test holds them over the golden
fixture; the second holds them over a document from a full pipeline run whose red team is
scripted to argue at full length — the content shape that broke the live document, which
no golden contains because the scripted adversary honestly raises nothing.

The stacked-pairs defect does not reproduce under the engine ``pyproject.toml`` now pins
(WeasyPrint >= 69 lays the cover grid out correctly; older engines without grid support
stacked every ``dt`` over its ``dd``). The dt/dd rule is what keeps that closed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from weasyprint import HTML
from weasyprint.formatting_structure import boxes as fsb

from aer.agents.red_team import ChallengeDimension, RedTeamChallenge, RedTeamReport
from aer.config import Settings
from aer.providers.fake import FakeProvider
from aer.render.document import assemble_document
from aer.render.html import render_html
from aer.storage.local import LocalArtefactStore
from tests.schema_guard import refuse_unanswerable_schema
from tests.test_report_sections import run_to_report
from tests.workflow_fixtures import (
    ScriptedSectionBrain,
    StubSecClient,
    declared_schema_name,
    seed_job,
    seed_request,
    seed_user,
)

pytestmark = pytest.mark.anyio

GOLDEN_HTML = Path(__file__).parent / "fixtures" / "fx_report" / "golden.html"

GENERATED_AT = datetime(2022, 7, 2, 9, 30, tzinfo=UTC)


# -- The geometry walk -----------------------------------------------------------------------


def _walk(box: Any) -> Any:
    yield box
    for child in getattr(box, "children", ()) or ():
        yield from _walk(child)


def _text(box: Any) -> str:
    return " ".join(part.text for part in _walk(box) if isinstance(part, fsb.TextBox))


def _document_text(rendered: Any) -> str:
    return " ".join(_text(page._page_box) for page in rendered.pages)


def _geometry_findings(rendered: Any) -> list[str]:
    """Every way a page can be wrong that the two live defects taught us to check.

    The walk yields several boxes per element (the block, its lines, its inlines), so
    each rule filters to the box class that carries the geometry it measures.
    """
    findings: list[str] = []
    for number, page in enumerate(rendered.pages, start=1):
        boxes = list(_walk(page._page_box))

        for box in boxes:
            tag = getattr(box, "element_tag", None)
            if tag is None or not isinstance(box, fsb.BlockContainerBox):
                continue
            right = box.border_box_x() + box.border_width()
            if right > page.width + 1:
                findings.append(
                    f"page {number}: <{tag}> paints past the right edge "
                    f"({right:.0f}px on a {page.width:.0f}px page)"
                )

        for row in (box for box in boxes if isinstance(box, fsb.TableRowBox)):
            if row.height > page.height + 1:
                findings.append(
                    f"page {number}: a table row is taller than the page "
                    f"({row.height:.0f}px against {page.height:.0f}px) — a cell is "
                    "holding prose the appendix should be laying out as paragraphs"
                )
            cells = [cell for cell in row.children if isinstance(cell, fsb.TableCellBox)]
            tops = {round(cell.position_y) for cell in cells}
            if len(tops) > 1:
                findings.append(f"page {number}: one row's cells sit on different lines: {tops}")

        # A label shares its line with its value. The cover's dl is the one label/value
        # construct that is not a table, and the one that stacked under pre-grid engines.
        pending: Any = None
        for box in boxes:
            if not isinstance(box, fsb.BlockBox):
                continue
            tag = getattr(box, "element_tag", None)
            if tag == "dt":
                pending = box
            elif tag == "dd" and pending is not None:
                if abs(box.position_y - pending.position_y) > 1:
                    findings.append(
                        f"page {number}: label {_text(pending)!r} sits on a different "
                        "line from its value — the pairing is being reassembled by counting"
                    )
                pending = None
    return findings


class TestTheGoldenDocumentGeometry:
    def test_nothing_overflows_and_every_pair_shares_its_line(self) -> None:
        rendered = HTML(string=GOLDEN_HTML.read_text(encoding="utf-8")).render()
        assert not _geometry_findings(rendered), "\n".join(_geometry_findings(rendered))


# -- A real run whose adversary argues -------------------------------------------------------

# Three challenges at the length the live run produced. The statements are what broke the
# table layout: an argument is a paragraph, and the v2 appendix put it in a column.
_CHALLENGES: tuple[tuple[ChallengeDimension, int, str, str], ...] = (
    (
        ChallengeDimension.GROWTH,
        4,
        "The growth thesis rests on a single year of re-acceleration and treats it as a "
        "trend. The filing shows one period of double-digit expansion after two of "
        "deceleration, and the note extrapolates that single observation forward through "
        "the whole forecast window without addressing why the two decelerating periods "
        "are the anomaly rather than the recovery. Nothing in the cited segment "
        "disclosures separates price from volume, so the re-acceleration could be a "
        "pricing action that resets once, not a demand trend that compounds; the draft "
        "does not consider that reading, and the valuation inherits the more favourable "
        "one without argument.",
        "One period of re-acceleration against two of deceleration in the same filing, "
        "with no price/volume split disclosed to support either reading.",
    ),
    (
        ChallengeDimension.VALUATION,
        4,
        "The discount rate and the terminal assumptions pull in opposite directions and "
        "the note does not reconcile them. A terminal growth assumption near the long-run "
        "nominal rate implies a mature, low-risk cash-flow stream, yet the equity risk "
        "premium applied belongs to a materially riskier profile; using both at once "
        "flatters the terminal value while appearing conservative on the near years. "
        "Moving either input to the posture the other implies moves the intrinsic value "
        "by more than the entire margin of safety the conclusion claims, which means the "
        "rating rests on the inconsistency itself rather than on the evidence either "
        "input cites.",
        "The terminal growth and discount-rate assumptions imply different risk profiles "
        "for the same cash-flow stream, and the sensitivity grid shows the gap exceeds "
        "the claimed margin of safety.",
    ),
    (
        ChallengeDimension.COMPETITIVE_POSITION,
        3,
        "The moat argument is asserted at the level of the company and evidenced at the "
        "level of one product line. Switching costs are documented for the flagship "
        "segment only, while the growth the thesis needs comes disproportionately from "
        "the newer segments where the filing itself describes competition as intense and "
        "pricing as promotional. The note carries the flagship segment's retention "
        "characteristics across the whole revenue base without a disclosed basis for "
        "doing so.",
        "Retention and switching-cost evidence is cited from the flagship segment; the "
        "projected growth is concentrated in segments the filing describes as intensely "
        "competitive.",
    ),
)


def _arguing_provider() -> FakeProvider:
    """The scripted brain, with the adversary overridden to argue at full length.

    The challenges must cite evidence that exists in the run — a fabricated id refuses
    the whole argument, by design — so the override reads a real source id back off the
    prompt the red team was just shown, exactly as the section brain reads its evidence
    listing.
    """
    brain = ScriptedSectionBrain()

    def answer(schema: type[Any]) -> Any:
        if declared_schema_name(schema) != "RedTeamReport":
            return brain(schema)
        assert brain.provider is not None, "bind the provider before the first call"
        # The evidence index reaches the prompt as a rendered listing, not as JSON, so
        # the id sits in whichever quotes the composition used.
        prompt = str(brain.provider.calls[-1]["messages"])
        ids = re.findall(r"['\"]source_document_id['\"]:\s*['\"]([0-9a-f-]{36})['\"]", prompt)
        assert ids, "the red team was shown no sources to cite"
        return RedTeamReport(
            challenges=[
                RedTeamChallenge(
                    dimension=dimension,
                    severity=severity,
                    statement=statement,
                    basis=basis,
                    source_document_ids=[ids[0]],
                )
                for dimension, severity, statement, basis in _CHALLENGES
            ],
            coverage_note="Scripted adversary arguing at full length, for the layout check.",
        )

    provider = FakeProvider(answer, inspect_schema=refuse_unanswerable_schema)
    brain.provider = provider
    return provider


@pytest.fixture
async def printed_run(
    db_session: AsyncSession,
    workflow_settings: Settings,
    workflow_store: LocalArtefactStore,
    sec_client: StubSecClient,
) -> dict[str, Any]:
    """A full pipeline run with a populated disagreement appendix, rendered and paged."""
    provider = _arguing_provider()
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    report = await run_to_report(
        db_session,
        settings=workflow_settings,
        provider=provider,
        store=workflow_store,
        sec_client=sec_client,
        job=job,
        user=user,
    )
    document = await assemble_document(
        db_session, job=job, request=request, generated_at=GENERATED_AT
    )
    html = render_html(document)
    return {"report": report, "html": html, "rendered": HTML(string=html).render()}


class TestARealRunsDocumentGeometry:
    async def test_every_section_prints_inside_the_page(self, printed_run: dict[str, Any]) -> None:
        findings = _geometry_findings(printed_run["rendered"])
        assert not findings, "\n".join(findings)

    async def test_the_appendix_argues_in_prose_not_in_a_column(
        self, printed_run: dict[str, Any]
    ) -> None:
        """The exact content shape that spanned three pages as one table row, now flowing.

        The statements reach the reader once each, as paragraphs under emphasised
        lead-ins — and none of them sits inside a table cell, which is the regression
        that would put §2.4 back.
        """
        html = printed_run["html"]
        text = _document_text(printed_run["rendered"])

        for _, _, statement, _ in _CHALLENGES:
            opening = statement.split(".")[0]
            assert opening in text, "a challenge never reached the printed document"
            assert not re.search(r"<t[dh][^>]*>[^<]*" + re.escape(opening), html), (
                "a challenge is back inside a table cell"
            )
        assert "<strong>Red team" in html
        assert "<strong>Resolution:</strong>" in html
