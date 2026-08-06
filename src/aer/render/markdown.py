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
from typing import TYPE_CHECKING

import structlog

from aer.render.document import (
    DISCLAIMER,
    AppendixRow,
    CalculationFootnote,
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

    from aer.calc.comps import WithheldComps
    from aer.db.models import Company, Job, ResearchRequest

__all__ = ["DISCLAIMER", "RenderedReport", "SectorNote", "render_markdown", "serialise_markdown"]

_log = structlog.get_logger("aer.render.markdown")


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
    comps: WithheldComps | None = None,
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
    warning at the foot of a report is a footnote, and `docs/PLAN.md` section 2.9 is
    explicit that a blocked model produces a block rather than a footnote: a reader has
    to meet the limitation before the numbers, because the number is what they will
    remember.
    """
    body: list[str] = []
    for section in document.sections:
        body.append("\n".join(markdown_lines(section.fragments)))

    return "\n".join(
        [
            *_header(document.header),
            *_sector_block(document.sector),
            *body,
            *_comps_block(document.comps_paragraph),
            *_footnotes(document.footnotes),
            *_appendix(document.appendix),
            *_footer(),
        ]
    )


# -- Header and footer ---------------------------------------------------------------------


def _header(header: HeaderView) -> list[str]:
    lines = [
        f"# {header.company_name} — Research Note",
        "",
        f"**Ticker:** {header.ticker} ({header.exchange})  ",
        f"**As-of date:** {header.as_of.isoformat()}  ",
        f"**Base currency:** {header.base_currency}  ",
        f"**Point-in-time:** {'enforced' if header.point_in_time else 'off'}  ",
        f"**Generated:** {header.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ",
    ]

    # "No view" is stated rather than omitted. A missing rating and a deliberate abstention
    # look identical unless one of them says so.
    lines.append(f"**Non-binding view:** {header.rating or 'no view reached'}  ")
    if header.confidence is not None:
        lines.append(f"**Confidence:** {header.confidence:.0%}  ")

    lines.extend(["", f"> {DISCLAIMER}", "", "---", ""])
    return lines


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


def _comps_block(paragraph: str | None) -> list[str]:
    """The comparables disclosure — that one was done, and that its figures are not here.

    The paragraph arrives from :class:`~aer.calc.comps.WithheldComps` via the assembler,
    which is the type-level guarantee that no figure can be in it (ADR 0034). Empty when
    no comparison was performed, because "no comps table" and "a comps table you are not
    being shown" are different claims and only the second needs saying.
    """
    if paragraph is None:
        return []

    return ["## Comparable companies", "", paragraph, ""]


def _footer() -> list[str]:
    return ["", "---", "", DISCLAIMER, ""]


# -- Footnotes and appendix ----------------------------------------------------------------


def _footnotes(footnotes: tuple[Footnote, ...]) -> list[str]:
    """The footnote block: one entry per marker, in marker order."""
    if not footnotes:
        return []

    lines = ["", "## Notes", ""]
    lines.extend(f"[^{footnote.number}]: {_footnote_text(footnote)}" for footnote in footnotes)
    lines.append("")
    return lines


def _footnote_text(footnote: Footnote) -> str:
    match footnote:
        case CalculationFootnote():
            return (
                f"Calculated: `{footnote.formula}` "
                f"= {footnote.value} {footnote.unit} "
                f"(`{footnote.function_ref}`, code version `{footnote.code_version_prefix}`)."
            )
        case SourceFootnote():
            parts = [footnote.title]
            if footnote.publisher:
                parts.append(footnote.publisher)
            if footnote.publication_date:
                parts.append(f"published {footnote.publication_date.isoformat()}")
            parts.append(f"retrieved {footnote.retrieved.isoformat()}")
            parts.append(f"tier {footnote.tier}")
            return f"{', '.join(parts)}. <{footnote.url}>"
        case UnresolvedFootnote():
            return (
                f"**Unresolved citation** — this claim references {footnote.kind_label} "
                f"`{footnote.identifier}`, which is no longer present. Do not rely on the "
                "figure it supports."
            )


def _appendix(rows: tuple[AppendixRow, ...]) -> list[str]:
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
                    row.publication_date.isoformat() if row.publication_date else "—",
                    row.retrieved.isoformat(),
                    row.tier,
                    f"`{row.digest_prefix}`" if row.digest_prefix else "`—`",
                ]
            )
            + " |"
        )

    lines.append("")
    return lines
