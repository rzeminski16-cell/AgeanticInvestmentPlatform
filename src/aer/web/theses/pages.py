"""What you believe about a company, written down, with what would defeat it.

Two screens and five forms. The list is every open thesis, with a form to write a new one;
the detail is one thesis and its premises, with forms to add a premise, withdraw one, and
retire the whole thing. Nothing on either page is a figure. A thesis is a document a
person wrote (ADR 0074), so the page renders prose, dates and names, and the one number a
premise may carry — its threshold — is shown beside the metric it tests and enters no
arithmetic anywhere.

**A premise says what would defeat it, or who will look again.** The add-premise form
asks the question ADR 0079 settles: a threshold code can test against a stored fact, or a
date by which a person reviews it. The form never invents the first for a premise that
only has the second, and a premise rendered as "reviewed by a person" is styled no lower
than one "tested by a threshold" — the unquantifiable premises are the ones that decide
whether a position works.

**Nothing here is regulated investment advice**, and the shell says so on every page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.enums import PremiseComparator
from aer.db.models import Company, Premise, Thesis
from aer.errors import AerError
from aer.services import theses as thesis_service
from aer.web import verdict as verdicts
from aer.web import vocabulary
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["COMPARATOR_LABELS", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.theses")

# What a comparator is called on the screen: the service's words, so the thesis page and
# the predicate the monitor reads out say the same thing.
COMPARATOR_LABELS: Final[dict[PremiseComparator, str]] = thesis_service.COMPARATOR_WORDS

# How the form asks what would defeat a premise. Two answers, named for what each is.
DEFEAT_THRESHOLD: Final = "threshold"
DEFEAT_REVIEW: Final = "review"


@dataclass(frozen=True, slots=True)
class PremiseRow:
    """One premise as the detail page shows it."""

    judgement_id: uuid.UUID
    position: int
    statement: str
    basis: str
    held_by: str
    held_on: str
    defeated_by: str
    is_tested: bool
    is_withdrawn: bool
    withdrawn_on: str
    withdrawn_reason: str


def _row(premise: Premise) -> PremiseRow:
    judgement = premise.judgement
    if premise.has_predicate and premise.comparator is not None:
        defeated_by = (
            f"{premise.metric} {COMPARATOR_LABELS[premise.comparator]} "
            f"{_plain(premise.threshold)} {premise.unit}"
        )
    else:
        defeated_by = f"A person reviews it by {premise.review_by:%d %B %Y}"
    return PremiseRow(
        judgement_id=premise.judgement_id,
        position=premise.position,
        statement=premise.statement,
        basis=judgement.basis,
        held_by=judgement.held_by,
        held_on=f"{judgement.held_at:%d %B %Y}",
        defeated_by=defeated_by,
        is_tested=premise.has_predicate,
        is_withdrawn=judgement.is_withdrawn,
        withdrawn_on=f"{judgement.withdrawn_at:%d %B %Y}" if judgement.withdrawn_at else "",
        withdrawn_reason=judgement.withdrawn_reason or "",
    )


def _plain(value: Decimal | None) -> str:
    """A threshold as typed, with the trailing zeros a NUMERIC(38, 12) round-trip adds gone."""
    if value is None:
        return ""
    trimmed = value.normalize()
    return f"{trimmed:f}"


def _thesis_verdict(thesis: Thesis) -> verdicts.Verdict:
    """The sentence the thesis leads with, composed from what its premises actually are."""
    held = [row for row in thesis.premises if not row.judgement.is_withdrawn]
    withdrawn = len(thesis.premises) - len(held)
    if thesis.is_retired:
        return verdicts.sentence(
            [f"retired on {thesis.retired_at:%d %B %Y}", thesis.retirement_reason or ""],
            when_none="Retired",
            tone=vocabulary.Tone.MUTED,
        )
    clauses: list[str] = []
    if held:
        tested = sum(1 for row in held if row.has_predicate)
        clauses.append(f"{len(held)} premise{'s' if len(held) != 1 else ''} held")
        clauses.append(f"{tested} tested by a threshold, {len(held) - tested} reviewed by a person")
    elif withdrawn:
        # Every premise given up is not the same state as none ever written: the first is
        # a thesis that has been argued with, and the sentence must not call it empty.
        clauses.append("no premise is currently held")
    else:
        clauses.append("nothing is asserted yet — add the first premise below")
    if withdrawn:
        clauses.append(f"{withdrawn} withdrawn, with the reason kept")
    return verdicts.sentence(
        clauses,
        when_none="Nothing is asserted yet",
        tone=vocabulary.Tone.INFO if held else vocabulary.Tone.MUTED,
    )


# -- The list --------------------------------------------------------------------------------


@router.get("/theses", response_class=HTMLResponse, summary="Theses")
async def theses_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Every open thesis, and the form to write one."""
    retired = request.query_params.get("retired") == "1"
    rows = await thesis_service.theses_for(session, user_id=user.id, retired=retired)
    companies = await thesis_service.companies_to_write_about(session)
    named = [
        {
            "thesis": thesis,
            "subject": await thesis_service.subject_name(session, thesis),
            "held": sum(1 for row in thesis.premises if not row.judgement.is_withdrawn),
        }
        for thesis in rows
    ]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "theses/index.html",
        {
            "rows": named,
            "showing_retired": retired,
            "companies": [
                {"value": str(company.id), "label": f"{company.name} ({company.ticker})"}
                for company in companies
            ],
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/theses", summary="Write a thesis")
async def write_thesis(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was written.")

    company = await _company(session, submitted.get("company_id", ""))
    if company is None:
        return _problem(
            request,
            "That company is not one the platform can resolve. A thesis is about a company "
            "the research tool has looked up, not about a ticker somebody typed.",
        )

    try:
        thesis = await thesis_service.write_thesis(
            session,
            user=user,
            company=company,
            title=submitted.get("title", ""),
            written_at=_date_at(submitted.get("written_on", "")),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


# -- One thesis ------------------------------------------------------------------------------


@router.get("/theses/{thesis_id}", response_class=HTMLResponse, summary="A thesis")
async def thesis_page(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "theses/detail.html",
        {
            "item": thesis,
            "subject": await thesis_service.subject_name(session, thesis),
            "premises": [_row(premise) for premise in thesis.premises],
            "verdict": _thesis_verdict(thesis),
            "comparators": [
                {"value": member.value, "label": label}
                for member, label in COMPARATOR_LABELS.items()
            ],
            "defeat_threshold": DEFEAT_THRESHOLD,
            "defeat_review": DEFEAT_REVIEW,
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/theses/{thesis_id}/premises", summary="Add a premise")
async def add_premise(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was added.")

    try:
        predicate, review_by = _what_defeats_it(submitted)
        await thesis_service.add_premise(
            session,
            thesis=thesis,
            actor=user,
            statement=submitted.get("statement", ""),
            basis=submitted.get("basis", ""),
            predicate=predicate,
            review_by=review_by,
            held_at=_date_at(submitted.get("held_on", "")),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    except (ValueError, InvalidOperation) as malformed:
        await session.rollback()
        return _problem(request, f"That premise could not be recorded: {malformed}", status=400)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/theses/{thesis_id}/premises/{judgement_id}/withdraw", summary="Withdraw a premise")
async def withdraw_premise(  # noqa: PLR0917 -- two path parameters and the four every handler takes
    thesis_id: uuid.UUID,
    judgement_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")
    premise = await thesis_service.premise_of(session, judgement_id, thesis=thesis)
    if premise is None:
        return _problem(request, "No such premise on this thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was withdrawn.")

    try:
        await thesis_service.withdraw_premise(
            session, premise=premise, actor=user, reason=submitted.get("reason", "")
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/theses/{thesis_id}/retire", summary="Retire a thesis")
async def retire_thesis(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was retired.")

    try:
        await thesis_service.retire_thesis(
            session, thesis=thesis, actor=user, reason=submitted.get("reason", "")
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


# -- Reading the form ------------------------------------------------------------------------


def _what_defeats_it(
    submitted: dict[str, str],
) -> tuple[thesis_service.Predicate | None, date | None]:
    """The form's answer to ADR 0079's question, as the two columns it may fill.

    The choice is a radio the operator made, and the other branch's fields are ignored
    rather than merged: a review date typed beside a threshold is a premise with two
    answers, and the one the operator chose is the one that counts.
    """
    chosen = submitted.get("defeated_by", DEFEAT_REVIEW)
    if chosen == DEFEAT_THRESHOLD:
        raw = submitted.get("threshold", "").replace(",", "").strip()
        if not raw:
            message = "a threshold needs a number to compare against"
            raise ValueError(message)
        predicate = thesis_service.Predicate(
            metric=submitted.get("metric", ""),
            comparator=PremiseComparator(submitted.get("comparator", "")),
            threshold=Decimal(raw),
            unit=submitted.get("unit", ""),
        )
        return predicate, None
    raw_date = submitted.get("review_by", "").strip()
    return None, date.fromisoformat(raw_date) if raw_date else None


def _date_at(raw: str) -> datetime | None:
    """A date the operator typed, as the start of that day, or ``None`` for today."""
    if not raw.strip():
        return None
    return datetime.combine(date.fromisoformat(raw.strip()), datetime.min.time(), tzinfo=UTC)


async def _company(session: Any, raw: str) -> Company | None:
    try:
        identifier = uuid.UUID(raw.strip())
    except ValueError:
        return None
    found: Company | None = await session.get(Company, identifier)
    return found


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
