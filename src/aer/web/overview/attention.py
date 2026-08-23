"""What is waiting for the operator, contributed by whichever tool it is waiting in.

The Overview screen is not a page about research runs that happens to be first in the nav.
It is the one surface that has to work when there are several tools, and the shape that
makes that true is this: **Overview owns no query.** It asks a registry what is waiting,
each tool answers for its own rows, and adding a tool adds a row here rather than a branch
in a template.

Written in the idiom `web/shell/badges.py` settled one slice earlier, and for the same
reasons: frozen rows, a lazy ``"module:function"`` reference so asking the screen a
question does not drag every tool's services underneath it, and an ``adr`` that refuses to
be empty (ADR 0071).

**A provider that fails is reported, not swallowed.** This is the difference from a badge,
and it is the whole point of the distinction. A badge is a hint, so losing one costs a
number nobody was relying on. This feed is the operator's answer to "is anything waiting
for me", and a provider that failed silently would answer "no" — the one answer that must
never be guessed. A failure becomes an item in the feed saying so.

``OSError`` is caught alongside the database errors because asyncpg raises the operating
system's error directly when it cannot reach the server, before SQLAlchemy has anything to
wrap — so a provider that cannot connect is a provider that failed, and says so, rather
than an unhandled exception.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol

import structlog
from sqlalchemy.exc import SQLAlchemyError

from aer.errors import AerError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "Attention",
    "AttentionProvider",
    "AttentionProviderError",
    "Severity",
    "items_for",
    "registered_providers",
]

_log = structlog.get_logger("aer.web.overview.attention")


class Severity(StrEnum):
    """How much of the operator's day this item is entitled to.

    Three, and the boundaries are decisions rather than shades. ``BLOCKED`` is work that has
    stopped and will not restart without a person; ``BROKEN`` is work that went wrong and
    needs diagnosis; ``IDLE`` is work nobody started. Anything that fits none of these is
    not an attention item — it is a page an operator visits when they choose to.
    """

    BLOCKED = "blocked"
    BROKEN = "broken"
    IDLE = "idle"


# What the feed shows first. Blocked before broken because a stopped run resumes the moment
# somebody decides, and a failed one needs reading before it needs anything else.
_ORDER: Final[dict[Severity, int]] = {
    Severity.BLOCKED: 0,
    Severity.BROKEN: 1,
    Severity.IDLE: 2,
}


@dataclass(frozen=True, slots=True)
class Attention:
    """One thing waiting, and the one place to go and deal with it.

    ``href`` is required, as it is on a provenance badge and for the same reason: an item
    that named a problem without leading anywhere would be a notification rather than a
    piece of work, and this feed exists to be worked through.
    """

    key: str
    tool: str
    severity: Severity
    title: str
    detail: str
    href: str
    action: str = ""
    """What the link does, when "open it" is not specific enough to be worth reading."""

    preview_href: str = ""
    """A fragment worth reading before leaving the page, if this item has one.

    Optional, and several items do not have one: an unrun draft has nothing to preview that
    the row does not already say. When it is set, the row renders a second link whose
    ``href`` is still the full page — the drawer is an enhancement over that link, never a
    replacement for it (ADR 0006).
    """

    def __post_init__(self) -> None:
        if not self.href:
            message = (
                f"The attention item {self.key!r} has no href. An item a reader cannot act "
                "on is a notification, and this feed is a work list."
            )
            raise ValueError(message)


class AttentionItems(Protocol):
    """What an ``items_ref`` must resolve to."""

    async def __call__(
        self, session: AsyncSession, *, user_id: uuid.UUID
    ) -> Sequence[Attention]: ...


class AttentionProviderError(AerError):
    """A provider is inconsistent with itself, or names something the code has lost."""

    code = "attention_provider"


@dataclass(frozen=True, slots=True)
class AttentionProvider:
    """One tool's answer to "is anything waiting for me"."""

    key: str
    tool: str
    items_ref: str
    adr: str

    def items_fn(self) -> AttentionItems:
        """The registered builder, resolved from its reference.

        Raises:
            AttentionProviderError: If the reference no longer resolves, in the idiom
                `RoleDefinition.output_schema` uses — loudly, at the moment of asking.
        """
        module_name, _, attribute = self.items_ref.partition(":")
        try:
            resolved = getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as exc:
            message = (
                f"The attention provider {self.key!r} registers {self.items_ref!r}, which "
                f"does not resolve: {exc}"
            )
            raise AttentionProviderError(
                message, context={"provider": self.key, "ref": self.items_ref}
            ) from exc
        if not callable(resolved):
            message = (
                f"The attention provider {self.key!r} registers {self.items_ref!r}, which "
                "resolves to something that cannot be called."
            )
            raise AttentionProviderError(
                message, context={"provider": self.key, "ref": self.items_ref}
            )
        return resolved  # type: ignore[no-any-return]


