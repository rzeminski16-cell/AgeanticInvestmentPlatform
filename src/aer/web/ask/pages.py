"""Ask: a question over a company's record, and what became of it (F6, ADR 0130).

Three screens. The ask page is one input over a company the account holds a record for,
with every question asked so far beneath it. The question page is the tier it resolved to,
shown as page specification §13 asks — *from the record*, *re-reading n documents*, *needs
new material* — and then the answer, the price, or the refusal. The note page is the
evidence drawer's answer for one marker: an excerpt as stored, a fact with its source, or
the walk on to a calculation.

**The tier is shown before the answer**, and on the page it is the first thing after the
question. A tier-3 question carries the sentence with the price and one control — the
go-ahead, which posts the hash of the price it showed — then says it is being researched
until the worker answers it, with what was added to the record listed beside the answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from aer.api.deps import (
    CurrentUser,
    DbSession,
    ProviderDep,
    RedisClient,
    RouterDep,
    SettingsDep,
    StoreDep,
)
from aer.core.ask import Tier
from aer.core.enums import JobStatus
from aer.db.models import Question
from aer.errors import AerError
from aer.queue import enqueue_ask
from aer.services import ask as ask_service
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["QuestionRow", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.ask")

_TIER_TONE: Final[dict[int, str]] = {
    int(Tier.RECOMPUTE): "info",
    int(Tier.RE_READ): "info",
    int(Tier.RESEARCH): "warning",
}


@dataclass(frozen=True, slots=True)
class QuestionRow:
    """One question as the list shows it."""

    id: uuid.UUID
    ticker: str
    company: str
    question: str
    tier: int
    tier_label: str
    state: str
    asked_on: str


def _state_of(question: Question) -> str:
    content = question.content if isinstance(question.content, dict) else {}
    kind = str(content.get("kind", ""))
    if question.is_answered:
        return "answered"
    if kind == "research":
        if content.get("outcome") in {"stopped", "failed"}:
            return "stopped"
        if question.is_approved:
            job = question.job
            return (
                "stopped" if job is not None and job.status is JobStatus.FAILED else "researching"
            )
        return "priced"
    if kind == "stopped":
        return "stopped"
    return "refused"


def _row(question: Question) -> QuestionRow:
    return QuestionRow(
        id=question.id,
        ticker=question.company.ticker,
        company=question.company.name,
        question=question.question,
        tier=question.tier,
        tier_label=Tier(question.tier).spoken,
        state=_state_of(question),
        asked_on=question.asked_at.strftime("%Y-%m-%d %H:%M UTC"),
    )


def _indicator(question: Question) -> dict[str, str]:
    """Page specification §13.1: the label the tier is shown by."""
    content = question.content if isinstance(question.content, dict) else {}
    if question.tier == int(Tier.RECOMPUTE):
        words = "from the record"
    elif question.tier == int(Tier.RE_READ):
        dealt = content.get("dealt") if isinstance(content.get("dealt"), dict) else {}
        documents = int(dealt.get("documents", 0)) if dealt else 0
        words = f"re-reading {documents} document{'' if documents == 1 else 's'}"
    else:
        words = "needs new material"
    return {"tone": _TIER_TONE[question.tier], "label": words}


def _cost_words(question: Question) -> str:
    if question.tier == int(Tier.RECOMPUTE) and question.is_answered:
        return (
            "Free. No model was called; every figure is arithmetic on this question's own ledger."
        )
    if question.actual_cost_gbp is None:
        return "Nothing has been spent."
    spent = figures.pounds(Decimal(question.actual_cost_gbp))
    if question.tier != int(Tier.RESEARCH):
        return f"Cost {spent}, shown after it ran."
    content = question.content if isinstance(question.content, dict) else {}
    outcome = str(content.get("outcome", ""))
    if outcome in {"answered", "nothing_useful"}:
        searches = int(content.get("searches", 0) or 0)
        added = len(question.documents_added or [])
        return (
            f"Cost {spent}: {searches} search{'' if searches == 1 else 'es'} and {added} "
            f"document{'' if added == 1 else 's'} added to the record, shown after it ran."
        )
    if outcome in {"stopped", "failed"}:
        return f"Cost {spent} before it stopped; nothing more has been spent."
    return f"The re-read cost {spent} and was discarded; nothing more has been spent."


def _verdict(question: Question) -> verdicts.Verdict:
    tier = Tier(question.tier)
    state = _state_of(question)
    if state == "answered":
        return verdicts.sentence(
            [f"answered at tier {int(tier)}, {tier.spoken.lower()}"],
            when_none="Answered",
            tone=vocabulary.Tone.SUCCESS,
        )
    if state == "priced":
        return verdicts.sentence(
            ["needs new material, and is priced"],
            when_none="Priced",
            tone=vocabulary.Tone.WARNING,
        )
    if state == "researching":
        return verdicts.sentence(
            ["approved at the price shown, and being researched"],
            when_none="Being researched",
            tone=vocabulary.Tone.INFO,
        )
    if state == "stopped":
        return verdicts.sentence(
            ["stopped at a spending ceiling before the model was asked"],
            when_none="Stopped",
            tone=vocabulary.Tone.REFUSAL,
        )
    return verdicts.sentence(
        ["could not be answered from the record"],
        when_none="Refused",
        tone=vocabulary.Tone.REFUSAL,
    )


# -- Pages ------------------------------------------------------------------------------------


@router.get("/ask", response_class=HTMLResponse, summary="Ask")
async def ask_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """One input over a company with a record, and every question asked so far."""
    companies = await ask_service.companies_with_a_record(session, user=user)
    rows = [_row(item) for item in await ask_service.questions_for(session, user=user)]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "ask/index.html",
        {
            "companies": [
                {"value": str(company.id), "label": f"{company.name} ({company.ticker})"}
                for company in companies
            ],
            "chosen": request.query_params.get("company", ""),
            "rows": rows,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/ask", summary="Ask a question")
async def ask_question(  # noqa: PLR0917 -- the service bundle, spelt out
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    provider: ProviderDep,
    model_router: RouterDep,
    store: StoreDep,
) -> Response:
    """Resolve the tier, run what runs unasked, and show the question."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was asked.")

    company_id = _uuid_or_none(submitted.get("company_id", ""))
    company = next(
        (
            row
            for row in await ask_service.companies_with_a_record(session, user=user)
            if row.id == company_id
        ),
        None,
    )
    if company is None:
        return _problem(request, "That does not name a company this account holds a record for.")

    try:
        question = await ask_service.ask(
            session,
            settings=settings,
            provider=provider,
            router=model_router,
            store=store,
            user=user,
            company=company,
            text=submitted.get("question", ""),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    _log.info("ask.page.asked", question_id=str(question.id), tier=question.tier)
    return RedirectResponse(f"/ask/{question.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/ask/{question_id}", response_class=HTMLResponse, summary="One question")
async def question_page(
    request: Request,
    question_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The tier first, then the answer, the price or the refusal."""
    question = await ask_service.question_of(session, question_id, user_id=user.id)
    if question is None:
        return _problem(request, "No such question.")
    content: dict[str, Any] = question.content if isinstance(question.content, dict) else {}
    token = new_csrf_token(settings)
    page: Response = render(
        request,
        "ask/question.html",
        {
            "question": question,
            "row": _row(question),
            "indicator": _indicator(question),
            "verdict": _verdict(question),
            "state": _state_of(question),
            "kind": str(content.get("kind", "")),
            "content": content,
            "figures": list(content.get("figures", [])),
            "paragraphs": list(content.get("paragraphs", [])),
            "notes": list(content.get("notes", [])),
            "documents": list(content.get("documents", [])),
            "cost_words": _cost_words(question),
            "rationale": question.tier_rationale,
            "estimate_hash": ask_service.estimate_hash_of(question),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(page, token)
    return page


@router.post("/ask/{question_id}/research", summary="Research a question at the stated price")
async def research_question(  # noqa: PLR0917 -- every one is an injected dependency
    request: Request,
    question_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """The go-ahead (ADR 0130 §5): the price's hash on the question's row, then the queue.

    The acquisition itself runs in the worker, which holds the fetcher; the page says the
    question is being researched until it answers.
    """
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was started.")
    question = await ask_service.question_of(session, question_id, user_id=user.id)
    if question is None:
        return _problem(request, "No such question.")
    try:
        await ask_service.approve_research(
            session,
            question=question,
            user=user,
            estimate_hash=submitted.get("estimate_hash", ""),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    await enqueue_ask(redis, question.id)
    _log.info("ask.page.approved", question_id=str(question.id))
    return RedirectResponse(f"/ask/{question.id}", status_code=HTTP_303_SEE_OTHER)


@router.get(
    "/ask/{question_id}/notes/{number}",
    response_class=HTMLResponse,
    summary="What one note on an answer rests on",
)
async def note_page(
    request: Request,
    question_id: uuid.UUID,
    number: int,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The drawer's answer for one marker: the excerpt, the fact, or the walk on."""
    question = await ask_service.question_of(session, question_id, user_id=user.id)
    if question is None:
        return _problem(request, "No such question.")
    note = await ask_service.note_of(session, question, number)
    if note is None:
        return _problem(request, f"This answer has no note {number}.")
    if note.calculation_id:
        return RedirectResponse(
            f"/calculations/{note.calculation_id}", status_code=HTTP_303_SEE_OTHER
        )
    page: Response = render(
        request,
        "ask/note.html",
        {"question": question, "note": note, "row": _row(question)},
    )
    return page


# -- Helpers ----------------------------------------------------------------------------------


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
