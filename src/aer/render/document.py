"""One assembly of a report, for every notation of it.

Task 46's organising rule: **what is approved is what exists.** The Gate 2 preview, the
stored HTML, the PDF derived from it and the Markdown beside it must be one assembly
serialised, never parallel renderings that could drift. So this module walks the run's
sections once — position order, global footnote numbering, footnote and appendix
resolution — and produces a :class:`ReportDocument`: everything a serialiser needs and
nothing it may decide. The Markdown notation lives in :mod:`aer.render.markdown`, the
HTML notation in :mod:`aer.render.html`, and neither can renumber a footnote or reorder a
section because the numbers and the order arrive already fixed.

**The comps parameter is a `WithheldComps` and cannot be a `CompsTable`.** A rendered
report is the shareable artefact: it gets exported, attached and sent, and every multiple
in a comps table derives from market data licensed for internal use with no derived-data
exemption (ADR 0030 route 2). So the type this assembler accepts is the one that has no
figures in it, and a caller wanting to put the numbers in a report cannot do it by
passing a different argument — there is no argument that would carry them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.comps import WithheldComps
from aer.charts import Chart
from aer.config import HouseStyle
from aer.db.models import (
    Calculation,
    Company,
    Evaluation,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
)
from aer.errors import ValidationError
from aer.sections.registry import sections_for_job
from aer.sections.render import CitationRef, Fragment, Heading, render_section

__all__ = [
    "DISCLAIMER",
    "UNDATED_MARKER",
    "UNDATED_NOTE",
    "AppendixRow",
    "CalculationFootnote",
    "ChartView",
    "CoverageNote",
    "Footnote",
    "HeaderView",
    "ReportDocument",
    "SectionView",
    "SectorNote",
    "SourceFootnote",
    "UnresolvedFootnote",
    "assemble_document",
]

DISCLAIMER = (
    "This is a personal research tool. It is **not** regulated investment advice, and "
    "nothing in this document is a recommendation to buy, sell or hold any security. Any "
    "rating expressed is a non-binding personal view."
)

# The C3 marker: point-in-time is a soft constraint, so a source with no stated
# publication date is used rather than excluded — and every section resting on one says
# so with this symbol by its heading, explained once by the note below.
UNDATED_MARKER = "\N{DAGGER}"
UNDATED_NOTE = (
    f"{UNDATED_MARKER} Rests in part on a source without a stated publication date. The "
    "point-in-time rule cannot be checked against such a source, so it is used with this "
    "caveat rather than excluded."
)

# How much of an artefact digest a document prints. Enough to identify the file among a
# run's artefacts, short enough to read; the full digest is in the database for anyone
# verifying.
_HASH_PREFIX = 12

# How much of a code version a calculation footnote prints, on the same reasoning.
_CODE_PREFIX = 12

# Footnote display precision for calculated values: four decimal places, marked when
# the rounding cut anything. The stored value is never touched.
_DISPLAY_EXPONENT = -4
_DISPLAY_QUANTUM = Decimal("0.0001")

# Sorts undated sources first in the appendix rather than raising on the comparison.
_EPOCH = date(1970, 1, 1)

_STATUS_NOTES = {
    SectionStatus.PENDING: "This section was not generated.",
    SectionStatus.FAILED: "This section could not be generated.",
    SectionStatus.SKIPPED_NOT_APPLICABLE: "This section does not apply to this company.",
}


@dataclass(frozen=True, slots=True)
class SectorNote:
    """What a specialist classification obliges the report to say about itself.

    Assembled by the caller from the confirmed classification rather than looked up here, so
    a report renders what the run was actually permitted to do rather than what the current
    seed says it would be permitted to do today.
    """

    label: str
    warnings: tuple[str, ...] = ()
    blocked_models: tuple[str, ...] = ()

    # The required-metric disclosure, already written as a sentence by
    # `aer.services.sectors.MetricDisclosure`. A string rather than the structure, because
    # *whether* the absence is disclosed must not depend on a template remembering to.
    metric_disclosure: str = ""


@dataclass(frozen=True, slots=True)
class HeaderView:
    """The document's masthead, as data."""

    company_name: str
    ticker: str
    exchange: str
    as_of: date
    base_currency: str
    point_in_time: bool
    generated_at: datetime
    rating: str | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class SectionView:
    """One section, walked: its fragments, and where it belongs.

    ``origin`` is what the contents page groups by — ``'skill'`` sections appear under
    the "Custom analysis" heading so bespoke methodology is attributed as the operator's
    own — while the body keeps position order regardless. ``generated`` is what the
    contents page marks: a reader should learn a section is missing from the contents,
    not by turning to it.
    """

    key: str
    title: str
    origin: str
    position: Decimal
    fragments: tuple[Fragment, ...]
    citations: tuple[CitationRef, ...]
    generated: bool = True

    # The exhibits this section's definition claims (gap N1): a chart supports analysis,
    # so it renders beside the analysis rather than in a pack at the back. Claimed
    # through the definition row's ``evidence_policy.exhibits`` — data, never a section
    # key in code — and an unclaimed chart still lands in the document's own pack.
    charts: tuple[ChartView, ...] = ()

    # Whether this section cites a source with no stated publication date (the C3
    # marker). Point-in-time is a soft constraint: such a source is used rather than
    # excluded, and the section says so with a small symbol by its heading.
    undated: bool = False


