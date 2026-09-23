"""The closing section: what this position would do to the book that already exists (F3).

ADR 0129. The request form carries a planned weight — the whole position after the trade,
as a fraction of the operator's book — and this module says what that fraction does to the
book's concentration, with the operator's stated horizon set against the model's own payback.
Consequences, never instructions: nothing here recommends, sizes in money, or reaches for a
target, and the model's commentary is refused if it does.

**Struck once, in the draft step, and composed from the ledger after that.** The first call
values the operator's book as at the run's date in a ledger of its own, strikes the after
figures beside it, and persists the lot under the run — so every figure the section prints is
a calculation row a footnote resolves to, and the whole book walk it rests on is in the
lineage. Every later call reads those rows back: a preview, a replayed draft and the render
step all compose the same block from the same record, and nothing is struck twice.

**The book is the operator's, and the grade travels with the figures** (ADR 0073). A holding
typed into the book rather than documented makes every figure above it *attested*, and an
attested lineage reaches no shareable rendering. The block carries the grade it was struck
under; :func:`consequences_for_audience` is where the operator's own copy keeps the figures
with the grade stated beside them and the copy that leaves the machine gets the disclosure
and nothing else — including no commentary, because prose that quotes a withheld figure is
the figure in another notation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc import consequences as calc
from aer.calc.attestation import Attested
from aer.calc.comps import Audience
from aer.calc.performance import exposure, grouped_value
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef
from aer.core.enums import Grade
from aer.db.models import Calculation, ResearchRequest, Security
from aer.services.calculations import lineage, new_context, persist_context
from aer.services.performance import (
    CONCENTRATION_COUNT,
    ExposureView,
    exposure_as_at,
    sector_of,
)
from aer.services.portfolio import (
    CLOSED,
    Figure,
    HoldingRow,
    PortfolioView,
    book_as_at,
    default_book,
    graded_figure,
)

__all__ = [
    "CONSEQUENCES_TITLE",
    "consequences_block",
    "consequences_for_audience",
    "consequences_note",
    "consequences_only",
    "consequences_problems",
]

_log = structlog.get_logger("aer.sections.consequences")

CONSEQUENCES_TITLE: Final = "What this would do to your book"

# The calculation names the section is composed from — the ledger's own words, which is
# what lets a later call find the rows the first call struck.
_CASH_AFTER: Final = "cash_weight_after"
_TOP_AFTER: Final = "top_holdings_share_after"
_SECTOR_AFTER: Final = "exposure_after"
_TOP_BEFORE: Final = "top_holdings_share"
_RECOVERY: Final = "forecast_recovery"
_PAYBACK: Final = "payback_year"
_STRUCK_HERE: Final = (_CASH_AFTER, _TOP_AFTER, _SECTOR_AFTER)

# `calculations.output_value` is NUMERIC(38, 12): what a footnote resolves to carries twelve
# places, so a figure read off a row's inputs is shown at the same.
_LEDGER_PLACES: Final = Decimal("1e-12")

# How many typed entries the evidence note names before it starts counting, as ADR 0073's
# own disclosure does: enough to recognise which entries are the problem, short enough that
# the sentence stays one.
_NAMED_IN_NOTE: Final = 3
_READ_BACK: Final = (*_STRUCK_HERE, _TOP_BEFORE, _RECOVERY, _PAYBACK)

# The request form's purposes, as the basis sentence says them. Words the section owns
# rather than the web vocabulary's, because a section module does not reach into a page.
_PURPOSE_WORDS: Final[dict[str, str]] = {
    "new_position": "a new position",
    "add": "adding to a position you hold",
    "review": "reviewing a position you hold",
    "watching_only": "watching only, with no trade in view",
}

# What a commentary may not do here. Advisory phrasing only: "the trade would increase
# concentration" is a consequence and stays; "you should increase the position" is an
# instruction and goes. Matched on word boundaries, case-insensitively.
_INSTRUCTIONS: Final[tuple[str, ...]] = (
    r"you should",
    r"recommend(?:ed|s|ation|ations)?",
    r"advis(?:e|ed|es|able)",
    r"ought to",
    r"it would be (?:wise|prudent|sensible) to",
    r"we suggest",
    r"suggest (?:you|that you)",
    r"consider (?:buying|selling|adding|trimming|topping up|reducing|increasing)",
    r"target price",
    r"price target",
    r"rating",
)


@dataclass(frozen=True, slots=True)
class _Struck:
    """What the first call produced: the grade of the figures, or why there are none."""

    grade: Grade = Grade.DOCUMENTED
    attested_inputs: tuple[str, ...] = ()
    problem: str = ""


# -- The block --------------------------------------------------------------------------------


async def consequences_block(
    session: AsyncSession, *, job_id: uuid.UUID, request: ResearchRequest
) -> dict[str, Any]:
    """The platform-filled fields of the closing section, from the ledger.

    Empty where the request states no planned weight, which is the section's applicability
    predicate saying no; a builder asked anyway has nothing to say and says nothing.
    """
    planned = _planned_weight(request)
    if planned is None:
        return {}

    stored = await _stored(session, job_id)
    if any(name in stored for name in _STRUCK_HERE):
        grade, typed = await _stored_grade(
            session, [stored[name].id for name in _STRUCK_HERE if name in stored]
        )
    else:
        struck = await _strike(session, job_id=job_id, request=request, planned=planned)
        if struck.problem:
            return {"basis": struck.problem, "grade": Grade.DOCUMENTED.value}
        stored = await _stored(session, job_id)
        grade, typed = struck.grade, struck.attested_inputs

    block: dict[str, Any] = {
        "basis": _basis(request),
        "consequences": _consequence_rows(stored, request=request, planned=planned),
        "horizon": _horizon_rows(stored, request=request),
        "evidence_note": _evidence_note(grade, typed),
        # Read by `consequences_for_audience` and rendered by nothing: the contract does not
        # declare them, and the walk renders only what the contract declares.
        "grade": grade.value,
        "attested_inputs": list(typed),
    }
    _log.info(
        "consequences.composed",
        job_id=str(job_id),
        rows=len(block["consequences"]),
        grade=grade.value,
    )
    return block


async def _strike(
    session: AsyncSession, *, job_id: uuid.UUID, request: ResearchRequest, planned: Decimal
) -> _Struck:
    """Value the book as at the run's date and strike the after figures beside it."""
    as_of = request.work_order.as_of_date
    when = f"{as_of:%d %B %Y}"
    book_row = await default_book(session, user_id=request.work_order.user_id)
    if book_row is None:
        return _Struck(
            problem=(
                f"You planned a weight for {request.ticker} and no book is on record to set "
                "it against. Nothing here can be computed until a portfolio exists; the "
                "rest of this report stands on its own."
            )
        )

    context = new_context()
    book = await book_as_at(session, context, portfolio=book_row, as_of=as_of)
    if book.net_assets is None or book.net_assets.value <= 0:
        why = book.problem or "net assets are not positive"
        return _Struck(
            problem=(
                f"The book as at {when} could not be valued, so there is no denominator to "
                f"set the planned weight against: {why}"
            )
        )
    exposure_view = await exposure_as_at(
        session, context, portfolio=book_row, as_of=as_of, view=book
    )
    security = await _security_of(session, request)
    held = _held(book, security)

    denominator = book.net_assets.record.id
    planned_weight = Quantity.of(
        planned,
        DIMENSIONLESS,
        source=SourceRef.planned_weight(
            request.id, label=f"planned weight for {request.ticker}, stated on the request"
        ),
    )
    current_weight = (
        held.weight.quantity
        if held is not None and held.weight is not None
        else _nil(denominator, f"the book as at {when}, which holds none of {request.ticker}")
    )

    figures: list[Figure] = []
    figures.append(
        graded_figure(
            context,
            calc.cash_weight_after(
                context,
                cash_weight=_cash_share(context, book, denominator=denominator, when=when),
                current_weight=current_weight,
                planned_weight=planned_weight,
            ),
        )
    )
    others = [
        row.weight.quantity
        for row in book.holdings
        if row.weight is not None
        and row.problem != CLOSED
        and (held is None or row.security.id != held.security.id)
    ]
    figures.append(
        graded_figure(
            context,
            calc.top_holdings_share_after(
                context,
                other_weights=others,
                planned_weight=planned_weight,
                count=CONCENTRATION_COUNT,
            ),
        )
    )
    sector = sector_of(security) if security is not None else None
    if sector is not None:
        share = _sector_share(exposure_view, sector)
        if share is None:
            share = _nil(denominator, f"the book as at {when}, which holds nothing in {sector}")
        figures.append(
            graded_figure(
                context,
                calc.exposure_after(
                    context,
                    sector_share=share,
                    current_weight=current_weight,
                    planned_weight=planned_weight,
                    sector=sector,
                ),
            )
        )

    await persist_context(session, context, job_id=job_id)
    typed = tuple(
        dict.fromkeys(
            name
            for figure in figures
            if isinstance(figure.shared, Attested)
            for name in figure.shared.attested_inputs
        )
    )
    _log.info(
        "consequences.struck",
        job_id=str(job_id),
        portfolio=str(book_row.id),
        as_of=as_of.isoformat(),
        figures=len(figures),
        attested=len(typed),
    )
    return _Struck(grade=Grade.ATTESTED if typed else Grade.DOCUMENTED, attested_inputs=typed)


