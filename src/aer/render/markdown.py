"""The Markdown notation of an assembled report.

Since task 46 the walking, numbering and resolution live in
:mod:`aer.render.document` — one assembly for every notation — and this module only
transcribes a :class:`~aer.render.document.ReportDocument` into Markdown. The golden
document test holds this transcription byte-identical to the renderer as it stood before
the split, so "the Markdown module becomes a serialiser" is a property, not a claim.

**The disclaimer is not optional and not configurable.** Every rendered surface says this
is a personal research tool and not regulated investment advice, because the one time it
is missing is the one time it matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

import structlog

from aer.config import HouseStyle
from aer.core.schemas.extraction import normalise_whitespace
from aer.render import display
from aer.render.document import (
    DISCLAIMER,
    AppendixRow,
    CalculationFootnote,
    ChartView,
    CoverageNote,
    DerivedFootnote,
    Footnote,
    HeaderView,
    ReportDocument,
    SectorNote,
    SourceFootnote,
    UnresolvedFootnote,
    assemble_document,
)
from aer.sections.render import CitationRef, markdown_lines

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.calc.comps import CompsTable, WithheldComps
    from aer.charts import Chart, ChartTable
    from aer.db.models import Company, Job, ResearchRequest

__all__ = ["DISCLAIMER", "RenderedReport", "SectorNote", "render_markdown", "serialise_markdown"]

_log = structlog.get_logger("aer.render.markdown")

# What introduces a quoted passage (ADR 0119). Named because `aer.eval.runtime` carries the
# matching pattern — the rest of that line is a filing's own words, which this platform may
# not edit and must therefore not be scored on. The two are held together by a test that
# scans *this module's own output*, not by an import: a metric importing a serialiser would
# be the wrong direction, and a shared literal proves nothing about the shape around it.
PASSAGE_LEAD: Final = "Verified passage"


@dataclass(slots=True)
class RenderedReport:
    """The document, and what it cited."""

    markdown: str
    citations: list[CitationRef]
    section_keys: list[str]

    @property
    def footnote_count(self) -> int:
        return len(self.citations)


async def render_markdown(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    company: Company | None = None,
    sector: SectorNote | None = None,
    comps: CompsTable | WithheldComps | None = None,
    charts: tuple[Chart, ...] = (),
    rating: str | None = None,
    confidence: float | None = None,
    generated_at: datetime | None = None,
) -> RenderedReport:
    """Assemble a run's sections and transcribe them into a Markdown document."""
    document = await assemble_document(
        session,
        job=job,
        request=request,
        company=company,
        sector=sector,
        comps=comps,
        charts=charts,
        rating=rating,
        confidence=confidence,
        generated_at=generated_at,
    )
    markdown = serialise_markdown(document)

    _log.info(
        "report.rendered",
        job_id=str(job.id),
        sections=len(document.sections),
        footnotes=document.footnote_count,
        characters=len(markdown),
    )
    return RenderedReport(
        markdown=markdown,
        citations=document.citations,
        section_keys=document.section_keys,
    )


def serialise_markdown(document: ReportDocument) -> str:
    """One :class:`ReportDocument`, as Markdown. Transcription only — nothing decided here.

    The sector block sits immediately after the header and before any analysis. A sector
    warning at the foot of a report is a footnote, and `docs/archive/PLAN.md` section 2.9 is
    explicit that a blocked model produces a block rather than a footnote: a reader has
    to meet the limitation before the numbers, because the number is what they will
    remember.
    """
    body: list[str] = []
    for section in document.sections:
        body.append("\n".join(markdown_lines(section.fragments)))
        # The exhibits this section claims render beside its analysis (gap N1), the
        # same text shape the back-of-document pack uses, one heading level down.
        for chart in section.charts:
            body.append("\n".join(_exhibit_lines(chart, level=3)))

    return "\n".join(
        [
            *_header(document.header, style=document.style),
            # The composed view (ADR 0117) before the numbers, and the numbers before the
            # prose: the judges' complaint was the absence of a position, and a position a
            # reader meets after eighteen sections is one they meet last.
            *(markdown_lines(document.view) if document.view else []),
            # The front page's numbers (gap R10).
            *(markdown_lines(document.glance) if document.glance else []),
            *_coverage_block(document.coverage),
            *_sector_block(document.sector),
            *body,
            *(markdown_lines(document.comps) if document.comps else []),
            *_exhibits_block(document.charts),
            *_limitations_block(document.limitations),
            *_undated_block(document.undated_note),
            *_footnotes(document.footnotes, style=document.style),
            *_appendix(document.appendix, style=document.style),
            *_footer(),
        ]
    )


# -- Header and footer ---------------------------------------------------------------------