# One row per tool that can have something waiting. A second tool adds a line.
_PROVIDERS: Final[tuple[AttentionProvider, ...]] = (
    AttentionProvider(
        key="research",
        tool="research",
        items_ref="aer.web.overview.research:items",
        adr="0071",
    ),
    AttentionProvider(
        key="platform",
        tool="platform",
        items_ref="aer.web.overview.platform:items",
        adr="0071",
    ),
)


def _build(
    providers: Sequence[AttentionProvider] = _PROVIDERS,
) -> dict[str, AttentionProvider]:
    built: dict[str, AttentionProvider] = {}
    for provider in providers:
        if not provider.adr.strip():
            message = (
                f"The attention provider {provider.key!r} has no ADR reference. What one "
                "tool may put in front of every other tool's operator is a decision, and "
                "ADR 0071 puts the record before the row."
            )
            raise AttentionProviderError(message, context={"provider": provider.key})
        if provider.key in built:
            message = (
                f"Two providers claim the key {provider.key!r}. Which one answered would "
                "depend on declaration order."
            )
            raise AttentionProviderError(message, context={"provider": provider.key})
        built[provider.key] = provider
    return built


_REGISTRY: Final[dict[str, AttentionProvider]] = _build()


def registered_providers() -> tuple[AttentionProvider, ...]:
    """Every provider, in key order so the feed is assembled the same way twice."""
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


async def items_for(session: AsyncSession, *, user_id: uuid.UUID) -> tuple[Attention, ...]:
    """Everything waiting, worst first, then in the order its tool returned it.

    Sequential rather than gathered, for the reason `badges.counts_for` gives: an
    `AsyncSession` is not safe to use concurrently, and a `gather` would interleave
    statements on one connection and fail as though the database were at fault.
    """
    collected: list[Attention] = []
    for provider in registered_providers():
        try:
            collected.extend(await provider.items_fn()(session, user_id=user_id))
        except (AerError, SQLAlchemyError, OSError) as failure:
            _log.warning(
                "attention.provider_failed",
                provider=provider.key,
                tool=provider.tool,
                error=str(failure),
            )
            collected.append(_could_not_ask(provider, failure))
    return tuple(sorted(collected, key=lambda item: _ORDER[item.severity]))


def _could_not_ask(provider: AttentionProvider, failure: Exception) -> Attention:
    """The item a failed provider becomes.

    Not a swallowed exception and not a 500. An empty feed is a claim — "nothing is waiting
    for you" — and it is exactly the claim a broken query would make by accident; a page
    that refused to render instead would take the other tools' real items down with it.
    """
    return Attention(
        key=f"{provider.key}.unavailable",
        tool=provider.tool,
        severity=Severity.BROKEN,
        title=f"{provider.tool.capitalize()} could not be asked what is waiting",
        detail=(
            f"{type(failure).__name__}: {failure}. Anything waiting in this tool is not "
            "listed below — treat the rest of this feed as incomplete."
        ),
        href="/healthz",
        action="Check the platform",
    )
