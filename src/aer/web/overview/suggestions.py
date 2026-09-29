"""Band 2 of Today — *worth doing* — where every card is earned by a condition in the record.

Page specification §1.2: a suggestion with no condition is a defect, not a default, and if
nothing qualifies the band is absent entirely. Five conditions:

| Suggestion | The condition that earns it |
|---|---|
| Review a completed refresh | a refresh run finished and ``changes_read_at`` is still null |
| Research a watched company | followed, no current report, nothing commissioned in 30 days |
| Write a thesis | a held position with no thesis |
| Review closed decisions | a position closed more than 30 days ago with no review |
| Look at your concentration | the five largest near or past the top-five ceiling you set |

The fifth reads a limit the operator stated (ADR 0136) and nothing else: with no top-five
ceiling stated there is nothing to be close to, and no threshold is invented to earn it.

Each suggestion carries the moment its condition was met, so the page can show the four
oldest and say how many more there are.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.dates import spoken_date
from aer.core.enums import JobStatus
from aer.db.models import Company, Job, Portfolio, Report, User, WorkOrder
from aer.render import display
from aer.services import company_record as record_service
from aer.services import limits as limit_service
from aer.services import portfolio as portfolio_service
from aer.services import post_trade
from aer.services import refresh as refresh_service
from aer.services import watchlist as watchlist_service
from aer.services.calculations import new_context
from aer.services.performance import exposure_as_at

__all__ = ["SHOWN", "Suggestion", "suggestions_for"]

# How many the band shows; the rest are counted in one line beneath (§1.2).
SHOWN: Final = 4

# The two thirty-day windows the specification names.
QUIET_FOR: Final = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One card: what to do, why the record says so, and where to do it."""

    kind: str
    title: str
    justification: str
    condition_met_at: datetime
    action_href: str
    action_label: str

    @property
    def key(self) -> str:
        return f"{self.kind}.{self.action_href}"


async def suggestions_for(
    session: AsyncSession, *, user: User, now: datetime | None = None
) -> tuple[Suggestion, ...]:
    """Every earned suggestion, oldest condition first."""
    moment = now or datetime.now(UTC)
    collected: list[Suggestion] = []
    collected.extend(await _refreshes_unread(session, user=user))
    collected.extend(await _watched_and_unresearched(session, user=user, now=moment))
    records = await record_service.records_for(session, user=user, now=moment)
    collected.extend(await _held_without_a_thesis(session, user=user, records=records))
    collected.extend(await _closed_and_unreviewed(session, user=user, now=moment))
    collected.extend(await _close_to_the_ceiling(session, user=user))
    return tuple(sorted(collected, key=lambda row: row.condition_met_at))


async def _close_to_the_ceiling(session: AsyncSession, *, user: User) -> list[Suggestion]:
    """The five largest within two points of the operator's own top-five ceiling, or past it.

    Dated from when the ceiling was stated: the earliest the condition could have been met,
    since nothing records the day the book first came within reach of it. The figure is the
    risk page's own (ADR 0136 §4), struck in a ledger this page drops.
    """
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return []
    ceiling = (await limit_service.limits_of(session, portfolio=book)).five_largest
    if ceiling is None:
        return []
    as_of = await portfolio_service.latest_close(session, portfolio=book)
    ledger = new_context()
    view = await portfolio_service.book_as_at(session, ledger, portfolio=book, as_of=as_of)
    exposure = await exposure_as_at(session, ledger, portfolio=book, as_of=as_of, view=view)
    top = exposure.top_holdings.value if exposure.top_holdings is not None else None
    over = limit_service.is_over(top, ceiling)
    if top is None or not (over or limit_service.is_near(top, ceiling)):
        return []
    share = display.percentage(top, in_table=True)
    where = "past" if over else "within two points of"
    return [
        Suggestion(
            kind="concentration",
            title="Look at your concentration",
            justification=(
                f"Your five largest positions are {share} of the book, {where} "
                f"{limit_service.ceiling_words(ceiling)}."
            ),
            condition_met_at=ceiling.stated_at,
            action_href="/risk#exposure",
            action_label="Open the risk page",
        )
    ]


