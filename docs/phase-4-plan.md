# Phase 4 — task sequence (tasks 33–43)

Continues from `docs/phase-3-plan.md`. The phase specification — objective, deliverables,
acceptance criteria — is `docs/PLAN.md` → Stage 3 → Phase 4, and it remains the authority.
This file is the dependency-ordered breakdown of it.

**Objective, restated.** The judgement layer, tightly bounded. Phases 1–3 built a platform in
which every fact traces to a hashed artefact and every number to a recorded calculation; what
runs today is one linear workflow with one agent in it. Phase 4 adds the agents that plan,
investigate, interpret, challenge and validate — and the skill-file engine that lets the
operator add analysis sections of their own — without any of them acquiring a way to weaken
the evidence contract the first three phases enforced.

**The rule this phase is mostly about.** The workflow-versus-agent boundary of
`docs/PLAN.md` §2.5: *the graph is code, the judgement is the LLM*. Every agent has a typed
input, a typed output, a token budget and a tool allowlist; anything expressible as a
function is a function. The explicit anti-pattern is a mesh of chatty agents negotiating
with each other. Phase 4 is where the temptation to build one is strongest, which is why the
mitigation is structural: **a new agent role requires an ADR**, and the registry — not the
prompt — is where tool authorisation lives.

---

## What Phases 1–3 already banked

| Deliverable | Where it landed |
|---|---|
| `Agent[InputT, OutputT]` base: typed contracts, `allowed_tools`, `require_tool` enforced in code | Task 10, `aer/agents/base.py` |
| The planner agent, structured output against a Pydantic contract | Task 10, `aer/agents/planner.py` |
| Provider abstraction: protocol, router with per-role `ModelRoute`s, cost metering, fake provider | Task 10, `aer/providers/` |
| `prompts`, `agent_runs`, `costs` tables | Task 10, migration 0006 |
| Workflow engine: sequential steps, gates (`StepPaused`), budget guard, SSE, cancellation, resume | Task 10, `aer/workflow/engine.py` |
| `section_definitions` / `report_sections`, data-driven; a third section is data, not code | Task 10 |
| Untrusted-content wrapper and injection heuristics (ADR 0019: containment is the control) | Task 13, `aer/agents/untrusted.py`, `aer/extract/injection.py` |
| `disagreements` and the deterministic resolution ladder | Task 19, `aer/core/disagreement.py` |
| The eight blocking evaluation metrics and the replay harness | Tasks 21 and 32, `aer/eval/` |
| Assumptions with proposal history; re-proposing un-confirms | Task 24, `aer/services/assumptions.py` |
| The valuation stack: statements → ratios → WACC → DCF → sector gates → comps → surface | Tasks 22–31 |

Two things the plan assigned to Phase 1 that were deliberately trimmed from it and so land
here instead: the **skills table and loader** (`docs/PLAN.md` §2.2 marked "schema + loader
Phase 1"; the vertical slice shipped without them) and the **DAG engine** (the Phase 1
engine is sequential by design — "DAG parallelism arrives in Phase 4").

## What must be decided before the tasks that need it

- **Nothing external.** Phase 4 needs no new data provider, no new API key and no licence
  determination. Every model call goes through the existing router; every test runs against
  the fake provider. The `live_llm` marker stays the only path to real spend, excluded from
  the default suite as ever.
- **Batch API support in the provider layer** is a Phase 4 build item (task 40), not a
  prerequisite: red-team and validator calls are latency-tolerant and batch pricing halves
  their cost, which matters against a ≤£100/month budget.
- **One scope decision is pre-made and recorded here:** skill files may *add* sections and
  methodology guidance; they may not override built-in sections in the MVP. `docs/PLAN.md`
  §2.12 and the phase risks both name this as the line that keeps the containment argument
  tractable. Revisiting it is a Phase 6 question, and would need an ADR.

## Why this order

Four constraints fix the sequence.

1. **The registry before any new agent.** Eleven agents built against an unenforced
   convention would be eleven retrofits. The per-role allowlists, token caps and contract
   validation land once, in code, and every subsequent task registers into them.
2. **The DAG engine before the workers that fan out.** Parallel research workers, parallel
   custom sections and batched validators all hang off the same bounded-fan-out primitive.
   It is built and tested with deterministic steps first, so agent defects and engine
   defects never present as each other.
3. **The skills schema before the engine that executes them.** The additive-only composer is
   pure code over typed policy objects — testable exhaustively without a model call — and
   plan-time resolution needs rows to resolve. Containment is proved before generation
   exists: the adversarial corpus attacks the composer and the contracts, not a prompt.
