# 0036 — Research workers request tools in a schema; code authorises and executes them

Date: 2026-08-06. Status: accepted.

## Context

Phase 4's research workers (`docs/archive/PLAN.md` §2.5) investigate five topics in parallel with
"max 12 tool calls each". The obvious implementation is provider-level tool use: declare
tools to the model API, receive `tool_use` blocks, execute, append results, loop. That
shape has three costs here. The provider protocol — deliberately two operations, a virtue
its own docstring argues for — would grow a vendor-shaped tool surface. The fake provider,
which every workflow test leans on, would need to script interleaved tool turns. And tool
authorisation would live partly in what was *declared to the API*, one step removed from
the platform's own rule that tool permission is enforced in code before a tool runs.

Meanwhile the platform already has a working answer to "the model wants something done":
the model **proposes, in a validated schema, and code decides** — assumptions, citations,
peer sets and classifications all work exactly this way.

## Decision

Workers use a **request/execute protocol** built from the existing single-shot structured
call. Each turn the worker returns one validated object: either tool *requests* (tool
name, query, reason) or its final report — never both, never neither. Code then, in order:
authorises each request against the `analysis` role's registry allowlist
(`require_tool`, ADR 0035); checks the executed-call budget (twelve per worker, §2.5);
executes deterministically in `aer.services.research`; and feeds results into the next
turn — structured internal results as data, anything text-bearing from outside inside
`<untrusted_source>` delimiters via the base agent's wrapping.

Refusals of unlisted tools cost nothing against the budget: a poisoned document must not
be able to burn a worker's budget by asking for capabilities it will never get. Rounds are
bounded separately, and a worker that exhausts them without a validated report fails
loudly (`WorkerExhaustedError`) rather than the run continuing with a silently absent
investigation.

Findings are validated in code against the run's own tables — a finding citing a source
document or fact id the run does not hold is refused back to the worker with the problem
named, and a worker that cannot fix it fails.

## Consequences

There is no tool-use surface in the provider at all, so an instruction smuggled into a
fetched document has nothing to invoke — the strongest available form of "assert at the
registry, not the prompt", because the assertion is that the capability does not exist.
The provider protocol stays narrow; the fake provider scripts multi-turn workers with a
stateful callable and no new machinery; every turn is archived and metered by the ordinary
agent path.

The cost is one full model call per loop round instead of interleaved tool blocks, with
the accumulated evidence re-sent each round — bounded by the role's input cap and mitigated
by the platform-contract prompt prefix being cacheable. At twelve tool calls and five
rounds per worker, the ceiling is known and priced into the worker estimates.

The `analysis` role is admitted with this ADR: tools `search_facts`, `search_sources`,
`fetch_known_url`; caps 30k in, 8k out. Custom sections (task 38) are expected to reuse the
same protocol against their own composed allowlists.
