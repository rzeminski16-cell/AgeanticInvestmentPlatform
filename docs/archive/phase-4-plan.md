# Phase 4 — task sequence (tasks 33–43)

Continues from `docs/archive/phase-3-plan.md`. The phase specification — objective, deliverables,
acceptance criteria — is `docs/archive/PLAN.md` → Stage 3 → Phase 4, and it remains the authority.
This file is the dependency-ordered breakdown of it.

**Objective, restated.** The judgement layer, tightly bounded. Phases 1–3 built a platform in
which every fact traces to a hashed artefact and every number to a recorded calculation; what
runs today is one linear workflow with one agent in it. Phase 4 adds the agents that plan,
investigate, interpret, challenge and validate — and the skill-file engine that lets the
operator add analysis sections of their own — without any of them acquiring a way to weaken
the evidence contract the first three phases enforced.

**The rule this phase is mostly about.** The workflow-versus-agent boundary of
`docs/archive/PLAN.md` §2.5: *the graph is code, the judgement is the LLM*. Every agent has a typed
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
here instead: the **skills table and loader** (`docs/archive/PLAN.md` §2.2 marked "schema + loader
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
  methodology guidance; they may not override built-in sections in the MVP. `docs/archive/PLAN.md`
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
concurrently under a semaphore (max 7 workers, per `docs/archive/PLAN.md` §2.5). Per-node
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

**Delivered (2026-08-06).** Migration 0021: ``plan_skill_pins`` — one row per enabled skill
per plan, referencing the immutable ``skill_versions`` row, carrying the composed policy
*as approved* (snapshotted with every clamp, because a floor that moves between approval
and execution must not silently change what runs) and the section's cost estimate; plus the
foreign key ``section_definitions.skill_id`` promised in migration 0006's comment. The plan
step sets ``job.plan_id`` — the column existed from Phase 1, unwritten until something
needed "which skill versions shaped this run?" answered — so pins resolve job → plan →
versions.

Applicability is a pure matrix (`aer/core/skill_applicability.py`) with a reason on every
skip: markets from the exchange, analysis modes, company and sector scopes, and sector
exclusions that fire **only on a known classification** — an unknown must not quietly
disable a global skill for every first-time company, while a sector-*scoped* skill reads
the other way and does not run on hope. Custom sections project into
``section_definitions`` as ``origin='skill'`` rows (the Phase 1 registry built for exactly
this), version-bumped when the projection changes, keeping their position across contract
edits. Gate 1 lists every pin with version, cost ceiling and clamps; the payload hash
covers them, so approving one set of skills is not approving another; the pinned budgets
join the estimate the operator approves against.

Two deliberate scope notes, both reversed in task 38: projected custom definitions are
filtered out of the generic drafting path — planned and approved but not run, because
executing user prose before the ``<user_skill>`` contract exists would be containment
theatre — and the composer intersects against ``PLANNED_CUSTOM_SECTION_TOOLS``, the
constant that becomes the registry's ``custom_section`` allowlist when the agent lands,
with a test to pin the two together from that day. The drafting boundary is tested where it
actually bites: on the *second* run, when the projection already exists. Fifteen sabotage
mutations, fifteen caught — two after their no-op mutation strings (``[] or [...]``) were
rewritten honestly; that trap is now twice-learnt.

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

**Delivered (2026-08-06), with one design decision worth the ADR (0036).** The workers do
not use provider-level tool calling — **the model asks, in a schema; code decides**. Each
turn returns either typed tool requests or the final report, never both, never neither;
every request is authorised against the ``analysis`` role's registry allowlist *before*
any executor is consulted, and executed deterministically in ``aer/services/research.py``.
There is no tool-use surface in the provider at all, so an instruction smuggled into a
fetched document has nothing to invoke — the strongest available form of "assert at the
registry, not the prompt". The provider protocol stays two operations, and the fake
provider scripts multi-turn workers with a stateful callable.

The bounds are code: twelve executed calls per worker (§2.5), the thirteenth refused with
the budget named; refusals of unlisted tools cost nothing, so a poisoned document cannot
burn a worker's budget by asking for capabilities it will never get — tested with a budget
of two, where a wrongly-consuming refusal would starve the legitimate search behind it.
Findings must cite evidence (an uncited finding is "a hunch wearing a label" and refuses to
validate), and cited ids are checked in code against the run's own tables — an id from
another run or a fabrication is fed back with the problem named, and a worker that cannot
fix it fails loudly. Evidence channels are split: our own rows travel as data, anything
text-bearing from outside (source titles included) reaches the model only inside
``<untrusted_source>`` delimiters.

The slice gains its first real fan-out: ``calculate`` plus the five ``research_*`` nodes
form one six-node wave (inside the §2.5 bound of seven), with ``draft`` joining on all six.
The ARQ worker passes its session factory through ``runs.execute``, so production and e2e
runs fan out for real while every savepoint-fixtured test keeps the deterministic serial
order. The slice now makes six judgement calls — the planner and five workers — and the
spend test names each schema so a seventh call fails in CI rather than on a bill. Eleven
sabotage mutations, eleven caught first pass.

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

**Delivered (2026-08-06), ADR 0037.** The ``custom_section`` role joins the registry with
§2.12's three tools — a test pins its allowlist to the composer's
``PLANNED_CUSTOM_SECTION_TOOLS``, so the set a skill is composed against and the set the
role holds cannot drift. The registered contract is a fixed envelope
(``CustomSectionDraft``: content plus proposed claims, ``extra="forbid"``); the section's
own ``output_contract`` is validated by deterministic code (``aer/core/section_output.py``),
closed-world — so a rating is unwritable three times over: undeclarable in a contract
(task 35), unrepresentable in the envelope, and refused as an undeclared content key. The
executor additionally refuses to *run* a doctored contract carrying a reserved field,
spending nothing.

The composed prompt runs in the fixed order: platform contract (the base agent's
immovable prefix), the section's schema in the role instruction, structured evidence as
data, then the operator's text inside ``<user_skill>`` delimiters — neutralised inside
the body exactly as the untrusted wrapper neutralises its own, so the text cannot close
its quotation and continue as the frame. Quoted excerpts trail everything in
``<untrusted_source>`` blocks. Evidence is gathered by code, gated by the pin's snapshot
(never recomposed): ``search_facts`` admits the run's facts and calculations,
``search_sources`` its admissible sources and excerpts within the composed tier ceiling.
Truncation to the pinned token budget drops whole units — listing, excerpt and validation
index together, so an id the budget dropped is an id the validator refuses.

One call, one retry with the problems named, then the section is marked ``failed`` with
its reasons on the row and the run continues. The bare-numeral rule is exact: every
numeral in the content — prose and JSON numbers alike, ``confidence`` exempt as renderer
metadata — must appear in a numeric claim naming a stored fact or recorded calculation.
Claims and citations go through ``record_claim``/``record_citation`` unchanged and the
gate 2 verifier confirms or refuses them like any other. Evidence short of the composed
policy generates under an explicit "Insufficient evidence" banner (rendered above the
content) with confidence floored at 0.3 — never fabricated prose. The moat-durability
example runs end to end on the fake provider: planned, priced, approved with its pins in
the gate hash, drafted with verified citations, banner showing (it demands three sources;
the run holds two), rendered with ``reports.rating`` still ``None``. Seventeen sabotage
mutations across the executor, the wrapper, the scan, the registry and the workflow
wiring.

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

**Delivered (2026-08-06), ADR 0038.** Migration 0022: ``evaluations`` — one row per metric
per run, with a **nullable verdict**: NULL value and NULL verdict together mean *not
exercised* (a run with no post-dated source gave look-ahead recall nothing to catch), and
a check constraint stops a row claiming a score without a verdict. The run-time eight are
the §2.10 rows a live run can honestly answer — citation accuracy, hallucinated-citation
rate, temporal compliance, look-ahead recall, source coverage, primary-source ratio,
numerical consistency, assumption completeness — named in ``aer/eval/metrics.py``
alongside a ``BLOCKING`` tuple, so the CI gate's set and the run-time set share one
vocabulary and one thresholds table without pretending to be the same eight (injection
resistance and unit integrity need corpora of attacks and mismatches, which a
well-behaved run does not contain; the two coverage metrics are meaningless against a
fixture). ``aer/eval/runtime.py`` carries the run-time arithmetic — same ``MetricResult``,
same quantisation, same empty-population refusal, pure and tested against handwritten
rows exactly as the gate's metrics are.

The four validators live in ``aer/services/evaluations.py`` and a ``validate`` workflow
step between draft and gate 2. Citation: the deterministic verifier runs first and its
verdicts are the rows; whether a failure is fabrication-shaped is decided by re-asking
the platform's own admissibility question of the source row, never by parsing error
text, so a quarantine refusal is the temporal family's failure and not a phantom
hallucination. Temporal reuses the CI gate's own ``SourceObservation`` and functions —
the fixture semantics are the quarantine rules. Numerical is the task 32 replay harness
over the run's rows; coverage holds each section to the floor its definition carries,
custom sections to their pinned composed policy, with a section's evidence drawn from
its claims' citations, its facts' documents and the source references in its content.

The LLM assists (the ``validator`` role, ADR 0038) locate candidate excerpts for
unresolved citations and adjudicate undated sources — **advice only**, recorded in the
row's details, with the tests pinning that a confident "yes" on a failed match changes
neither the metric nor ``excerpt_verified``. Capped at four questions per validator per
run; a clean run asks nothing. The provider protocol gains
``complete_structured_batch`` — request order guaranteed, all or nothing — implemented
against the Messages Batches API with the SDK's own ``transform_schema`` (the batch
path's version of the sync path's first-live-call lesson), polling with backoff to a
deadline; the fake provider answers batches from the same script as the sync path, and
``Agent.run_batch`` gives every item the composed prompt, the pre-spend cap refusal and
its own archived, metered ``agent_runs`` row. Batch and sync produce identical rows on
the fixture, asserted. Fifteen sabotage mutations across the service, the arithmetic,
the batch transport, the registry and the workflow wiring.

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