def _cash_share(
    context: Any, book: PortfolioView, *, denominator: uuid.UUID, when: str
) -> Quantity:
    """The cash's share of the book, by the same arithmetic the currency band uses."""
    in_base = [row.in_base.quantity for row in book.cash if row.in_base is not None]
    if not in_base or book.net_assets is None:
        return _nil(denominator, f"the book as at {when}, which holds no cash")
    return exposure(
        context,
        value=grouped_value(context, values=in_base),
        net_assets=book.net_assets.quantity,
    )


def _nil(denominator: uuid.UUID, label: str) -> Quantity:
    """A zero the book walk established, sourced to the total that saw the whole book.

    An unsourced zero is still unsourced (the valuation's net-debt rule): a weight of
    nothing is a fact about the book, and the row that read every position is where it
    comes from.
    """
    return Quantity.of(
        Decimal(0), DIMENSIONLESS, source=SourceRef.calculation(denominator, label=label)
    )


async def _security_of(session: AsyncSession, request: ResearchRequest) -> Security | None:
    found: Security | None = await session.scalar(
        select(Security)
        .options(selectinload(Security.company))
        .where(Security.ticker == request.ticker, Security.exchange == request.exchange)
        .order_by(Security.is_active.desc())
        .limit(1)
    )
    return found


