"""The Markdown report: sections in position order, footnotes that resolve, a disclaimer.

**Sections are iterated, never enumerated.** This module asks
:func:`aer.sections.registry.sections_for_job` for the run's sections in position order and
renders whatever comes back through the generic renderer. It contains no section key, no
ordering logic and no per-section branch — which is why inserting a third
``section_definitions`` row makes a third section appear here, correctly numbered, with no
code change. There is a test that does exactly that.

**Footnotes are numbered across the whole document, in the order the markers appear.** A
reader chasing ``[^3]`` finds the third marker, not the third marker of whichever section
they happen to be in. That means numbering is assigned as sections are rendered, in
sequence, which is why the renderer takes a starting number rather than restarting.

**Every footnote resolves to something checkable.** A source-document footnote carries the
URL, publisher, publication date, retrieval date, tier and the first characters of the
artefact hash — enough to find the bytes and confirm they are the bytes. A calculation
footnote carries the formula and the code version. A footnote that only said "SEC EDGAR"
would look like a citation and support nothing.

**The disclaimer is not optional and not configurable.** Every rendered surface says this
is a personal research tool and not regulated investment advice, because the one time it is
missing is the one time it matters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import (
    Calculation,
    Company,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
)
from aer.sections.registry import sections_for_job
from aer.sections.render import CitationRef, render_section

__all__ = ["RenderedReport", "render_markdown"]

_log = structlog.get_logger("aer.render.markdown")

DISCLAIMER = (
    "This is a personal research tool. It is **not** regulated investment advice, and "
    "nothing in this document is a recommendation to buy, sell or hold any security. Any "
    "rating expressed is a non-binding personal view."
)

# How much of an artefact digest to print. Enough to identify the file among a run's
# artefacts, short enough to read; the full digest is in the database for anyone verifying.
_HASH_PREFIX = 12

# Sorts undated sources first in the appendix rather than raising on the comparison.
_EPOCH = date(1970, 1, 1)

_STATUS_NOTES = {
    SectionStatus.PENDING: "This section was not generated.",
    SectionStatus.FAILED: "This section could not be generated.",
    SectionStatus.SKIPPED_NOT_APPLICABLE: "This section does not apply to this company.",
}


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
    rating: str | None = None,
    confidence: float | None = None,
    generated_at: datetime | None = None,
) -> RenderedReport:
    """Assemble a run's sections into a Markdown document.

    Args:
        generated_at: Stamped on the document. A parameter rather than a clock read so a
            test can assert the whole output byte for byte, and so a re-render of an
            archived report can carry the date it was actually produced.
    """
    sections = await sections_for_job(session, job.id)
    definitions = await _definitions_for(session, sections)

    body: list[str] = []
    citations: list[CitationRef] = []
    keys: list[str] = []

    for section in sections:
        definition = definitions.get(section.section_definition_id)
        rendered = render_section(
            key=section.section_key,
            title=definition.title if definition else section.section_key,
            contract=(definition.output_contract if definition else {}),
            content=section.content,
            # Numbering continues across the document, so a reader chasing [^3] finds the
            # third marker in the report rather than the third in some section.
            footnote_start=len(citations) + 1,
            status_note=_STATUS_NOTES.get(section.status),
        )
        body.append(rendered.markdown)
        citations.extend(rendered.citations)
        keys.append(section.section_key)

    header = _header(
        request=request,
        company=company,
        rating=rating,
        confidence=confidence,
        generated_at=generated_at or datetime.now(UTC),
    )
    footnotes = await _footnotes(session, citations)
    appendix = await _source_appendix(session, citations)

    document = "\n".join([*header, *body, *footnotes, *appendix, *_footer()])

    _log.info(
        "report.rendered",
        job_id=str(job.id),
        sections=len(sections),
        footnotes=len(citations),
        characters=len(document),
    )
    return RenderedReport(markdown=document, citations=citations, section_keys=keys)


# -- Header and footer ---------------------------------------------------------------------


def _header(
    *,
    request: ResearchRequest,
    company: Company | None,
    rating: str | None,
    confidence: float | None,
    generated_at: datetime,
) -> list[str]:
    name = company.name if company is not None else request.company_name
    lines = [
        f"# {name} — Research Note",
        "",
        f"**Ticker:** {request.ticker} ({request.exchange})  ",
        f"**As-of date:** {request.as_of_date.isoformat()}  ",
        f"**Base currency:** {request.base_currency}  ",
        f"**Point-in-time:** {'enforced' if request.point_in_time else 'off'}  ",
        f"**Generated:** {generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ",
    ]

    # "No view" is stated rather than omitted. A missing rating and a deliberate abstention
    # look identical unless one of them says so.
    lines.append(f"**Non-binding view:** {rating or 'no view reached'}  ")
    if confidence is not None:
        lines.append(f"**Confidence:** {confidence:.0%}  ")

    lines.extend(["", f"> {DISCLAIMER}", "", "---", ""])
    return lines


def _footer() -> list[str]:
    return ["", "---", "", DISCLAIMER, ""]


# -- Footnotes -----------------------------------------------------------------------------


async def _footnotes(session: AsyncSession, citations: list[CitationRef]) -> list[str]:
    """The footnote block: one entry per marker, in marker order."""
    if not citations:
        return []

    documents = await _load_source_documents(session, citations)
    calculations = await _load_calculations(session, citations)

    lines = ["", "## Notes", ""]
    for number, reference in enumerate(citations, start=1):
        lines.append(f"[^{number}]: {_footnote_text(reference, documents, calculations)}")
    lines.append("")
    return lines


def _footnote_text(
    reference: CitationRef,
    documents: dict[str, SourceDocument],
    calculations: dict[str, Calculation],
) -> str:
    if reference.kind == "calculation":
        calculation = calculations.get(reference.identifier)
        if calculation is None:
            return _unresolved(reference, "calculation")
        return (
            f"Calculated: `{calculation.formula}` "
            f"= {calculation.output_value} {calculation.output_unit} "
            f"(`{calculation.function_ref}`, code version `{calculation.code_version[:12]}`)."
        )

    document = documents.get(reference.identifier)
    if document is None:
        return _unresolved(reference, "source document")

    parts = [document.title or document.url]
    if document.publisher:
        parts.append(document.publisher)
    if document.publication_date:
        parts.append(f"published {document.publication_date.isoformat()}")
    parts.append(f"retrieved {document.retrieved_at.date().isoformat()}")
    parts.append(f"tier {document.source_tier.value}")

    return f"{', '.join(parts)}. <{document.url}>"


def _unresolved(reference: CitationRef, kind: str) -> str:
    """A citation whose target is gone.

    Stated in the document rather than dropped. A report that silently omitted a broken
    citation would read as though the claim were unsupported by accident; saying so makes
    it a finding.
    """
    return (
        f"**Unresolved citation** — this claim references {kind} `{reference.identifier}`, "
        "which is no longer present. Do not rely on the figure it supports."
    )


# -- Source appendix -------------------------------------------------------------------------


async def _source_appendix(session: AsyncSession, citations: list[CitationRef]) -> list[str]:
    """Every source document the report rests on, with enough to verify it.

    The hash prefix is what makes this an appendix rather than a bibliography: a reader can
    take the digest, find the artefact, and confirm the bytes are the bytes the report was
    written from.
    """
    documents = await _load_source_documents(session, citations)
    if not documents:
        return []

    ordered = sorted(documents.values(), key=lambda d: (d.publication_date or _EPOCH, d.url))

    lines = [
        "",
        "## Sources",
        "",
        "| Source | Publisher | Published | Retrieved | Tier | Artefact |",
        "|---|---|---|---|---|---|",
    ]
    for document in ordered:
        digest = document.artefact.sha256[:_HASH_PREFIX] if document.artefact else "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"[{document.title or document.url}]({document.url})",
                    document.publisher or "—",
                    document.publication_date.isoformat() if document.publication_date else "—",
                    document.retrieved_at.date().isoformat(),
                    document.source_tier.value,
                    f"`{digest}`",
                ]
            )
            + " |"
        )

    lines.append("")
    return lines


# -- Loading -----------------------------------------------------------------------------------


async def _definitions_for(
    session: AsyncSession, sections: list[ReportSection]
) -> dict[uuid.UUID, SectionDefinition]:
    ids = {section.section_definition_id for section in sections}
    if not ids:
        return {}
    rows = await session.scalars(select(SectionDefinition).where(SectionDefinition.id.in_(ids)))
    return {row.id: row for row in rows}


async def _load_source_documents(
    session: AsyncSession, citations: list[CitationRef]
) -> dict[str, SourceDocument]:
    ids = _uuids(citations, kind="source_document")
    if not ids:
        return {}
    rows = await session.scalars(select(SourceDocument).where(SourceDocument.id.in_(ids)))
    loaded = list(rows)
    for row in loaded:
        # Touch the relationship inside the async context; the appendix reads the digest
        # and a lazy load at render time would raise outside a greenlet.
        await session.refresh(row, ["artefact"])
    return {str(row.id): row for row in loaded}


async def _load_calculations(
    session: AsyncSession, citations: list[CitationRef]
) -> dict[str, Calculation]:
    ids = _uuids(citations, kind="calculation")
    if not ids:
        return {}
    rows = await session.scalars(select(Calculation).where(Calculation.id.in_(ids)))
    return {str(row.id): row for row in rows}


def _uuids(citations: list[CitationRef], *, kind: str) -> list[uuid.UUID]:
    """Parse the identifiers of one kind, ignoring any that are not UUIDs.

    An unparseable id resolves to nothing and renders as an unresolved citation, which is
    the honest outcome — better than a query that raises and takes the whole report with it.
    """
    found: list[uuid.UUID] = []
    for reference in citations:
        if reference.kind != kind:
            continue
        try:
            found.append(uuid.UUID(reference.identifier))
        except (ValueError, AttributeError, TypeError):
            continue
    return found
