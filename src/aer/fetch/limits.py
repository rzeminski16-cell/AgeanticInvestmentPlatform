"""Rate limiting and circuit breaking, shared across processes.

**Why Redis and not a local counter.** A token bucket held in one process limits that
process. Run the web server and two workers and the provider sees three times the agreed
rate — which for the SEC means an IP ban rather than a throttle. The bucket lives in Redis
so every process draws from the same budget, and the refill arithmetic runs inside a Lua
script so that a check-then-decrement cannot interleave with another worker's.

**Why the clock is injected.** "Does the eleventh request wait?" is a question about
elapsed time, and a test that answers it by sleeping is a slow test that still only proves
one timing. Passing a clock in makes the behaviour exactly assertable — advance the fake
clock by half a second and see precisely how many tokens returned.

The circuit breaker is the other half of the same concern. A provider returning errors is
usually a provider under strain, and continuing to ask makes it worse for everyone
including this run. After a few consecutive failures the breaker opens and requests fail
immediately for a cooldown.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import structlog
from redis.asyncio import Redis

from aer.fetch.errors import CircuitOpenError

__all__ = ["CircuitBreaker", "RateLimiter", "TokenBucketResult"]

_log = structlog.get_logger("aer.fetch.limits")

Clock = Callable[[], float]

_KEY_PREFIX: Final = "aer:fetch"

# Refill and take, atomically. Written as one script because the alternative -- read,
# compute, write -- has a window in which another worker reads the same tokens and both
# proceed. On an eight-per-second budget that window is wide enough to matter.
#
# Returns {allowed, tokens_remaining, wait_seconds}. wait_seconds is how long the caller
# should sleep before the next token exists, so the caller never has to guess.
_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local state = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(state[1])
local updated_at = tonumber(state[2])

if tokens == nil then
  tokens = burst
  updated_at = now
end

-- Refill for the elapsed time, capped at the burst size. A bucket that kept accumulating
-- while idle would let a worker that has been quiet for an hour fire a thousand requests
-- at once, which is exactly the behaviour the provider's limit forbids.
local elapsed = math.max(0, now - updated_at)
tokens = math.min(burst, tokens + elapsed * rate)

local allowed = 0
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  wait = (cost - tokens) / rate
end

redis.call('HSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', key, ttl)

return {allowed, tostring(tokens), tostring(wait)}
"""


@dataclass(frozen=True, slots=True)
class TokenBucketResult:
    """Whether a request may proceed, and how long to wait if not."""

    allowed: bool
    tokens_remaining: float
    wait_seconds: float


class RateLimiter:
    """A token bucket per provider, held in Redis."""

    def __init__(self, redis: Redis, *, clock: Clock | None = None) -> None:
        self._redis = redis
        self._clock = clock or time.monotonic
        self._script = redis.register_script(_ACQUIRE_SCRIPT)

    async def try_acquire(
        self, provider: str, *, rate: float, burst: int, cost: float = 1.0
    ) -> TokenBucketResult:
        """Take a token if one is available. Never blocks."""
        # The key expires after enough idle time for the bucket to have refilled
        # completely; recreating it full is the same state, so keeping it costs nothing.
        ttl = max(60, int(burst / rate) * 2 + 60)
        raw = await self._script(
            keys=[f"{_KEY_PREFIX}:bucket:{provider}"],
            args=[rate, burst, self._clock(), cost, ttl],
        )
        allowed, tokens, wait = raw
        return TokenBucketResult(
            allowed=bool(int(allowed)),
            tokens_remaining=float(tokens),
            wait_seconds=float(wait),
        )

    async def acquire(
        self,
        provider: str,
        *,
        rate: float,
        burst: int,
        sleep: Callable[[float], Awaitable[object]] | None = None,
        max_wait_seconds: float = 30.0,
    ) -> float:
        """Wait until a token is available, then take it. Returns seconds spent waiting.

        ``sleep`` is injected for the same reason the clock is: a test asserting that the
        eleventh request waits should not itself have to wait.

        Raises:
            CircuitOpenError: If the wait would exceed ``max_wait_seconds``. A request
                queued behind half a minute of backlog is a request whose caller should
                be told, not one that should silently hold a worker.
        """
        sleeper = sleep or asyncio.sleep
        waited = 0.0
        while True:
            result = await self.try_acquire(provider, rate=rate, burst=burst)
            if result.allowed:
                return waited
            if waited + result.wait_seconds > max_wait_seconds:
                message = (
                    f"The rate limit for {provider} is saturated; the next token is "
                    f"{result.wait_seconds:.1f}s away, past the {max_wait_seconds:.0f}s "
                    "this request is willing to wait."
                )
                raise CircuitOpenError(
                    message, provider=provider, retry_after_seconds=result.wait_seconds
                )
            waited += result.wait_seconds
            await sleeper(result.wait_seconds)


class CircuitBreaker:
    """Stops asking a provider that keeps failing.

    Consecutive failures only: a single failure among successes is noise, a run of them is
    a provider that is down or has started refusing us. The counter resets on any success.
    """

    def __init__(self, redis: Redis, *, clock: Clock | None = None) -> None:
        self._redis = redis
        self._clock = clock or time.monotonic

    def _failure_key(self, provider: str) -> str:
        return f"{_KEY_PREFIX}:failures:{provider}"

    def _open_key(self, provider: str) -> str:
        return f"{_KEY_PREFIX}:open:{provider}"

    async def check(self, provider: str) -> None:
        """Raise if the circuit is open.

        Raises:
            CircuitOpenError: While the cooldown is still running.
        """
        remaining = await self._redis.ttl(self._open_key(provider))
        if remaining is not None and remaining > 0:
            message = (
                f"{provider} has failed repeatedly, so requests are paused for another "
                f"{remaining}s. Continuing to ask a struggling provider makes it worse "
                "for everyone, including this run."
            )
            raise CircuitOpenError(message, provider=provider, retry_after_seconds=float(remaining))

    async def record_success(self, provider: str) -> None:
        await self._redis.delete(self._failure_key(provider))

    async def record_failure(
        self, provider: str, *, threshold: int, cooldown_seconds: float
    ) -> int:
        """Count a failure and open the circuit if the threshold is reached.

        Returns the new consecutive-failure count.
        """
        key = self._failure_key(provider)
        failures = int(await self._redis.incr(key))
        # The counter itself expires: a failure an hour ago is not part of a run of
        # failures now, and without a TTL the count would creep up over days and open the
        # circuit for no present reason.
        await self._redis.expire(key, max(60, int(cooldown_seconds) * 4))

        if failures >= threshold:
            await self._redis.set(self._open_key(provider), "1", ex=int(cooldown_seconds))
            await self._redis.delete(key)
            _log.warning(
                "fetch.circuit_opened",
                provider=provider,
                consecutive_failures=failures,
                cooldown_seconds=cooldown_seconds,
            )
        return failures