@dataclass(frozen=True, slots=True)
class CoverageNote:
    """What this report could not cover, said once, at the front (gap A40).

    The operator's decision on thin runs: still render the research note, with a small
    warning and the sources in reach. This is that warning — derived from recorded state
    (section statuses and failed evaluation metrics), never from prose, so it cannot
    drift from what actually happened.
    """

    sections_failed: tuple[str, ...]
    sections_total: int
    checks_failed: tuple[str, ...]

    @property
    def sentence(self) -> str:
        """The notice, minus the sources link — each notation attaches its own."""
        parts: list[str] = []
        if self.sections_failed:
            named = ", ".join(self.sections_failed)
            parts.append(
                f"{len(self.sections_failed)} of {self.sections_total} sections could "
                f"not be generated ({named})"
            )
        if self.checks_failed:
            checks = ", ".join(self.checks_failed)
            plural = "checks" if len(self.checks_failed) > 1 else "check"
            parts.append(f"the {checks} validation {plural} failed")
        return "; and ".join(parts) + "." if parts else ""


@dataclass(frozen=True, slots=True)
class CalculationFootnote:
    number: int
    formula: str
    value: str
    unit: str
    function_ref: str
    code_version_prefix: str


@dataclass(frozen=True, slots=True)
class SourceFootnote:
    number: int
    title: str
    url: str
    publisher: str | None
    publication_date: date | None
    retrieved: date
    tier: str


@dataclass(frozen=True, slots=True)
class UnresolvedFootnote:
    """A citation whose target is gone.

    Stated in the document rather than dropped. A report that silently omitted a broken
    citation would read as though the claim were unsupported by accident; saying so makes
    it a finding.
    """

    number: int
    kind_label: str
    identifier: str

    @property
    def statement(self) -> str:
        """The honest state, as one sentence — the same words in every surface.

        The document's footnote and the drill-down page both render this, so a reader
        who follows a broken marker is told exactly what the document told them, not a
        softer paraphrase of it.
        """
        return (
            f"Unresolved citation — this claim references {self.kind_label} "
            f"{self.identifier}, which is no longer present. Do not rely on the figure "
            "it supports."
        )


@dataclass(frozen=True, slots=True)
class ChartView:
    """One exhibit, numbered into the document.

    ``markers`` are the chart's citations as global footnote numbers, in caption order —
    assigned here so no serialiser can renumber them, exactly as for a section's markers.
    """

    key: str
    title: str
    svg: str
    caption: str
    markers: tuple[int, ...]
    placeholder: bool
    licence_note: str


Footnote = CalculationFootnote | SourceFootnote | UnresolvedFootnote


@dataclass(frozen=True, slots=True)
class AppendixRow:
    """One source the report rests on, with enough to verify it."""

    title: str
    url: str
    publisher: str | None
    publication_date: date | None
    retrieved: date
    tier: str
    digest_prefix: str | None