def _held(book: PortfolioView, security: Security | None) -> HoldingRow | None:
    if security is None:
        return None
    return next(
        (row for row in book.holdings if row.security.id == security.id and row.problem != CLOSED),
        None,
    )


def _sector_share(exposure_view: ExposureView, sector: str) -> Quantity | None:
    band = next((row for row in exposure_view.bands if row.kind == "sector"), None)
    if band is None:
        return None
    found = next((row for row in band.slices if row.label == sector), None)
    return found.share.quantity if found is not None else None


# -- Reading the record back ------------------------------------------------------------------


async def _stored(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Calculation]:
    """The run's rows this section composes from, the latest of each name."""
    rows = await session.scalars(
        select(Calculation)
        .where(Calculation.job_id == job_id, Calculation.name.in_(_READ_BACK))
        .order_by(Calculation.sequence)
    )
    found: dict[str, Calculation] = {}
    for row in rows:
        found[row.name] = row
    return found


async def _stored_grade(
    session: AsyncSession, calculation_ids: list[uuid.UUID]
) -> tuple[Grade, tuple[str, ...]]:
    """The grade of the figures as struck, from the stored lineage.

    The same walk :func:`aer.calc.attestation.grade_of` makes over a live ledger, over the
    persisted rows: an attestation leaf carries the grade it was computed under, so a
    holding corrected to documented afterwards does not retroactively evidence a figure
    struck before it (ADR 0073).
    """
    typed: list[str] = []
    for calculation_id in calculation_ids:
        tree = await lineage(session, calculation_id)
        typed.extend(
            node.label or node.identifier
            for node in tree.walk()
            if node.kind == "attestation" and node.detail.get("grade") == Grade.ATTESTED.value
        )
    unique = tuple(dict.fromkeys(typed))
    return (Grade.ATTESTED if unique else Grade.DOCUMENTED), unique


