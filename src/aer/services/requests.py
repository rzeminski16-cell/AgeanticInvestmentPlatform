"""Creating and reading research requests.

**One validation path, two front doors.** The JSON API and the HTML form both call
:func:`create_request`, and neither validates anything itself. That is the whole point: a
form that checks its own rules eventually checks slightly different ones, and the drift
always goes the same way — the form becomes more permissive than the API, and something
invalid reaches the database through the door nobody was testing.

**Nothing is fetched here.** No ticker lookup, no exchange lookup, no market data.
Writing a request is offline, instant and free, so what is stored is exactly what the
operator typed and ``resolved`` stays false until something external confirms it. The
universe rules therefore work from the typed values alone, which makes them heuristics —
hence exclusion messages that name the rule and explain themselves rather than simply
refusing.

**A request stops being editable once a run has left something behind.** Not when a run
starts — when it produces a report or gathers evidence. Those are the two things that exist
only here, so they are the two an edit would falsify and a deletion would destroy, and each
is checked by name in :func:`immutable_reason`.

The distinction is not pedantry. The first version of this froze a request as soon as a
``jobs`` row existed, using that as a proxy for "research happened". Cancel a run before it
fetched anything and the request became permanently uneditable and undeletable, with
nothing anywhere to justify it — a dead end the operator could not get out of. A run that
gathered nothing, cited nothing and spent nothing leaves nothing an edit could falsify.

**Three ways to get rid of a request, and the difference between them is the point.**
:func:`delete_request` is the safe one and follows the line above exactly — it refuses
anything with evidence or a report behind it, which is threat T16's retention rule arriving
early in its safest form. :func:`archive_request` destroys nothing at all and therefore
refuses nothing; it is the one the list page offers, and the one an operator wanting a
tidier list actually needs. :func:`purge_request` is the destructive one, added because
"researched, therefore permanent" left the operator with no way to remove a finished run at
all. It deletes the request and everything derived from it and keeps the three things that
would be a lie to lose: the audit chain, the spend ledger and the artefacts.

Spend is deliberately not a reason to refuse any of them — since migration 0009 the
``costs`` rows outlive the request, so the month's total is unaffected by what gets deleted
and there is nothing left for a refusal to protect.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

import structlog
from sqlalchemy import ColumnElement, Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import RequestStatus
from aer.core.schemas.request import (
    FieldProblem,
    RequestLimits,
    ResearchRequestCreate,
    check_limits,
)
from aer.core.universe import Exclusion, ExclusionRule, check_universe
from aer.db.base import Base
from aer.db.models import (
    AuditEvent,
    Cost,
    FinancialFact,
    Job,
    Report,
    ResearchRequest,
    SourceDocument,
    User,
    WorkOrder,
)
from aer.errors import ConflictError, ValidationError

__all__ = [
    "archive_request",
    "count_requests",
    "create_request",
    "delete_request",
    "get_request",
    "immutable_reason",
    "limits_from",
    "list_requests",
    "purge_request",
    "removal_preview",
    "request_spend",
    "restore_request",
    "update_request",
]

_log = structlog.get_logger("aer.services.requests")

# The only foreign-key action that means "this row is part of that one" — the path the
# database would itself propagate a delete along. `SET NULL` says the row outlives its
# referent, and `RESTRICT` says only "remove me first", which constrains the *order* of a
# deletion and says nothing about who owns what. See `_owned_scopes`.
_OWNING_EDGE: Final = "CASCADE"

# The one table a purge takes that the schema does not declare as owned. A fact is pinned
# to the source document that supplied it by a `RESTRICT`, so it has no cascade path to the
# request — but the document cannot be removed while the fact is there, so leaving facts
# behind would simply make the purge fail. Named as a constant so the exception is visible
# and so a test can assert it is still the only one.
FACTS_FOLLOW_THEIR_DOCUMENT: Final = "financial_facts"

# Which input an exclusion is the operator's fault for, so a form can highlight the field
# they can actually change. Getting an "this is a fund" message next to the exchange box
# is the kind of small wrongness that makes a form feel untrustworthy.
_EXCLUSION_FIELDS: dict[ExclusionRule, str] = {
    ExclusionRule.UNSUPPORTED_EXCHANGE: "exchange",
    ExclusionRule.OTC_VENUE: "exchange",
    ExclusionRule.EXCHANGE_TRADED_FUND: "ticker",
    ExclusionRule.INVESTMENT_TRUST: "company_name",
    ExclusionRule.MICRO_CAP: "ticker",
}


def limits_from(settings: Settings, *, today: datetime | None = None) -> RequestLimits:
    """Build the validation limits from configuration and the clock.

    The clock is read here rather than inside :func:`~aer.core.schemas.request.check_limits`
    so the rule itself stays a pure function of its arguments.

    "Today" is the UTC date. Deterministic beats locally intuitive: a server-side rule
    that depends on the reader's timezone gives different answers to the same request. The
    consequence is that shortly after local midnight in a positive-offset timezone, today's
    date can still be tomorrow by UTC — which is why the rejection message states the date
    it compared against rather than only saying "in the future".
    """
    moment = today or datetime.now(UTC)
    return RequestLimits(
        today=moment.astimezone(UTC).date(),
        per_run_budget_gbp=settings.per_run_budget_gbp,
    )


# Every column an operator controls, which is exactly the set :func:`_apply` writes. Kept
# as an explicit tuple because it is what the edit audit entry diffs over, and a test
# checks it against `_apply`'s own assignments so the two cannot drift: a field missing
# here would be edited without the change ever appearing in the audit trail.
_EDITABLE_FIELDS: tuple[str, ...] = (
    "company_name",
    "ticker",
    "exchange",
    "isin",
    "as_of_date",
    "base_currency",
    "reporting_currency",
    "investment_horizon_months",
    "horizon_label",
    "analysis_mode",
    "point_in_time",
    "portfolio_context",
    "risk_tolerance",
    "liquidity_constraint_gbp",
    "esg_sensitivity",
    "focus_questions",
    "excluded_sources",
    "max_cost_gbp",
)


def _as_problem(exclusion: Exclusion) -> FieldProblem:
    return FieldProblem(
        field=_EXCLUSION_FIELDS.get(exclusion.rule, "ticker"),
        message=exclusion.message,
        code=exclusion.rule.value,
    )


def _reject(problems: Sequence[FieldProblem]) -> ValidationError:
    fields = sorted({problem.field for problem in problems})
    summary = (
        f"The research request was rejected ({len(problems)} problem"
        f"{'' if len(problems) == 1 else 's'}): {', '.join(fields)}."
    )
    return ValidationError(
        summary,
        context={"problems": [problem.as_dict() for problem in problems]},
    )


def _refuse_if_invalid(payload: ResearchRequestCreate, limits: RequestLimits) -> None:
    """Apply every contextual and universe rule, reporting all failures together.

    Shared by creation and editing rather than duplicated, so an edit can never sneak past
    a rule a creation would have been refused for. Told "wrong exchange", an operator fixes
    the exchange and resubmits only to learn it is also a fund; one round trip per rule is
    a bad way to discover something was never going to work.
    """
    problems = [
        *check_limits(payload, limits),
        *(
            _as_problem(exclusion)
            for exclusion in check_universe(
                ticker=payload.ticker,
                exchange=payload.exchange,
                company_name=payload.company_name,
            )
        ),
    ]
    if not problems:
        return

    _log.info(
        "request.rejected",
        ticker=payload.ticker,
        exchange=payload.exchange,
        problem_fields=sorted({problem.field for problem in problems}),
        problem_codes=sorted({p.code for p in problems if p.code}),
    )
    raise _reject(problems)


def _apply(request: ResearchRequest, payload: ResearchRequestCreate) -> None:
    """Write a validated payload onto a request row.

    Every field the operator controls, assigned in one place. Creation and editing share it
    so that a field added to the schema cannot end up settable at creation and silently
    ignored on edit — which would look exactly like an edit that did not save.
    """
    request.company_name = payload.company_name
    request.ticker = payload.ticker
    request.exchange = payload.exchange
    request.isin = payload.isin
    request.as_of_date = payload.as_of_date
    request.base_currency = payload.base_currency
    request.reporting_currency = payload.reporting_currency
    request.investment_horizon_months = payload.investment_horizon_months
    request.horizon_label = payload.horizon_label
    request.analysis_mode = payload.analysis_mode
    request.point_in_time = payload.point_in_time
    # mode="json" so Decimal weights land as JSON strings the database can read back
    # without a float ever being involved. The CHECK constraints on this column cast
    # the text to numeric, which a float's repr would eventually break.
    request.portfolio_context = payload.portfolio_context.model_dump(mode="json", exclude_none=True)
    request.risk_tolerance = payload.risk_tolerance.value if payload.risk_tolerance else None
    request.liquidity_constraint_gbp = payload.liquidity_constraint_gbp
    request.esg_sensitivity = payload.esg_sensitivity.value if payload.esg_sensitivity else None
    request.focus_questions = payload.focus_questions
    request.excluded_sources = payload.excluded_sources
    request.max_cost_gbp = payload.max_cost_gbp


async def _mirror_to_work_order(session: AsyncSession, request: ResearchRequest) -> None:
    """Copy the run-root columns from a mandate onto its work order.

    ADR 0068 duplicates `as_of_date`, `point_in_time`, `max_cost_gbp`, `status` and
    `archived_at` for exactly one revision, because a column cannot be dropped from a
    migration while a model still declares it. Duplicated columns diverge unless something
    stops them, and two of these are read from the work order already: the spend guard
    takes its cap from there, and the look-ahead refusal takes its date and its
    point-in-time flag from there. An edit that moved an as-of date on the mandate alone
    would leave the guard checking the old one — a silent widening of what a run may cite.

    The follow-up revision deletes this function along with the columns.
    """
    work_order = await session.get(WorkOrder, request.id)
    if work_order is None:  # pragma: no cover -- every request is created with one
        return
    work_order.as_of_date = request.as_of_date
    work_order.point_in_time = request.point_in_time
    work_order.max_cost_gbp = request.max_cost_gbp
    work_order.status = request.status
    work_order.archived_at = request.archived_at


async def create_request(
    session: AsyncSession,
    *,
    user: User,
    payload: ResearchRequestCreate,
    limits: RequestLimits,
) -> ResearchRequest:
    """Validate and persist a research request in ``DRAFT``.

    Raises:
        ValidationError: If any contextual rule or universe rule rejects it. Every
            problem is reported together — told "wrong exchange", an operator fixes the
            exchange and resubmits only to learn it is also a fund, and one round trip per
            rule is a bad way to discover something was never going to work.
    """
    _refuse_if_invalid(payload, limits)

    request = ResearchRequest(
        user_id=user.id,
        status=RequestStatus.DRAFT,
        # Nothing external has been consulted, so the identity is unverified by
        # construction. Task 8 sets this.
        resolved=False,
    )
    _apply(request, payload)

    # The run root, created first because it is the root: the request is its 1:1 detail row
    # and takes its id (ADR 0068). Everything that needs a cap — every model call in the
    # platform — walks to this row rather than to the mandate.
    work_order = WorkOrder(
        user_id=user.id,
        tool="research",
        subject_kind="company",
        as_of_date=request.as_of_date,
        point_in_time=request.point_in_time,
        max_cost_gbp=request.max_cost_gbp,
        status=RequestStatus.DRAFT,
    )
    session.add(work_order)
    await session.flush()

    request.id = work_order.id
    session.add(request)
    await session.flush()

    await _record(
        session,
        actor=str(user.id),
        event_type="request.created",
        request_id=request.id,
        payload={
            "request_id": str(request.id),
            "ticker": request.ticker,
            "exchange": request.exchange,
            "as_of_date": request.as_of_date.isoformat(),
            "analysis_mode": request.analysis_mode.value,
            "point_in_time": request.point_in_time,
            "max_cost_gbp": str(request.max_cost_gbp),
        },
    )

    _log.info(
        "request.created",
        request_id=str(request.id),
        ticker=request.ticker,
        exchange=request.exchange,
        analysis_mode=request.analysis_mode.value,
    )
    return request


async def immutable_reason(session: AsyncSession, *, request: ResearchRequest) -> str | None:
    """Why this request can no longer be changed, or ``None`` if it still can.

    Returned as prose rather than a boolean because both callers need the sentence: the API
    puts it in the problem detail, and the detail page puts it where the edit button would
    otherwise be. "Editing is disabled" with no reason is the kind of answer that sends
    someone to the source code.

    **What freezes a request is what a run left behind, not that a run existed.** The first
    version of this used "a job row exists" as a proxy for "research happened", and the
    proxy is wrong at exactly the boundary that matters: cancel a run before it fetches
    anything and the request became permanently uneditable and undeletable, with nothing
    anywhere to justify it. A job that gathered nothing, cited nothing and spent nothing
    leaves nothing an edit could falsify.

    So each condition below is a *specific* thing an edit or a deletion would damage, and
    each says which. They are checked in order of how badly, so the message names the most
    serious one rather than the first one queried.
    """
    latest = await session.scalar(
        select(Job)
        .where(Job.work_order_id == request.id)
        .order_by(Job.started_at.desc().nullslast())
    )

    if latest is None:
        # Nothing downstream can exist without a run, so there is nothing else to ask.
        return _not_a_draft(request)

    if not latest.status.is_terminal:
        return (
            f"A run is {latest.status.value.lower().replace('_', ' ')} for this request. "
            "Wait for it to finish, or cancel it, before changing anything — a worker may "
            "be reading these values right now."
        )

    for statement, message in _what_a_run_left_behind(request):
        if await _exists(session, statement):
            return message

    return _not_a_draft(request)


def _what_a_run_left_behind(
    request: ResearchRequest,
) -> tuple[tuple[Select[tuple[uuid.UUID]], str], ...]:
    """Each durable thing a run can produce that an edit or a deletion would damage.

    Two, not five. **Spend and approvals used to be here and should not have been**, and
    the reason each was removed is worth stating because it is the same reason:

    * **Spend** blocked deletion only because ``costs`` cascaded away with the request. The
      defect was the cascade, not the deletion — a monthly cap you can get under by
      deleting what you spent it on is not a cap. Migration 0009 makes those references
      ``SET NULL``, so the ledger now survives and has nothing to be protected from.
    * **Approvals** are recorded in the audit chain by
      :func:`aer.services.approvals.record_decision`, with the payload hash of exactly what
      was shown, and the chain outlives the request by design. The ``approvals`` table is a
      convenient index over that record, not the record itself.

    What remains are the two things that exist *only* here: a report, and the provenance of
    evidence that was gathered. Ordered by severity, so the operator is told the most
    serious reason rather than whichever query happened to run first.
    """
    return (
        (
            select(Report.id).where(Report.request_id == request.id),
            "A report has been produced from this request, so it can no longer be changed "
            "or removed. The report cites the terms it was researched under; editing them "
            "now would not correct the record, it would falsify it. Create a new request "
            "instead.",
        ),
        (
            select(SourceDocument.id).where(SourceDocument.work_order_id == request.id),
            "Evidence has been gathered against this request. The as-of date and "
            "point-in-time setting are what admitted that evidence, so changing them now "
            "would leave the stored sources inconsistent with the rules that selected them "
            "— and deleting the request would throw away the provenance of bytes that are "
            "still on disk. Create a new request instead.",
        ),
    )


def _not_a_draft(request: ResearchRequest) -> str | None:
    """The backstop, for a request moved out of ``DRAFT`` by something other than a run.

    Nothing does that today — starting a run leaves the status alone — so this is a guard
    against a future change silently re-opening editing rather than a condition currently
    reachable.
    """
    if request.status is RequestStatus.DRAFT:
        return None
    return f"This request is {request.status.value}, not a draft, so it can no longer be changed."


async def _exists(session: AsyncSession, statement: Select[tuple[uuid.UUID]]) -> bool:
    return await session.scalar(statement.limit(1)) is not None


async def _refuse_if_immutable(
    session: AsyncSession, *, request: ResearchRequest, verb: str
) -> None:
    reason = await immutable_reason(session, request=request)
    if reason is None:
        return
    raise ConflictError(
        f"This request cannot be {verb}. {reason}",
        context={"request_id": str(request.id), "status": request.status.value},
    )


async def update_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
    payload: ResearchRequestCreate,
    limits: RequestLimits,
) -> ResearchRequest:
    """Replace a draft request's contents with a re-validated payload.

    A whole-payload replace, not a patch. The form submits every field it renders, and a
    partial update would need its own rule for what an absent field means — "leave it" and
    "clear it" are both defensible, which is exactly why neither should have to be guessed.

    Raises:
        ConflictError: If a run has been started, or the request has left ``DRAFT``.
        ValidationError: If the new contents fail any rule a new request would fail.
    """
    await _refuse_if_immutable(session, request=request, verb="edited")
    _refuse_if_invalid(payload, limits)

    before = _snapshot(request)
    _apply(request, payload)
    after = _snapshot(request)
    changes = {
        name: [before[name], after[name]]
        for name in _EDITABLE_FIELDS
        if before[name] != after[name]
    }

    if {"ticker", "exchange", "isin"} & changes.keys():
        # The identity changed, so whatever confirmed the old one confirmed something else.
        # Leaving `resolved` true here would be a claim about a security nobody looked up.
        request.resolved = False

    await _mirror_to_work_order(session, request)
    await session.flush()

    await _record(
        session,
        actor=str(actor.id),
        event_type="request.edited",
        request_id=request.id,
        payload={
            "request_id": str(request.id),
            # Before and after, not just the field names: "as_of_date changed" is a fact
            # you cannot act on months later, and the row itself only remembers the after.
            "changes": changes,
        },
    )

    _log.info(
        "request.edited",
        request_id=str(request.id),
        ticker=request.ticker,
        changed_fields=sorted(changes),
    )
    return request


async def delete_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
) -> None:
    """Delete a draft request that has never been run.

    The guard is the whole point. A request that produced a report, or gathered evidence,
    has something attached that exists nowhere else, and the ORM cascade would take it — so
    this refuses rather than relying on an operator not to ask. Nothing here can delete
    anything that was researched; that needs an explicit retention policy, which is Phase 6
    work.

    **Spend is not destroyed and therefore does not block.** Since migration 0009 the
    ``costs`` rows survive with their references nulled, so the month's total is unaffected
    by what is deleted. The audit entry below records what those now-orphaned rows were
    spent on, which keeps them attributable: ``audit_events`` carries ``request_id`` as a
    plain column with no foreign key, precisely so a record survives the thing it describes.

    Raises:
        ConflictError: If a run is still live, or a report or evidence exists.
    """
    await _refuse_if_immutable(session, request=request, verb="deleted")

    request_id = request.id
    spent = await _spend_on(session, request_id=request_id)
    await _record(
        session,
        actor=str(actor.id),
        event_type="request.deleted",
        request_id=request_id,
        payload={
            "request_id": str(request_id),
            # Enough to say what was removed without keeping the row. A deletion whose log
            # entry is just an id answers "was something deleted" and nothing else.
            "ticker": request.ticker,
            "exchange": request.exchange,
            "company_name": request.company_name,
            "as_of_date": request.as_of_date.isoformat(),
            "status": request.status.value,
            # The cost rows outlive this deletion but lose their job reference. Without
            # this line the money would still be counted and no longer explicable.
            "spend_gbp": str(spent),
        },
    )

    await session.delete(request)
    await session.flush()

    _log.info(
        "request.deleted", request_id=str(request_id), actor=str(actor.id), spend_gbp=str(spent)
    )


# ==========================================================================================
# Getting a request out of the way, and — separately — destroying it
# ==========================================================================================
#
# Two verbs, deliberately far apart in what they cost to get wrong. **Archiving is the one
# an operator should reach for**, and it is the one the list page offers: it hides the row,
# keeps every byte, and is undone by clicking again. Purging is the other one, and there is
# no version of it that can be undone.


async def archive_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
) -> ResearchRequest:
    """Put a request out of the way without destroying anything.

    **No immutability check, and that is the point.** Everything :func:`immutable_reason`
    protects — the report, the provenance of gathered evidence — is exactly as intact after
    this as before it, because nothing is removed. A finished run is in fact the *usual*
    thing to archive; refusing it would leave the operator with the problem this exists to
    solve.

    Raises:
        ConflictError: If it is already archived. A second archive is not a second event,
            and recording it as one would put a false date on the first.
    """
    if request.archived_at is not None:
        # `.day` rather than a `%-d` format: the no-leading-zero codes are a glibc
        # extension, and on Windows strftime raises for them — which turned this refusal
        # into a 500 on the first machine that was not Linux.
        message = (
            f"This request was archived on "
            f"{request.archived_at.day} {request.archived_at:%B %Y}. Restore it "
            "first if you meant to change that."
        )
        raise ConflictError(message, context={"request_id": str(request.id)})

    request.archived_at = datetime.now(UTC)
    await _mirror_to_work_order(session, request)
    await _record(
        session,
        actor=str(actor.id),
        event_type="request.archived",
        request_id=request.id,
        payload={"request_id": str(request.id), "ticker": request.ticker},
    )
    await session.flush()

    _log.info("request.archived", request_id=str(request.id), actor=str(actor.id))
    return request


async def restore_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
) -> ResearchRequest:
    """Put an archived request back on the list.

    Raises:
        ConflictError: If it was not archived.
    """
    if request.archived_at is None:
        message = "This request is not archived, so there is nothing to restore."
        raise ConflictError(message, context={"request_id": str(request.id)})

    request.archived_at = None
    await _mirror_to_work_order(session, request)
    await _record(
        session,
        actor=str(actor.id),
        event_type="request.restored",
        request_id=request.id,
        payload={"request_id": str(request.id), "ticker": request.ticker},
    )
    await session.flush()

    _log.info("request.restored", request_id=str(request.id), actor=str(actor.id))
    return request


async def removal_preview(session: AsyncSession, *, request: ResearchRequest) -> dict[str, int]:
    """How many rows a purge would delete, by table, excluding the empty ones.

    Shown in the confirmation before anything happens. A destructive action whose dialog
    says "are you sure?" and nothing else is asking the operator to confirm a number they
    have not been told.
    """
    counts: dict[str, int] = {}
    for name, scope in _owned_scopes(request.id):
        found = await session.scalar(select(func.count()).select_from(scope.subquery()))
        if found:
            counts[name] = int(found)
    return counts


async def purge_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
) -> dict[str, int]:
    """Delete a request and everything derived from it. Returns what was removed.

    **This is the destructive one**, and unlike :func:`delete_request` it does not refuse a
    request that has been researched — refusing was the whole complaint. What it keeps
    instead is everything that would be a lie to lose:

    * **The audit chain.** ``audit_events`` carries ``request_id`` as a plain column with
      no foreign key, precisely so the record of a deletion survives the thing deleted.
    * **The spend.** ``costs`` references its job with ``SET NULL`` since migration 0009,
      so the month's total is unchanged. A monthly cap you can get under by deleting what
      you spent it on is not a cap.
    * **The artefacts.** Content-addressed and shared between runs, so they are never a
      request's to destroy. ``aer gc-artefacts`` collects the ones nothing points at.

    The order comes from SQLAlchemy's own dependency sort, reversed, so a ``RESTRICT``
    reference — a citation pinning its extraction, a claim pinning its calculation — is
    never asked to break. If one cannot be honoured this fails saying which table, rather
    than steamrollering it.

    Raises:
        ConflictError: If a run is still live, or if something outside this request holds a
            ``RESTRICT`` reference to evidence inside it.
    """
    latest = await session.scalar(
        select(Job)
        .where(Job.work_order_id == request.id)
        .order_by(Job.started_at.desc().nullslast())
    )
    if latest is not None and not latest.status.is_terminal:
        message = (
            f"A run is {latest.status.value.lower().replace('_', ' ')} for this request. "
            "Cancel it first — deleting what a worker is still writing to would fail "
            "halfway and leave the request in pieces."
        )
        raise ConflictError(message, context={"request_id": str(request.id)})

    await _refuse_if_others_depend_on_it(session, request=request)

    request_id = request.id
    spent = await _spend_on(session, request_id=request_id)
    removed = await removal_preview(session, request=request)

    await _record(
        session,
        actor=str(actor.id),
        event_type="request.purged",
        request_id=request_id,
        payload={
            "request_id": str(request_id),
            "ticker": request.ticker,
            "exchange": request.exchange,
            "company_name": request.company_name,
            "as_of_date": request.as_of_date.isoformat(),
            "status": request.status.value,
            # The cost rows outlive this and lose their job reference. Without this line
            # the money would still be counted and no longer explicable.
            "spend_gbp": str(spent),
            # What went, by table. "A request was deleted" is not an answer to "where did
            # those four hundred facts go?"
            "removed": removed,
        },
    )

    for name, scope in _owned_scopes(request_id):
        table = Base.metadata.tables[name]
        await session.execute(delete(table).where(table.c.id.in_(scope)))

    await session.delete(request)
    await session.flush()

    _log.warning(
        "request.purged",
        request_id=str(request_id),
        actor=str(actor.id),
        spend_gbp=str(spent),
        removed=removed,
    )
    return removed


async def _refuse_if_others_depend_on_it(
    session: AsyncSession, *, request: ResearchRequest
) -> None:
    """Refuse a purge that another request's research is resting on.

    **Facts are shared and the schema knows it.** They are deduplicated on an observation
    key that deliberately excludes the source document, so the second run of a company
    inserts none of them and reads the first run's — through the first run's source
    document, pinned by a ``RESTRICT``. Purging that first request would take facts a
    surviving report cites.

    The database would refuse anyway, which is the point of ``RESTRICT``; this exists so
    the refusal arrives as a sentence naming the table rather than as a foreign-key
    violation from inside a half-finished transaction, with the session already aborted and
    the page unable to say what went wrong.

    Checked over every ``RESTRICT`` edge rather than the fact table alone, so the guard
    keeps up with a schema that grows one.
    """
    scopes = dict(_owned_scopes(request.id))
    blocked: list[str] = []

    for referring in Base.metadata.sorted_tables:
        for key in sorted(referring.foreign_keys, key=lambda fk: fk.parent.name):
            target = key.column.table.name
            if key.ondelete != "RESTRICT" or target not in scopes:
                continue
            outsiders = (
                select(func.count()).select_from(referring).where(key.parent.in_(scopes[target]))
            )
            if referring.name in scopes:
                # Its own rows go too, so only rows belonging to some *other* request are
                # a reason to stop.
                outsiders = outsiders.where(referring.c.id.not_in(scopes[referring.name]))
            if await session.scalar(outsiders):
                blocked.append(f"{referring.name} → {target}")

    if not blocked:
        return

    message = (
        "Another request's research depends on evidence gathered by this one, so removing "
        f"it would break that: {', '.join(sorted(set(blocked)))}. This is what happens when "
        "the same company has been researched twice — the later run reads the earlier run's "
        "facts rather than storing a second copy. Remove the other request first, or "
        "archive this one instead."
    )
    raise ConflictError(message, context={"request_id": str(request.id), "blocked": blocked})


def _owned_scopes(request_id: uuid.UUID) -> tuple[tuple[str, Select[Any]], ...]:
    """Every table a purge may empty for one request, in the order it must be emptied.

    Walked from the foreign keys rather than listed, for the reason
    :func:`aer.cli._research_tables` gives: a hand-written list that has gone stale is how
    a "deleted" request keeps half its rows.

    **Ownership follows ``CASCADE`` and only ``CASCADE``**, because that is the schema
    saying "this row is part of that one" — it is the path the database would itself
    propagate a delete along. The other two actions say something different and neither
    means ownership:

    * ``SET NULL`` says the row outlives its referent. Four tables reach a request only
      this way and each would be a real loss: ``costs`` (the spend ledger, nulled to its
      job since migration 0009), ``price_bars`` and ``corporate_actions`` (per security,
      shared by every request that looked at it), and ``macro_observations`` (a vintage
      belongs to nobody's run).
    * ``RESTRICT`` says "remove me before you remove that", which is a constraint on the
      order of deletion and not a statement about who owns what. Following it was this
      walk's first bug: ``claims.financial_fact_id`` is a ``RESTRICT`` reference, so a
      *later* request's claim that happened to cite this request's fact was pulled into
      the scope and silently deleted with it.

    ``financial_facts`` is the single exception, and it is a policy decision rather than
    something the schema declares. A fact has no ``CASCADE`` path to a request — it is
    pinned to the source document that supplied it by a ``RESTRICT``, so the document
    cannot go while the fact remains. Deleting the request's research and leaving its facts
    behind would fail on that pin, so the facts go too, scoped through the documents this
    request acquired. ``TestOnlyOneTableIsAnException`` fails if the schema ever grows a
    second table in this position, so the exception cannot quietly become a list.
    """
    # One forward pass over the dependency sort, which puts every parent before its
    # children — so by the time a table is reached, the scope of everything it references
    # has already been built and can simply be nested as a subquery.
    scopes: dict[str, Select[Any]] = {
        "research_requests": select(ResearchRequest.id).where(ResearchRequest.id == request_id)
    }
    for table in Base.metadata.sorted_tables:
        if table.name in scopes:
            continue
        owning = [
            fk.parent.in_(scopes[fk.column.table.name])
            for fk in sorted(table.foreign_keys, key=lambda fk: fk.parent.name)
            if fk.ondelete == _OWNING_EDGE and fk.column.table.name in scopes
        ]
        if table.name == FACTS_FOLLOW_THEIR_DOCUMENT:
            owning.append(FinancialFact.source_document_id.in_(scopes["source_documents"]))
        if owning:
            scopes[table.name] = select(table.c.id).where(or_(*owning))

    return tuple(
        (table.name, scopes[table.name])
        for table in reversed(Base.metadata.sorted_tables)
        if table.name in scopes and table.name != "research_requests"
    )


async def request_spend(session: AsyncSession, *, request: ResearchRequest) -> Decimal:
    """What this request's runs have cost so far.

    Public because the removal confirmation shows it: money already spent is the one thing
    a deletion cannot give back, and an operator deciding whether to destroy a run should
    see what it cost to produce.
    """
    return await _spend_on(session, request_id=request.id)


async def _spend_on(session: AsyncSession, *, request_id: uuid.UUID) -> Decimal:
    """What this request's runs have cost, read before its jobs are deleted."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0))
        .join(Job, Job.id == Cost.job_id)
        .where(Job.work_order_id == request_id)
    )
    return Decimal(str(total or 0))