@dataclass(slots=True)
class ReportDocument:
    """The whole document, assembled once. Serialisers may only transcribe it.

    ``job_id`` is the run the document was assembled from — what lets the HTML notation
    write each footnote's drill-down link, so a reader can walk any marker back to the
    excerpt, verification state and artefact digest behind it.
    """

    header: HeaderView
    sector: SectorNote | None
    sections: tuple[SectionView, ...]
    comps_paragraph: str | None
    footnotes: tuple[Footnote, ...]
    appendix: tuple[AppendixRow, ...]
    citations: list[CitationRef]
    charts: tuple[ChartView, ...] = ()
    disclaimer: str = DISCLAIMER
    job_id: uuid.UUID | None = None
    coverage: CoverageNote | None = None

    # The house style the document was assembled under (gap R1, ADR 0056). Carried on the
    # document so every serialiser prints dates and values the same way; the fragments
    # were already formatted with it during the walk.
    style: HouseStyle = dataclass_field(default_factory=HouseStyle)

    # Every degraded section's note, consolidated (gap R4): the reader meets each
    # limitation once, in one place near the end, instead of a recurring banner carrying
    # the same sentence through the whole note. The per-section banner stays, one line.
    limitations: tuple[tuple[str, str], ...] = ()

    # The undated-source legend (the C3 marker), present exactly when some section
    # carries the symbol — a legend with no marker, or a marker with no legend, would
    # each leave the reader guessing.
    undated_note: str | None = None

    @property
    def section_keys(self) -> list[str]:
        return [section.key for section in self.sections]

    @property
    def footnote_count(self) -> int:
        return len(self.citations)


async def assemble_document(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    company: Company | None = None,
    sector: SectorNote | None = None,
    comps: WithheldComps | None = None,
    charts: tuple[Chart, ...] = (),
    rating: str | None = None,
    confidence: float | None = None,
    generated_at: datetime | None = None,
    style: HouseStyle | None = None,
) -> ReportDocument:
    """Assemble a run's sections into one document.

    **Sections are iterated, never enumerated**: whatever
    :func:`aer.sections.registry.sections_for_job` returns is walked through the generic
    renderer, which is why inserting a section definition makes a section appear here —
    correctly placed, correctly numbered — with no code change.

    **Footnotes are numbered across the whole document, in the order the markers
    appear.** A reader chasing marker 3 finds the third marker, not the third of
    whichever section they happen to be in.

    Args:
        charts: The exhibit pack from :func:`aer.services.exhibits.exportable_charts_for`.
            A chart with ``exportable=False`` is refused outright — a rendered report is
            the shareable artefact, and licensed geometry does not become shareable by
            being passed to the wrong function (ADR 0043).
        generated_at: Stamped on the document. A parameter rather than a clock read so a
            test can assert the whole output byte for byte, and so a re-render of an
            archived report can carry the date it was actually produced.

    Raises:
        ValidationError: If any chart in ``charts`` is internal-only.
    """
    internal = [chart.key for chart in charts if not chart.exportable]
    if internal:
        message = (
            f"Internal-only charts cannot enter a report document: {', '.join(internal)}. "
            "They render solely on the valuation surface."
        )
        raise ValidationError(message)

    sections = await sections_for_job(session, job.id)
    definitions = await _definitions_for(session, sections)
    active_style = style if style is not None else HouseStyle()

    views: list[SectionView] = []
    citations: list[CitationRef] = []

    # A chart renders beside the section whose definition claims it (gap N1) — reading
    # order, so the interleaved footnote numbering stays "marker 3 is the third marker".
    unclaimed: dict[str, Chart] = {chart.key: chart for chart in charts}

    for section in sections:
        definition = definitions.get(section.section_definition_id)
        rendered = render_section(
            key=section.section_key,
            title=definition.title if definition else section.section_key,
            contract=(definition.output_contract if definition else {}),
            content=section.content,
            # Numbering continues across the document, so a reader chasing marker 3
            # finds the third marker in the report rather than the third in some section.
            footnote_start=len(citations) + 1,
            status_note=_STATUS_NOTES.get(section.status),
            # A failed section's recorded reason is the validator's diagnostics — raw
            # ids and schema paths, written for the operator's console. The reader gets
            # the status line and the coverage notice; the diagnostics stay in the run.
            warning=(
                None if section.status is SectionStatus.FAILED else section.low_confidence_reason
            ),
            style=active_style,
        )
        citations.extend(rendered.citations)
        placed = tuple(
            _chart_view(unclaimed.pop(chart_key), citations)
            for chart_key in _declared_exhibits(definition)
            if chart_key in unclaimed
        )
        views.append(
            SectionView(
                key=section.section_key,
                title=rendered.title,
                origin=definition.origin if definition else "builtin",
                position=Decimal(str(section.position)),
                fragments=rendered.fragments,
                citations=tuple(rendered.citations),
                generated=section.status not in (SectionStatus.FAILED, SectionStatus.PENDING),
                charts=placed,
            )
        )

    # Whatever no section claimed keeps the pack at the back — a chart is never dropped
    # for want of a claim, only relocated by one.
    chart_views = [_chart_view(chart, citations) for chart in unclaimed.values()]

    footnotes = await _footnotes(session, citations)
    appendix = await _appendix(session, citations)
    coverage = await _coverage(session, job=job, sections=sections, definitions=definitions)

    # The C3 marker, derived from stored rows: any section citing a source whose
    # publication date is unknown carries the symbol, and the legend appears once.
    undated_ids = {
        identifier
        for identifier, row in (await _load_source_documents(session, citations)).items()
        if row.publication_date is None
    }
    if undated_ids:
        views = [_marked_if_undated(view, undated_ids) for view in views]

    return ReportDocument(
        header=HeaderView(
            company_name=company.name if company is not None else request.company_name,
            ticker=request.ticker,
            exchange=request.exchange,
            as_of=request.as_of_date,
            base_currency=request.base_currency,
            point_in_time=request.point_in_time,
            generated_at=generated_at or datetime.now(UTC),
            rating=rating,
            confidence=confidence,
        ),
        sector=sector,
        sections=tuple(views),
        comps_paragraph=comps.as_paragraph() if comps is not None else None,
        footnotes=footnotes,
        appendix=appendix,
        style=active_style,
        limitations=tuple(
            (view.title, str(section.low_confidence_reason))
            for section, view in zip(sections, views, strict=True)
            if section.low_confidence_reason
        ),
        citations=citations,
        charts=tuple(chart_views),
        job_id=job.id,
        coverage=coverage,
        undated_note=UNDATED_NOTE if any(view.undated for view in views) else None,
    )