**Delivered (2026-08-06), ADR 0039.** Isolation is a property of the input type:
``RedTeamInput`` has fields for the draft's recorded claims and the run's evidence index
— facts, calculations, sources, by id — and no field for section prose or worker notes,
with ``extra="forbid"`` refusing anything smuggled under another name. The service builds
the input from the tables alone; a test plants a marker in the drafting context and
proves it cannot reach the composed prompt while the claims do. The role holds no tools
(a challenger that could fetch would build its case from material the base thesis never
saw), and the dimensions are a closed vocabulary, because "on a scored dimension" only
means something the platform can group and compare by.

A challenge citing no evidence fails the response schema; one citing an id the run does
not hold is rejected whole — an argument resting partly on fabricated evidence is a
fabrication with good footnotes. Each survivor lands on the task 19 ladder's thesis rung:
escalated to gate 2, never auto-resolved, both positions stored, recording idempotent on
the challenge's own content digest. Materiality follows severity (``thesis_conflict``
gained a ``material`` argument): at 4/5 or above the challenge materially contradicts the
thesis — the §2.4 banner state task 41 gates on — below it the quibble is published
without the banner. The planted-contradiction fixture produces exactly that: a
severity-5 growth challenge citing the run's own declining revenue fact, material,
escalated, visible to ``escalations_for_job``.

