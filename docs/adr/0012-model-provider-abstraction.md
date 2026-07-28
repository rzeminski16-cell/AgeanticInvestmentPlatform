# 12. Model calls go through a provider abstraction, a router and a meter

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Claude is the only model provider this platform will use. That makes an abstraction over
providers look like speculative generality, so the reason for having one has to be
something other than "we might switch vendors".

It is. Three concrete problems arrive the moment model calls exist, and all three are
solved by the same seam.

**A test suite that costs money is a test suite nobody runs.** The vertical slice — plan,
acquire, calculate, draft, render — has to be exercised on every commit. If exercising it
means a real model call, then it runs in CI never, locally rarely, and the end-to-end
property it proves stops being proven. The suite needs a provider that answers from a
script and spends nothing, and that provider has to be a *real implementation of the same
interface*, not a monkeypatch.

**A model identifier at a call site is a cost decision nobody can see.** Routing source
triage to Opus rather than Haiku is roughly a thirtyfold cost difference on a step that
runs dozens of times per report. With identifiers scattered through the agents, changing
the cost profile of a run is a code change across many files, and the current profile is
not stated anywhere.

**A budget cap fed by an estimate is not a cap.** The £100/month ceiling has to compare
against what was actually consumed, priced by category. Cache reads cost a tenth of input
tokens and cache writes a quarter more; a meter that treated them alike would misreport a
cached run by roughly an order of magnitude, in the direction that flatters the platform.

## Decision

### One narrow protocol, two operations

`aer.providers.protocol.LLMProvider` requires exactly two things:

```python
async def complete_structured[T: BaseModel](schema, *, system, messages, model, effort, max_tokens) -> StructuredResult[T]
async def count_tokens(*, system, messages, model) -> int
```

**Structured output, never free text.** Every call names a Pydantic model and returns an
instance of it. Nothing downstream parses prose. Parsing prose is how a system ends up with
a rating field containing "probably a buy, though it depends".

**Token counting is a separate operation and is not optional.** The figure shown at the
approval gate is the difference between a person agreeing to spend money and a person
agreeing to something vague. For Anthropic that costs a round trip, and it is worth it.

**Usage is returned, never inferred.** `Usage` carries input, output, cache-read and
cache-write counts separately, because they are priced separately.

The interface is deliberately narrow. A wide one would encode one vendor's feature set as
though it were the shape of the problem, and the next implementation would either fake the
parts it lacks or the abstraction would leak.

### The fake provider lives in `src`, not in `tests`

`aer.providers.fake.FakeProvider` is the reference implementation. It returns scripted
objects, records what it was asked, and derives plausible token counts from the actual
prompt text — so a longer prompt really does cost more and the budget arithmetic under test
behaves like the real thing.

It ships in `src` because a protocol whose only other implementation lives in the test tree
is a protocol nobody has checked is implementable.

It **refuses** to answer a schema it has no script for. A fake that returned a default
object would make every test using it vacuous.

### Roles route to models; call sites never name one

`aer.providers.router.Router` maps a role — `planner`, `red_team`, `source_triage` — to a
model and an effort level, from configuration. No agent contains a model identifier.

A role with no route **raises**. It does not fall back to a default, because a silent
default is precisely how a run costs thirty times what the operator expected while looking
entirely normal.

Every `agent_runs` row records the role and the resolved model, so a report's provenance
can say which model wrote which section.

### Costs are metered in code, in `Decimal`, with the exchange rate on the row

`aer.providers.costs` prices usage against a published-rate table, one line per non-zero
category, and every model call writes those lines to `costs`. Prices are USD; the budget is
GBP; **the rate is stored on the row** rather than applied and forgotten, so last month's
costs stay reconcilable when the configured rate changes.

An unknown model is priced at the **most expensive** known one. A model nobody has priced
is one whose cost cannot be verified, and the safe error is to overstate: an overstatement
pauses a run for a decision, an understatement spends money nobody agreed to.

### The budget guard runs before a step, not after

`BudgetGuard.check` compares a step's projected cost against what remains of the request's
ceiling and raises before the provider is called. Checking afterwards tells you what you
already spent. There is a test that asserts the provider's call count is zero after a run
that hit the cap.

### Only `aer.providers.anthropic` may import the SDK

The vendor SDK is imported in exactly one module, and inside a function rather than at
module scope — so a process that never makes a model call never pays for the import, and a
deployment missing the package still serves pages.

This is enforced by a test that parses every file under `src/` and by a subprocess that
imports the application and checks `sys.modules`.

## Consequences

**The whole workflow runs in CI for nothing.** Plan, gates, budget guard, rendered report —
all of it, on every commit, with no network and no spend.

**Changing the cost profile is a config edit.** `AER_MODEL_ROUTES` is JSON; moving triage
from Sonnet to Haiku touches no code.

**Every figure in the cost table is exact.** `Decimal` throughout, `NUMERIC(12,6)` in the
database — six decimal places of a pound, so a single cheap call is not lost to rounding
and a thousand of them still sum correctly.

**Adding a provider means implementing two methods.** Not because a second provider is
planned, but because the seam that makes testing free is the same seam that would make one
possible.

**A cost the meter does not see is a cost the cap does not enforce.** Anything that spends
money — a data API, a web search — must write a `costs` row, or the ceiling silently stops
covering it. This is a standing obligation on every future integration, not a property the
current code guarantees on its own.