# -- Resolution ------------------------------------------------------------------------------


def _marked_if_undated(view: SectionView, undated_ids: set[str]) -> SectionView:
    """The view with the C3 marker on its heading, when it cites an undated source.

    The symbol travels in the heading fragment's text, so every notation carries it the
    same way; ``view.title`` stays clean for the contents page and the limitations list.
    An unresolved citation is not marked — its footnote already says not to rely on it.
    """
    rests_on_undated = any(
        ref.kind == "source_document" and ref.identifier in undated_ids for ref in view.citations
    )
    if not rests_on_undated or not view.fragments:
        return view
    heading = view.fragments[0]
    if not isinstance(heading, Heading):  # pragma: no cover -- render_section leads with one
        return replace(view, undated=True)
    marked = Heading(level=heading.level, text=f"{heading.text} {UNDATED_MARKER}")
    return replace(view, fragments=(marked, *view.fragments[1:]), undated=True)


def _declared_exhibits(definition: SectionDefinition | None) -> list[str]:
    """The chart keys a definition row claims, in its declared order (gap N1).

    Read from ``evidence_policy.exhibits`` — the definition's JSONB, so the mapping from
    exhibit to section is a row a migration seeds, never a section key in code. Absent or
    malformed means the section claims nothing, and the charts keep the pack at the back.
    """
    stated = (definition.evidence_policy or {}).get("exhibits") if definition else None
    if not isinstance(stated, list):
        return []
    return [str(item) for item in stated]


def _chart_view(chart: Chart, citations: list[CitationRef]) -> ChartView:
    """One chart numbered into the document at the current footnote position.

    An exhibit's marker is a footnote like any other: the numbers are taken where the
    chart lands in reading order, and both notations receive them already assigned.
    """
    markers = tuple(range(len(citations) + 1, len(citations) + 1 + len(chart.citations)))
    citations.extend(chart.citations)
    return ChartView(
        key=chart.key,
        title=chart.title,
        svg=chart.svg,
        caption=chart.caption,
        markers=markers,
        placeholder=chart.placeholder,
        licence_note=chart.licence_note,
    )