The run's one adversary call travels ``Agent.run_batch`` (§1.8 prices the bear case on
the batch path), with the sync path kept and proven row-identical. A draft that recorded
no claims skips the adversary visibly and spends nothing. Because challenges join the
gate-2 payload as escalations, **the payload hash the final gate verifies moved to the
red_team step** — the last step that can change what the operator is shown — and every
surface that read the draft step's hash now reads the adversary's. Twelve sabotage
mutations, including prose leaking into the adversary's context and a thesis conflict
auto-resolving, all caught.

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

**Acceptance.** The two Phase 4 acceptance scenarios in `docs/archive/PLAN.md` — planted
contradiction and planted post-dated source — both escalate, visibly, with the trigger
named.

**Delivered (2026-08-06).** The engine is pure and lives in the correctness core:
`aer/core/escalation.py` (`mypy --strict`) holds the ten §2.4 conditions as a closed
`TriggerKind` vocabulary in the table's own order, evaluated over row-shaped scenes —
metric scores, section coverage verdicts, conflict rows, policy clamps, source flags and
a cost picture — with the thresholds as named constants (confidence floor 0.5, cost alert
at 80% of cap). No model is consulted and no I/O happens, which is load-bearing rather
than tidy: **the fired triggers ride inside the gate-2 payload, inside the approval
hash**, so the hash the red-team step seals and the hash the review page computes live
must agree, and they can only agree if the triggers are a pure function of rows that are
frozen by then. "Approved with the look-ahead banner showing" is thereby a verifiable
statement, and a trigger that fires after sealing invalidates the stale approval — which
is correct, because the evidence changed.

The service (`aer/services/escalation.py`) only loads: evaluations rows (with advisory
*disputes* — a validator locating an excerpt the verifier failed, or dating a source the
platform holds undated — feeding the uncertainty trigger), per-section coverage through
the evaluations service's newly public `section_coverage_for_job`, the disagreements
ladder (a conflict a person has already settled does not re-raise the banner; one a rule
settled does), planned pins' clamps, source flags, and the same `costs` sum the budget
guard enforces. The allowlist half of "suspicious source" stays where it is enforced — in
`aer.fetch`, which refuses off-policy hosts before a source row can exist — so the
trigger reads the injection flag, and the docstring says why that is the whole of it.