4. **Validators before the red-team, both before the escalation engine.** Escalation
   triggers consume evaluation and disagreement rows; the dashboard renders what the engine
   recorded. Building surfaces last means they surface real data from day one.

Cost control is not a numbered task because it already exists: every call goes through the
router and writes a cost row, and the budget guard pauses a run that projects past its cap.
Phase 4's additions — per-section and per-worker token budgets, batch routing — extend that
machinery and are tested in the task that introduces each.

---

## Task 33 — The agent registry: allowlists, caps and contracts as data

**Objective.** One place where every agent role is declared, and the only place tool
authorisation and token budgets come from.

**Build.** An `aer/agents/registry.py` mapping role → `{input contract, output contract,
tool allowlist, token cap, model route key}`. The base agent resolves its own entry at
construction; `require_tool` keeps refusing anything outside the allowlist, now sourced from
the registry rather than a class attribute. Token caps enforced at the provider boundary —
a call that would exceed the role's cap is refused before it is made. Prompt assembly gains
the stable-prefix structure prompt caching needs (platform contract first, volatile content
last), recorded per role in the `prompts` table.

**Tests.** An unregistered role cannot construct; a tool outside the allowlist raises at the
registry regardless of what any prompt says; a call projected past the token cap is refused
and recorded; the planner, re-pointed at the registry, behaves identically.

**Acceptance.** Every existing agent resolves through the registry; no agent-side constant
grants a tool or a budget. **ADR: a new agent role requires an ADR** — the registry refuses
roles with no ADR reference, making the mitigation structural rather than procedural.

**Delivered (2026-08-06).** `aer/agents/registry.py` (one `RoleDefinition` per implemented
role; contracts by `module:Attribute` reference, the `function_ref` idiom), ADR 0035, and
the base agent rewired: construction resolves the definition, refuses an unregistered role,
and verifies the class's declared schema is the registered one. The input cap is checked
against a real token count before a call is made; the output cap is the `max_tokens` the
provider receives. The platform contract now leads every composed system prompt, with the
containment rule last.

One design point found by its own sabotage pass: a subclass class-attribute named
`allowed_tools` would *shadow* the base property and win attribute lookup — the quiet
widening path in miniature. Two layers close it: `__init_subclass__` refuses
capability-shaped attributes at class definition, and `require_tool` reads the definition
rather than the property, tested by bolting the attribute on after class creation, which
bypasses the first layer. The injection suite's containment assertions moved from agent
classes to the registry, which is the stronger claim: a role with a network tool is the
breach whether or not an agent class for it exists yet. Eleven sabotage mutations, eleven
caught — the eleventh after the shadowing test was added.

---

## Task 34 — The DAG workflow engine with bounded fan-out

**Objective.** Parallelism for breadth of source coverage, hard-bounded, without losing
resume, cancellation, gates or the budget guard.

**Build.** The engine's step list becomes a dependency graph; independent nodes run
concurrently under a semaphore (max 7 workers, per `docs/PLAN.md` §2.5). Per-node
idempotency keys as today; outputs written before status transitions; a failed node fails
its dependants and only its dependants. The budget guard is checked before each node starts,
and a `StepPaused` gate drains in-flight siblings before pausing. The vertical slice
workflow is re-expressed as a (still mostly linear) graph, unchanged in behaviour.

**Tests.** A diamond graph runs the middle nodes concurrently (observed, not assumed); the
bound is respected under load; resume after a crash re-runs only incomplete nodes;
cancellation and gates behave as they do today; two nodes writing calculations get distinct
`sequence` ranges — the migration 0019 ordering survives concurrency.

**Acceptance.** The existing e2e suite passes against the DAG engine with no workflow
changes beyond the graph declaration.

**Delivered (2026-08-06).** `WorkflowStep.needs`: ``None`` — the default — chains a step
after the one declared before it, so every existing workflow (the vertical slice included)
keeps its exact order with **no edits at all**; an explicit set places the node in the
graph, and dependencies must point at earlier-declared steps, which makes a cycle
unrepresentable rather than checked for. Waves of independent nodes run concurrently under
the §2.5 bound of seven — a module constant, not configuration — with each node on its own
session from ``services["session_factory"]``, because one ``AsyncSession`` must never be
shared across tasks; without a factory the engine is byte-for-byte the serial engine, which
is the path every fixture-session test still exercises.

