"""The daily weighted-call allowance, which the token bucket cannot see.

EODHD meters two different things. The first is a **request rate** — a thousand HTTP
requests a minute — and :class:`~aer.fetch.limits.RateLimiter` already handles that, because
a token bucket is exactly the right shape for it.

The second is a **daily allowance of weighted API calls**, and it is a different quantity
with a different unit. One end-of-day request costs one call; a fundamentals document costs
ten; a whole-exchange bulk request costs a hundred. A limiter counting requests per second
sees a hundred-call bulk request as one event and cannot tell that a handful of them have
spent a morning's worth of a day's budget. Nothing in the fetch layer knows about endpoint
weights, and nothing in the fetch layer should: weights are one provider's pricing decision,
not a property of HTTP.

**The reservation happens before the request, not after it.** Counting spend on the way back
means two concurrent requests both pass the check, both proceed, and the allowance is
overshot by however many were in flight. Incrementing first and refunding on refusal is a
single atomic Redis operation and has no such window.

**A cap that only warns is not a cap.** Exceeding the allowance raises
:class:`~aer.fetch.errors.CircuitOpenError` with the seconds until the allowance resets, so
the caller can be told when to come back rather than being left to retry into a wall. This is
the same rule `docs/PLAN.md` applies to model spend, for the same reason: the failure this
prevents is a quiet one, discovered on an invoice or on a locked account.

**The day is the provider's day.** The key is the UTC date, because that is when the
provider's counter resets. A ledger keyed on the operator's local midnight would drift out of
step for however many hours the offset is, and would do it silently — the count would simply
be wrong in one direction in the morning and the other in the evening.

**The provider is the authority, and this is only a model of it.** Every response carries the
remaining allowance in a header; :meth:`WeightedCallBudget.reconcile` writes that figure back
over the local estimate. A ledger that never listened would drift from the truth on every
retried request, every request made from another process, and every weight this module has
wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

import structlog
from redis.asyncio import Redis

from aer.fetch.errors import CircuitOpenError

__all__ = [
    "DAILY_WEIGHTED_CALLS",
    "BudgetState",
    "EndpointCost",
    "WeightedCallBudget",
]

_log = structlog.get_logger("aer.sources.eodhd.budget")

_KEY_PREFIX: Final = "aer:eodhd:calls"

# The published daily allowance. Not read from configuration: it is a property of the
# subscription, changing it is a commercial decision rather than a tuning knob, and a value an
# operator can raise in a `.env` file is a cap that stops being one the first time it is
# inconvenient.
DAILY_WEIGHTED_CALLS: Final = 100_000

# Kept a whole day plus an hour, so a key written just before the boundary still expires and
# a clock a few minutes out does not resurrect yesterday's count.
_KEY_TTL_SECONDS: Final = 25 * 60 * 60

_SECONDS_IN_A_DAY: Final = 24 * 60 * 60

# The header EODHD returns its own remaining allowance in.
REMAINING_HEADER: Final = "x-ratelimit-remaining"


class EndpointCost(StrEnum):
    """What each endpoint costs against the daily allowance.

    A ``StrEnum`` whose members carry the weight in :data:`WEIGHTS` rather than an int enum,
    because the name is what a caller writes and a bare number at a call site is a number
    nobody can check against the price list.
    """

    EOD = "eod"
    SPLITS = "splits"
    DIVIDENDS = "div"
    FUNDAMENTALS = "fundamentals"
    SEARCH = "search"
    BULK = "bulk"


# The published weights. Anything not listed costs one, which is the documented default, and
# a new endpoint therefore under-counts rather than failing — deliberate, because
# :meth:`WeightedCallBudget.reconcile` corrects an under-count from the provider's own header
# on the very next response, and a hard failure on an unlisted endpoint would stop a run over
# a price list this module is not the authority on.
WEIGHTS: Final[dict[EndpointCost, int]] = {
    EndpointCost.EOD: 1,
    EndpointCost.SPLITS: 1,
    EndpointCost.DIVIDENDS: 1,
    EndpointCost.SEARCH: 1,
    EndpointCost.FUNDAMENTALS: 10,
    EndpointCost.BULK: 100,
}


@dataclass(frozen=True, slots=True)
class BudgetState:
    """What has been spent today, and what is left."""

    day: str
    spent: int
    allowance: int

    @property
    def remaining(self) -> int:
        return max(0, self.allowance - self.spent)

    @property
    def is_exhausted(self) -> bool:
        return self.spent >= self.allowance


class WeightedCallBudget:
    """A daily ledger of weighted API calls, held in Redis."""

    def __init__(
        self,
        redis: Redis,
        *,
        allowance: int = DAILY_WEIGHTED_CALLS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis
        self._allowance = allowance
        # Injected for the same reason the token bucket's is: a test asserting that the
        # allowance resets tomorrow should not have to wait until tomorrow.
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def allowance(self) -> int:
        return self._allowance

    async def reserve(self, endpoint: EndpointCost, *, count: int = 1) -> BudgetState:
        """Claim this call's weight against today's allowance, or refuse.

        Args:
            endpoint: Which endpoint is about to be called, which fixes the weight.
            count: How many calls of that kind. More than one for a batched request.

        Returns:
            The state *after* the reservation, so a caller can log how close it is.

        Raises:
            CircuitOpenError: If the reservation would take the day past its allowance. The
                error carries the seconds until the counter resets.
        """
        weight = WEIGHTS.get(endpoint, 1) * count
        key = self._key()

        spent = int(await self._redis.incrby(key, weight))
        # Set on every increment rather than only on creation. `INCRBY` on a missing key
        # creates it with no expiry, and a race where two callers both see it as existing
        # would otherwise leave a key that never goes away and a count that never resets.
        await self._redis.expire(key, _KEY_TTL_SECONDS)

        if spent > self._allowance:
            await self._redis.decrby(key, weight)
            raise self._exhausted(endpoint, weight=weight, spent=spent - weight)

        state = BudgetState(day=self._day(), spent=spent, allowance=self._allowance)
        _log.debug(
            "eodhd.budget.reserved",
            endpoint=endpoint.value,
            weight=weight,
            spent=state.spent,
            remaining=state.remaining,
        )
        return state

    async def state(self) -> BudgetState:
        """Today's spend, without reserving anything."""
        raw = await self._redis.get(self._key())
        spent = int(raw) if raw is not None else 0
        return BudgetState(day=self._day(), spent=spent, allowance=self._allowance)

    async def reconcile(self, headers: dict[str, str]) -> BudgetState | None:
        """Overwrite the local estimate with the provider's own remaining count.

        The provider is the authority. This ledger is a model of its counter, and a model
        drifts: a retried request spends twice while reserving once, a second process shares
        the same subscription and this Redis key does not know about it, and any weight in
        :data:`WEIGHTS` that is wrong is wrong on every call.

        Returns ``None`` when the response carried no such header, which is not an error —
        it means there was nothing to learn from.
        """
        raw = _header(headers, REMAINING_HEADER)
        if raw is None:
            return None

        try:
            remaining = int(raw.strip())
        except ValueError:
            _log.warning("eodhd.budget.unreadable_header", value=raw)
            return None

        spent = max(0, self._allowance - remaining)
        key = self._key()
        await self._redis.set(key, spent, ex=_KEY_TTL_SECONDS)

        state = BudgetState(day=self._day(), spent=spent, allowance=self._allowance)
        _log.debug("eodhd.budget.reconciled", spent=state.spent, remaining=state.remaining)
        return state

    # -- Internals -----------------------------------------------------------------------

    def _day(self) -> str:
        return self._clock().astimezone(UTC).date().isoformat()

    def _key(self) -> str:
        return f"{_KEY_PREFIX}:{self._day()}"

    def _seconds_until_reset(self) -> float:
        now = self._clock().astimezone(UTC)
        elapsed = now.hour * 3600 + now.minute * 60 + now.second
        return float(_SECONDS_IN_A_DAY - elapsed)

    def _exhausted(self, endpoint: EndpointCost, *, weight: int, spent: int) -> CircuitOpenError:
        wait = self._seconds_until_reset()
        message = (
            f"Today's EODHD allowance is spent: {spent} of {self._allowance} weighted calls "
            f"used, and this {endpoint.value} request costs {weight} more. The counter "
            f"resets in {wait / 3600:.1f}h. Stopping here rather than making the request is "
            "what keeps the subscription in good standing."
        )
        return CircuitOpenError(message, provider="eodhd", retry_after_seconds=wait)


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup.

    HTTP header names are case-insensitive and the transport does not promise a normalised
    mapping, so a lookup that assumed lowercase would find nothing against a server that
    replied in title case — and finding nothing here is silent.
    """
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None
