"""The limits the operator stated on their book, and whether a figure is over one (ADR 0136).

**The operator's statements, and only theirs.** A limit is written here when the operator
states one on Platform, under *Book*, and nowhere else: no default, no suggestion, no example
value, and nothing a model or a skill file returns can reach :func:`state_limit`, whose only
caller is the operator's own form.

**A limit blocks nothing.** It is not consulted by the ledger, the decision service, the
research run or the pre-trade check's arithmetic; it changes what the operator's own pages say
about the figures those already strike. The comparison is :func:`is_over`, over the recorded
weight, five-largest share or sector share the risk page prints — a comparison made in code,
never a new figure.

**Superseded, never edited.** :func:`state_limit` writes a new row that supersedes the limit
in force of the same kind (and sector), and :func:`withdraw_limit` records when and why. What
the operator allowed themselves when they decided something is part of the record a review of
that decision reads, so no row is ever changed but to withdraw it.

**Never on anything that leaves the machine** (ADR 0136 §5): the report's closing section does
not read this module, and no export does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from aer.core.enums import LimitKind
from aer.db.models import AuditEvent, BookLimit, Portfolio, User
from aer.errors import ConflictError, ValidationError
from aer.services.performance import ExposureView
from aer.services.portfolio import CLOSED, PortfolioView

__all__ = [
    "KIND_WORDS",
    "NEAR",
    "Limits",
    "Standing",
    "ceiling_words",
    "history_of",
    "is_near",
    "is_over",
    "limit_percent",
    "limits_of",
    "parse_percent",
    "standings",
    "state_limit",
    "withdraw_limit",
]

_log = structlog.get_logger("aer.services.limits")

# What each kind is called on a page, in the drawing's words.
KIND_WORDS: Final[dict[LimitKind, str]] = {
    LimitKind.SINGLE_POSITION: "Single position ceiling",
    LimitKind.FIVE_LARGEST: "Top-five ceiling",
    LimitKind.SECTOR: "Sector ceiling",
}

# How close to a limit counts as close: two percentage points, the distance Today's fifth
# suggestion names (ADR 0136 §2; information architecture §5, "within 2 points").
NEAR: Final = Decimal("0.02")

_HUNDRED: Final = Decimal(100)
_PLACES: Final = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Limits:
    """The limits in force on one book: at most one of each kind, and one per sector."""

    single_position: BookLimit | None = None
    five_largest: BookLimit | None = None
    sectors: dict[str, BookLimit] = field(default_factory=dict)

    @property
    def stated(self) -> bool:
        return bool(self.single_position or self.five_largest or self.sectors)

    def for_sector(self, sector: str) -> BookLimit | None:
        return self.sectors.get(sector)

    def all(self) -> tuple[BookLimit, ...]:
        rows = [row for row in (self.single_position, self.five_largest) if row is not None]
        return (*rows, *(self.sectors[name] for name in sorted(self.sectors)))


# -- The comparison ------------------------------------------------------------------------------


def is_over(share: Decimal | None, limit: BookLimit | None) -> bool:
    """Whether a share of the book is above the operator's limit. Never with no limit."""
    return share is not None and limit is not None and share > limit.fraction


def is_near(share: Decimal | None, limit: BookLimit | None) -> bool:
    """Whether a share is within :data:`NEAR` of the limit without being over it."""
    if share is None or limit is None or share > limit.fraction:
        return False
    return limit.fraction - share <= NEAR


def limit_percent(limit: BookLimit) -> str:
    """The limit as the operator stated it — *10%*, *12.5%* — with no zeros it never typed."""
    scaled = (limit.fraction * _HUNDRED).normalize()
    return f"{scaled:f}%"


def ceiling_words(limit: BookLimit) -> str:
    """*the 10% ceiling you set* — the words every page uses beside the figure."""
    return f"the {limit_percent(limit)} ceiling you set"


