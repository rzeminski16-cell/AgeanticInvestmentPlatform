"""The report reader's notes: what one marker rests on, for the drawer (page specification §8.2).

A marker on the reader page opens its note beside the text. The note says what kind of thing
the figure is — a recorded calculation, a stored fact, a figure derived from facts, or a
citation that resolves to nothing — and then walks it one step: a calculation's formula and
each input with its own origin, a document's excerpt, dates, tier and digest. The full walk
stays one link away, on the pages that already render it; this is the step a reader takes
before deciding whether they need that one.

**The numbering is the document's.** A note's number means something only against the
document that printed the marker, so the drawer resolves it against the same assembly the
reader page rendered. Assembling a long report takes a second or more, and a drawer that
waited that long on every marker would be the slow way to check a figure — so the page
remembers the references it rendered, for approved reports only, whose sections cannot
change. The note itself is read fresh from the record each time; only the number's meaning
is remembered, and it is remembered per report, behind the page's own ownership check.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import HouseStyle
from aer.core.disagreement import spoken_tier
from aer.db.models import Calculation, FinancialFact, Job
from aer.render import display
from aer.render.document import (
    CalculationFootnote,
    DerivedFootnote,
    Footnote,
    ReportDocument,
    SourceFootnote,
)
from aer.sections.render import CitationRef
from aer.services import calculations as calculation_service
from aer.services import provenance
from aer.services.valuation_view import lineage_rows
from aer.web import figures
from aer.web.figures import RenderedFigure

__all__ = ["Note", "NoteInput", "NoteSource", "note_for", "recall", "remember"]

REMEMBERED_REPORTS: Final = 8
"""How many reports' note references the process keeps. Enough for a morning's reading."""

_REMEMBERED: OrderedDict[uuid.UUID, tuple[tuple[CitationRef, ...], tuple[Footnote, ...]]] = (
    OrderedDict()
)


def remember(report_id: uuid.UUID, document: ReportDocument) -> None:
    """Keep what each of an approved report's note numbers refers to."""
    _REMEMBERED[report_id] = (tuple(document.citations), tuple(document.footnotes))
    _REMEMBERED.move_to_end(report_id)
    while len(_REMEMBERED) > REMEMBERED_REPORTS:
        _REMEMBERED.popitem(last=False)


def recall(report_id: uuid.UUID) -> tuple[tuple[CitationRef, ...], tuple[Footnote, ...]] | None:
    """The references :func:`remember` kept for this report, or ``None``."""
    return _REMEMBERED.get(report_id)


@dataclass(frozen=True, slots=True)
class NoteInput:
    """One input to a calculation, with where it came from."""

    label: str
    figure: RenderedFigure
    origin: str
    """Where the input came from, in words: a calculation, a filing, an assumption."""


@dataclass(frozen=True, slots=True)
class NoteSource:
    """The document a figure was read from, as the drawer states it."""

    title: str
    url: str
    record_href: str
    publisher: str
    published: str
    retrieved: str
    tier: str
    digest: str
    excerpt: str | None
    excerpt_state: str
    """``verified``, ``overridden``, ``unverified`` — or empty when no claim was checked."""


@dataclass(frozen=True, slots=True)
class Note:
    """One marker's note, ready for the drawer.

    ``verdict`` is the drawer's header word and ``tone`` its colour; the word carries the
    meaning on its own, because colour never does alone.
    """

    number: int
    kind: str
    verdict: str
    tone: str
    what_it_is: str
    figure: str | None
    caption: str
    formula: str | None
    inputs: tuple[NoteInput, ...]
    source: NoteSource | None
    statement: str | None
    code_version: str | None
    record_href: str


# What each kind of figure is, said once. The distinction is invariant 3's: a figure is a
# stored fact, a recorded calculation or an attestation, and a reader checking one should be
# told which before being shown anything else.
_WHAT_IT_IS: Final[dict[str, str]] = {
    "calculation": (
        "A recorded calculation — worked out by code from the inputs below, not read from a "
        "document and not a number a model wrote."
    ),
    "fact": (
        "A stored fact — the figure as the filing below states it. The filing was hashed "
        "and kept when it was fetched."
    ),
    "citation": (
        "A citation — the document below is what this passage rests on. It was hashed and "
        "kept when it was fetched."
    ),
    "derived": (
        "Derived, not reported — computed from figures the filings state, because no filing "
        "states this one."
    ),
    "unresolved": (
        "Nothing — the record this note pointed at is no longer there, so the figure it "
        "supports should not be relied on."
    ),
}

_ORIGIN: Final[dict[str, str]] = {
    "calculation": "a calculation",
    "calculation_ref": "a calculation",
    "fact": "a filing",
    "assumption": "an assumption",
    "attestation": "your book",
    "macro": "a published series",
}