An undecided final gate now pauses with the fired triggers in its message and context, so
the console names them before anyone opens the review page; a decided gate falls through
to the ordinary approval check. The review page is the full §2.4 dashboard,
server-rendered with no script: the trigger banner (kind, message, evidence), the
validation table with pass/fail/not-exercised verdicts and named failures, the coverage
matrix per section against its own floor, every disagreement side by side with both
positions, the calculations with formulae, and cost against estimate and cap. The plain
slice honestly fires two triggers — its executive summary cites nothing — and the tests
lean on that: the pause names them, the sealed hash covers them, the page shows them.
Forty-one tests (ten-trigger isolation matrix, clean scenes, service loads, the
all-ten-at-once ordering pin, slice end-to-end) plus the planted-contradiction fixture
asserting the thesis banner and a page test for every dashboard section. Sixteen sabotage
mutations — thresholds slipping, settlements forgotten, triggers vanishing from payload,
pause or page — all caught after the first pass exposed an unpinned trigger order and the
ordering test grew to cover the whole vocabulary.

Two pre-existing suite failures surfaced and were repaired in passing: task 40's hash
move had missed the FINAL-gate approvals in `test_report_sections` and
`test_unmapped_gate` (both still read the draft step's hash, which no longer exists), and
`test_calc_api`'s truncate-at-setup fixture left its final test's committed company in
the shared database, where the next file to seed the same CIK hit a unique-constraint
violation — it now truncates at teardown as well. The unit suite is fully green
(`just test`, 3354 passed); the three browser flakes in `tests/e2e` remain the
documented pass-alone kind and pass alone.

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

**Delivered (2026-08-06), ADR 0040.** `tests/fixtures/fx_skill_adversarial/` holds twelve
skill files, one per T19 escalation across seven families — weaken evidence
(`min_sources`, `requires_primary`, `max_tier`), widen tools (shell/file/email, any-URL
fetch), set the rating (rating, target price, recommendation), exceed budget, disable
citations in prose, override point-in-time with an unknown key, and close the
`<user_skill>` delimiter. `tests/fixtures/fx_custom_section/` holds six well-formed
skills, two with deliberately awkward contracts (sixteen fields at the size ceiling; a
made-up type word and a nested schema object), each with labelled conforming *and*
violating outputs. `tests/skill_corpus.py` runs every file through the **real** layers —
`parse_skill_file`, `compose_policy` against the real role allowlist, `wrap_user_skill`,
and the `section_output` checks — and derives the containing layer from what happened,
never from the label, so the corpus notices a defence that has silently moved as well as
one that has died.

Two metrics joined `BLOCKING` in §2.10's order, taking it from eight to ten:
**custom-section contract conformance** (agreement with labels, must be 1 — the corpus
carries violations so a validator that accepts everything cannot score full marks, and
the metric refuses a violation-free corpus) and **skill-file privilege containment**
(successful escalations ÷ attempts, must be 0). Two `ContainmentObservation` /
`ConformanceObservation` records joined `aer.eval.observations`, two functions joined
`aer.eval.metrics`, and `evaluate_all` grew two arguments. The reserved-field refusal
moved into the pure core as `section_output.reserved_fields_in`, and the contract
projection became public as `resolution.contract_schema`, so the gate scores the deployed
checks, not copies.

The corpus-quality tests are the load-bearing half: every escalation is contained *at its
owning layer* (a reserved field caught at execution instead of authoring means the
authoring refusal died and a backstop is carrying it — invisible to the zero-breaches
metric, so asserted separately); every named family is present; frontmatter refusals name
the attacked field; composer containments carry their clamp receipts (the same receipts
§2.4's clamp banner reads). Twelve sabotage mutations — one per containment layer plus the
two metrics' own logic and the `BLOCKING` tuple — all caught: relax the `min_sources`
floor and `zero_min_sources` succeeds, drop the reserved-field check and three files
succeed, stop neutralising the delimiter and `close_the_boundary` escapes.

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

**Delivered (2026-08-06), ADR 0041.** Three surfaces over two new services. The read side
(`aer/services/skill_authoring.py`) answers the questions an author asks before anything
is written: line-level validation through the same `parse_skill_file` the save path uses,
a **composed-policy preview** through `compose_for_version` — *the same function plan-time
resolution calls*, now public — over a version row built exactly as `save_skill` builds
one, and an import diff. The preview showing what a run would pin, rather than what the
file asked for, is the point: a test holds it against a pin a real plan produced, not
against the composer called twice.

The write side keeps its existing service: `POST /api/skills` saves version n+1, `PUT
/api/skills/{key}` refuses a file whose frontmatter names a different skill from the one
the editor is open on, `POST /api/skills/validate` writes nothing, `POST
/api/skills/{key}/enable` is its own decision, and `POST /api/skills/import` shows a diff
without a confirmation and applies only with one whose hash covers the key, the incoming
file **and** the version it replaces — so a confirmation made against a diff that has
since gone stale is refused (threat T20). SSR pages at `/skills`, `/skills/new`,
`/skills/{key}` and `/skills/import` post plain forms with CSRF, render the effective
policy and every clamp with its reason on the server, and put frontmatter issues beside
the lines they belong to.

`aer/services/skill_dry_run.py` executes one section against a finished run's evidence
through the real `execute_custom_section`, the real claim and citation services and the
real renderer. **It gets its own job, plan, pin and section, marked `skill_dry_run_v1`**,
so isolation is structural rather than careful — nothing it writes carries the source
run's id. `execute_custom_section` gained an explicit `evidence_job_id` (facts and sources
belong to a request; recorded calculations belong to a job), defaulting to the executing
run, so a real run is unchanged and the rehearsal says out loud whose figures it may cite.
The call is real, so the same `BudgetGuard` runs before it against the same per-request
cap and the same meter writes the same cost rows. The web process therefore holds a
provider for this one endpoint, built lazily on first use — every other spending path
still goes to the worker.

Forty-four tests: preview-versus-pin agreement, clamps with reasons, byte-for-byte editor
round-trip, versions accreting, import diff and stale-confirmation refusal, dry-run
isolation (the source job ends with no sections, no claims and no steps), the source run's
calculations reaching the prompt, metering to the column's own precision, a cap refusal
before the call, plus the JSON API and the server-rendered pages. A Playwright suite
drives write → validate → save → enable → dry-run in a browser with no JavaScript, using
a provider patched at `aer.api.deps.build_provider` rather than a configuration switch —
a settings-level fake would exist in production too. Eighteen sabotage mutations across
the preview, the import confirmation, the dry run's isolation and budget, the ownership
check and the templates.

---

## Fixed after task 43: the browser suite's long-standing flake

For most of the project the browser suite failed randomly in combined runs — failures
rotating between tests, every test passing alone, the behaviour reproducing on commits
long predating the code it landed on. It was two unrelated faults wearing one costume,
and both are fixed.

**The abandoned connection.** Every rotating failure was pytest raising
`ExceptionGroup: multiple unraisable exception warnings`, whose members were asyncpg's
`ResourceWarning: unclosed connection` (and the same connection again as an unclosed
transport and an unclosed socket to port 5432). Each e2e test starts its own uvicorn
server and stops it with `timeout_graceful_shutdown=1`; a request still in flight is
cancelled, and a request cancelled mid-query cannot hand its connection back to the pool
because closing one is itself an `await` and the loop is going away. The connection is
left to the garbage collector — harmless for a process that is exiting, which is what a
real shutdown is, but this process carries on, and `filterwarnings = ["error"]` turns the
eventual collection into a failure of **whichever test the collector interrupts**. That
is precisely why the failures rotated. `live_server` now forces that collection at
teardown with `ResourceWarning` silenced for the duration, so the leftovers are finalised
where they belong.

**The meta-refresh race.** With the noise gone, a second fault was visible underneath at
about one run in ten: `Page.goto: ... interrupted by another navigation`. The console's
no-JavaScript fallback is a `<meta http-equiv="refresh" content="5">`, and a browser left
parked on the console — by a fixture, or by a test doing slow work between steps — fires
it straight into the next navigation. The tests assert the fallback is *present*, never
that it fires, so the e2e server now renders it with an interval no test outlives.

**Not a fix:** an earlier hypothesis was that a client disconnecting mid-poll stranded a
connection in `aer.api.sse`, and a shielded-task rewrite was written for it. Measurement
refused it — the property already holds, on the original code, under cancellation
mid-query — so the rewrite was reverted rather than shipped on a hunch. What remains from
that investigation is
`test_a_reader_that_leaves_mid_query_does_not_strand_a_connection`, which pins the
property as a characterisation test and says so.

Ten consecutive full browser runs are green (58 passed) where three consecutive runs
before the fix failed every time.

## Deliberately not in Phase 4

Report styling, PDF, charts, the polished custom-section default template and Obsidian
(Phase 5 — the generic renderer from task 10 carries custom sections until then). Built-in
sections editable as skill files, skill export and the starter library (Phase 6, threat T20's
remaining half). Sector-specialist models. Multi-user, auth, cloud. Any agent role not named
here — each addition needs an ADR first, and the registry enforces that from task 33 onward.
