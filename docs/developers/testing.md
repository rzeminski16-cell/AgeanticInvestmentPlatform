# Testing

*How the suite is layered, what each layer buys, and the two rules that stop it lying to
you.*

---

```bash
uv run pytest --ignore=tests/e2e     # default suite: no network, no model spend
uv run pytest tests/e2e              # browser tests (Chromium + PostgreSQL)
just test-all                        # both, as two processes
just eval                            # the eight blocking metrics, on their own
uv run pytest --cov                  # with coverage
uv run pytest -m integration         # database tests only
uv run pytest -m "not integration"   # skip anything needing PostgreSQL
```

**The evaluation gate is inside the default suite and is also its own step in CI.** Both,
deliberately: it is ordinary pytest so it cannot be forgotten, and a named step so a build
that goes red because citation accuracy moved says so on the summary line rather than in the
middle of two thousand dots.

**The browser tests must run in their own pytest process.** Playwright's synchronous API
drives an asyncio loop on the main thread and keeps it running for the life of its session
fixture, so any asyncio-based fixture that runs after a browser test in the same process
fails with "Runner.run() cannot be called from a running event loop". `just test-all` is
therefore two commands rather than one `pytest` invocation.

The whole vertical slice — plan, both gates, budget guard, acquisition, calculation,
rendered report — runs in the default suite against a fake provider and a stubbed EDGAR
client, so it costs nothing and needs no network. That is the entire reason the provider
abstraction exists: a suite that spent money would be a suite nobody ran.

**A fake provider proves nothing about what goes on the wire**, and the first real model call
found that out: a 400 on a request shape 1,300 passing tests had never looked at. So
`tests/test_anthropic_provider.py` asserts the payload itself, with the SDK client stubbed —
which parameters are sent, which are deliberately absent, and what each failure message tells
the operator to do. Alongside it, `TestTheSdkContract` checks the *installed* SDK for the
surface the provider depends on, so an upgrade that moves it fails there rather than on the
next live run. See `docs/adr/0015-the-vendor-contract-is-asserted-not-assumed.md`.

The browser tests drive a real Chromium against a real uvicorn server on an ephemeral
port. They exist to catch what an in-process HTTP client structurally cannot — a form
field the server never receives, a submit button outside the form, an HTMX response the
browser silently discards. Two genuine bugs found that way are recorded in
`docs/adr/0007-request-validation-boundaries.md`.

If Chromium was installed outside Playwright's own cache, point at it with
`PLAYWRIGHT_CHROMIUM_PATH`; `/opt/pw-browsers/chromium` is picked up automatically.

Tests that would make real, billable model calls are marked `live_llm` and never run by
default.

Database tests run against a separate `aer_test` database, inside a transaction that is
rolled back afterwards, so they never touch your development data. If PostgreSQL is not
running they **skip with the reason** rather than failing, so `uv run pytest` still works
on a machine with nothing started. Point them elsewhere with `AER_TEST_DATABASE_URL`.

### Verifying it by hand

`docs/developers/manual-verification.md` is a checklist for everything the suite structurally cannot
prove: Docker Compose, the pages as a person sees them, the guards provoked deliberately,
and one real run against the real SEC and a real model call. Most of it costs nothing; the
one section that spends money says so and says roughly how much.

---

## Two rules that are not optional

**One pytest process per database.** The suite empties tables between tests, so two runs
sharing `aer_test` delete each other's rows and fail in whichever one was mid-test — showing
up as "no user exists" moments after a fixture committed one, nowhere near the cause. Give
each concurrent run its own `AER_TEST_DATABASE_URL`.

**The full suite is two processes**, `pytest --ignore=tests/e2e` then `pytest tests/e2e`, for
the Playwright reason above. A single invocation produces a result that depends on collection
order, which is worse than a slower one.
