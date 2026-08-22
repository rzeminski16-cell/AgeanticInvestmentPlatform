# ADR 0067 — A tool is a registered capability, not a package

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** `docs/investment-os.md` §7. ADRs 0068 to 0077 each describe a capability
that has nowhere to register itself until this record exists.
**Extends.** ADR 0035, one level up: the rule that admits an agent role now admits a tool.

## Context

The platform is becoming an Investment OS — watchlists, decisions, positions, thesis
monitoring, risk, a trade journal, post-trade review. This record answers the narrow
question that comes before all of them: **how does a second tool come to exist in this
codebase?**

Most of the answer is already written. The equity research tool is not really an equity
research tool; it is an evidence-and-arithmetic engine with one workflow mounted on it.
`aer/workflow/engine.py` is 1,083 lines, and everything it imports from this platform is
`JobStatus`, `canonical_json`, `sha256_hex`, `Cost`, `Job`, `JobCancellation`, `JobStep`,
two errors and `span`. No company, no filing, no fact, no report. `aer/sections/render.py`
is 684 lines and imports `HouseStyle` and `aer.render.display`; it renders a section from
its JSON Schema and could not name an equity concept if asked. The fetch layer, the
artefact store, the extraction locators, hashing, CSRF, templating and the payload-hash
approval machinery are all the same story.

So the framework is not a thing to be built. It is a boundary that mostly exists and that
nothing declares — and an undeclared boundary erodes the first time a convenient import
crosses it.

What is genuinely equity-shaped is small and enumerable. Five modules import
`vertical_slice_v1` by name; `approvals.request_id` is a `NOT NULL` foreign key to
`research_requests`; `Agent._refuse_what_cannot_be_afforded` walks `Job → ResearchRequest`
before any model call may spend, so nothing calls a model outside a research mandate.
Those are ADR 0068's problem. This record decides only how a capability is admitted, and
where the boundary around it is enforced.

## Decision

**A tool is a row in a registry, written in the exact idiom of
`aer/agents/registry.py`.** `ToolDefinition` is a frozen, slotted dataclass carrying the
tool's key and title, the subject kinds it operates on, its mandate model, its workflows,
the agent roles it may use, its API and page routers, its navigation entries — and the ADR
that admitted it. Contracts and models are named lazily as `"module:Attribute"` strings,
the `function_ref` idiom, so asking a registry question does not drag every tool's package
underneath the asker.

**The ADR field refuses to be empty, and a test walks the references to files.** ADR 0035's
rule verbatim, and it earns its place again for the same reason: the registry raises on a
blank `adr`, and `test_every_role_names_an_adr_that_exists` already proves the shape works
by globbing `docs/adr/{number}-*.md`. Admitting a tool without writing down why is
therefore not a smaller diff than doing it properly — it is a red build.

**Discovery is an explicit `INSTALLED_TOOLS` tuple. Never entry points, never
`pkgutil.walk_packages`.** This is not taste. Dynamic discovery would make the contents of
`Base.metadata` a function of what happens to be pip-installed, and
`tests/test_migrations.py` runs the real chain and then calls Alembic's `compare_metadata`
against exactly one `Base.metadata`. Metadata that varies with the environment turns that
comparison into a test failing on somebody else's machine for reasons nobody can reproduce.
`src/aer/db/models/__init__.py` already settled this for models — thirty-eight explicit
imports, with the docstring stating that a forgotten one is "silently absent from every
migration". Explicit-import-is-registration is the house pattern, and a registry that
discovered itself would be the exception that breaks the drift probe.

### The word "tool" is already spent, and the collision is deliberate

`RoleDefinition.allowed_tools` means a capability an agent may request — `search_facts`,
`fetch_known_url` — and invariant 8's force is that those are authorised in code. A
`ToolDefinition` is a product surface with its own tables, workflows and pages. This record
keeps the user-facing word and disambiguates structurally rather than lexically: agent
tools are strings inside a `RoleDefinition`, product tools are `ToolDefinition` rows, and
the one place the two meet is the privilege rule below — which is where a reader should be
paying attention anyway.

## No package move

A full `aer.kernel.*` / `aer.tools.*` split is the obvious shape and it is rejected.

It would touch 279 source files and 183 test files. It would invalidate file references
across 66 ADRs — records whose value is that they still point at the code they decided —
and break `tests/test_knowledge_map.py::test_every_module_appears`, which walks the
top-level entries of `src/aer` and asserts each is named in `docs/knowledge-map.md`. It
would change the `mypy` strict globs, `aer.core.*` and `aer.calc.*`, which are the reason
the correctness core is provably pure. And it would collide with the active report-quality
work for weeks.

**It buys a boundary an AST test enforces for nothing.** The boundary this codebase needs
is a runtime one — kernel code must not know what tools exist — and a directory layout is
a weak way to say so, because an import crosses a directory as easily as it crosses a
comment. A test that parses `src/` and fails when a kernel module imports from a tool
package says it exactly, and it is the same shape as ADR 0018's single-writer test, which
parses the tree to prove `aer.verify.citations.verify` is the only writer of
`excerpt_verified` — the strongest structural control in the repository, and it required
no packaging at all.

The honest cost is that the layout stops describing the architecture: a newcomer learns the
boundary from `docs/knowledge-map.md` and a test rather than from `ls`.

## The privilege rule

**`__post_init__` must intersect a tool's declared `roles` against `registered_roles()`,
and must never union.** This is the one line in the whole design that is not negotiable.