def parse_percent(raw: str) -> Decimal:
    """A limit typed as a percentage — *10*, *12.5%* — as the fraction the row stores.

    Raises:
        ValidationError: If it is not a number, or not a share of the book.
    """
    cleaned = raw.strip().removesuffix("%").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as refused:
        message = f"{raw.strip() or 'Nothing'} is not a percentage. Type a number such as 10."
        raise ValidationError(message, context={"field": "percent"}) from refused
    if not value.is_finite() or value <= 0 or value > _HUNDRED:
        message = (
            "A limit is a share of the book: more than nothing and no more than all of it, so "
            "a number above 0 and at most 100."
        )
        raise ValidationError(message, context={"field": "percent"})
    return (value / _HUNDRED).quantize(_PLACES)


@dataclass(frozen=True, slots=True)
class Standing:
    """One limit in force beside the figure it caps, as the book stands.

    ``share`` is the recorded figure itself — the largest holding's weight, the five largest
    together, the sector's share — or ``None`` where the book gives none. ``over`` names the
    holdings above a single-position ceiling; a figure a whole-book limit caps has no one to
    name.
    """

    limit: BookLimit
    share: Decimal | None
    over: tuple[str, ...] = ()

    @property
    def is_over(self) -> bool:
        return bool(self.over) or is_over(self.share, self.limit)

    @property
    def is_near(self) -> bool:
        return not self.is_over and is_near(self.share, self.limit)


def standings(
    limits: Limits, *, book: PortfolioView, exposure: ExposureView
) -> tuple[Standing, ...]:
    """Every limit in force against the figures the risk page strikes for the same book.

    One comparison for every page that draws a limit (ADR 0136 §4): the weights are the
    book's, the five largest and the sector shares the exposure view's, so a page cannot
    compare a limit with a figure no other page prints.
    """
    held = [
        row
        for row in book.holdings
        if row.problem != CLOSED and row.weight is not None and row.value is not None
    ]
    rows: list[Standing] = []
    if limits.single_position is not None:
        limit = limits.single_position
        largest = max((row.weight.value for row in held if row.weight is not None), default=None)
        rows.append(
            Standing(
                limit=limit,
                share=largest,
                over=tuple(
                    row.security.ticker
                    for row in held
                    if row.weight is not None and row.weight.value > limit.fraction
                ),
            )
        )
    if limits.five_largest is not None:
        top = exposure.top_holdings
        rows.append(
            Standing(limit=limits.five_largest, share=top.value if top is not None else None)
        )
    sectors = next((band for band in exposure.bands if band.kind == "sector"), None)
    shares = {row.label: row.share.value for row in sectors.slices} if sectors else {}
    for name in sorted(limits.sectors):
        rows.append(Standing(limit=limits.sectors[name], share=shares.get(name, Decimal(0))))
    return tuple(rows)


# -- Reading -------------------------------------------------------------------------------------


async def limits_of(session: AsyncSession, *, portfolio: Portfolio) -> Limits:
    """The limits in force on a book: stated, not withdrawn, and not superseded."""
    successor = aliased(BookLimit)
    rows = await session.scalars(
        select(BookLimit)
        .where(
            BookLimit.portfolio_id == portfolio.id,
            BookLimit.user_id == portfolio.user_id,
            BookLimit.withdrawn_at.is_(None),
            ~select(successor.id).where(successor.supersedes_id == BookLimit.id).exists(),
        )
        .order_by(BookLimit.stated_at)
    )
    single: BookLimit | None = None
    largest: BookLimit | None = None
    sectors: dict[str, BookLimit] = {}
    for row in rows:
        if row.kind is LimitKind.SINGLE_POSITION:
            single = row
        elif row.kind is LimitKind.FIVE_LARGEST:
            largest = row
        elif row.sector is not None:
            sectors[row.sector] = row
    return Limits(single_position=single, five_largest=largest, sectors=sectors)


async def history_of(session: AsyncSession, *, portfolio: Portfolio) -> list[BookLimit]:
    """Every limit ever stated on the book, newest first — the ones replaced included."""
    rows = await session.scalars(
        select(BookLimit)
        .where(BookLimit.portfolio_id == portfolio.id, BookLimit.user_id == portfolio.user_id)
        .order_by(BookLimit.stated_at.desc(), BookLimit.id.desc())
    )
    return list(rows)


def _in_force(limits: Limits, kind: LimitKind, sector: str | None) -> BookLimit | None:
    if kind is LimitKind.SINGLE_POSITION:
        return limits.single_position
    if kind is LimitKind.FIVE_LARGEST:
        return limits.five_largest
    return limits.for_sector(sector or "")