async def get_request(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
) -> ResearchRequest | None:
    """Fetch one request belonging to ``user_id``, or ``None``.

    Scoped by owner even though the MVP has a single user. An unscoped lookup is a
    horizontal access-control bug the day a second user exists, and it is invisible until
    then — so the filter goes in now, while there is no way for it to be wrong.
    """
    found: ResearchRequest | None = await session.scalar(
        select(ResearchRequest).where(
            ResearchRequest.id == request_id,
            ResearchRequest.user_id == user_id,
        )
    )
    return found


async def list_requests(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    archived: bool = False,
) -> Sequence[ResearchRequest]:
    """Most recent first, live by default.

    ``archived`` selects *only* the archived ones rather than adding them to the live
    list. Archiving is for getting something out of the way, and a filter that quietly
    kept it in view would not be archiving.
    """
    result = await session.scalars(
        select(ResearchRequest)
        .where(ResearchRequest.user_id == user_id, _archived_filter(archived))
        .order_by(ResearchRequest.created_at.desc(), ResearchRequest.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.all()


async def count_requests(
    session: AsyncSession, *, user_id: uuid.UUID, archived: bool = False
) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(ResearchRequest)
        .where(ResearchRequest.user_id == user_id, _archived_filter(archived))
    )
    return int(total or 0)


def _archived_filter(archived: bool) -> ColumnElement[bool]:
    column = ResearchRequest.archived_at
    return column.is_not(None) if archived else column.is_(None)


# -- Audit and diffing -----------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    request_id: uuid.UUID,
) -> None:
    """Append one link to the audit chain.

    Always via :meth:`AuditEvent.create_linked`, and always reading the current tail first:
    a hand-built row with a hand-written hash is how a chain silently stops verifying.
    """
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            request_id=request_id,
        )
    )
    await session.flush()


def _jsonable(value: Any) -> Any:
    """Render a column value for the audit payload without going through ``float``.

    ``Decimal`` becomes a string, never a number. JSON has no decimal type, so serialising
    £2.50 as a number is a round trip through binary floating point — and a monetary
    ceiling that comes back as 2.4999999999999996 in the audit trail is worse than useless.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _snapshot(request: ResearchRequest) -> dict[str, Any]:
    """The operator-controlled fields, in a form the audit payload can hold."""
    return {name: _jsonable(getattr(request, name)) for name in _EDITABLE_FIELDS}
