"""The numbers beside a nav item, and why none of them is on the render path.

A badge count is a query whose cost belongs to the tool that owns it, rendered into a
shell every other tool shares. Computed inline, one slow count in a future tool would make
every page in the product slow — and it would do so invisibly, because nothing about a
sidebar suggests it is doing work. So a count is never computed while a page is being
built: the nav renders an empty slot, and `GET /_shell/badges` fills it afterwards.

Providers are registered in the idiom `agents/registry.py` settled and
`workflow/registry.py` reuses: frozen rows, a lazy ``"module:function"`` reference so
asking a registry question does not drag a tool's services underneath the shell, and an
``adr`` field that refuses to be empty (ADR 0071).

**A badge counts rows. It never carries a figure.** ADR 0077 forbids JavaScript owning a
figure, and the same reasoning bounds what a badge may say: money and ratios go through
`render/display.py` and a house style, in a KPI tile a reader can trace. A count of things
waiting for them is a hint about where to look, and a hint may be a few seconds stale.

**A provider that fails leaves its slot empty rather than showing nothing or showing
zero.** Zero would be a lie — "nothing is waiting" is a claim — and blanking the whole
fragment would let one tool's broken query hide another tool's real count. The key is
simply absent from the result, the slot keeps whatever it had, and the failure is logged.

``OSError`` is in that list because asyncpg raises the operating system's error directly
when it cannot reach the server — a bare `ConnectionRefusedError`, before SQLAlchemy has
anything to wrap. Without it this fragment answered 500 on every load of the landing page,
which is the one page in the product designed to render with Postgres down: a page that
degrades gracefully and logs an unhandled exception each time it does.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

import structlog
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from aer.errors import AerError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "Badge",
    "BadgeProvider",
    "BadgeProviderError",
    "badges_from",
    "cached_counts_for",
    "counts_for",
    "registered_badges",
    "resolve_badge",
]

_log = structlog.get_logger("aer.web.shell.badges")


class BadgeProviderError(AerError):
    """A provider is inconsistent with itself, or names something the code has lost.

    Two providers claiming one key, a provider with no ADR behind it, or a reference that
    no longer resolves. All three mean the sidebar would render a number whose origin
    nobody can name, which is the state this registry exists to refuse.
    """

    code = "badge_provider"


class BadgeCount(Protocol):
    """What a ``count_ref`` must resolve to."""

    async def __call__(self, session: AsyncSession, *, user_id: uuid.UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class Badge:
    """One filled slot: what the number is, and what it means.

    ``label`` is not decoration. A bare numeral beside a word is read as "Requests 3" by a
    screen reader and means nothing; the label is what turns it into a sentence. ``title``
    is the same count named rather than spoken, for a surface that has room for a heading —
    the Overview screen renders every registered badge as a tile, from this one row, so
    that the sidebar and the dashboard cannot disagree about a number.
    """

    key: str
    count: int
    label: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class BadgeProvider:
    """One tool's answer to "how many, and of what".

    ``key`` is the string a `NavItem.badge_key` names. The two are joined by a test rather
    than by an import, because the nav registry already imports this module's siblings and
    a cycle here would be paid for on every page.
    """

    key: str
    tool: str
    title: str
    label: str
    count_ref: str
    adr: str

    def count_fn(self) -> BadgeCount:
        """The registered counter, resolved from its reference.

        Raises:
            BadgeProviderError: If the reference no longer resolves. A registry naming a
                function the code lost fails the way `RoleDefinition.output_schema` does —
                loudly, at the moment of asking, rather than as a slot that never fills.
        """
        module_name, _, attribute = self.count_ref.partition(":")
        try:
            resolved = getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as exc:
            message = (
                f"The badge {self.key!r} registers the counter {self.count_ref!r}, which "
                f"does not resolve: {exc}"
            )
            raise BadgeProviderError(
                message, context={"badge": self.key, "ref": self.count_ref}
            ) from exc
        if not callable(resolved):
            message = (
                f"The badge {self.key!r} registers {self.count_ref!r}, which resolves to "
                "something that cannot be called."
            )
            raise BadgeProviderError(message, context={"badge": self.key, "ref": self.count_ref})
        return resolved  # type: ignore[no-any-return]


# One row per counted thing. A tool contributes its own and nothing here changes.
_PROVIDERS: Final[tuple[BadgeProvider, ...]] = (
    BadgeProvider(
        key="approvals",
        tool="research",
        title="Waiting for you",
        # Read aloud after the number: "3 runs waiting for your approval". Phrased as the
        # operator's own decision because that is what the count is for — these runs are
        # stopped, and only a person can start them again.
        label="runs waiting for your approval",
        count_ref="aer.services.runs:awaiting_approval_count",
        adr="0071",
    ),
)


def _build(providers: Sequence[BadgeProvider] = _PROVIDERS) -> dict[str, BadgeProvider]:
    built: dict[str, BadgeProvider] = {}
    for provider in providers:
        if not provider.adr.strip():
            message = (
                f"The badge {provider.key!r} has no ADR reference. A count in the shell is "
                "a claim one tool makes on every other tool's pages, and ADR 0071 puts the "
                "record before the row."
            )
            raise BadgeProviderError(message, context={"badge": provider.key})
        if provider.key in built:
            message = (
                f"Two providers claim the badge {provider.key!r}. One slot cannot hold two "
                "counts, and which one won would depend on declaration order."
            )
            raise BadgeProviderError(message, context={"badge": provider.key})
        built[provider.key] = provider
    return built


_REGISTRY: Final[dict[str, BadgeProvider]] = _build()


def resolve_badge(key: str) -> BadgeProvider:
    """The provider for a key.

    Raises:
        BadgeProviderError: If nothing defines it. There is deliberately no default: a
            slot with no provider is a number the sidebar would invent.
    """
    found = _REGISTRY.get(key)
    if found is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        message = f"No badge provider named {key!r} is registered. Registered badges: {known}."
        raise BadgeProviderError(message, context={"badge": key, "registered": sorted(_REGISTRY)})
    return found


def registered_badges() -> tuple[BadgeProvider, ...]:
    """Every provider, in key order so the fragment renders the same way twice."""
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


async def counts_for(session: AsyncSession, *, user_id: uuid.UUID) -> tuple[Badge, ...]:
    """Every badge this operator has a number for.

    Sequential rather than gathered: these are counts against one session, and an
    `AsyncSession` is not safe to use concurrently — a `gather` here would interleave
    statements on one connection and fail in a way that reads as a database problem.
    """
    filled: list[Badge] = []
    for provider in registered_badges():
        try:
            count = await provider.count_fn()(session, user_id=user_id)
        except (AerError, SQLAlchemyError, OSError) as failure:
            # Deliberately not fatal, and deliberately not zero. See the module docstring.
            _log.warning(
                "badge.count_failed",
                badge=provider.key,
                tool=provider.tool,
                error=str(failure),
            )
            continue
        filled.append(_badge(provider, count))
    return tuple(filled)


def badges_from(cached: Mapping[str, Any]) -> tuple[Badge, ...]:
    """Rebuild badges from a cache payload, dropping anything the registry no longer knows.

    A cached count for a badge that has since been removed would render into a slot no
    template declares; a cached key whose value is not a whole number would render as
    whatever it happens to be. Both are dropped rather than trusted, because the cache is
    the one input here that the current code did not produce.
    """
    rebuilt: list[Badge] = []
    for provider in registered_badges():
        value = cached.get(provider.key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        rebuilt.append(_badge(provider, value))
    return tuple(rebuilt)


def _badge(provider: BadgeProvider, count: int) -> Badge:
    return Badge(key=provider.key, count=count, label=provider.label, title=provider.title)


# Short enough that acting on a badge and watching it change feels like cause and effect,
# long enough that a page-heavy minute costs one round of counts rather than twenty. The
# staleness is deliberate and is the reason a badge may only be a hint: approve a gate and
# the sidebar can be up to this many seconds behind, while the page behind it never is.
CACHE_TTL_SECONDS: Final = 10

_CACHE_PREFIX: Final = "aer:shell:badges:"


async def cached_counts_for(
    redis: Any, session: AsyncSession, *, user_id: uuid.UUID
) -> tuple[Badge, ...]:
    """`counts_for`, with the answer remembered for a few seconds per operator.

    **Best effort in both directions.** A cache miss costs the queries; a cache that is
    down costs the queries and a log line. Neither may cost the page, because the sidebar
    is chrome and Redis being unavailable is already visible where it matters — on the
    pages that queue work.
    """
    key = f"{_CACHE_PREFIX}{user_id}"
    try:
        raw = await redis.get(key)
    except RedisError as failure:
        _log.warning("badge.cache_unavailable", error=str(failure))
        raw = None

    if raw is not None:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            # A payload this code did not write, or wrote in an older shape. Recomputing is
            # always correct; trusting it is not.
            stored = None
        if isinstance(stored, dict):
            return badges_from(stored)

    badges = await counts_for(session, user_id=user_id)
    try:
        await redis.set(
            key,
            json.dumps({badge.key: badge.count for badge in badges}),
            ex=CACHE_TTL_SECONDS,
        )
    except RedisError as failure:
        _log.warning("badge.cache_write_failed", error=str(failure))
    return badges