def _consequence_rows(
    stored: dict[str, Calculation], *, request: ResearchRequest, planned: Decimal
) -> list[dict[str, str]]:
    """The book before and after, as rows the walk can footnote.

    The before figures are the after figures' own inputs, read off the stored rows: the
    weight held today is what `cash_weight_after` subtracted, the sector's share today is
    what `exposure_after` started from. One record, read in both directions, so the two
    columns cannot disagree.
    """
    purpose = _PURPOSE_WORDS.get(str((request.portfolio_context or {}).get("purpose") or ""))
    rows: list[dict[str, str]] = [
        {
            "label": f"Planned share of the book in {request.ticker}",
            "value": str(planned),
            "unit": "pure",
            "provenance": "Stated on the request" + (f" — {purpose}" if purpose else ""),
        }
    ]
    cash_after = stored.get(_CASH_AFTER)
    if cash_after is not None:
        rows.append(
            _input_row(
                cash_after, "current_weight", label=f"Share of the book in {request.ticker} today"
            )
        )
        rows.append(_input_row(cash_after, "cash_weight", label="Share of the book in cash today"))
        rows.append(_figure_row("Share of the book in cash after", cash_after))
    top_before = stored.get(_TOP_BEFORE)
    if top_before is not None:
        rows.append(_figure_row("Share of the book in its five largest holdings today", top_before))
    top_after = stored.get(_TOP_AFTER)
    if top_after is not None:
        rows.append(_figure_row("Share of the book in its five largest holdings after", top_after))
    sector_after = stored.get(_SECTOR_AFTER)
    if sector_after is not None:
        sector = str((sector_after.parameters or {}).get("sector") or "its sector")
        rows.append(
            _input_row(sector_after, "sector_share", label=f"Share of the book in {sector} today")
        )
        rows.append(_figure_row(f"Share of the book in {sector} after", sector_after))
    return rows


def _horizon_rows(
    stored: dict[str, Calculation], *, request: ResearchRequest
) -> list[dict[str, str]]:
    """The stated horizon against the model's payback, or the honest reason there is none."""
    stated = "Stated on the request"
    if request.horizon_label:
        stated = f"{stated}: {request.horizon_label}"
    rows: list[dict[str, str]] = [
        {
            "label": "Your stated horizon",
            "value": str(request.investment_horizon_months),
            "unit": "months",
            "provenance": stated,
        }
    ]
    recovery = stored.get(_RECOVERY)
    if recovery is None:
        rows.append(
            {
                "label": "The model's payback",
                "value": "",
                "unit": "",
                "provenance": (
                    "Not measured: the run holds no market capitalisation to set the "
                    "forecast against, or produced no valuation."
                ),
            }
        )
        return rows
    years = sum(
        1 for entry in recovery.inputs if str(entry.get("name", "")).startswith("present_values[")
    )
    rows.append(
        _figure_row(
            "Share of today's enterprise value the explicit forecast recovers",
            recovery,
            provenance=(
                f"Over the {years}-year explicit forecast, at the valuation's own discount rate."
            ),
        )
    )
    payback = stored.get(_PAYBACK)
    if payback is not None:
        rows.append(
            _figure_row(
                "The model's payback year",
                payback,
                provenance=(
                    f"Within the {years}-year explicit forecast; your horizon is stated above."
                ),
            )
        )
    else:
        rows.append(
            {
                "label": "The model's payback",
                "value": "",
                "unit": "",
                "provenance": (
                    f"Beyond the {years}-year explicit forecast, which recovers the share "
                    "above; the terminal value is what the market pays for after it."
                ),
            }
        )
    return rows


def _figure_row(label: str, row: Calculation, *, provenance: str = "") -> dict[str, str]:
    return {
        "label": label,
        "value": str(row.output_value),
        "unit": row.output_unit,
        "provenance": provenance or "Computed from the book as at the run's date",
        "calculation_id": str(row.id),
    }


def _input_row(row: Calculation, name: str, *, label: str) -> dict[str, str]:
    """One of a stored row's inputs as a row of its own, footnoted where it was computed."""
    entry = next((item for item in row.inputs if item.get("name") == name), None)
    if entry is None:
        return {"label": label, "value": "", "unit": "", "provenance": "Not recorded"}
    source = entry.get("source") or {}
    source_label = str(source.get("label") or "")
    out = {
        "label": label,
        # At the ledger's stored precision: the input was recorded at full precision and
        # the row it cites stores twelve places, and a figure printed from the one beside
        # its footnote resolving to the other would be one figure in two notations.
        "value": _stored_precision(entry.get("value", "")),
        "unit": str(entry.get("unit") or ""),
        "provenance": source_label or "Computed from the book as at the run's date",
    }
    # A nil the book walk established is footnoted by its explanation rather than by the
    # total it was sourced to: the marker would land a reader on the whole book's net
    # assets, which is true and unhelpful.
    if source.get("kind") == "calculation" and "holds no" not in source_label:
        out["calculation_id"] = str(source.get("id") or "")
    return out


def _basis(request: ResearchRequest) -> str:
    when = f"{request.work_order.as_of_date:%d %B %Y}"
    purpose = _PURPOSE_WORDS.get(str((request.portfolio_context or {}).get("purpose") or ""))
    what = f"{purpose}, " if purpose else ""
    return (
        f"Computed from your book as at {when}, the day this report was commissioned, with "
        f"the position funded from cash at the share of the book you planned for "
        f"{request.ticker} — {what}stated on the request. Every figure is a fraction of the "
        "book and a recorded calculation; nothing here is a size in money, and nothing here "
        "is a recommendation. The horizon is yours; the payback is the discounted cash "
        "flow's, on its own explicit forecast."
    )