Three behaviours the tests observe rather than assume: concurrency itself (a high-water
mark recorded by the nodes); **drain before stopping** (a sibling in flight when a pause,
budget refusal or failure lands still reaches its own recorded outcome); and **a failed
node abandons only its dependants** (the independent branch completes and keeps its work
before the failure re-raises). The wave budget projection counts in-flight siblings — two
nodes each individually under the cap can jointly be over it — and a refusal stops further
starts. ``stop_after`` now runs only the target's ancestor closure, and an unknown key is
refused rather than ignored, because ignoring it would run the whole workflow past the gate
the caller meant to stop at.

The ledger keeps its promise under concurrency: ``persist_context`` continues a job's
sequence from the stored maximum under a per-job advisory lock, so two parallel nodes write
distinct ranges and a replay label like ``present_value#0`` still names one row of one run.

Thirteen sabotage mutations, thirteen caught — the last two after their escapes bought
tests. Any-dependency readiness was invisible to the diamond (whole waves drain before the
join is considered), so a staggered-depth graph now exists where the mistake starts the
join early; and pause-then-continue on the serial path was distinguishable only in a
parallel-shaped graph run *without* a session factory, where the paused gate's independent
sibling is next in line and must not run — that case is now a test of its own.

---

## Task 35 — The skills schema, the frontmatter validator and the additive-only composer

**Objective.** Skill files as validated data, and the composition rules that make them
incapable of relaxing anything — before anything executes one.

**Build.** Migration: `skills` (key, kind, version, scope, content hash, frontmatter as
typed columns plus the body as text, enabled flag) with history on edit, mirroring the
assumptions pattern — editing creates a version, never rewrites one. A frontmatter schema
validator returning **line-level** errors at save time. The composer, pure and
`mypy --strict`: evidence policy `max(builtin_floor, request)`; `allowed_tools` intersected
with the role allowlist from task 33; `token_budget` clamped by config; every clamp returned
as a named warning for the UI. Output contracts are structurally unable to carry `rating`,
`confidence` or a valuation range — the fields do not exist on the custom-section contract
type, the ADR 0034 pattern applied again.

**Tests.** Happy and sad frontmatter paths with exact line numbers; `min_sources: 0` clamps
to the floor and warns; a request for `fetch_arbitrary_url` intersects to nothing rather
than escalating; budget clamps; the contract type cannot express a rating (a test that fails
to construct one); content hash changes on any edit.

**Acceptance.** A skill row cannot exist with invalid frontmatter, and no composed policy is
ever looser than the built-in floor.

**Delivered (2026-08-06).** Migration 0020: ``skills`` (identity: key, kind, enabled) and
``skill_versions`` (one immutable row per save, typed columns for everything the platform
acts on, the source byte-for-byte, its hash). The parser
(`aer/skills/frontmatter.py`) reports **every** problem at once, each with the 1-based file
line it lives on — nested fields included — and the write path validates before it
constructs, so the acceptance criterion is a property of the code shape. The schema
(`aer/core/schemas/skill.py`) makes the reserved output fields (rating, recommendation,
target price, valuation range) *undeclarable*: there is deliberately no downstream check on
what a custom section wrote into a rating field, because no such field can exist to be
written into — ADR 0034's pattern applied to authorship.

The composer (`aer/core/skill_policy.py`, pure, strict) clamps rather than refuses:
``max(floor, request)`` per evidence field in its own direction of strictness, tools as the
intersection with the role allowlist — an unknown tool intersects to nothing, never
escalates to a question — and the budget under the configured ceiling
(``AER_CUSTOM_SECTION_TOKEN_CEILING``). Every clamp is a named warning, because the
effective policy differing from what the author wrote is exactly what they must be shown.
The containment claim itself is a **hypothesis property**: for any request the schema
admits, against any allowlist and any ceiling, nothing composes looser, wider or larger —
and every difference from the request is named.

Versions are allocated from what is stored, never from the author's own ``version`` field
(two edits both claiming ``version: 3`` must not fight over history); identical bytes are
refused; a key keeps its kind. Sixteen sabotage mutations, sixteen caught — one after the
no-fence test learned to assert the diagnosis as well as the line, and one after a
broken mutation string was rewritten honestly.

---

## Task 36 — Skill resolution, version pinning and custom sections in the plan

**Objective.** An enabled skill becomes a planned, costed, approvable section — pinned so a
mid-run edit changes nothing.

**Build.** Plan-time resolution: enabled skills matching scope and applicability (markets,
analysis modes, sector exclusions honouring the task 19 profiles) are pinned to exact
versions on the plan. Each resolved custom section is projected into `section_definitions`
(`origin='custom'`) and becomes a DAG node with its own composed budget. Gate 1 lists each
custom section with its estimated cost and any composer warnings, so the operator approves
them explicitly.

