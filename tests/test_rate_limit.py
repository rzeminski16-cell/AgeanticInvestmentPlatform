"""The token bucket and the circuit breaker.

Every timing question here is answered by moving a fake clock rather than by sleeping. A
test that sleeps half a second to prove a half-second refill is slow *and* imprecise — it
proves the behaviour at one timing and nothing about the arithmetic. Advancing the clock
proves the arithmetic exactly, and the whole file runs in milliseconds.

Redis is real (in-process), including its Lua scripting, so the atomicity the bucket
depends on is genuinely exercised rather than assumed.
"""

from __future__ import annotations

import pytest

from aer.fetch.errors import CircuitOpenError
from aer.fetch.limits import CircuitBreaker, RateLimiter
from tests.fetch_fixtures import RecordingSleeper

pytestmark = pytest.mark.usefixtures("no_real_sockets")

PROVIDER = "sec_edgar"


class TestTokenBucket:
    async def test_a_fresh_bucket_starts_full(self, limiter):
        results = [await limiter.try_acquire(PROVIDER, rate=8.0, burst=8) for _ in range(8)]

        assert all(result.allowed for result in results)

    async def test_the_request_past_the_burst_is_refused(self, limiter):
        for _ in range(8):
            await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)

        ninth = await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)

        assert ninth.allowed is False
        assert ninth.wait_seconds == pytest.approx(1 / 8, abs=1e-6)

    async def test_tokens_return_as_time_passes(self, limiter, clock):
        for _ in range(8):
            await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)
        assert not (await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)).allowed

        # Half a second at eight per second is four tokens.
        clock.advance(0.5)

        allowed = [
            (await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)).allowed for _ in range(5)
        ]
        assert allowed == [True, True, True, True, False]

    async def test_an_idle_bucket_does_not_accumulate_beyond_its_burst(self, limiter, clock):
        # A bucket that kept filling while idle would let a worker quiet for an hour fire
        # thousands of requests at once, which is exactly what a provider's limit forbids.
        await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)
        clock.advance(3600)

        allowed = [
            (await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)).allowed for _ in range(9)
        ]

        assert allowed.count(True) == 8
        assert allowed[-1] is False

    async def test_providers_have_separate_buckets(self, limiter):
        # One provider exhausting its budget must not throttle another; they are separate
        # agreements with separate publishers.
        for _ in range(8):
            await limiter.try_acquire("sec_edgar", rate=8.0, burst=8)

        other = await limiter.try_acquire("companies_house", rate=1.8, burst=2)

        assert other.allowed is True

    async def test_the_bucket_is_shared_between_limiter_instances(self, redis_client, clock):
        # The reason it lives in Redis. Two workers are two instances; a bucket held in
        # process memory would give the provider twice the agreed rate.
        first = RateLimiter(redis_client, clock=clock)
        second = RateLimiter(redis_client, clock=clock)

        for _ in range(8):
            await first.try_acquire(PROVIDER, rate=8.0, burst=8)

        assert (await second.try_acquire(PROVIDER, rate=8.0, burst=8)).allowed is False


class TestWaiting:
    async def test_acquire_returns_immediately_when_a_token_is_free(self, limiter, sleeper):
        waited = await limiter.acquire(PROVIDER, rate=8.0, burst=8, sleep=sleeper)

        assert waited == 0.0
        assert sleeper.calls == []

    async def test_acquire_waits_exactly_long_enough(self, limiter, sleeper):
        for _ in range(8):
            await limiter.try_acquire(PROVIDER, rate=8.0, burst=8)

        waited = await limiter.acquire(PROVIDER, rate=8.0, burst=8, sleep=sleeper)

        # One token at eight per second is an eighth of a second, and the sleeper advances
        # the clock as it "sleeps" so the retry finds the token genuinely there.
        assert waited == pytest.approx(0.125, abs=1e-6)
        assert sleeper.calls == [pytest.approx(0.125, abs=1e-6)]

    async def test_a_backlog_longer_than_the_patience_raises(self, limiter, sleeper):
        # A request queued behind half a minute of backlog is one whose caller should be
        # told, not one that should silently hold a worker open.
        await limiter.try_acquire("slow", rate=0.01, burst=1)

        with pytest.raises(CircuitOpenError, match="saturated"):
            await limiter.acquire("slow", rate=0.01, burst=1, sleep=sleeper, max_wait_seconds=5.0)

    async def test_the_injected_sleeper_is_what_gets_called(self, limiter, clock):
        # If the real asyncio.sleep were used instead, this test would take a second and
        # the recorder would be empty. It takes neither.
        recorded = RecordingSleeper(clock)
        for _ in range(2):
            await limiter.try_acquire("tiny", rate=1.0, burst=2)

        waited = await limiter.acquire("tiny", rate=1.0, burst=2, sleep=recorded)

        assert recorded.calls == [pytest.approx(1.0, abs=1e-6)]
        assert waited == pytest.approx(1.0, abs=1e-6)


class TestCircuitBreaker:
    async def test_a_new_circuit_is_closed(self, breaker):
        await breaker.check(PROVIDER)  # does not raise

    async def test_failures_below_the_threshold_leave_it_closed(self, breaker):
        for _ in range(2):
            await breaker.record_failure(PROVIDER, threshold=3, cooldown_seconds=60)

        await breaker.check(PROVIDER)

    async def test_reaching_the_threshold_opens_it(self, breaker):
        for _ in range(3):
            await breaker.record_failure(PROVIDER, threshold=3, cooldown_seconds=60)

        with pytest.raises(CircuitOpenError) as excinfo:
            await breaker.check(PROVIDER)

        assert excinfo.value.provider == PROVIDER
        assert excinfo.value.retry_after_seconds > 0

    async def test_a_success_resets_the_count(self, breaker):
        # Consecutive failures only. A single failure among successes is noise; a run of
        # them is a provider that is down.
        for _ in range(2):
            await breaker.record_failure(PROVIDER, threshold=3, cooldown_seconds=60)
        await breaker.record_success(PROVIDER)
        await breaker.record_failure(PROVIDER, threshold=3, cooldown_seconds=60)

        await breaker.check(PROVIDER)

    async def test_the_count_returned_is_the_running_total(self, breaker):
        counts = [
            await breaker.record_failure(PROVIDER, threshold=5, cooldown_seconds=60)
            for _ in range(3)
        ]

        assert counts == [1, 2, 3]

    async def test_providers_break_independently(self, breaker):
        for _ in range(3):
            await breaker.record_failure("sec_edgar", threshold=3, cooldown_seconds=60)

        await breaker.check("companies_house")
        with pytest.raises(CircuitOpenError):
            await breaker.check("sec_edgar")

    async def test_the_refusal_says_how_long_to_wait(self, breaker):
        for _ in range(3):
            await breaker.record_failure(PROVIDER, threshold=3, cooldown_seconds=45)

        with pytest.raises(CircuitOpenError) as excinfo:
            await breaker.check(PROVIDER)

        assert "paused" in str(excinfo.value)
        assert excinfo.value.retry_after_seconds <= 45

    async def test_the_state_is_shared_between_breaker_instances(self, redis_client, clock):
        # Same reason as the bucket: one worker discovering a provider is down must stop
        # the others asking too.
        first = CircuitBreaker(redis_client, clock=clock)
        second = CircuitBreaker(redis_client, clock=clock)

        for _ in range(3):
            await first.record_failure(PROVIDER, threshold=3, cooldown_seconds=60)

        with pytest.raises(CircuitOpenError):
            await second.check(PROVIDER)