def _evidence_note(grade: Grade, typed: tuple[str, ...]) -> str:
    if grade is Grade.ATTESTED:
        beyond = len(typed) - _NAMED_IN_NOTE
        named = ", ".join(typed[:_NAMED_IN_NOTE]) + (
            f" and {beyond} other(s)" if beyond > 0 else ""
        )
        return (
            "Some of these figures rest on entries typed into the book rather than "
            f"documented — {named} — so they are attested, not evidenced. This section is "
            "shown to you in full with that said, and withheld from any copy of the report "
            "that leaves this machine."
        )
    return (
        "Every figure here rests on documented entries in the book, so this section crosses "
        "to a shared copy unchanged."
    )


def _stored_precision(raw: Any) -> str:
    """A value as the ``calculations`` table would return it: twelve places, half up."""
    try:
        return str(Decimal(str(raw)).quantize(_LEDGER_PLACES, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return str(raw)


def _planned_weight(request: ResearchRequest) -> Decimal | None:
    raw = (request.portfolio_context or {}).get("planned_weight")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


# -- The audience -----------------------------------------------------------------------------


def consequences_for_audience(content: dict[str, Any], audience: Audience) -> dict[str, Any]:
    """The section as one audience may have it.

    The operator's own copy keeps everything, with the grade stated in the evidence note.
    A copy that leaves the machine keeps the figures only where their lineage is documented
    throughout; an attested lineage gets ADR 0073's disclosure and nothing else — not the
    rows, and not the commentary, because prose that quotes a withheld figure is the figure
    in another notation.
    """
    if audience is Audience.INTERNAL or content.get("grade") != Grade.ATTESTED.value:
        return content
    typed = tuple(str(name) for name in content.get("attested_inputs") or ())
    return {"withheld": Attested(label="The closing section", attested_inputs=typed).as_sentence()}


# -- The commentary's deterministic edge ------------------------------------------------------


def consequences_problems(content: dict[str, Any], block: dict[str, Any]) -> list[str]:
    """What the commentary says that the section may not: an instruction.

    The block states consequences; the commentary interprets them; neither recommends.
    Each problem names the phrase so a retry can remove it rather than guess.
    """
    commentary = str(content.get("commentary") or "")
    if not commentary:
        return []
    figures = ", ".join(_labels(block)) or "none"
    return [
        f"The commentary says {_found(commentary, pattern)!r}, and this section computes "
        "consequences for the operator's book without issuing an instruction: no "
        "recommendation, no rating, no target, no size. Say what the figures above mean "
        f"for the book — {figures} — and leave the decision to the operator."
        for pattern in _INSTRUCTIONS
        if _found(commentary, pattern)
    ]


def consequences_only(block: dict[str, Any]) -> str:
    """Why the block is the section's whole content, or empty for the ordinary path.

    With no figures — no book on record, or one that would not value — there is nothing
    for a commentary to interpret, and a writer call would pay for prose about an absence.
    The basis sentence already says what the absence is.
    """
    if block.get("consequences"):
        return ""
    return str(block.get("basis") or "There are no figures for a commentary to interpret.")


def consequences_note(block: dict[str, Any]) -> str:
    """What the writer is told about the block it cannot see."""
    named = ", ".join(_labels(block)) or "none"
    return (
        "The block rendered above your commentary states the operator's book before and "
        f"after the planned position, and their horizon against the model's payback: {named}. "
        "Say what those figures mean for the book — what moves, in which direction, and "
        "whether the horizon outruns the payback — and nothing else. Issue no instruction: "
        "no buy, sell, add, trim, size, rating, target or recommendation. The decision is "
        "the operator's, and a commentary that makes it for them is refused."
    )


def _labels(block: dict[str, Any]) -> list[str]:
    """Every figure the block carries, by label, in a stable order."""
    return sorted(
        {
            str(row.get("label", "")).strip()
            for field in ("consequences", "horizon")
            for row in block.get(field) or []
            if isinstance(row, dict) and str(row.get("label", "")).strip()
        }
    )


def _found(text: str, pattern: str) -> str:
    match = re.search(rf"\b(?:{pattern})\b", text, flags=re.IGNORECASE)
    return match.group(0) if match else ""
