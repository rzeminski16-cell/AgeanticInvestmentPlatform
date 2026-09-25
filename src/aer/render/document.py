"""One assembly of a report, for every notation of it.

Task 46's organising rule: **what is approved is what exists.** The Gate 2 preview, the
stored HTML, the PDF derived from it and the Markdown beside it must be one assembly
serialised, never parallel renderings that could drift. So this module walks the run's
sections once — position order, global footnote numbering, footnote and appendix
resolution — and produces a :class:`ReportDocument`: everything a serialiser needs and
nothing it may decide. The Markdown notation lives in :mod:`aer.render.markdown`, the
HTML notation in :mod:`aer.render.html`, and neither can renumber a footnote or reorder a
section because the numbers and the order arrive already fixed.

**The comps parameter is whatever `CompsTable.for_audience` returned, and nothing else.**
A rendered report is the shareable artefact — it gets exported, attached and sent — so
ADR 0034 gave this assembler a parameter that could only be a `WithheldComps`, the type
with no figures in it. ADR 0030's amendment of 2026-08-09 then determined that a figure
*computed from* the licensed feed may be published, and said in as many words that the
comps section of an exported report now shows the multiples. So the parameter widens to
the union `for_audience` produces, and the containment moves upstream of it rather than
away: the withheld arm still has no figures in it, so a renderer that took the wrong
branch would print nothing at all. What the determination did **not** cover — the price
series and any chart of it — is held where it always was, by `Chart.exportable` and the
refusal a few lines into :func:`assemble_document` (ADR 0043). See ADR 0034's amendment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.comps import Audience, CompsTable, WithheldComps
from aer.calc.units import SourceKind
from aer.charts import Chart, ChartTable
from aer.config import HouseStyle
from aer.core.enums import FactBasis
from aer.core.section_output import (
    LENGTH_EDIT_NOTE,
    NUMERAL_EDIT_NOTE,
    editorial_notes_in,
    reader_warning,
)
from aer.db.models import (
    Calculation,
    Company,
    Evaluation,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
)
from aer.errors import ValidationError
from aer.eval.metrics import spoken_metric
from aer.render import display
from aer.render.glance import GLANCE_CONTRACT, GLANCE_TITLE, glance_content
from aer.render.view import VIEW_CONTRACT, VIEW_TITLE, view_content
from aer.sections.consequences import consequences_for_audience
from aer.sections.evidence import refusal_causes_in
from aer.sections.registry import sections_for_job
from aer.sections.render import (
    Bullet,
    Bullets,
    CitationRef,
    Fragment,
    Heading,
    Paragraph,
    Table,
    TableRow,
    render_section,
)
from aer.services.extractions import printable_excerpts

__all__ = [
    "COMPS_TITLE",
    "DISCLAIMER",
    "NO_VIEW",
    "UNCITABLE_MULTIPLES",
    "UNDATED_MARKER",
    "UNDATED_NOTE",
    "AppendixRow",
    "CalculationFootnote",
    "ChartView",
    "CoverageNote",
    "DerivedFootnote",
    "DerivedInput",
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

# The C3 marker: a source with no stated publication date is used rather than excluded
# (ADR 0111) — and every section resting on one says so with this symbol by its heading,
# explained once by the note below.
UNDATED_MARKER = "\N{DAGGER}"

# What the masthead says where a view would go (ADR 0135). The report takes no side: the
# view is the operator's, written in a thesis and a decision after the report is read, and
# the line says so rather than reading as a view nobody got round to stating.
NO_VIEW = "none \N{EM DASH} this report takes no side"
UNDATED_NOTE = (
    f"{UNDATED_MARKER} Rests in part on a source without a stated publication date. Such a "
    "source is a weaker one than a dated document, so it is used with this caveat, and "
    "never as the primary source a section requires, rather than excluded."
)

# The comparables block's own heading, at the level a section takes, because that is what
# it is: a block of the report the platform writes rather than a model.
COMPS_TITLE = "Comparable companies"
_COMPS_HEADING_LEVEL = 2

# What a report says about multiples it holds and cannot footnote. Reachable only for a run
# recorded before the comps step stored each figure's calculation id, and said rather than
# passed over, because a section that simply stopped would read as an analysis that was
# never done.
UNCITABLE_MULTIPLES = (
    "The subject's own multiples were computed and are in this run's record, but the "
    "record does not say which calculation produced each of them. They are not shown "
    "here: a figure in this report carries a note leading to the arithmetic behind it, "
    "and one that led nowhere would be worth less than its absence."
)

# What introduces the confirmed peer set. It says who chose them and on what standing,
# because the lines beneath it carry no markers and a reader is entitled to know why: they
# are a person's judgement about which companies are comparable, not a filed fact.
_PEER_SET_LEAD = (
    "The companies below are the comparable set for this research, each proposed with a "
    "stated reason and confirmed by the operator. The reasons are judgements rather than "
    "reported facts, and carry no source note."
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

# Each refusal cause as a reader meets it (gap R6). The CHRW report's three missing
# sections each said only "This section could not be generated." — no cause, no
# consequence, no direction — while each failure was legible from the run and each had a
# *different* cause. The cause names are `aer.sections.evidence`'s own; the sentences are
# about the analysis, never about the machinery that refused it.
_FAILED_CAUSE_PHRASES: Final[dict[str, str]] = {
    "citation": "its draft cited evidence this research does not hold",
    "numeral": "its draft stated figures that could not be traced to a recorded source",
    "gaps": "the evidence it needed was not acquired",
    "length": "no draft fitted the length allotted to it",
    "truncation": "no complete draft could be produced within the space allowed",
    "policy": "its draft relied on forward-looking statements this section excludes",
    "method": "its draft described method inputs the valuation record does not hold",
    "calendar": "the evidence dated no catalyst, and the reporting calendar is not one",
}


def _status_note(section: ReportSection) -> str | None:
    """The status line under an absent section's heading, with its cause where one is legible.

    A failed section's stored reason is the validator's diagnostics — raw ids and schema
    paths, written for the operator's console — so the diagnostics themselves stay out of
    the document. But *which kinds* of refusal happened is readable from the same text,
    and a reader told the cause and the consequence can weigh the absence instead of
    merely noticing it. A reason matching no signature keeps the plain line: a wrong
    cause is worse than none.
    """
    if section.status is not SectionStatus.FAILED:
        return _STATUS_NOTES.get(section.status)
    causes = refusal_causes_in(section.low_confidence_reason or "")
    phrases = [phrase for cause in causes if (phrase := _FAILED_CAUSE_PHRASES.get(cause))]
    if not phrases:
        return _STATUS_NOTES[SectionStatus.FAILED]
    return (
        f"This section could not be generated: {', and '.join(phrases)}. What it would "
        "have covered is absent from this note rather than asserted without support."
    )


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
    generated_at: datetime
    rating: str | None
    confidence: float | None

    # What each terminal method gives, as one line — "$227.43 (perpetuity growth) and
    # $442.01 (exit multiple) a share" — composed from the same rows the view block prints,
    # so the two cannot disagree (ADR 0117). Two figures and never a range: ADR 0132, after
    # every judge of the verdict round read "$227.43 to $442.01" as a view the document then
    # contradicted. ``None`` for a run that produced no valuation.
    method_values: str | None = None

    @property
    def view(self) -> str:
        """The view line: a stated rating where one exists, which no run now writes."""
        return self.rating or NO_VIEW


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
    # marker). Such a source is used rather than excluded, capped and never primary (ADR
    # 0111), and the section says so with a small symbol by its heading.
    undated: bool = False

    # Whether the definition row claims a place on the one-page summary (gap O8) —
    # ``evidence_policy.one_pager``, data for the same reason the exhibit claims are.
    one_pager: bool = False


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

    # The at-a-glance block's stated reason for not rendering, or None when it rendered
    # (or had honestly nothing to show, which stays silent). Set only by the mixing guard
    # (ADR 0061), and carried here because a withheld front page needs its account in the
    # same place every other shortfall gives one.
    glance_withheld: str | None = None

    # The editing disclosures (gap R1): how many sections were shortened to their length,
    # and how many lost sentences whose figures traced to nothing. Counts, not names —
    # the Scope and limitations appendix names each one, and this notice's job is to say
    # once that the document was edited, not to be a second appendix.
    sections_shortened: int = 0
    sections_pruned: int = 0

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
            # The record keeps the metric names; the sentence says them (roadmap §2.11).
            checks = ", ".join(spoken_metric(name) for name in self.checks_failed)
            plural = "checks" if len(self.checks_failed) > 1 else "check"
            parts.append(f"the {checks} validation {plural} failed")
        if self.sections_shortened:
            count = self.sections_shortened
            parts.append(
                f"{count} section{'s were' if count != 1 else ' was'} shortened to fit "
                "the length allotted"
            )
        if self.sections_pruned:
            count = self.sections_pruned
            parts.append(
                "sentences whose figures could not be traced to a recorded source were "
                f"removed from {count} section{'s' if count != 1 else ''}"
            )
        if self.glance_withheld:
            parts.append(self.glance_withheld)
        return "; and ".join(parts) + "." if parts else ""


@dataclass(frozen=True, slots=True)
class CalculationFootnote:
    number: int
    formula: str

    # The figure as a reader meets it, **with its unit already in it** — `$66,987m`, not
    # `66987000000` beside a `unit` field. It carried the two apart until roadmap §3.19
    # item 40, and all three renderers then joined them the same way in three copies, none
    # of which could apply the house style: the note under a cell reading `$66,987m` said
    # `66987000000 USD`, and `presentation_integrity` counted it twice on the September
    # round's MSFT run. `aer.render.display.figure` is the one place that puts a figure and
    # its unit together for prose.
    value: str

    function_ref: str
    code_version_prefix: str

    # The reporting period the calculation was struck on — "FY2025" — or ``None`` for a
    # figure that is not a statement-period result. Stored since gap C1; rendered since
    # gap A54, because the live report anchored its structural reading on FY2021 ratios
    # and nothing the reader could see dated them.
    period_label: str | None = None


@dataclass(frozen=True, slots=True)
class SourceFootnote:
    number: int
    title: str
    url: str
    publisher: str | None
    publication_date: date | None
    retrieved: date

    # What kind of source it is, in words (`SourceTier.spoken`). The note used to read
    # "…, retrieved 12 March 2026, tier T1_REGULATORY", in the published document, at a
    # reader being asked to trust the figure above it. The tier *number* is the ordering
    # rule the ladder decides conflicts by and stays on the row; a footnote is prose.
    tier: str

    # ADR 0119's printed passage, and the digest that lets a reader confirm it is the
    # passage. Both ``None`` where the run verified nothing against this document, or
    # where one of the three gates refused it — in which case the note is exactly the
    # reference it was before, which is the state the ADR names as what is given up.
    #
    # **Printed once per document, at its first marker.** The stored runs carry 22 to 37
    # source markers resolving to four or five distinct documents, so a passage repeated
    # on every marker would be the same four paragraphs printed nine times each.
    # ``quoted_at`` is the note that does carry it, set on the others so a reader is told
    # where to look rather than left to notice.
    excerpt: str | None = None
    digest_prefix: str | None = None
    quoted_at: int | None = None


@dataclass(frozen=True, slots=True)
class DerivedInput:
    """One component of a derived figure, and the document that did state it."""

    label: str
    value: str
    source_title: str
    source_url: str


@dataclass(frozen=True, slots=True)
class DerivedFootnote:
    """A figure this platform computed at the fact layer, which no filing states.

    ADR 0114 derives a bank's total revenue from net interest income and non-interest
    income, because a bank has no revenue caption to extract. The row is a *fact* rather
    than a calculation — it is what the filing would have said had it said one — and a
    citation of it resolves to the document its components came from. Without this note
    the reader meets "Form 10-K, published…, tier T1_REGULATORY" against a number the
    10-K does not contain anywhere, which is the one thing a provenance chain must not do.

    So the note says what the figure is: the sum, its components by name and value, and
    each component's own document. The ADR asked for exactly this and parked it here.
    """

    number: int
    label: str
    value: str
    period_label: str | None
    inputs: tuple[DerivedInput, ...]
    code_version_prefix: str

    @property
    def statement(self) -> str:
        """The arithmetic in words, without the sources — each notation links those itself.

        Built here rather than in each notation, because the two must not be able to
        describe the same derivation differently — the same reason ``UnresolvedFootnote``
        owns its wording.
        """
        period = f" for {self.period_label}" if self.period_label else ""
        parts = " plus ".join(f"{one.label} of {one.value}" for one in self.inputs)
        composed = f", being {parts}" if parts else ""
        opening = self.label[:1].upper() + self.label[1:]
        return f"{opening}{period} is {self.value}{composed}."

    @property
    def sources(self) -> tuple[tuple[str, str], ...]:
        """The components' documents as (title, url), each once, in derivation order.

        Deduplicated because the two halves of a bank's revenue are ordinarily two lines
        of one filing, and naming it twice would read as two pieces of evidence.
        """
        seen: dict[str, str] = {}
        for one in self.inputs:
            if one.source_url and one.source_url not in seen:
                seen[one.source_url] = one.source_title or one.source_url
        return tuple((title, url) for url, title in seen.items())


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

    ``table`` is the builder's own values as text, carried for the Markdown edition, which
    cannot show the drawing. ``None`` where the geometry is not rows.
    """

    key: str
    title: str
    svg: str
    caption: str
    markers: tuple[int, ...]
    placeholder: bool
    licence_note: str
    table: ChartTable | None = None


