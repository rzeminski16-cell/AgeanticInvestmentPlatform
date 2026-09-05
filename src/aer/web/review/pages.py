"""A closed position, read for the quality of the decision behind it; and what the reviewed
ones have in common.

Four screens and two forms. The list is every closed position in the operator's books with
where its review stands — not started, proposed, stopped, reviewed — and the button that
runs the reviewer over one. The proposal is what the reviewer said beside what code
computed, with the form that confirms it. The review is the operator's judgement as
confirmed, the proposal kept beside it. The analytics page is what the reviewed positions
have in common, every statistic with its ``n``.

**The outcome is code's and the review is the operator's** (ADR 0105). Every figure on
these pages — cost, proceeds, the realised return — is a recorded calculation linked to its
formula; the reviewer quoted them and could not write them. The verdicts and the quality
are a person's, held on a basis that is theirs, and the reviewer's draft is kept as what
was proposed rather than as what was decided.

**The reviewer runs in this process**, like the skill dry run and unlike the monitor: one
call, no tools, over a position that is not going to change. A worker would add a queue
and a page that says "queued" to an operation whose whole output is one screen the
operator is waiting to read.

**Nothing here recommends anything.** There is no field for a next action, a size or a
methodology change, on the proposal or on the review (ADR 0081), and the analytics page
counts what happened rather than what to do about it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, ProviderDep, RouterDep, SettingsDep, StoreDep
from aer.core.enums import PremiseVerdict, ProcessQuality
from aer.db.models import Portfolio, Review, Security
from aer.errors import AerError
from aer.services import post_trade
from aer.web import verdict as verdicts
from aer.web import vocabulary
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["Grid", "GridCell", "GridRow", "VerdictRow", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.review")

VERDICT_FIELD: Final = "verdict-"
NOTE_FIELD: Final = "note-"

_QUALITY_CHOICES: Final = [
    {"value": member.value, "label": vocabulary.PROCESS_QUALITIES[member].label}
    for member in ProcessQuality
]
_VERDICT_CHOICES: Final = [
    {"value": member.value, "label": vocabulary.PREMISE_VERDICTS[member].label}
    for member in PremiseVerdict
]


# -- Rows -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EpisodeRow:
    """One closed position as the list shows it, with where its review stands."""

    key: str
    portfolio_id: uuid.UUID
    security_id: uuid.UUID
    ticker: str
    exchange: str
    book: str
    opened_on: str
    closed_on: str
    closed_on_iso: str
    trades: int
    state: str
    pass_id: uuid.UUID | None
    review_id: uuid.UUID | None
    reason: str


@dataclass(frozen=True, slots=True)
class FigureRow:
    """One figure of the outcome, rendered, with the calculation it resolves to."""

    label: str
    value: str
    note: str = ""
    href: str = ""


@dataclass(frozen=True, slots=True)
class PremiseForm:
    """One premise on the confirm form, prefilled with what the reviewer proposed."""

    premise_id: str
    statement: str
    withdrawn: bool
    verdict: str
    note: str


@dataclass(frozen=True, slots=True)
class VerdictRow:
    """One premise as the review found it, beside what the reviewer proposed."""

    position: int
    statement: str
    label: str
    tone: str
    note: str
    proposed: str
    """The reviewer's label where it differed from the operator's, else empty."""
    proposed_tone: str = ""
    proposed_note: str = ""


@dataclass(frozen=True, slots=True)
class GridCell:
    """One of the four cells: a count, and its share once the sample can bear one."""

    key: str
    count: int
    share: str


@dataclass(frozen=True, slots=True)
class GridRow:
    label: str
    cells: tuple[GridCell, ...]


@dataclass(frozen=True, slots=True)
class Grid:
    """Process against outcome as a two-by-two: quality down, the sign of the return across.

    The off-diagonal cells — sound process with a loss, flawed process with a gain — are the
    ones the page exists to make reachable, and a two-by-two puts them where the eye lands.
    Built from the same `Statistic`, so the `n` and the tally rule are the type's, not the
    template's.
    """

    label: str
    count: int
    is_a_finding: bool
    columns: tuple[str, ...]
    rows: tuple[GridRow, ...]
    remainder: GridCell | None
    """Reviews whose outcome could not be computed: in the ``n``, in no cell."""


_GRID_ROWS: Final[tuple[tuple[str, str], ...]] = (
    ("Sound process", "sound process"),
    ("Flawed or questionable process", "flawed or questionable process"),
)
_GRID_COLUMNS: Final[tuple[tuple[str, str], ...]] = (("Gain", "gain"), ("Loss", "loss"))
_REMAINDER: Final = "outcome not computed"


def _grid(row: post_trade.Statistic) -> Grid:
    """The cells statistic laid out two by two. Every part the service counted lands in a
    cell or the remainder; a part it did not is a template out of step with the service."""
    counts = {part.label: part.count for part in row.parts}
    rows = tuple(
        GridRow(
            label=label,
            cells=tuple(
                GridCell(
                    key=f"{quality}, {sign}".replace(" ", "-").replace(",", ""),
                    count=counts.get(f"{quality}, {sign}", 0),
                    share=_share(counts.get(f"{quality}, {sign}", 0), row.count)
                    if row.is_a_finding
                    else "",
                )
                for _, sign in _GRID_COLUMNS
            ),
        )
        for label, quality in _GRID_ROWS
    )
    unknown = counts.get(_REMAINDER, 0)
    return Grid(
        label=row.label,
        count=row.count,
        is_a_finding=row.is_a_finding,
        columns=tuple(label for label, _ in _GRID_COLUMNS),
        rows=rows,
        remainder=(
            GridCell(
                key=_REMAINDER.replace(" ", "-"),
                count=unknown,
                share=_share(unknown, row.count) if row.is_a_finding else "",
            )
            if unknown
            else None
        ),
    )


def _episode_row(state: post_trade.EpisodeState) -> EpisodeRow:
    episode = state.episode
    reason = ""
    if state.state == "stopped" and state.proposal is not None:
        reason = str((state.proposal.error or {}).get("message") or "The pass recorded no reason.")
    return EpisodeRow(
        key=episode.key,
        portfolio_id=episode.portfolio.id,
        security_id=episode.security.id,
        ticker=episode.security.ticker,
        exchange=episode.security.exchange,
        book=episode.portfolio.name,
        opened_on=f"{episode.opened_on:%d %B %Y}",
        closed_on=f"{episode.closed_on:%d %B %Y}",
        closed_on_iso=episode.closed_on.isoformat(),
        trades=len(episode.trades),
        state=state.state,
        pass_id=state.proposal.id if state.proposal is not None else None,
        review_id=state.review.judgement_id if state.review is not None else None,
        reason=reason,
    )


def _figure_rows(outcome: dict[str, Any]) -> list[FigureRow]:
    """The outcome as the page shows it: rendered here, linked to its formula (ADR 0105 §2)."""
    ids = outcome.get("calculation_ids") or {}
    currency = str(outcome.get("currency") or "")
    rows: list[FigureRow] = []
    if outcome.get("realised_return"):
        rows.append(
            FigureRow(
                "Realised return",
                _percent(str(outcome["realised_return"])),
                note="On cost, in the book's currency, every flow converted at its own date.",
                href=_calculation_href(ids.get("realised_return")),
            )
        )
    if outcome.get("cost"):
        rows.append(
            FigureRow(
                "Cost",
                _money(str(outcome["cost"]), currency),
                note="Every purchase's consideration and dealing costs.",
                href=_calculation_href(ids.get("episode_cost")),
            )
        )
    if outcome.get("proceeds"):
        rows.append(
            FigureRow(
                "Proceeds",
                _money(str(outcome["proceeds"]), currency),
                note="Every sale's cash effect, and every dividend paid while open.",
                href=_calculation_href(ids.get("episode_proceeds")),
            )
        )
    days = int(outcome.get("holding_days") or 0)
    intended = outcome.get("intended_horizon_months")
    rows.append(
        FigureRow(
            "Holding period",
            f"{days} day{'' if days == 1 else 's'}",
            note=(
                f"Against {intended} month{'' if intended == 1 else 's'} intended."
                if intended
                else "No decision stated an intended holding period."
            ),
        )
    )
    return rows


def _calculation_href(calculation_id: Any) -> str:
    return f"/calculations/{calculation_id}" if calculation_id else ""


def _percent(raw: str) -> str:
    """A fraction of cost as a percentage with one decimal place: "0.2" reads as 20.0%."""
    share = (Decimal(raw) * 100).quantize(Decimal("0.1"))
    return f"{share:+.1f}%"


def _money(raw: str, currency: str) -> str:
    return f"{Decimal(raw).quantize(Decimal('0.01')):,.2f} {currency}".strip()


def _review_verdict(states: list[EpisodeRow]) -> verdicts.Verdict:
    counted = {
        name: sum(1 for row in states if row.state == name)
        for name in ("proposed", "unreviewed", "stopped", "reviewed")
    }
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(
            counted["proposed"],
            "proposal is waiting for you to confirm",
            "proposals are waiting for you to confirm",
        ),
        verdicts.Count(
            counted["unreviewed"],
            "closed position has not been reviewed",
            "closed positions have not been reviewed",
        ),
        verdicts.Count(
            counted["stopped"], "pass stopped at its ceiling", "passes stopped at their ceiling"
        ),
        verdicts.Count(
            counted["reviewed"], "position has been reviewed", "positions have been reviewed"
        ),
    ]
    tone = vocabulary.Tone.WARNING if counted["proposed"] else vocabulary.Tone.INFO
    return verdicts.sentence(
        clauses,
        when_none=(
            "No position has closed yet. A review starts when a holding returns to nil, and "
            "scores the decision against the process rather than the result."
        ),
        tone=tone,
    )


# -- The list -------------------------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse, summary="Post-trade review")
async def review_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Every closed position with where its review stands, and the button that runs one."""
    books = await _books(session, user.id)
    rows = [
        _episode_row(state)
        for book in books
        for state in await post_trade.states_for(session, portfolio=book)
    ]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "review/index.html",
        {
            "verdict": _review_verdict(rows),
            "proposed": [row for row in rows if row.state == "proposed"],
            "unreviewed": [row for row in rows if row.state == "unreviewed"],
            "stopped": [row for row in rows if row.state == "stopped"],
            "reviewed": [row for row in rows if row.state == "reviewed"],
            "has_books": bool(books),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/review/run", summary="Run the reviewer over a closed position")
async def run_review(  # noqa: PLR0917 -- the service bundle, spelt out
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    provider: ProviderDep,
    model_router: RouterDep,
    store: StoreDep,
) -> Response:
    """One reviewer pass, in this process, and then the proposal it left."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was run.")

    portfolio_id = _uuid_or_none(submitted.get("portfolio_id", ""))
    security_id = _uuid_or_none(submitted.get("security_id", ""))
    try:
        closed_on = date.fromisoformat(submitted.get("closed_on", ""))
    except ValueError:
        closed_on = None
    if portfolio_id is None or security_id is None or closed_on is None:
        return _problem(request, "That does not name a closed position.", status=400)

    book = await session.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user.id)
    )
    if book is None:
        return _problem(request, "No such book.")
    episode = post_trade.episode_of(
        await post_trade.closed_episodes(session, portfolio=book),
        security_id=security_id,
        closed_on=closed_on,
    )
    if episode is None:
        return _problem(request, "No such closed position.")

    try:
        job = await post_trade.run_review(
            session,
            settings=settings,
            provider=provider,
            router=model_router,
            store=store,
            user=user,
            episode=episode,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    _log.info("review.run", job_id=str(job.id), status=job.status.value)
    return RedirectResponse(f"/review/passes/{job.id}", status_code=HTTP_303_SEE_OTHER)


# -- The proposal -----------------------------------------------------------------------------


@router.get("/review/passes/{pass_id}", response_class=HTMLResponse, summary="A proposal")
async def proposal_page(
    pass_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """What the reviewer proposed beside what code computed, and the form that confirms it."""
    proposal = await post_trade.proposal_of(session, pass_id, user_id=user.id)
    if proposal is None:
        return _problem(request, "No such pass.")

    output = proposal.output
    episode = output.get("episode") or {}
    outcome = output.get("outcome") or {}
    draft = proposal.draft
    proposed = {row.premise_id: row for row in draft.verdicts} if draft is not None else {}
    premises = [
        PremiseForm(
            premise_id=str(row["premise_id"]),
            statement=str(row.get("statement") or ""),
            withdrawn=bool(row.get("withdrawn")),
            verdict=(
                proposed[str(row["premise_id"])].verdict.value
                if str(row["premise_id"]) in proposed
                else PremiseVerdict.UNTESTED.value
            ),
            note=proposed[str(row["premise_id"])].note
            if str(row["premise_id"]) in proposed
            else "",
        )
        for row in output.get("premises") or []
    ]
    review = await session.scalar(select(Review).where(Review.job_id == proposal.job.id))
    if review is None and episode:
        review = await _review_of_episode(session, episode)

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "review/proposal.html",
        {
            "pass_id": proposal.job.id,
            "episode": episode,
            "subject": _subject(episode, output),
            "figures": _figure_rows(outcome) if outcome else [],
            "problem": str(outcome.get("problem") or ""),
            "decisions": output.get("decisions") or [],
            "findings": output.get("findings") or [],
            "premises": premises,
            "draft": draft,
            "is_failed": proposal.failed,
            "reason": proposal.reason,
            "review_id": review.judgement_id if review is not None else None,
            "started": f"{proposal.job.started_at:%d %B %Y}" if proposal.job.started_at else "",
            "qualities": _QUALITY_CHOICES,
            "verdict_choices": _VERDICT_CHOICES,
            "verdict_field": VERDICT_FIELD,
            "note_field": NOTE_FIELD,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/review/passes/{pass_id}/confirm", summary="Confirm a review")
async def confirm_review(
    pass_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The operator's judgement: the draft as amended, held by them on their basis."""
    proposal = await post_trade.proposal_of(session, pass_id, user_id=user.id)
    if proposal is None:
        return _problem(request, "No such pass.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was confirmed.")

    try:
        quality = ProcessQuality(submitted.get("process_quality", ""))
        chosen = _verdicts_from(submitted)
    except ValueError:
        return _problem(request, "That is not one of the answers the form offers.", status=400)

    try:
        review = await post_trade.confirm_review(
            session,
            user=user,
            proposal=proposal,
            process_quality=quality,
            basis=submitted.get("basis", ""),
            lessons=submitted.get("lessons", ""),
            verdicts=chosen,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/review/{review.judgement_id}", status_code=HTTP_303_SEE_OTHER)


def _verdicts_from(submitted: dict[str, str]) -> dict[uuid.UUID, tuple[PremiseVerdict, str]]:
    chosen: dict[uuid.UUID, tuple[PremiseVerdict, str]] = {}
    for name, value in submitted.items():
        if not name.startswith(VERDICT_FIELD):
            continue
        premise_id = uuid.UUID(name.removeprefix(VERDICT_FIELD))
        chosen[premise_id] = (PremiseVerdict(value), submitted.get(f"{NOTE_FIELD}{premise_id}", ""))
    return chosen


# -- The review -------------------------------------------------------------------------------


@router.get("/review/{review_id}", response_class=HTMLResponse, summary="A review")
async def review_detail(
    review_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    review = await post_trade.review_of(session, review_id, user_id=user.id)
    if review is None:
        return _problem(request, "No such review.")

    security = await session.get(Security, review.security_id)
    book = await session.get(Portfolio, review.portfolio_id)
    proposal = review.proposal or {}
    proposed = {
        str(row.get("premise_id")): row
        for row in proposal.get("verdicts") or []
        if isinstance(row, dict)
    }
    words = vocabulary.PROCESS_QUALITIES[review.process_quality]
    proposed_quality = (
        vocabulary.PROCESS_QUALITIES[ProcessQuality(str(proposal["process_quality"]))]
        if proposal.get("process_quality")
        else None
    )
    rows = []
    for verdict in sorted(review.verdicts, key=lambda row: row.position):
        found = vocabulary.PREMISE_VERDICTS[verdict.verdict]
        draft = proposed.get(str(verdict.premise_id)) if verdict.premise_id else None
        drafted = str(draft.get("verdict") or "") if draft else ""
        differed = bool(drafted) and drafted != verdict.verdict.value
        drafted_words = vocabulary.PREMISE_VERDICTS[PremiseVerdict(drafted)] if differed else None
        rows.append(
            VerdictRow(
                position=verdict.position,
                statement=verdict.statement,
                label=found.label,
                tone=found.tone.value,
                note=verdict.note,
                proposed=drafted_words.label if drafted_words is not None else "",
                proposed_tone=drafted_words.tone.value if drafted_words is not None else "",
                proposed_note=str(draft.get("note") or "") if differed and draft else "",
            )
        )
    response: Response = render(
        request,
        "review/detail.html",
        {
            "item": {
                "id": review.judgement_id,
                "ticker": security.ticker if security is not None else "a delisted security",
                "exchange": security.exchange if security is not None else "",
                "book": book.name if book is not None else "",
                "opened_on": f"{review.opened_on:%d %B %Y}",
                "closed_on": f"{review.closed_on:%d %B %Y}",
                "held_by": review.judgement.held_by,
                "held_on": f"{review.judgement.held_at:%d %B %Y}",
                "quality": words.label,
                "quality_tone": words.tone.value,
                "quality_detail": words.detail,
                "basis": review.judgement.basis,
                "lessons": review.lessons,
                "thesis_id": review.thesis_id,
                "pass_id": review.job_id,
                "agreement": _agreement(review),
            },
            "figures": _figure_rows(review.outcome),
            "problem": str(review.outcome.get("problem") or ""),
            "verdicts": rows,
            "proposal": proposal,
            "proposed_quality": proposed_quality,
            # Amended: the reviewer's quality sits beside the operator's, not under it.
            "quality_amended": (
                proposed_quality is not None
                and str(proposal.get("process_quality")) != review.process_quality.value
            ),
        },
    )
    return response


def _agreement(review: Review) -> str:
    """Whether the operator confirmed the reviewer's quality or amended it — decision data."""
    if not review.proposal:
        return "No proposal: the pass stopped, and this review was written without one."
    if review.proposal.get("process_quality") == review.process_quality.value:
        return "Confirmed as the reviewer proposed."
    return "Amended: the reviewer proposed a different quality, kept beside this one."


# -- Analytics --------------------------------------------------------------------------------


@router.get("/analytics", response_class=HTMLResponse, summary="Decision analytics")
async def analytics_page(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """What the reviewed positions have in common, with the ``n`` beside every statistic."""
    analytics = await post_trade.analytics_for(session, user_id=user.id)
    statistics = [
        _statistic(row)
        for row in (
            analytics.qualities,
            analytics.verdicts,
            analytics.horizons,
            analytics.written_down,
            analytics.agreement,
        )
    ]
    response: Response = render(
        request,
        "review/analytics.html",
        {
            "reviewed": analytics.reviewed,
            "grid": _grid(analytics.cells),
            "statistics": statistics,
            "minimum": post_trade.MINIMUM_SAMPLE,
        },
    )
    return response


def _statistic(row: post_trade.Statistic) -> dict[str, Any]:
    """A breakdown as the page shows it: a percentage only once the sample can bear one."""
    return {
        "label": row.label,
        "count": row.count,
        "is_a_finding": row.is_a_finding,
        "parts": [
            {
                "label": part.label,
                "count": part.count,
                "share": _share(part.count, row.count) if row.is_a_finding else "",
            }
            for part in row.parts
        ],
    }


def _share(count: int, total: int) -> str:
    if not total:
        return ""
    return f"{(Decimal(count) * 100 / Decimal(total)).quantize(Decimal('1'))}%"


# -- Reading ----------------------------------------------------------------------------------


async def _books(session: Any, user_id: uuid.UUID) -> list[Portfolio]:
    return list(
        await session.scalars(
            select(Portfolio)
            .where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
            .order_by(Portfolio.name)
        )
    )


async def _review_of_episode(session: Any, episode: dict[str, Any]) -> Review | None:
    portfolio_id = _uuid_or_none(str(episode.get("portfolio_id") or ""))
    security_id = _uuid_or_none(str(episode.get("security_id") or ""))
    if portfolio_id is None or security_id is None:
        return None
    found: Review | None = await session.scalar(
        select(Review).where(
            Review.portfolio_id == portfolio_id,
            Review.security_id == security_id,
            Review.closed_on == date.fromisoformat(str(episode["closed_on"])),
        )
    )
    return found


def _subject(episode: dict[str, Any], output: dict[str, Any]) -> str:
    ticker = str(episode.get("ticker") or "a position")
    title = str(output.get("thesis_title") or "")
    return f"{ticker} · {title}" if title else ticker


def _uuid_or_none(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


def _problem(request: Request, message: str, *, status: int = HTTP_404_NOT_FOUND) -> Response:
    rendered: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return rendered


def _refused(request: Request, consequence: str) -> Response:
    return _problem(
        request,
        f"This form's security token was missing or had expired. {consequence}",
        status=HTTP_403_FORBIDDEN,
    )