Invariant 7 makes skill files additive-only and proves it with an adversarial corpus.
`aer/core/skill_policy.py:170` is that rule in code — `granted_tools =
frozenset(requested_tools) & role_allowlist` — so a skill asking for a capability its role
does not hold gets the empty set and a recorded refusal, not the capability. **Nothing
equivalent guards a tool descriptor**, and a `ToolDefinition` is a far larger grant than a
skill file: it names roles, routers and tables in one frozen literal.

If `roles` unioned, a tool would hand itself an agent capability the registry never
admitted, and ADR 0035's enforcement would have been routed around by a data structure
written a year later. Worse, the containment that proves invariant 8 would quietly change
meaning: `tests/test_injection.py::test_no_role_has_a_network_tool` iterates
`registered_roles()`, so a role reachable only through a tool descriptor is a role that
test never sees. An assertion scoped to the wrong set is more dangerous than no assertion,
because it reports green.

Intersection fails in the right direction: a tool naming an unregistered role gets nothing,
the mismatch is refused at import, and the only way to add a role remains ADR 0035's row
and ADR 0035's decision record.

## The gate vocabulary

This is the contested part of this record, and it is stated as such. `GateKind`
(`aer/core/enums.py:123`) is a closed Postgres enum with eight values, shared by
`approvals.gate` and `disagreements.escalated_to_gate`. Three options were live:

1. **Keep the closed enum.** Every new tool then migrates a shared type it does not own.
   Migration 0048 is the worked precedent: `ALTER TYPE gate_kind ADD VALUE` in an autocommit
   block, and a downgrade that rebuilds the type, converts both columns through `text`,
   drops and renames. Correct, and unpleasant to repeat per tool for a value only one tool
   means anything by.
2. **Convert the column to `TEXT`.** Cheap, and it discards the constraint ADR 0005 exists
   to argue for — "constraints that can be expressed in the schema are expressed in the
   schema" — so a typo in a maintenance script could record a decision at a gate that does
   not exist.
3. **A `tool_gates` reference table**, primary-keyed `(tool, gate)`, seeded from the
   registry by migration, with `approvals` and `disagreements` carrying a real composite
   foreign key into it and a test asserting the table and the registry agree.

**The third is decided.** The vocabulary is owned by the registry in Python — that is the
whole point of this record — so a Postgres enum listing the same values is a second source
of truth that can drift from the first and is only caught at `INSERT`, at the moment
somebody is trying to approve something. A reference table gives one source of truth, real
referential integrity rather than a type check, and no shared-type migration per tool:
adding a gate becomes an insert, and removing one a delete that fails loudly if a decision
was recorded against it. ADR 0013 already established that a vocabulary can be rows —
report sections are `section_definitions`, not code — and this is that argument applied to
gates.

The costs are real. `approvals` and `disagreements` each gain a `tool` column to carry the
composite key, stored rather than joined because `approvals.job_id` deliberately carries no
foreign key. Integrity moves from "this value is a member of a type" to "this pair exists
in a table" — weaker read locally, stronger read across tools — and the drift test becomes
load-bearing, because if the seed and the registry disagree the database wins at runtime.

**This one was decided by the author rather than settled with the operator, and it is the
piece of this record most worth overturning.** Option 1 stays defensible for anyone who
weighs a native enum's local clarity above the per-tool migration cost. Reversing it after
the first `tool_gates` rows exist is a data migration rather than a refactor, so the moment
to disagree is now.

## One database, one schema, one chain

**One database, one `public` schema, one linear Alembic chain.** `migrations/env.py` sets
`include_schemas=False`; `aer/db/schema_check.py` calls `inspector.get_table_names()` with
no schema argument, so the runtime drift probe sees the default schema and nothing else;
`tests/test_migrations.py::test_there_is_exactly_one_head` asserts a single head because
"two heads mean someone branched without merging". Per-tool schemas or migration branches
would mean editing all three, plus every `__table_args__` and the search path in every
session, to obtain isolation the design does not want. **Cross-tool foreign keys are the
point**: a position, an attestation and a report claim are worth having in one database
precisely because they can reference each other and be joined in one query.

The honest cost of staying linear is an occasional one-line `down_revision` conflict when
two branches each add a migration — a merge resolution, not a design problem.

## Consequences

**Adding a tool becomes a reviewed table entry plus routers.** One `ToolDefinition` in a
file whose whole purpose is to be looked at, its routers included by key, its navigation
contributed rather than hand-written. `_nav.html` is eight hand-written anchors today with
no active state and nothing detecting drift from the real routes, and `create_app` is
fourteen literal `include_router` calls; both become iterations over the registry, which is
what turns "a tool contributed a page nobody can reach" into a test failure instead of a
silent lie.

**The registry refuses a tool with no ADR**, so each of ADRs 0068 to 0077 is a precondition
for code rather than a document written afterwards. That is ADR 0035's rule, applied here
because the sprawl risk at tool granularity is larger, not smaller.

**The boundary becomes something that can be breached and detected.** Before this record,
"the kernel is generic" was an observation about the current state of the imports; after
it, a property with a test attached, and the first kernel module reaching into a tool
package fails the build rather than quietly making the second tool conditional on the first.

**Nothing about the research tool moves.** It becomes the first row in `INSTALLED_TOOLS`
and keeps its package, its imports and its file paths, so every ADR reference and every
line of `docs/knowledge-map.md` still resolves. Migration is lazy and per-module, driven by
what a second tool actually needs — starting with the run root, which is ADR 0068's.