async def note_for(
    session: AsyncSession,
    *,
    job: Job,
    number: int,
    reference: CitationRef,
    footnote: Footnote,
    style: HouseStyle,
) -> Note:
    """One note, read from the record: the figure, what it is, and one step of its walk."""
    record_href = f"/runs/{job.id}/footnotes/{number}"
    identifier = _uuid_or_none(reference.identifier)

    if reference.kind == "calculation" and identifier is not None:
        calculation = await session.get(Calculation, identifier)
        if calculation is not None:
            return await _calculation_note(
                session,
                job=job,
                number=number,
                calculation=calculation,
                footnote=footnote,
                style=style,
            )
        return _unresolved(number, record_href=record_href)

    source = await provenance.source_detail(session, identifier) if identifier else None
    if source is None:
        return _unresolved(number, record_href=record_href)

    claims = await provenance.claims_citing(session, job_id=job.id, source_document_id=source.id)
    checked = [c for claim in claims for c in claim.citations if c.source.id == source.id]
    printed = footnote.excerpt if isinstance(footnote, SourceFootnote) else None
    shown = next((c for c in checked if c.state == "verified"), checked[0] if checked else None)
    excerpt = printed or (shown.excerpt.excerpt if shown is not None else None)
    state = "verified" if printed else (shown.state if shown is not None else "")
    note_source = NoteSource(
        title=source.title or source.url,
        url=source.url,
        record_href=f"/runs/{job.id}/sources#source-{source.id}",
        publisher=source.publisher or "",
        published=(
            display.date_text(source.publication_date, style=style)
            if source.publication_date
            else "no date it could determine"
        ),
        retrieved=display.date_text(source.retrieved_at.date(), style=style),
        tier=spoken_tier(source.source_tier.value),
        digest=source.sha256[:12] if source.sha256 else "",
        excerpt=excerpt,
        excerpt_state=state,
    )

    if isinstance(footnote, DerivedFootnote):
        return Note(
            number=number,
            kind="derived",
            verdict="Derived",
            tone="info",
            what_it_is=_WHAT_IT_IS["derived"],
            figure=footnote.value,
            caption=_caption(footnote.label, footnote.period_label),
            formula=None,
            inputs=(),
            source=note_source,
            statement=footnote.statement,
            code_version=footnote.code_version_prefix or None,
            record_href=record_href,
        )

    # The figure itself, where the marker names the stored fact it is (ADR 0114's
    # `fact_id`): its value as the filing stated it, and the filing that stated it.
    fact_id = _uuid_or_none(reference.fact_id)
    fact = await session.get(FinancialFact, fact_id) if fact_id is not None else None
    # The verifier's word leads (§8.2): a passage it failed to find is said first, whatever
    # else is true; a stored fact was read by code from the filing, which is its own check.
    verdict, tone = (
        ("Not confirmed", "failure")
        if any(c.state == "unverified" for c in checked)
        else ("Verified", "success")
        if state == "verified"
        else ("Stored fact", "info")
        if fact is not None
        else ("Cited", "info")
    )
    caption = figures.concept_name(reference.label) if reference.label else ""
    shown_figure: str | None = None
    if fact is not None:
        shown_figure = display.scalar(fact.value, style=style, unit=fact.unit, label=fact.concept)
        caption = _caption(figures.concept_name(fact.concept), _fiscal(fact))
        filed = display.date_text(fact.filed_date, style=style)
        stated = f"{fact.form} filed {filed}" if fact.form else f"a filing of {filed}"
        note_source = replace(note_source, title=f"{note_source.title} — the {stated}")
    return Note(
        number=number,
        kind="fact" if fact is not None else "citation",
        verdict=verdict,
        tone=tone,
        what_it_is=_WHAT_IT_IS["fact" if fact is not None else "citation"],
        figure=shown_figure,
        caption=caption,
        formula=None,
        inputs=(),
        source=note_source,
        statement=None,
        code_version=None,
        record_href=record_href,
    )


def _fiscal(fact: FinancialFact) -> str:
    """The period a fact is about, the way a filing names it: *FY2026*, *Q3 2026*."""
    if fact.fiscal_year is None:
        return ""
    if fact.fiscal_period in {None, "FY"}:
        return f"FY{fact.fiscal_year}"
    return f"{fact.fiscal_period} {fact.fiscal_year}"


async def _calculation_note(
    session: AsyncSession,
    *,
    job: Job,
    number: int,
    calculation: Calculation,
    footnote: Footnote,
    style: HouseStyle,
) -> Note:
    # One level down and no further: the drawer shows what this figure is made of, and the
    # calculation's own page renders the rest of the tree.
    tree = await calculation_service.lineage(session, calculation.id, max_depth=1)
    inputs = tuple(
        NoteInput(
            label=figures.concept_name(str(row["label"] or row["kind"])),
            figure=figures.lineage_figure(
                row, request_id=job.work_order_id, job_id=job.id, style=style
            ),
            origin=_ORIGIN.get(str(row["kind"]), "the record"),
        )
        for row in lineage_rows(tree)
        if row["depth"] == 1
    )
    period = footnote.period_label if isinstance(footnote, CalculationFootnote) else None
    return Note(
        number=number,
        kind="calculation",
        verdict="Calculated",
        tone="info",
        what_it_is=_WHAT_IT_IS["calculation"],
        figure=display.scalar(
            calculation.output_value,
            style=style,
            unit=calculation.output_unit,
            label=calculation.name,
        ),
        caption=_caption(figures.concept_name(calculation.name), period),
        formula=calculation.formula,
        inputs=inputs,
        source=None,
        statement=None,
        code_version=calculation.code_version[:12] if calculation.code_version else None,
        record_href=f"/calculations/{calculation.id}",
    )


def _unresolved(number: int, *, record_href: str) -> Note:
    return Note(
        number=number,
        kind="unresolved",
        verdict="Unresolved",
        tone="refusal",
        what_it_is=_WHAT_IT_IS["unresolved"],
        figure=None,
        caption="",
        formula=None,
        inputs=(),
        source=None,
        statement=None,
        code_version=None,
        record_href=record_href,
    )


def _caption(label: str, period: str | None) -> str:
    return f"{label}, {period}" if period else label


def _uuid_or_none(identifier: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(identifier)
    except (ValueError, AttributeError, TypeError):
        return None