async def _refreshes_unread(session: AsyncSession, *, user: User) -> list[Suggestion]:
    """A refresh that finished and whose change summary nobody has opened (ADR 0131)."""
    runs = await session.scalars(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(
            WorkOrder.user_id == user.id,
            Job.refresh_kind == refresh_service.REFRESH,
            Job.status == JobStatus.SUCCEEDED,
            Job.changes_read_at.is_(None),
            Job.finished_at.is_not(None),
        )
        .order_by(Job.finished_at)
    )
    found: list[Suggestion] = []
    for job in runs:
        produced = await session.scalar(select(Report).where(Report.job_id == job.id))
        prior = (
            await session.get(Report, job.refreshes_report_id)
            if job.refreshes_report_id is not None
            else None
        )
        named = await _company_name(session, produced or prior)
        finished = job.finished_at
        assert finished is not None
        found.append(
            Suggestion(
                kind="refresh",
                title=f"Review the {named} refresh",
                justification=(
                    f"A refresh completed on {spoken_date(finished)} and its summary of what "
                    "changed has not been read."
                ),
                condition_met_at=finished,
                action_href=f"/reports/{produced.id}"
                if produced is not None
                else f"/runs/{job.id}",
                action_label="Read what changed",
            )
        )
    return found


async def _company_name(session: AsyncSession, report: Report | None) -> str:
    if report is None:
        return "company"
    company = (
        await session.get(Company, report.company_id) if report.company_id is not None else None
    )
    return company.name if company is not None else "company"


async def _watched_and_unresearched(
    session: AsyncSession, *, user: User, now: datetime
) -> list[Suggestion]:
    """Followed, with no current report and nothing commissioned in the last thirty days."""
    found: list[Suggestion] = []
    for state in await watchlist_service.queue_for(session, user_id=user.id):
        entry = state.entry
        latest = max(
            (row.commissioned_at for row in entry.commissions if row.commissioned_at),
            default=None,
        )
        quiet_since = max(entry.followed_at, latest + QUIET_FOR) if latest else entry.followed_at
        if latest is not None and now - latest < QUIET_FOR:
            continue
        found.append(
            Suggestion(
                kind="research",
                title=f"Research {entry.company_name}",
                justification=(
                    f"Followed on {spoken_date(entry.followed_at)} with no report, and nothing "
                    "commissioned in the last thirty days."
                ),
                condition_met_at=quiet_since,
                action_href="/watchlist",
                action_label="Commission research",
            )
        )
    return found


async def _held_without_a_thesis(
    session: AsyncSession, *, user: User, records: list[record_service.CompanyRecord]
) -> list[Suggestion]:
    """A held position with no thesis, dated from when the position was opened."""
    unwritten = [row for row in records if row.is_held and row.thesis_state == "none"]
    if not unwritten:
        return []
    opened: dict[uuid.UUID, datetime] = {}
    book = await session.scalar(
        select(Portfolio)
        .where(Portfolio.user_id == user.id, Portfolio.archived_at.is_(None))
        .order_by(Portfolio.created_at)
        .limit(1)
    )
    if book is not None:
        positions = await post_trade.positions_of(session, portfolio=book)
        for held in positions.held:
            if held.security.company_id is not None:
                opened[held.security.company_id] = datetime.combine(
                    held.opened_on, datetime.min.time(), tzinfo=UTC
                )
    return [
        Suggestion(
            kind="thesis",
            title=f"Write a thesis for {row.name}",
            justification=f"{row.name} is held and nothing is written down about why.",
            condition_met_at=opened.get(
                row.company.id if row.company is not None else uuid.uuid4(),
                datetime.now(UTC),
            ),
            action_href=f"/theses?company={row.company.id}" if row.company else "/theses",
            action_label="Write a thesis",
        )
        for row in unwritten
    ]


async def _closed_and_unreviewed(
    session: AsyncSession, *, user: User, now: datetime
) -> list[Suggestion]:
    """A position closed more than thirty days ago that nobody has reviewed — and nobody
    has deferred to a date still to come (F14). A lapsed deferral is due from its date."""
    books = await session.scalars(
        select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.archived_at.is_(None))
    )
    found: list[Suggestion] = []
    for book in books:
        for state in await post_trade.states_for(session, portfolio=book, today=now.date()):
            if state.state != "unreviewed":
                continue
            closed = datetime.combine(state.episode.closed_on, datetime.min.time(), tzinfo=UTC)
            due = closed + QUIET_FOR
            lapsed = ""
            if state.has_lapsed and state.deferral is not None:
                review_by = datetime.combine(
                    state.deferral.review_by, datetime.min.time(), tzinfo=UTC
                )
                due = max(due, review_by)
                deferred_to = spoken_date(state.deferral.review_by)
                lapsed = f" You deferred it to {deferred_to}, which has passed."
            if now < due:
                continue
            found.append(
                Suggestion(
                    kind="review",
                    title=f"Review the {state.episode.security.ticker} position",
                    justification=(
                        f"Closed on {spoken_date(state.episode.closed_on)}, more than thirty days "
                        f"ago, and not yet scored against the process it was meant to follow."
                        f"{lapsed}"
                    ),
                    condition_met_at=due,
                    action_href="/review",
                    action_label="Review it",
                )
            )
    return found