async def _coverage(
    session: AsyncSession,
    *,
    job: Job,
    sections: list[ReportSection],
    definitions: dict[uuid.UUID, SectionDefinition],
) -> CoverageNote | None:
    """The coverage notice's inputs, from recorded state only. ``None`` when full.

    Derived rather than written: the section statuses and the evaluation rows are what
    actually happened, so the notice cannot say less than the run recorded — the failure
    mode gap A40 exists to prevent is a note-perfect document shape wrapped around
    content that is not there, with nothing at the front saying so.
    """
    failed = tuple(
        (
            definitions[section.section_definition_id].title
            if section.section_definition_id in definitions
            else section.section_key
        )
        for section in sections
        if section.status in (SectionStatus.FAILED, SectionStatus.PENDING)
    )
    checks = await session.scalars(
        select(Evaluation.metric)
        .where(Evaluation.job_id == job.id, Evaluation.passed.is_(False))
        .order_by(Evaluation.metric)
    )
    failed_checks = tuple(checks)
    if not failed and not failed_checks:
        return None
    return CoverageNote(
        sections_failed=failed,
        sections_total=len(sections),
        checks_failed=failed_checks,
    )


async def _footnotes(session: AsyncSession, citations: list[CitationRef]) -> tuple[Footnote, ...]:
    """One footnote per marker, in marker order, resolved to something checkable.

    A source footnote carries the URL, publisher, publication date, retrieval date and
    tier — enough to find the bytes and confirm they are the bytes. A calculation
    footnote carries the formula and the code version. A footnote that only said
    "SEC EDGAR" would look like a citation and support nothing.
    """
    documents = await _load_source_documents(session, citations)
    calculations = await _load_calculations(session, citations)

    footnotes: list[Footnote] = []
    for number, reference in enumerate(citations, start=1):
        if reference.kind == "calculation":
            calculation = calculations.get(reference.identifier)
            if calculation is None:
                footnotes.append(
                    UnresolvedFootnote(
                        number=number, kind_label="calculation", identifier=reference.identifier
                    )
                )
                continue
            footnotes.append(
                CalculationFootnote(
                    number=number,
                    formula=calculation.formula,
                    value=_display_value(calculation.output_value),
                    # "pure" is the unit algebra's own vocabulary for a dimensionless
                    # ratio; to a reader it is noise beside the number. The stored row
                    # keeps it — this is display, and the drill-down shows everything.
                    unit=("" if calculation.output_unit == "pure" else calculation.output_unit),
                    function_ref=calculation.function_ref,
                    code_version_prefix=calculation.code_version[:_CODE_PREFIX],
                )
            )
            continue

        document = documents.get(reference.identifier)
        if document is None:
            footnotes.append(
                UnresolvedFootnote(
                    number=number, kind_label="source document", identifier=reference.identifier
                )
            )
            continue
        footnotes.append(
            SourceFootnote(
                number=number,
                title=document.title or document.url,
                url=document.url,
                publisher=document.publisher,
                publication_date=document.publication_date,
                retrieved=document.retrieved_at.date(),
                tier=document.source_tier.value,
            )
        )

    return tuple(footnotes)


async def _appendix(session: AsyncSession, citations: list[CitationRef]) -> tuple[AppendixRow, ...]:
    """Every source document the report rests on, with enough to verify it.

    The hash prefix is what makes this an appendix rather than a bibliography: a reader
    can take the digest, find the artefact, and confirm the bytes are the bytes the
    report was written from.
    """
    documents = await _load_source_documents(session, citations)
    ordered = sorted(documents.values(), key=lambda d: (d.publication_date or _EPOCH, d.url))
    return tuple(
        AppendixRow(
            title=document.title or document.url,
            url=document.url,
            publisher=document.publisher,
            publication_date=document.publication_date,
            retrieved=document.retrieved_at.date(),
            tier=document.source_tier.value,
            digest_prefix=(document.artefact.sha256[:_HASH_PREFIX] if document.artefact else None),
        )
        for document in ordered
    )


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


def _display_value(value: Decimal) -> str:
    """A calculation's value as a reader meets it: four decimal places, marked when cut.

    The live report printed ``0.437565271053`` in a footnote — twelve decimal places of
    asset turnover, which is storage precision leaking into prose. The stored value is
    untouched and the drill-down page shows it in full; this is presentation, and it
    says so when it rounds rather than pretending the shorter number is the recorded one.
    """
    exponent = value.as_tuple().exponent
    needs_rounding = isinstance(exponent, int) and exponent < _DISPLAY_EXPONENT
    quantised = value.quantize(_DISPLAY_QUANTUM) if needs_rounding else value
    if quantised == value:
        return str(value)
    return f"{quantised.normalize():f} (rounded; full precision stored)"


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
