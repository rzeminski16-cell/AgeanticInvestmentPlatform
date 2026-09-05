"""What you follow, the queue of what to research next, and the standing budget it spends.

One screen and four forms. The screen is every followed listing with where it stands —
queued, commissioned, researched, stopped — beside the standing budget's room this month
and what a run typically costs. The forms follow a company, stop following one with a
reason, commission one entry as at a date, and commission the next the budget affords.

**Nothing on this page is a figure a report rests on.** The budget is money, shown as
money; the states are read from runs; the research a commission starts is the research
tool's, with its gates and its report, reached from here by link.

**Two clocks, said plainly** (ADR 0107): an entry shows when it was followed; each
commission shows the date its run is dated as at. Neither is the other.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.errors import AerError
from aer.queue import enqueue_run
from aer.services import overview as overview_service
from aer.services import watchlist as watchlist_service
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.watchlist")

# What each state is called on the screen, and the tone it reads in. Not an enum in
# `core/enums.py` — a state read off a run rather than stored — so the words live here.
STATE_WORDS: Final[dict[str, vocabulary.HumanState]] = {
    "queued": vocabulary.HumanState(
        "Queued", vocabulary.Tone.MUTED, "Followed and not yet researched."
    ),
    "commissioned": vocabulary.HumanState(
        "Commissioned", vocabulary.Tone.INFO, "A run is alive; it stops at gate one for you."
    ),
    "researched": vocabulary.HumanState(
        "Researched", vocabulary.Tone.SUCCESS, "A report exists for the latest commission."
    ),
    "stopped": vocabulary.HumanState(
        "Stopped", vocabulary.Tone.WARNING, "The last run died, so this is back in the queue."
    ),
    "withdrawn": vocabulary.HumanState("No longer followed", vocabulary.Tone.MUTED),
}


# -- The screen ---------------------------------------------------------------------------


@router.get("/watchlist", response_class=HTMLResponse, summary="Watchlist")
async def watchlist_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    showing_withdrawn = request.query_params.get("withdrawn") == "1"
    states = await watchlist_service.states_for(session, user_id=user.id)
    withdrawn = (
        await watchlist_service.states_for(session, user_id=user.id, withdrawn=True)
        if showing_withdrawn
        else []
    )
    budget = await watchlist_service.standing_budget(session, settings=settings, user_id=user.id)
    typical = await overview_service.typical_cost(
        session, user_id=user.id, mode=watchlist_service.DEFAULT_MODE
    )
    queued = [state for state in states if state.is_queued]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "watchlist/index.html",
        {
            "verdict": _watchlist_verdict(states, budget),
            "budget": _budget_context(budget),
            "cost_guidance": figures.cost_guidance(typical),
            "rows": [_row(state) for state in states],
            "withdrawn": [_row(state) for state in withdrawn],
            "showing_withdrawn": showing_withdrawn,
            "queued": len(queued),
            "next": _row(queued[0]) if queued else None,
            "today": datetime.now(UTC).date().isoformat(),
            "queued_notice": request.query_params.get("queued", ""),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


def _watchlist_verdict(
    states: list[watchlist_service.EntryState], budget: watchlist_service.StandingBudget
) -> verdicts.Verdict:
    counted = {name: sum(1 for state in states if state.state == name) for name in STATE_WORDS}
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(
            counted["queued"] + counted["stopped"],
            "company is followed and not yet researched",
            "companies are followed and not yet researched",
        ),
        verdicts.Count(counted["commissioned"], "run is alive", "runs are alive"),
        verdicts.Count(counted["researched"], "has been researched", "have been researched"),
    ]
    if counted["queued"] + counted["stopped"]:
        plural = "" if budget.fits == 1 else "s"
        clauses.append(
            f"the standing budget affords {budget.fits} more run{plural} this month"
            if budget.fits
            else "the standing budget is spent for the month"
        )
    tone = vocabulary.Tone.INFO if states else vocabulary.Tone.MUTED
    return verdicts.sentence(
        clauses,
        when_none=(
            "Nothing is followed. Follow a company below, and the queue is what to research "
            "next and what it would cost."
        ),
        tone=tone,
    )


def _budget_context(budget: watchlist_service.StandingBudget) -> dict[str, Any]:
    return {
        "budget": figures.pounds(budget.budget_gbp),
        "spent": figures.pounds(budget.spent_gbp),
        "reserved": figures.pounds(budget.reserved_gbp),
        "room": figures.pounds(budget.room_gbp),
        "cap": figures.pounds(budget.cap_gbp),
        "fits": budget.fits,
        "month": f"{budget.month_start:%B %Y}",
        "is_spent": budget.fits == 0,
    }


def _row(state: watchlist_service.EntryState) -> dict[str, Any]:
    words = STATE_WORDS[state.state]
    entry = state.entry
    job = state.job
    return {
        "id": entry.id,
        "company_name": entry.company_name,
        "listing": entry.listing,
        "why": entry.why,
        "followed_on": f"{entry.followed_at:%d %B %Y}",
        "state": state.state,
        "label": words.label,
        "tone": words.tone.value,
        "detail": words.detail,
        "is_queued": state.is_queued,
        "is_withdrawn": entry.is_withdrawn,
        "withdrawn_reason": entry.withdrawn_reason,
        "withdrawn_on": f"{entry.withdrawn_at:%d %B %Y}" if entry.withdrawn_at else "",
        "as_of": f"{state.commission.as_of_date:%d %B %Y}" if state.commission else "",
        "commissioned_on": (
            f"{state.commission.commissioned_at:%d %B %Y}" if state.commission else ""
        ),
        "commissions": len(entry.commissions),
        "request_href": f"/requests/{state.request.id}" if state.request else "",
        "run_href": f"/runs/{job.id}" if job else "",
        "run_state": vocabulary.JOB_STATES[job.status].label if job else "",
        "report_href": f"/reports/{state.report.id}" if state.report else "",
        "cost": figures.pounds(job.total_cost_gbp) if job else "",
        # The "researched as at" history: every commission, newest first, once there is
        # more than the one the row already shows.
        "history": [_commission_row(record) for record in state.history]
        if len(state.history) > 1
        else [],
    }


def _commission_row(record: watchlist_service.CommissionRecord) -> dict[str, Any]:
    job = record.job
    return {
        "as_of": f"{record.commission.as_of_date:%d %B %Y}",
        "commissioned_on": f"{record.commission.commissioned_at:%d %B %Y}",
        "request_href": f"/requests/{record.request.id}" if record.request else "",
        "run_href": f"/runs/{job.id}" if job else "",
        "run_state": vocabulary.JOB_STATES[job.status].label if job else "",
        "report_href": f"/reports/{record.report.id}" if record.report else "",
        "cost": figures.pounds(job.total_cost_gbp) if job else "",
        "is_purged": record.request is None,
    }


# -- The forms ----------------------------------------------------------------------------


@router.post("/watchlist", summary="Follow a company")
async def follow(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was followed.")
    try:
        await watchlist_service.follow(
            session,
            user=user,
            company_name=submitted.get("company_name", ""),
            ticker=submitted.get("ticker", ""),
            exchange=submitted.get("exchange", ""),
            why=submitted.get("why", ""),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    return RedirectResponse("/watchlist", status_code=HTTP_303_SEE_OTHER)


@router.post("/watchlist/{entry_id}/stop", summary="Stop following a company")
async def stop_following(
    entry_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was withdrawn.")
    entry = await watchlist_service.entry_of(session, entry_id, user_id=user.id)
    if entry is None:
        return _problem(request, "No such watchlist entry.")
    try:
        await watchlist_service.stop_following(
            session, user=user, entry=entry, reason=submitted.get("reason", "")
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    return RedirectResponse("/watchlist", status_code=HTTP_303_SEE_OTHER)


@router.post("/watchlist/{entry_id}/commission", summary="Commission research on a company")
async def commission(  # noqa: PLR0917 -- the dependencies, spelt out
    entry_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    redis: RedisClient,
) -> Response:
    """One entry into one run, as at today or the date stated, inside the standing budget."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was commissioned.")
    entry = await watchlist_service.entry_of(session, entry_id, user_id=user.id)
    if entry is None:
        return _problem(request, "No such watchlist entry.")
    as_of = _date_or_none(submitted.get("as_of", ""))
    try:
        _, job = await watchlist_service.commission(
            session, settings=settings, user=user, entry=entry, as_of=as_of
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    queued = await enqueue_run(redis, job.id)
    _log.info("watchlist.commissioned_from_page", job_id=str(job.id), queued=queued is not None)
    return RedirectResponse(
        f"/runs/{job.id}" if queued is not None else "/watchlist?queued=0",
        status_code=HTTP_303_SEE_OTHER,
    )


@router.post("/watchlist/commission-next", summary="Commission the next the budget affords")
async def commission_next(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    redis: RedisClient,
) -> Response:
    """Walk the queue in order, starting runs while the standing budget affords them."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was commissioned.")
    limit = _int_or_none(submitted.get("limit", ""))
    try:
        drain = await watchlist_service.commission_next(
            session, settings=settings, user=user, limit=limit
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    queued = 0
    for _, job in drain.commissioned:
        if await enqueue_run(redis, job.id) is not None:
            queued += 1
    _log.info(
        "watchlist.drained",
        commissioned=len(drain.commissioned),
        queued=queued,
        left=drain.left,
        stopped=drain.stopped,
        skipped=list(drain.skipped),
    )
    outcome = (
        str(queued) if queued == len(drain.commissioned) else f"{queued}of{len(drain.commissioned)}"
    )
    return RedirectResponse(f"/watchlist?queued={outcome}", status_code=HTTP_303_SEE_OTHER)


# -- Reading ------------------------------------------------------------------------------


def _date_or_none(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw.strip() else None
    except ValueError:
        return None


def _int_or_none(raw: str) -> int | None:
    try:
        return int(raw) if raw.strip() else None
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