Footnote = CalculationFootnote | DerivedFootnote | SourceFootnote | UnresolvedFootnote


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

    # The comparables block, already walked into fragments, empty when the run performed
    # no comparison. Fragments rather than a paragraph string since Phase 4.3: the block
    # now carries a table whose cells are footnoted figures, and a marker is a number the
    # assembler assigns — so both notations receive it already numbered, exactly as they
    # do the glance and every section.
    comps: tuple[Fragment, ...]

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

    # The front page's numbers (gap R10), already walked into fragments — empty when the
    # run holds nothing to show.
    glance: tuple[Fragment, ...] = ()

    # ADR 0117's composed half: what each terminal method gives, why the two differ, the
    # distance from the market and the levers, all of them recorded calculations (ADR
    # 0132). Empty for a run with no valuation. Its markers are the document's first,
    # because it is the first thing a reader meets.
    view: tuple[Fragment, ...] = ()

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
    comps: CompsTable | WithheldComps | None = None,
    charts: tuple[Chart, ...] = (),
    rating: str | None = None,
    confidence: float | None = None,
    generated_at: datetime | None = None,
    style: HouseStyle | None = None,
    audience: Audience = Audience.SHAREABLE,
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
        audience: Who is reading (ADR 0129). The default is the shareable assembly — the
            stored HTML, the PDF, the Markdown: everything that gets exported, attached
            and sent. The operator's own screens pass ``INTERNAL``, and the one thing
            that differs is the closing section: its figures rest on the operator's book,
            and a lineage with an attested node reaches no shareable rendering (ADR
            0073), so the shareable assembly carries the disclosure in its place. Same
            walk, same numbering; the containment is a type with no field for the figure.

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

    # The keys of the three sections an audience transforms are spelled in the deterministic
    # registry and nowhere else in code (the section-key scan), and the registry reaches the
    # evaluation service, which assembles documents: a cycle at import time and none at call
    # time, so the names and the two transforms that live beside them are read here.
    from aer.sections.deterministic import (  # noqa: PLC0415
        CHANGE_SUMMARY_KEY,
        CONSEQUENCES_KEY,
        PRIOR_COMPARISON_KEY,
    )
    from aer.sections.what_changed import summary_for_audience  # noqa: PLC0415
    from aer.services.history import comparison_for_audience  # noqa: PLC0415

    # ADR 0129's shape, applied to the three sections that read the operator's own book or
    # judgements: the operator's copy in full, the copy that leaves without them.
    transforms = {
        CONSEQUENCES_KEY: consequences_for_audience,
        PRIOR_COMPARISON_KEY: comparison_for_audience,
        CHANGE_SUMMARY_KEY: summary_for_audience,
    }

    sections = await sections_for_job(session, job.id)
    definitions = await _definitions_for(session, sections)
    active_style = style if style is not None else HouseStyle()

    views: list[SectionView] = []
    citations: list[CitationRef] = []

    # The composed view (ADR 0117), first of all: the judges' complaint was the absence of
    # a position, and a position printed after eighteen sections of evidence is a position
    # the reader meets last. Every figure in it is a row the run struck; nothing is
    # computed here.
    view_fragments: tuple[Fragment, ...] = ()
    view = await view_content(session, job=job)
    if view.content:
        rendered_view = render_section(
            key="the_view",
            title=VIEW_TITLE,
            contract=VIEW_CONTRACT,
            content=view.content,
            footnote_start=1,
            style=active_style,
        )
        citations.extend(rendered_view.citations)
        view_fragments = rendered_view.fragments

    # The front page's numbers (gap R10), next in reading order. Assembled from stored rows
    # alone; an empty run shows nothing here and the coverage notice carries the honest
    # account.
    glance_fragments: tuple[Fragment, ...] = ()
    glance = await glance_content(session, job=job, request=request)
    if glance.content:
        rendered_glance = render_section(
            key="at_a_glance",
            title=GLANCE_TITLE,
            contract=GLANCE_CONTRACT,
            content=glance.content,
            footnote_start=len(citations) + 1,
            style=active_style,
        )
        citations.extend(rendered_glance.citations)
        glance_fragments = rendered_glance.fragments

    # A chart renders beside the section whose definition claims it (gap N1) — reading
    # order, so the interleaved footnote numbering stays "marker 3 is the third marker".
    unclaimed: dict[str, Chart] = {chart.key: chart for chart in charts}

    for section in sections:
        definition = definitions.get(section.section_definition_id)
        rendered = render_section(
            key=section.section_key,
            title=definition.title if definition else section.section_key,
            contract=(definition.output_contract if definition else {}),
            content=(
                transforms[section.section_key](section.content, audience)
                if section.section_key in transforms and section.content
                else section.content
            ),
            # Numbering continues across the document, so a reader chasing marker 3
            # finds the third marker in the report rather than the third in some section.
            footnote_start=len(citations) + 1,
            status_note=_status_note(section),
            # A failed section's recorded reason is the validator's diagnostics — raw
            # ids and schema paths, written for the operator's console. The reader gets
            # the status line and the coverage notice; the diagnostics stay in the run.
            # A generated section's reason is filtered too (gaps R1/R3): an editing
            # disclosure never opens a section — it belongs in the appendix and the
            # coverage notice — while an evidence shortfall still banners in place.
            warning=(
                None
                if section.status is SectionStatus.FAILED
                else reader_warning(section.low_confidence_reason)
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
                one_pager=bool(
                    (definition.evidence_policy or {}).get("one_pager") if definition else False
                ),
            )
        )

    # The comparables block sits after the analysis and before the exhibit pack, which is
    # where its markers are taken: reading order decides the numbering, here as everywhere.
    comps_fragments = _comps_fragments(comps, citations, style=active_style)

    # Whatever no section claimed keeps the pack at the back — a chart is never dropped
    # for want of a claim, only relocated by one.
    chart_views = [_chart_view(chart, citations) for chart in unclaimed.values()]

    footnotes = await _footnotes(session, citations, job_id=job.id, style=active_style)
    appendix = await _appendix(session, citations)
    coverage = await _coverage(
        session,
        job=job,
        sections=sections,
        definitions=definitions,
        glance_withheld=glance.refused,
    )

    # The C3 marker, derived from stored rows: any section citing a source whose
    # publication date is unknown carries the symbol, and the legend appears once.
    undated_ids = {
        identifier
        for identifier, row in (await _load_source_documents(session, citations)).items()
        if not row.is_dated
    }
    if undated_ids:
        views = [_marked_if_undated(view, undated_ids) for view in views]

    return ReportDocument(
        header=HeaderView(
            company_name=company.name if company is not None else request.company_name,
            ticker=request.ticker,
            exchange=request.exchange,
            as_of=request.work_order.as_of_date,
            base_currency=request.base_currency,
            generated_at=generated_at or datetime.now(UTC),
            rating=rating,
            confidence=confidence,
            method_values=_method_values(view.content, style=active_style),
        ),
        sector=sector,
        sections=tuple(views),
        comps=comps_fragments,
        footnotes=footnotes,
        appendix=appendix,
        style=active_style,
        # The appendix keeps the whole account per section — the evidence part and the
        # editing part together, in the current register (gap R3: this listing is the
        # disclosure's home; the inline banner above carries only the evidence part).
        # A failed section's entry is its cause sentence, never its raw diagnostics (gap
        # R9): the validator's strings quote the very tokens they flagged — the CHRW
        # run's ten "unformatted integer" findings were integers this appendix had put
        # into the document — so printing them re-fails the presentation scan forever,
        # and shows a reader schema paths besides.
        limitations=tuple(
            (view.title, note)
            for section, view in zip(sections, views, strict=True)
            if (
                note := (
                    _status_note(section)
                    if section.status is SectionStatus.FAILED
                    else _limitation_note(section.low_confidence_reason)
                )
            )
        ),
        citations=citations,
        charts=tuple(chart_views),
        job_id=job.id,
        coverage=coverage,
        undated_note=UNDATED_NOTE if any(view.undated for view in views) else None,
        glance=glance_fragments,
        view=view_fragments,
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


def _method_values(content: dict[str, Any] | None, *, style: HouseStyle) -> str | None:
    """What each terminal method gives, as the masthead's one line, or ``None``.

    Read off the block's own rows rather than recomputed, so the line and the table under
    it cannot disagree — the failure `glance` was built to avoid, one page earlier. Each
    figure is named by its method, and the two are joined by "and", never "to": they are
    two answers, and a range is a claim neither method makes (ADR 0132).
    """
    rows = [row for row in (content or {}).get("base") or [] if row.get("value")]
    if not rows:
        return None
    shown = []
    for row in rows:
        currency = str(row.get("unit", "")).split("/")[0]
        money = display.money(Decimal(row["value"]), currency, style=style)
        shown.append(f"{money} ({str(row['label']).lower()})")
    return f"{' and '.join(shown)} a share"


def _comps_fragments(
    comps: CompsTable | WithheldComps | None,
    citations: list[CitationRef],
    *,
    style: HouseStyle,
) -> tuple[Fragment, ...]:
    """The comparables block: the disclosure always, the figures where they can be cited.

    Two things decide what a reader sees, and neither is decided here. Whether a multiple
    may be published at all was decided by the operator on 2026-08-09 and is enforced one
    call upstream, by :meth:`~aer.calc.comps.CompsTable.for_audience`: a `WithheldComps`
    arriving means the answer was no, and this function could not print a figure from one
    if it wanted to, because there is none in it.

    Whether a figure that *may* be published *is* published is decided by whether it can
    be footnoted. Every multiple here is a traced calculation, and the run's record carries
    the id — so the marker resolves to the formula, the inputs and the code version that
    struck it. A record written before Phase 4.3 stored that id sources its multiples to
    the comps step instead, and citing the step would resolve to nothing and print the
    report's own broken-citation notice against a figure that is perfectly sound. Such a
    record gets the disclosure, no table, and a sentence saying the figures exist and
    cannot be cited — rather than silence, which reads as an analysis nobody did. No
    figure in this document appears without a marker that resolves.

    The block never promises a figure it has not printed. The disclosure describes the
    analysis, the table's own column names the company, and what could not be computed is
    said afterwards in labels. The first draft ended the paragraph "the multiples below
    are the subject's own" and the offline full run printed it over nothing.
    """
    if comps is None:
        return ()

    # Formatted here, as every other fragment is: the walk applies the house style and the
    # notations transcribe. A paragraph reaching a serialiser unformatted would print the
    # as-of date in ISO in the one block the platform writes for itself.
    fragments: list[Fragment] = [
        Heading(level=_COMPS_HEADING_LEVEL, text=COMPS_TITLE),
        Paragraph(text=display.prose(comps.as_paragraph(), style=style)),
    ]
    if not isinstance(comps, CompsTable):
        # The withheld arm keeps the set. A licence about the vendor's prices is not a
        # reason to stop naming the companies a person chose (ADR 0034, amended
        # 2026-09-18) — and this early return is where the first draft of that amendment
        # quietly dropped them again.
        return (*fragments, *_confirmed_peers(comps, style=style))

    cited = _cited_multiples(comps)
    if cited:
        # The company names the column, so the table says whose figures these are without
        # the paragraph promising them. A peer with multiples gets a column of its own
        # here once a peer can be priced; today none can, and the set below says who they
        # are regardless.
        fragments.append(
            Table(
                columns=("Multiple", comps.subject.name),
                rows=tuple(
                    _multiple_row(label, value, identifier, citations)
                    for label, value, identifier in cited
                ),
            )
        )
    elif comps.subject_has_a_figure:
        fragments.append(Paragraph(text=UNCITABLE_MULTIPLES))

    absent = comps.absent_note()
    if absent:
        fragments.append(Paragraph(text=display.prose(absent, style=style)))
    fragments.extend(_confirmed_peers(comps, style=style))
    return tuple(fragments)


def _confirmed_peers(
    comps: CompsTable | WithheldComps, *, style: HouseStyle
) -> tuple[Fragment, ...]:
    """Who the operator agreed this company is comparable to, and why they said so.

    The report used to name none of them. A run holds a registry-confirmed set with a
    written reason for each, approved at a gate, and §17 was one sentence saying they had
    all been excluded — which a judge read as "no relative anchor of any kind". The set is
    the operator's own work, so it crosses to a shareable surface whatever the licence says
    about the vendor's figures (ADR 0034, amended 2026-09-18).

    **No markers.** A rationale is a view somebody held, not a filed fact: there are no
    bytes to hash and nothing to re-read, so ADR 0074 forbids it the shape of a source
    reference. A footnote here would tell a reader the sentence had been verified, and the
    honest account is that a person agreed with it.

    The filer's own spelling of each name, shouting registry case and all, for the reason
    the subject's is kept: a title-casing rule invented here would render "SAP SE" as "Sap
    Se" and put this platform's guess where the register has a fact.
    """
    peers = comps.confirmed_peers()
    if not peers:
        return ()
    named = [peer for peer in peers if peer.name]
    if not named:
        return ()
    return (
        Paragraph(text=display.prose(_PEER_SET_LEAD, style=style)),
        Bullets(
            items=tuple(
                Bullet(pairs=((peer.name, display.prose(peer.rationale, style=style)),))
                if peer.rationale
                else Bullet(text=peer.name)
                for peer in named
            )
        ),
    )


def _cited_multiples(table: CompsTable) -> tuple[tuple[str, Decimal, str], ...]:
    """The subject's multiples that have both a value and a calculation row to point at.

    **A calculation reference is not the same as a resolvable one.** The record's fallback
    source is a `SourceRef.calculation` naming the comps *step* — the same kind, and an
    identifier no `calculations` row carries. :func:`_uuids` already draws that line for the
    footnote resolver, so this draws it in the same place: an id the resolver would drop is
    one this must not print a figure against.
    """
    found: list[tuple[str, Decimal, str]] = []
    for row in table.subject.multiples:
        quantity = row.quantity
        source = quantity.source if quantity is not None else None
        if quantity is None or source is None or source.kind is not SourceKind.CALCULATION:
            continue
        try:
            uuid.UUID(source.identifier)
        except (ValueError, AttributeError, TypeError):
            continue
        found.append((row.label, quantity.value, source.identifier))
    return tuple(found)


def _multiple_row(
    label: str, value: Decimal, identifier: str, citations: list[CitationRef]
) -> TableRow:
    """One multiple as a table row, its marker taken where the row lands."""
    citations.append(CitationRef(kind="calculation", identifier=identifier, label=label))
    return TableRow(cells=(label, display.multiple(value)), markers=(len(citations),))


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
        table=chart.table,
    )