**Tests.** Pinning: editing a skill after plan approval does not change the run; scope and
applicability matrices; a sector-excluded skill shows `skipped_not_applicable` in the plan;
the pre-run estimate includes custom-section budgets; Gate 1 payload hashing covers the
pinned versions — approving one set of skills is not approving another.

**Acceptance.** A run's report can name the exact version of every skill that shaped it.

---

## Task 37 — The research workers: bounded parallel investigation

**Objective.** Breadth of source coverage through parallel per-topic workers — company,
industry, macro, recent developments, technical context — under one contract.

**Build.** One worker agent class, parameterised by topic; all workers share a single typed
contract, one tool allowlist (`search_facts`, `search_sources`, bounded incremental
acquisition through the existing fetch layer) and one per-worker token cap, max 12 tool
calls each. Fan-out through the task 34 engine. Workers produce *leads and structured
findings referencing evidence*, never numbers — anything numeric is a fact id or a proposed
calculation for the deterministic layer.

**Tests.** Tool-call and token bounds enforced (a worker that tries a thirteenth call is
refused); fan-out bounded at the engine; a worker fed a poisoned document (the task 13
corpus) causes no out-of-policy tool authorisation; findings referencing nonexistent fact
ids fail validation; all with the fake provider.

**Acceptance.** A run's source coverage widens measurably on the fixture corpus with cost
still under the per-run cap.

---

## Task 38 — Custom-section execution: the `<user_skill>` boundary

**Objective.** The operator's prose runs — inside the platform's contract, never above it.

**Build.** The composed prompt in the fixed order §2.12 specifies: immutable platform
contract, output schema, structured evidence, then the user's text inside `<user_skill>`
delimiters. One structured-output call validated against the section's `output_contract`;
one retry on schema violation, then the section is marked `failed` and the run continues.
Claim extraction over the output — every factual and numeric statement becomes a `claims`
row through the existing verifier, and a bare numeral resolving to no fact or calculation is
a validation failure. Degradation ladder as specified: insufficient evidence renders the
insufficiency banner, never fabricated prose; budget exhaustion truncates cleanly and flags.

**Tests.** Prompt-order is pinned structurally (the user text cannot precede the contract);
schema-violation retry-then-fail; the unsourced-numeral failure; the insufficiency banner;
a custom section writing to `reports.rating` is impossible by type — reasserted here at the
execution boundary; the moat-durability example from §2.12 runs end to end on the fake
provider.

**Acceptance.** A custom section appears in the draft with its own cited evidence, and its
failure modes are all visible states rather than absent sections.

---

## Task 39 — The validators, and `evaluations` rows per run

**Objective.** The gate's judgement, applied to every live run — with LLM assistance where
ambiguity is real, and deterministic authority everywhere else.

**Build.** Migration: `evaluations` (job, metric, value, threshold, passed, details JSONB).
Per-run validators: citation (deterministic verifier authoritative; the LLM only *locates*
candidate excerpts for claims the verifier could not resolve), temporal (deterministic; LLM
adjudication only for ambiguous dates, recorded as advisory), numerical (the task 32 replay
harness over the run's rows), coverage (per-section evidence floors, custom sections held to
their composed policy). Batch API support lands in the provider layer here, with the fake
provider growing a batch path so parity is testable.

**Tests.** Each validator writes rows the dashboard can render; the LLM-assist paths cannot
overrule a deterministic verdict (an LLM "yes" on a failed excerpt match stays failed);
batch and sync paths produce identical rows on the fixture; a run with a planted unverified
claim fails its citation evaluation.

**Acceptance.** Every completed run carries evaluation rows for all eight §2.10 run-time
metrics, written by the same `aer/eval` arithmetic CI trusts.

---

## Task 40 — The red-team challenger

**Objective.** A separate context that attacks the thesis, scored and recorded — the defence
against self-consistent nonsense.

**Build.** The red-team agent: adversarial prompt, **no access to the bull thesis's working
notes** — it receives the draft's claims and evidence index, not the drafting context. Runs
on the batch path from task 39. Challenges are structured: dimension, severity, the evidence
each rests on. Each becomes a `disagreements` row via the task 19 ladder, so resolution is
recorded, never silent. Cost routed and capped like any role.

**Tests.** Context isolation is structural (the input type cannot carry working notes); ≥3
scored challenges on the fixture draft; challenges citing no evidence are rejected; each
challenge lands as a disagreement with the ladder's states reachable; batch parity.