def _header(header: HeaderView, *, style: HouseStyle) -> list[str]:
    lines = [
        f"# {header.company_name} — Research Note",
        "",
        f"**Ticker:** {header.ticker} ({header.exchange})  ",
        f"**As-of date:** {display.date_text(header.as_of, style=style)}  ",
        f"**Base currency:** {header.base_currency}  ",
        f"**Generated:** "
        f"{display.date_text(header.generated_at.date(), style=style)}"
        f"{header.generated_at.strftime(', %H:%M UTC')}  ",
    ]

    # What the valuation gives, then the view, on two lines that cannot be mistaken for
    # each other (ADR 0132). The first is what code composed. The second says the report
    # takes no side (ADR 0135) rather than leaving the line out, because a missing view and
    # a deliberate one look identical unless the line says so — and not "no view reached",
    # which every judge of the verdict round read as the document failing to conclude.
    if header.method_values:
        lines.append(f"**What each method gives:** {header.method_values}  ")
    lines.append(f"**Non-binding view:** {header.view}  ")
    if header.confidence is not None:
        lines.append(f"**Confidence:** {header.confidence:.0%}  ")

    lines.extend(["", f"> {DISCLAIMER}", "", "---", ""])
    return lines


def _coverage_block(coverage: CoverageNote | None) -> list[str]:
    """The coverage notice, at the front, once (gap A40).

    The operator's decision for a thin run: the research note still renders, with a
    small warning and the sources in reach — instead of nine sections each independently
    rediscovering the same shortfall at four hundred words apiece.
    """
    if coverage is None:
        return []
    return [
        f"> **Coverage notice:** {coverage.sentence} The evidence this report rests on "
        "is listed in [Sources](#sources).",
        "",
    ]


def _sector_block(sector: SectorNote | None) -> list[str]:
    """The sector limitations, as a block at the top of the report.

    Empty for an ordinary company: a report that announced "this company is not a bank" on
    every run would train a reader to skip the block on the run where it matters.
    """
    if sector is None or not sector.label:
        return []

    lines = [f"## Sector: {sector.label}", ""]

    if sector.blocked_models:
        lines.extend(
            [
                f"**This report does not run {', '.join(sector.blocked_models)}.** "
                "The model is blocked for this sector rather than discouraged: it was not "
                "run and no figure below came from it.",
                "",
            ]
        )

    for warning in sector.warnings:
        lines.extend([f"> {warning}", ""])

    if sector.metric_disclosure:
        lines.extend([sector.metric_disclosure, ""])

    lines.extend(["---", ""])
    return lines


def _exhibits_block(charts: tuple[ChartView, ...]) -> list[str]:
    """The chart pack, as text: captions, markers and figures, with the drawing deferred.

    Markdown cannot carry the SVGs, and inlining them base64 would make the file
    unreadable for the one thing Markdown is kept for. So this notation carries each
    exhibit's caption with its markers — the figures resolve through the notes exactly
    as in the HTML — and says plainly where the rendered chart lives. Absent entirely
    when the run recorded nothing chartable, so a chart-less report reads unchanged.
    """
    if not charts:
        return []

    lines = ["## Exhibits", ""]
    for chart in charts:
        lines.extend(_exhibit_lines(chart, level=3))
    return lines


def _exhibit_lines(chart: ChartView, *, level: int) -> list[str]:
    markers = "".join(f"[^{number}]" for number in chart.markers)
    return [
        f"{'#' * level} {chart.title}",
        "",
        f"{chart.caption}{markers}",
        "",
        "*Rendered in the HTML and PDF editions of this report.*",
        "",
        *_exhibit_table(chart.table),
    ]


def _exhibit_table(table: ChartTable | None) -> list[str]:
    """The exhibit's own values, beneath the deferral line.

    Deferring the drawing used to defer the numbers with it, and for the segment mix the
    exhibit was the only place in the whole document a reader could meet segment revenue
    at all — so the Markdown edition of a report whose store held revenue for nine
    geographies showed none of the nine. The values are the builder's, formatted once, so
    the table and the bars say the same thing.
    """
    if table is None:
        return []
    return [
        "| " + " | ".join(table.columns) + " |",
        "|" + "|".join(["---"] * len(table.columns)) + "|",
        *("| " + " | ".join(row) + " |" for row in table.rows),
        "",
    ]


def _limitations_block(rows: tuple[tuple[str, str], ...]) -> list[str]:
    """Every degraded section's note, once, near the end (gap R4).

    A reader who wants the caveats finds them gathered; a reader working through the
    analysis is not handed the same sentence under every heading.
    """
    if not rows:
        return []
    lines = ["## Scope and limitations", ""]
    lines.extend(f"- **{title}:** {note}" for title, note in rows)
    lines.append("")
    return lines