def _limitation_note(reason: str | None) -> str | None:
    """A stored degradation note as the appendix shows it: current register, edits last.

    Rebuilt from the two halves the reader vocabulary distinguishes, so a row written
    before the register change comes out in today's words rather than naming an ADR at
    a reader.
    """
    parts = [part for part in (reader_warning(reason), *editorial_notes_in(reason)) if part]
    return " ".join(parts) or None


async def _coverage(
    session: AsyncSession,
    *,
    job: Job,
    sections: list[ReportSection],
    definitions: dict[uuid.UUID, SectionDefinition],
    glance_withheld: str | None = None,
) -> CoverageNote | None:
    """The coverage notice's inputs, from recorded state only. ``None`` when full.

    Derived rather than written: the section statuses and the evaluation rows are what
    actually happened, so the notice cannot say less than the run recorded — the failure
    mode gap A40 exists to prevent is a note-perfect document shape wrapped around
    content that is not there, with nothing at the front saying so.

    The two editing disclosures count here too (gap R1): a document whose sections were
    shortened or pruned says so once, at the front, in one line each — instead of six
    identical banners inside the analysis.
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
    notes = [editorial_notes_in(section.low_confidence_reason) for section in sections]
    shortened = sum(1 for edits in notes if LENGTH_EDIT_NOTE in edits)
    pruned = sum(1 for edits in notes if NUMERAL_EDIT_NOTE in edits)
    checks = await session.scalars(
        select(Evaluation.metric)
        .where(Evaluation.job_id == job.id, Evaluation.passed.is_(False))
        .order_by(Evaluation.metric)
    )
    failed_checks = tuple(checks)
    nothing_to_say = (
        not failed and not failed_checks and glance_withheld is None and not (shortened or pruned)
    )
    if nothing_to_say:
        return None
    return CoverageNote(
        sections_failed=failed,
        sections_total=len(sections),
        checks_failed=failed_checks,
        glance_withheld=glance_withheld,
        sections_shortened=shortened,
        sections_pruned=pruned,
    )


async def _footnotes(
    session: AsyncSession,
    citations: list[CitationRef],
    *,
    job_id: uuid.UUID,
    style: HouseStyle | None = None,
) -> tuple[Footnote, ...]:
    """One footnote per marker, in marker order, resolved to something checkable.

    A source footnote carries the URL, publisher, publication date, retrieval date and
    tier — enough to find the bytes and confirm they are the bytes — and, since ADR 0119,
    the passage itself where the three gates permit one. A calculation footnote carries
    the formula and the code version. A footnote that only said "SEC EDGAR" would look
    like a citation and support nothing.

    **The passage prints once per document.** A reader arriving at a later marker for the
    same document is sent to the note that carries it rather than shown it again; see
    :class:`SourceFootnote`, and the measurement in its comment that decided this.

    **A derived figure takes a note of its own** (ADR 0114): it is a fact, so it cites a
    document, but the document does not state it — so the note says what it is instead of
    describing a filing the reader would look in vain through. See
    :class:`DerivedFootnote`.
    """
    active = style if style is not None else HouseStyle()
    documents = await _load_source_documents(session, citations)
    calculations = await _load_calculations(session, citations)
    derived = await _load_derived_facts(session, citations)
    # A component's own filing need not be cited anywhere else in the report, so the
    # documents a derived note names are loaded on top of the cited ones rather than
    # assumed to be among them.
    documents |= await _component_documents(session, derived, known=documents)
    excerpts = await printable_excerpts(
        session, job_id=job_id, source_document_ids=[row.id for row in documents.values()]
    )
    quoted_at: dict[uuid.UUID, int] = {}

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
                    value=_display_value(
                        calculation.output_value,
                        unit=calculation.output_unit,
                        # The calculation's own name reads the dimensionless figures: a
                        # `net_margin` of 0.174 is 17.4% and a `current_ratio` of 1.8 is
                        # 1.8x, and the label is what tells the formatter which.
                        label=calculation.name,
                        style=style or HouseStyle(),
                    ),
                    function_ref=calculation.function_ref,
                    code_version_prefix=calculation.code_version[:_CODE_PREFIX],
                    period_label=calculation.period_label,
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

        fact = derived.get(reference.fact_id)
        if fact is not None:
            # Before the document note, not beside it: a derived figure is not in the
            # document, so describing the document is the wrong answer rather than an
            # incomplete one. The components' own documents are named in the note.
            footnotes.append(
                _derived_footnote(fact, number=number, documents=documents, style=active)
            )
            continue

        already = quoted_at.get(document.id)
        printed = excerpts.get(document.id) if already is None else None
        if printed is not None:
            quoted_at[document.id] = number
        footnotes.append(
            SourceFootnote(
                number=number,
                title=document.title or document.url,
                url=document.url,
                publisher=document.publisher,
                publication_date=document.publication_date,
                retrieved=document.retrieved_at.date(),
                tier=document.source_tier.spoken,
                excerpt=printed,
                digest_prefix=(
                    document.artefact.sha256[:_HASH_PREFIX]
                    if printed is not None and document.artefact
                    else None
                ),
                quoted_at=already,
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
            tier=document.source_tier.spoken,
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
    return await _documents_by_id(session, _uuids(citations, kind="source_document"))


async def _documents_by_id(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[str, SourceDocument]:
    if not ids:
        return {}
    rows = await session.scalars(select(SourceDocument).where(SourceDocument.id.in_(ids)))
    loaded = list(rows)
    for row in loaded:
        # Touch the relationship inside the async context; the appendix reads the digest
        # and a lazy load at render time would raise outside a greenlet.
        await session.refresh(row, ["artefact"])
    return {str(row.id): row for row in loaded}


async def _load_derived_facts(
    session: AsyncSession, citations: list[CitationRef]
) -> dict[str, FinancialFact]:
    """The cited facts this platform computed rather than read, by id.

    Only the derived ones: an as-reported fact needs no note of its own, because the
    document its citation already names is the document that states it. Loading the rest
    would buy a query and change nothing.
    """
    ids: list[uuid.UUID] = []
    for reference in citations:
        if not reference.fact_id:
            continue
        try:
            ids.append(uuid.UUID(reference.fact_id))
        except (ValueError, AttributeError, TypeError):
            continue
    if not ids:
        return {}
    rows = await session.scalars(
        select(FinancialFact).where(
            FinancialFact.id.in_(ids), FinancialFact.basis == FactBasis.DERIVED
        )
    )
    return {str(row.id): row for row in rows}


async def _component_documents(
    session: AsyncSession, derived: dict[str, FinancialFact], *, known: dict[str, SourceDocument]
) -> dict[str, SourceDocument]:
    """The filings a derived fact's components came from, minus the ones already loaded."""
    wanted: list[uuid.UUID] = []
    for fact in derived.values():
        for part in (fact.derivation or {}).get("inputs") or []:
            identifier = str(part.get("source_document_id", "")) if isinstance(part, dict) else ""
            if not identifier or identifier in known:
                continue
            try:
                wanted.append(uuid.UUID(identifier))
            except (ValueError, AttributeError, TypeError):
                continue
    return await _documents_by_id(session, wanted)