# -- Writing -------------------------------------------------------------------------------------


async def state_limit(
    session: AsyncSession,
    *,
    portfolio: Portfolio,
    actor: User,
    kind: LimitKind,
    fraction: Decimal,
    sector: str | None = None,
) -> BookLimit:
    """A limit the operator states, superseding the one of its kind in force.

    Raises:
        ConflictError: If the book is not this person's, or the limit is the one in force.
        ValidationError: If the fraction is not a share of the book, or a sector is named
            for a kind that has none, or none for the kind that needs one.
    """
    if portfolio.user_id != actor.id:
        message = "A limit is stated by the person whose book it limits."
        raise ConflictError(message, context={"portfolio_id": str(portfolio.id)})
    if not fraction.is_finite() or fraction <= 0 or fraction > 1:
        message = "A limit is a share of the book: more than nothing and no more than all of it."
        raise ValidationError(message, context={"field": "fraction"})
    named = (sector or "").strip()
    if kind is LimitKind.SECTOR and not named:
        message = "A sector ceiling names the sector it caps."
        raise ValidationError(message, context={"field": "sector"})
    if kind is not LimitKind.SECTOR and named:
        message = f"A {KIND_WORDS[kind].lower()} caps the whole book; it names no sector."
        raise ValidationError(message, context={"field": "sector"})
    fraction = fraction.quantize(_PLACES)

    current = _in_force(await limits_of(session, portfolio=portfolio), kind, named or None)
    if current is not None and current.fraction == fraction:
        message = f"{limit_percent(current)} is already the {KIND_WORDS[kind].lower()} in force."
        raise ConflictError(message, context={"limit_id": str(current.id)})

    limit = BookLimit(
        portfolio_id=portfolio.id,
        user_id=actor.id,
        kind=kind,
        sector=named or None,
        fraction=fraction,
        stated_by=actor.email,
        supersedes_id=current.id if current is not None else None,
    )
    session.add(limit)
    await session.flush()
    await session.refresh(limit)
    await _record(
        session,
        actor=actor.email,
        event_type="limit.stated",
        portfolio_id=portfolio.id,
        payload={
            "limit_id": str(limit.id),
            "kind": kind.value,
            "sector": limit.sector,
            "fraction": str(limit.fraction),
            "supersedes_id": str(current.id) if current is not None else None,
        },
    )
    _log.info("limit.stated", portfolio=str(portfolio.id), kind=kind.value)
    return limit


async def withdraw_limit(
    session: AsyncSession, *, portfolio: Portfolio, limit: BookLimit, actor: User, reason: str
) -> BookLimit:
    """The operator no longer holds themselves to a limit, and says why. The row stays.

    Raises:
        ConflictError: If the limit is not on this person's book, or is not in force.
        ValidationError: If the reason is blank.
    """
    if portfolio.user_id != actor.id or limit.portfolio_id != portfolio.id:
        message = "A limit is withdrawn by the person whose book it limits."
        raise ConflictError(message, context={"limit_id": str(limit.id)})
    in_force = {row.id for row in (await limits_of(session, portfolio=portfolio)).all()}
    if limit.id not in in_force:
        message = "This limit is not in force: it was withdrawn or replaced, and that stands."
        raise ConflictError(message, context={"limit_id": str(limit.id)})
    if not reason.strip():
        message = (
            "Withdrawing a limit needs a reason. What you stopped holding yourself to, and why, "
            "is what a later review of a decision made without it asks first."
        )
        raise ValidationError(message, context={"field": "reason"})
    limit.withdrawn_at = datetime.now(UTC)
    limit.withdrawn_reason = reason.strip()
    await session.flush()
    await _record(
        session,
        actor=actor.email,
        event_type="limit.withdrawn",
        portfolio_id=portfolio.id,
        payload={"limit_id": str(limit.id), "reason": limit.withdrawn_reason},
    )
    return limit


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    portfolio_id: uuid.UUID,
) -> None:
    """One link on the audit chain, with the book as its subject."""
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            job_id=None,
            subject_kind="portfolio",
            subject_id=portfolio_id,
        )
    )
    await session.flush()