**Acceptance.** A fixture run with a planted contradiction produces a challenge that
materially contradicts the base thesis on a scored dimension — the escalation task 41 will
gate on.

---

## Task 41 — The escalation engine and the Gate 2 dashboard

**Objective.** Any one trigger pauses the run and says why, in a place the operator cannot
miss.

**Build.** The trigger engine over the §2.4 table: coverage floors, credible-source
conflict, potential look-ahead, model uncertainty, missing sections, skill-policy clamps,
cost threshold, validation failure, suspicious source, thesis disagreement. Deterministic
code over recorded rows — no trigger consults a model. Gate 2 becomes the full review:
validation results, coverage matrix, disagreements side by side, calculations, cost, and
the escalation banner naming each fired trigger. Server-rendered in the established
pattern.

**Tests.** A trigger matrix — each trigger fired in isolation on a seeded scene pauses the
run and names itself; a clean run raises no banner; the planted-contradiction and
planted-look-ahead fixtures escalate end to end; the dashboard renders every state without
JavaScript.

**Acceptance.** The two Phase 4 acceptance scenarios in `docs/PLAN.md` — planted
contradiction and planted post-dated source — both escalate, visibly, with the trigger
named.

---

## Task 42 — The adversarial corpora, and containment in the blocking gate

**Objective.** Prove the skill-file containment the way injection resistance was proved:
with a corpus of real attacks that must all fail, forever, in CI.

**Build.** `fx_skill_adversarial` — twelve skill files, one per escalation in threat T19:
weaken evidence policy, widen tools, set the rating, exceed budget, disable citations,
override PIT, and the rest. `fx_custom_section` — six well-formed skills including two
deliberately awkward schemas, for contract conformance. Two corpus-backed measurements join
the blocking gate: **skill-file privilege containment** (successful escalations, must be 0)
and **custom-section contract conformance** (must be 100%), through the same
observation-and-metric machinery as the existing eight.

**Tests.** The gate corpus tests themselves: every T19 escalation is represented; each
adversarial skill is shown to fail *at the layer that should stop it* (composer, contract,
registry — not merely "somewhere"); the sabotage pass for this task disables one containment
layer at a time and every corpus entry that layer guards goes red.

**Acceptance.** 0 successful escalations, asserted continuously; the gate grows to ten
blocking measurements without loosening any existing one.

---

## Task 43 — The skills library, editor and dry-run

**Objective.** The authoring surface: write a skill, see what it composes to, try it against
a finished run without spending a full run to find out.

**Build.** Skills library and editor pages (SSR, the established pattern): create, edit —
each edit a new version — enable and disable; line-level frontmatter errors inline; the
**composed-policy preview** showing exactly what the additive rules did to each request.
Import shows a diff and requires explicit confirmation, and imported skills get the same
validation and hashing (threat T20's confirmation-diff; the starter library and
export tooling stay in Phase 6). Dry-run: execute one skill against a chosen previous run's
stored evidence, at its own cost cap, rendering the section as it would have appeared.
APIs: `GET/POST/PUT /api/skills`, `POST /api/skills/validate`,
`POST /api/skills/{key}/dry-run`, `GET /api/runs/{id}/draft`.

**Tests.** Editor round-trip preserves body byte-for-byte; the preview matches what a real
run composes (same code path, asserted); dry-run spends within its cap, touches only stored
evidence, and leaves the source run untouched; import-without-confirmation is refused;
Playwright covers author → validate → enable → dry-run.

**Acceptance.** The §2.12 user story holds: write a skill file describing a section, enable
it, and see it appear in a draft with its own cited evidence — with the dry-run making the
loop minutes rather than a run.

---

## Known issue, not yet scheduled

The browser suite's ``test_run_console.py`` is flaky against the session-scoped e2e
server: failures rotate between tests run to run, every test passes alone, and the
behaviour reproduces identically on the task-34 commit before the skills work existed —
so it is an unfixed race in how those tests share a live server (SSE and gate timing),
not a functional regression in anything recent. It has widened from "combined runs only"
(first recorded in task 31) to intra-file. Worth its own fix before the Phase 4 surfaces
(tasks 41 and 43) add more e2e weight to the same server.

## Deliberately not in Phase 4

Report styling, PDF, charts, the polished custom-section default template and Obsidian
(Phase 5 — the generic renderer from task 10 carries custom sections until then). Built-in
sections editable as skill files, skill export and the starter library (Phase 6, threat T20's
remaining half). Sector-specialist models. Multi-user, auth, cloud. Any agent role not named
here — each addition needs an ADR first, and the registry enforces that from task 33 onward.