def _derived_footnote(
    fact: FinancialFact,
    *,
    number: int,
    documents: dict[str, SourceDocument],
    style: HouseStyle,
) -> DerivedFootnote:
    """One derived fact as its note, read from the workings the row carries.

    The inputs come from ``derivation`` rather than from a second query of the fact table:
    the row recorded what it was computed from, at the moment it was computed, and reading
    the components afresh would answer a subtly different question — what those concepts
    say *now* — which is how a re-render comes to disagree with the report it re-renders.
    """
    workings = fact.derivation or {}
    inputs: list[DerivedInput] = []
    for part in workings.get("inputs") or []:
        if not isinstance(part, dict):  # pragma: no cover -- the column's own shape
            continue
        source = documents.get(str(part.get("source_document_id", "")))
        inputs.append(
            DerivedInput(
                label=str(part.get("concept", "")).replace("_", " "),
                value=display.scalar(
                    part.get("value"),
                    style=style,
                    unit=str(part.get("unit") or ""),
                    label=str(part.get("concept", "")),
                ),
                source_title=(source.title or source.url) if source is not None else "",
                source_url=source.url if source is not None else "",
            )
        )
    return DerivedFootnote(
        number=number,
        label=fact.concept.replace("_", " "),
        value=display.scalar(fact.value, style=style, unit=fact.unit, label=fact.concept),
        period_label=_period_label(fact),
        inputs=tuple(inputs),
        code_version_prefix=str(workings.get("code_version", ""))[:_CODE_PREFIX],
    )


