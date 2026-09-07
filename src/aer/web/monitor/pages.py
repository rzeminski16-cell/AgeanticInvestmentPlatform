"""What has happened since a thesis was written that bears on it, and what you did about it.

Two screens and four forms. The list is every open finding, grouped into the ones that
opened a gate and the ones that did not, with the reviews a person promised and the passes
that ran; the detail is one finding in full — the premise, what code measured, what the
model read into it, the documents it names — with the forms that close it.

**A finding is labelled a finding, everywhere on both pages** (ADR 0078). The list is not an
inbox of approvals, and the words on it say so: a contradicted premise is *a decision
waiting*, everything else is *a question raised*. The one form that records an approval is
the gate on a contradicted finding, and it asks what to do about the premise rather than
whether to approve anything.

**Nothing here is a figure.** The observation code made is rendered as a sentence beside
the calculation or fact it came from; it enters no arithmetic on this page and no page
after it. And nothing here reads a price (ADR 0079).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.core.enums import Decision, FindingAction, FindingKind, GateKind
from aer.db.models import Finding, SourceDocument, Thesis
from aer.errors import AerError
from aer.queue import enqueue_monitor
from aer.services import theses as thesis_service
from aer.services import thesis_monitor
from aer.services.approvals import payload_hash_for
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.gates import CONSEQUENCES
from aer.web.templating import render

__all__ = ["FindingRow", "ObservedFigures", "ThesisGroup", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.monitor")

# What the two buttons on the gate say. The decision values are the approvals table's; the
# words are what the operator is actually choosing between.
DECISION_WITHDRAW: Final = Decision.APPROVED
DECISION_KEEP: Final = Decision.REJECTED


@dataclass(frozen=True, slots=True)
class ResolutionRow:
    action: str
    reason: str
    actor: str
    at: str


@dataclass(frozen=True, slots=True)
class FindingRow:
    """One finding as either page shows it."""

    id: uuid.UUID
    thesis_id: uuid.UUID
    thesis_title: str
    premise: str
    label: str
    tone: str
    detail: str
    justification: str
    observed: str
    raised_on: str
    opens_gate: bool
    gate_is_decidable: bool
    is_open: bool
    is_stopped: bool
    has_premise: bool
    resolutions: tuple[ResolutionRow, ...]
    source_document_ids: tuple[str, ...]

    @property
    def last_resolution(self) -> ResolutionRow | None:
        return self.resolutions[-1] if self.resolutions else None


@dataclass(frozen=True, slots=True)
class ThesisGroup:
    """One thesis and the open findings on it, as the list shows them together.

    A thesis with three findings reads as one card with three lines rather than three rows
    that repeat its title; the thesis is the thing the reader holds a view on, and the
    findings are what happened to it.
    """

    thesis_id: uuid.UUID
    thesis_title: str
    subject: str
    findings: tuple[FindingRow, ...]


@dataclass(frozen=True, slots=True)
class ObservedFigures:
    """What code measured, as two figures a reader compares at a glance.

    The value against the threshold with the period beneath, and the verdict on the predicate
    as a chip. Every string arrives already rendered from the observation the pass recorded;
    nothing here is computed, and the sentence beside them says the same thing in words.
    """

    metric: str
    value: str
    period: str
    prior: str
    calculation_href: str
    threshold: str
    verdict_label: str
    verdict_tone: str


async def _grouped(session: Any, findings: list[Finding]) -> list[ThesisGroup]:
    """The findings by thesis, in the order the theses first appear, each named once."""
    by_thesis: dict[uuid.UUID, list[FindingRow]] = {}
    theses: dict[uuid.UUID, Thesis] = {}
    for finding in findings:
        by_thesis.setdefault(finding.thesis_id, []).append(_row(finding))
        theses[finding.thesis_id] = finding.thesis
    return [
        ThesisGroup(
            thesis_id=thesis_id,
            thesis_title=theses[thesis_id].title,
            subject=await thesis_service.subject_name(session, theses[thesis_id]),
            findings=tuple(rows),
        )
        for thesis_id, rows in by_thesis.items()
    ]


def _observed_figures(observed: dict[str, Any] | None) -> ObservedFigures | None:
    if not observed:
        return None
    unit = observed.get("unit") or "ratio"
    threshold_unit = observed.get("threshold_unit") or unit
    holds = bool(observed.get("holds"))
    prior = ""
    if observed.get("prior_value"):
        prior = (
            f"was {observed['prior_value']} {unit} for the period ending "
            f"{observed.get('prior_period_end', '')}"
        )
    calculation_id = observed.get("calculation_id")
    return ObservedFigures(
        metric=str(observed.get("metric") or "the metric"),
        value=f"{observed.get('value')} {unit}",
        period=f"for the period ending {observed.get('period_end')}",
        prior=prior,
        calculation_href=f"/calculations/{calculation_id}" if calculation_id else "",
        threshold=f"{observed.get('comparator')} {observed.get('threshold')} {threshold_unit}",
        verdict_label="The predicate holds" if holds else "The predicate does not hold",
        verdict_tone=vocabulary.Tone.SUCCESS.value if holds else vocabulary.Tone.WARNING.value,
    )


def _row(finding: Finding) -> FindingRow:
    if finding.kind is FindingKind.STOPPED or finding.status is None:
        words = vocabulary.STOPPED_PASS
    else:
        words = vocabulary.PREMISE_STATES[finding.status]
    return FindingRow(
        id=finding.id,
        thesis_id=finding.thesis_id,
        thesis_title=finding.thesis.title,
        premise=finding.premise.statement if finding.premise is not None else "",
        label=words.label,
        tone=words.tone.value,
        detail=words.detail,
        justification=finding.justification,
        observed=_observed_sentence(finding.observed),
        raised_on=f"{finding.created_at:%d %B %Y}",
        opens_gate=finding.opens_gate,
        gate_is_decidable=finding.gate_is_decidable,
        is_open=finding.is_open,
        is_stopped=finding.kind is FindingKind.STOPPED,
        has_premise=finding.premise is not None,
        resolutions=tuple(
            ResolutionRow(
                action=row.action.value,
                reason=row.reason,
                actor=row.actor,
                at=f"{row.resolved_at:%d %B %Y}",
            )
            for row in finding.resolutions
        ),
        source_document_ids=tuple(str(ref) for ref in finding.source_document_ids),
    )


def _observed_sentence(observed: dict[str, Any] | None) -> str:
    """What code measured, as one sentence a reader can check against the calculation."""
    if not observed:
        return ""
    unit = observed.get("unit") or "ratio"
    threshold_unit = observed.get("threshold_unit") or unit
    holds = "holds" if observed.get("holds") else "does not hold"
    prior = ""
    if observed.get("prior_value"):
        prior = (
            f", against {observed['prior_value']} {unit} for the period ending "
            f"{observed.get('prior_period_end', '')}"
        )
    return (
        f"{observed.get('metric', 'the metric')} measured {observed.get('value')} {unit} for "
        f"the period ending {observed.get('period_end')}{prior}. The predicate — "
        f"{observed.get('comparator')} {observed.get('threshold')} {threshold_unit} — {holds}."
    )


def _monitor_verdict(
    gated: list[ThesisGroup], unread: list[ThesisGroup], reviews: int, theses: int
) -> verdicts.Verdict:
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(
            sum(len(group.findings) for group in gated),
            "premise was contradicted and is waiting for your decision",
            "premises were contradicted and are waiting for your decision",
        ),
        verdicts.Count(
            sum(len(group.findings) for group in unread),
            "finding is raised and not yet acted on",
            "findings are raised and not yet acted on",
        ),
        verdicts.Count(
            reviews, "premise is due for your review", "premises are due for your review"
        ),
    ]
    if not theses:
        when_none = "No open thesis to monitor. Write one, and give a premise a threshold."
    else:
        when_none = (
            f"Nothing is waiting. The monitor reads {theses} open "
            f"{'thesis' if theses == 1 else 'theses'} against each new filing."
        )
    tone = vocabulary.Tone.WARNING if gated else vocabulary.Tone.INFO
    return verdicts.sentence(clauses, when_none=when_none, tone=tone)


# -- The list -------------------------------------------------------------------------------


@router.get("/monitor", response_class=HTMLResponse, summary="Monitor")
async def monitor_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Every open finding, the reviews due, the recent passes, and the button that runs one."""
    showing_resolved = request.query_params.get("resolved") == "1"
    opened, closed = await thesis_monitor.findings_partitioned(session, user_id=user.id)
    gated = await _grouped(session, [row for row in opened if row.opens_gate])
    unread = await _grouped(session, [row for row in opened if not row.opens_gate])
    resolved = [_row(finding) for finding in closed] if showing_resolved else []
    today = datetime.now(UTC).date()
    due = await thesis_monitor.reviews_due(session, user_id=user.id, today=today)
    theses = await thesis_monitor.theses_to_monitor(session, user_id=user.id)
    passes = await thesis_monitor.recent_passes(session, user_id=user.id)
    titles = {thesis.id: thesis.title for thesis in theses}

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "monitor/index.html",
        {
            "verdict": _monitor_verdict(gated, unread, len(due), len(theses)),
            "gated": gated,
            "unread": unread,
            "resolved": resolved,
            "showing_resolved": showing_resolved,
            "reviews": [
                {
                    "thesis_id": thesis.id,
                    "thesis_title": thesis.title,
                    "judgement_id": premise.judgement_id,
                    "statement": premise.statement,
                    "review_by": f"{premise.review_by:%d %B %Y}" if premise.review_by else "",
                }
                for thesis, premise in due
            ],
            "passes": [
                {
                    "job_id": row.job.id,
                    "thesis_title": titles.get(row.thesis_id, "a retired thesis"),
                    "status": vocabulary.JOB_STATES[row.job.status].label
                    if row.job.status in vocabulary.JOB_STATES
                    else row.job.status.value,
                    "started": f"{row.job.started_at:%d %B %Y}" if row.job.started_at else "",
                    "findings": row.findings,
                    "cost": figures.pounds(row.job.total_cost_gbp),
                }
                for row in passes
            ],
            "theses_to_monitor": len(theses),
            "queued": request.query_params.get("queued", ""),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/monitor/run", summary="Run the monitor")
async def run_monitor(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    redis: RedisClient,
) -> Response:
    """Queue one pass per open thesis. The worker reads them; this page spends nothing."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was queued.")

    theses = await thesis_monitor.theses_to_monitor(session, user_id=user.id)
    queued = 0
    for thesis in theses:
        if await enqueue_monitor(redis, thesis.id) is not None:
            queued += 1
    _log.info("monitor.queued", theses=len(theses), queued=queued)
    outcome = str(queued) if queued == len(theses) else f"{queued}of{len(theses)}"
    return RedirectResponse(f"/monitor?queued={outcome}", status_code=HTTP_303_SEE_OTHER)


# -- One finding ------------------------------------------------------------------------------


@router.get("/monitor/findings/{finding_id}", response_class=HTMLResponse, summary="A finding")
async def finding_page(
    finding_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    finding = await thesis_monitor.finding_of(session, finding_id, user_id=user.id)
    if finding is None:
        return _problem(request, "No such finding.")

    row = _row(finding)
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "monitor/finding.html",
        {
            "item": row,
            "subject": await _subject(session, finding),
            "measured": _observed_figures(finding.observed),
            "sources": await _sources(session, finding),
            "gate_words": vocabulary.GATES[GateKind.THESIS],
            "gate_consequence": CONSEQUENCES[GateKind.THESIS],
            "payload_hash": payload_hash_for(thesis_monitor.finding_payload(finding)),
            "decision_withdraw": DECISION_WITHDRAW.value,
            "decision_keep": DECISION_KEEP.value,
            "action_dismissed": FindingAction.DISMISSED.value,
            "action_withdrawn": FindingAction.WITHDRAWN.value,
            "action_reopened": FindingAction.REOPENED.value,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/monitor/findings/{finding_id}/decide", summary="Decide the thesis gate")
async def decide_finding(
    finding_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    finding = await thesis_monitor.finding_of(session, finding_id, user_id=user.id)
    if finding is None:
        return _problem(request, "No such finding.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was decided.")

    try:
        decision = Decision(submitted.get("decision", ""))
        await thesis_monitor.decide_finding(
            session,
            finding=finding,
            actor=user,
            decision=decision,
            reason=submitted.get("reason", ""),
            payload_hash=submitted.get("payload_hash", ""),
        )
        await session.commit()
    except ValueError:
        await session.rollback()
        return _problem(request, "That is not one of the two answers the gate offers.", status=400)
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/monitor/findings/{finding.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/monitor/findings/{finding_id}/resolve", summary="Act on a finding")
async def resolve_finding(
    finding_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    finding = await thesis_monitor.finding_of(session, finding_id, user_id=user.id)
    if finding is None:
        return _problem(request, "No such finding.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was recorded.")

    try:
        action = FindingAction(submitted.get("action", ""))
        await thesis_monitor.resolve_finding(
            session,
            finding=finding,
            actor=user,
            action=action,
            reason=submitted.get("reason", ""),
        )
        await session.commit()
    except ValueError:
        await session.rollback()
        return _problem(request, "That is not something that can be done to a finding.", status=400)
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/monitor/findings/{finding.id}", status_code=HTTP_303_SEE_OTHER)


# -- Reading ----------------------------------------------------------------------------------


async def _subject(session: Any, finding: Finding) -> str:
    return await thesis_service.subject_name(session, finding.thesis)


async def _sources(session: Any, finding: Finding) -> list[dict[str, str]]:
    """The documents the justification names, by title where the store has one.

    An id the store no longer holds is listed as the id: the justification named it, and a
    row that silently dropped it would make the finding rest on less than it said.
    """
    wanted: list[uuid.UUID] = []
    for ref in finding.source_document_ids:
        try:
            wanted.append(uuid.UUID(str(ref)))
        except ValueError:
            continue
    held = {
        str(row.id): row
        for row in await session.scalars(
            select(SourceDocument).where(SourceDocument.id.in_(wanted))
        )
    }
    listed: list[dict[str, str]] = []
    for ref in finding.source_document_ids:
        document = held.get(str(ref))
        listed.append(
            {
                "id": str(ref),
                "title": (document.title or document.url) if document is not None else str(ref),
                # No page shows one source document on its own; the title is the answer.
                "href": "",
            }
        )
    return listed


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