def _undated_block(note: str | None) -> list[str]:
    """The C3 marker's legend, once, when any heading carries the symbol."""
    if note is None:
        return []
    return [f"*{note}*", ""]


def _footer() -> list[str]:
    return ["", "---", "", DISCLAIMER, ""]


# -- Footnotes and appendix ----------------------------------------------------------------


def _footnotes(footnotes: tuple[Footnote, ...], *, style: HouseStyle) -> list[str]:
    """The footnote block: one entry per marker, in marker order."""
    if not footnotes:
        return []

    lines = ["", "## Notes", ""]
    lines.extend(
        f"[^{footnote.number}]: {_footnote_text(footnote, style=style)}" for footnote in footnotes
    )
    lines.append("")
    return lines


def _footnote_text(footnote: Footnote, *, style: HouseStyle) -> str:
    match footnote:
        case CalculationFootnote():
            # `value` already carries its unit, said once by the display formatter — this
            # used to join a bare number to a `unit` field, in one of three copies of the
            # same line, and none of the three could apply the house style. The period,
            # when the row carries one, is part of what the figure *is* (gap A54): the
            # live report set FY2021 ratios beside FY2025 ones and no note dated either.
            # The function reference stays on the calculation row and its drill-down:
            # printed, `aer.calc.ratios:net_margin` is a module path in a reader's
            # footnote (§2.11), and the formula and the code version are what make the
            # figure checkable.
            period = f" for {footnote.period_label}" if footnote.period_label else ""
            return (
                f"Calculated: `{footnote.formula}` "
                f"= {footnote.value}{period} "
                f"(code version `{footnote.code_version_prefix}`)."
            )
        case SourceFootnote():
            parts = [footnote.title]
            if footnote.publisher:
                parts.append(footnote.publisher)
            if footnote.publication_date:
                published = display.date_text(footnote.publication_date, style=style)
                parts.append(f"published {published}")
            parts.append(f"retrieved {display.date_text(footnote.retrieved, style=style)}")
            parts.append(footnote.tier)
            return f"{', '.join(parts)}. <{footnote.url}>{_passage(footnote, style=style)}"
        case DerivedFootnote():
            named = ", ".join(f"[{title}]({url})" for title, url in footnote.sources)
            stated = f" Each component is stated in {named}." if named else ""
            # The code version, as a calculation's note carries one and for the same
            # reason: the sum is this platform's arithmetic, not the filer's.
            version = (
                f" (code version `{footnote.code_version_prefix}`)"
                if footnote.code_version_prefix
                else ""
            )
            return f"**Derived, not reported.** {footnote.statement}{stated}{version}"
        case UnresolvedFootnote():
            return (
                f"**Unresolved citation** — this claim references {footnote.kind_label} "
                f"`{footnote.identifier}`, which is no longer present. Do not rely on the "
                "figure it supports."
            )


def _passage(footnote: SourceFootnote, *, style: HouseStyle) -> str:
    """The quoted passage, or the pointer to the note that carries it, or nothing.

    **On one line, whitespace collapsed.** A footnote definition ends at the first line
    that is not indented under it, so a passage carrying the document's own line breaks
    would end its own note and continue as body text. The collapse is the same one the
    verifier compares under, so what is printed is the excerpt as it was checked.

    The provenance is the ADR's: where it came from, when it was retrieved, and the digest
    of the bytes it came from — an excerpt printed without its origin is the unverifiable
    quotation this platform argues against. It leads rather than trails, as an attribution
    does, and because that puts the quotation last on its line: a filing's own text may
    contain quotation marks, so nothing else can reliably say where it ends, and
    `presentation_integrity` has to know exactly that to keep its scan off it.
    """
    if footnote.excerpt is None:
        if footnote.quoted_at is None:
            return ""
        return f" The passage is quoted at note {footnote.quoted_at}."

    retrieved = display.date_text(footnote.retrieved, style=style)
    digest = f", artefact `{footnote.digest_prefix}`" if footnote.digest_prefix else ""
    return (
        f" {PASSAGE_LEAD} (retrieved {retrieved}{digest}): "
        f'"{normalise_whitespace(footnote.excerpt)}"'
    )


def _appendix(rows: tuple[AppendixRow, ...], *, style: HouseStyle) -> list[str]:
    if not rows:
        return []

    lines = [
        "",
        "## Sources",
        "",
        "| Source | Publisher | Published | Retrieved | Tier | Artefact |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"[{row.title}]({row.url})",
                    row.publisher or "—",
                    display.date_text(row.publication_date, style=style)
                    if row.publication_date
                    else "—",
                    display.date_text(row.retrieved, style=style),
                    row.tier,
                    f"`{row.digest_prefix}`" if row.digest_prefix else "`—`",
                ]
            )
            + " |"
        )

    lines.append("")
    return lines