def _period_label(fact: FinancialFact) -> str | None:
    """ "FY2025", or the period end where the filer's own labels are absent."""
    if fact.fiscal_year is None:
        return fact.period_end.isoformat()
    if fact.fiscal_period and fact.fiscal_period.upper() not in {"FY", "Y"}:
        return f"{fact.fiscal_period.upper()} {fact.fiscal_year}"
    return f"FY{fact.fiscal_year}"


async def _load_calculations(
    session: AsyncSession, citations: list[CitationRef]
) -> dict[str, Calculation]:
    ids = _uuids(citations, kind="calculation")
    if not ids:
        return {}
    rows = await session.scalars(select(Calculation).where(Calculation.id.in_(ids)))
    return {str(row.id): row for row in rows}


def _display_value(value: Decimal, *, unit: str, label: str, style: HouseStyle) -> str:
    """A calculation's value as a reader meets it, with its unit, marked when cut.

    The live report printed ``0.437565271053`` in a footnote — twelve decimal places of
    asset turnover, which is storage precision leaking into prose. The stored value is
    untouched and the drill-down page shows it in full; this is presentation, and it
    says so when it rounds rather than pretending the shorter number is the recorded one.

    **The "did I lose anything?" test was scale-blind, and let the worst cases through.**
    ``Decimal("0.1800") == Decimal("0.180000000000")`` is ``True`` — equality compares
    value, not scale — so every calculation whose stored digits happened to end in zeros
    took the untouched branch and printed all twelve places anyway. `fx_report`'s golden
    carried ``0.180000000000`` under that rule for as long as the rule has existed.

    **And it only ever governed precision, which is half of what a figure needs.** It
    trimmed decimal places and had nothing to say about magnitude or currency, so
    ``66987000000`` went through untouched — eleven digits, no separators, no symbol —
    under a table cell reading ``$66,987m``. The rounding decision below is still made on
    the stored value, because that is what "did I lose anything?" is a question about; the
    figure is then said by :func:`~aer.render.display.figure`, which is the same door every
    other published number goes through (ADR 0056). Roadmap §3.19 item 40.

    **The marker is not a claim that every printed figure equals the stored one**, and was
    never in a position to be: ``$67.0bn`` has dropped eight significant figures and carries
    no marker, because the house style's scaling is visible in the answer and nobody reads a
    rounded billion as exact. What the marker answers is the narrower question a decimal
    raises — *does this look more precise than it is?* — which is why it is decided on the
    stored value's own scale and not on what the style did with it afterwards.
    """
    exponent = value.as_tuple().exponent
    needs_rounding = isinstance(exponent, int) and exponent < _DISPLAY_EXPONENT
    quantised = value.quantize(_DISPLAY_QUANTUM) if needs_rounding else value
    shown = display.figure(quantised, unit=unit, label=label, style=style)
    if quantised == value:
        return shown
    return f"{shown} (rounded; full precision stored)"


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
