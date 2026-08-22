# The Investment OS — framework design

**Status: draft for discussion. Nothing here is decided until it is an ADR.**

This document proposes the framework that turns a single equity-research tool into a
platform that can hold several tools, and records the decisions taken so far. It is a
design note, not a plan: `docs/PLAN.md` remains the authority on scope, and §3 below is a
question for the operator rather than an answer.

---

## 1. What is being proposed

Today the platform produces one institutional-style research report at a time. The
ambition is a workspace that supports the whole cycle around that report:

```text
Discover an idea → Research the company → Define a testable thesis
→ Assess the proposed trade → Record the position → Monitor risk and evidence
→ Review or exit → Learn from the outcome
```

The research tool is the second step. The framework is what lets the other seven exist
beside it without each one reinventing runs, gates, budgets, evidence and audit.

**What is not being proposed.** No trade execution. No broker connection. No portfolio
optimiser, no efficient frontier, no allocation solver. No multi-user deployment. No
advice: every surface keeps the disclaimer it carries today.

## 2. The finding that reframes the work

The equity-research tool is not really an equity-research tool. It is an
evidence-and-arithmetic engine with one workflow mounted on it, and most of the engine is
already domain-agnostic:

| Already generic | Evidence |
|---|---|
| Workflow engine | `aer/workflow/engine.py` — 1,083 lines importing only `Job`, `JobStep`, `Cost`, `JobCancellation`, hashing, errors, tracing. Zero equity vocabulary. |
| Section rendering | `aer/sections/render.py` — 684 lines importing only `HouseStyle` and `render.display`. |
| Evidence spine | `aer/storage`, `aer/extract`, `aer/core/schemas/extraction.py` — a locator points into an extraction, not into a subject. |
| Fetch, providers, hashing, CSRF, templating, DB base | No module mentions a company. |
| Approval machinery | `services/approvals.py` — payload hashing, ordering and refuse-double-decision are all subject-agnostic. |

So the framework is largely a matter of *declaring* a boundary that mostly exists, and
then fixing the four places where an equity mandate is genuinely load-bearing (§6).

Two patterns the UX ambition needs are also already invented here, and should be
generalised rather than designed afresh:

- **Suggested → Approved** is `assumption_proposals` → `assumptions`, with `supersedes_id`
  and a sequence. It is not a new idea; it is an existing table shape.
- **Contribute-or-fail registration** is `db/models/__init__.py` and `agents/registry.py`.
  Explicit imports plus a test that fails when you forget. Not entry points, not
  `pkgutil.walk_packages`.

## 3. Where this sits against `docs/PLAN.md` — a question, not an answer

`CLAUDE.md` says PLAN.md is the authority when the two disagree, and that an unclear
architectural choice is a stop-and-ask. Three passages bear on this expansion:

| Passage | Text | Reading |
|---|---|---|
| §2.1 | "**Non-goals (MVP).** Multi-user; portfolio management; real-time or intraday data; trade execution" | Scoped to the MVP. Going beyond it is a scope *extension*, not a contradiction — but PLAN should say so rather than be silently outgrown. |
| §2.3 | "Portfolio optimisation — **Never (out of product scope)**" | Not contradicted. We propose no optimiser. Position *sizing* is adjacent and must be kept clearly distinct from optimisation. |
| `gap-analysis.md` B12 | "Multi-company and portfolio views — named here so it stays a decision rather than an oversight" | Directly in scope of this change. B12 is the entry that this work reopens. |

**Recommendation:** amend §2.1 to move portfolio management from MVP non-goal to a named
later stage, leave §2.3 untouched and state explicitly that optimisation remains excluded,
and rewrite B12 to record that the decision was revisited and when. Do this *before* the
first table, so the authority document and the schema never disagree.

## 4. Decisions taken

| Question | Decision |
|---|---|
| Record model | New first-class record kinds, not a widened `Fact` |
| Framework | Registry and shell built around the research tool; it migrates lazily |
| Frontend | Server-rendered Jinja + htmx retained; new sidebar shell |
| First slice | Shell + attention queue + one further tool |
| Position data | Manual entry only. No broker API, no order placement |
| Deployment | Single-user, local-first. `user_id` preserves the option; no auth work |
| Sequencing | Parallel lane to Phase 4; new agent roles wait for Phase 4's role machinery |
| Naming | `aer` stays. Documentation reframes to Investment OS |
| Attention items | Explicitly resolvable with a recorded reason, never auto-clearing |
| Market data | Nightly EODHD pull; NAV computed from stored bars |
| Extensibility | Internal tools only. No third-party plugin API |
| Dark mode | Retained; a dark variant derived from the specified tokens |

## 5. The record taxonomy

This is the load-bearing decision and the hardest to reverse once rows accumulate.

`SourceKind` in `aer/calc/units.py` is closed at three values, and says why:

> Three kinds, and the list is deliberately closed. Every number in a report resolves,
> eventually, to a fact somebody filed, an assumption somebody made and justified, or a
> calculation over those two. **A fourth kind would be a way in for a number with no
> story.**

Every portfolio figure is none of the three. A fill price is not filed, not chosen, and
not calculated. So a fourth kind is genuinely required, and the docstring above is exactly
the standard it must meet: the new kind must arrive *with* a story, not as an exemption.

### The five classes

| Class | Guarantee | Status |
|---|---|---|
| **Fact** | Somebody else asserted this publicly on a date, and here are the bytes. | Exists |
| **Calculation** | Re-running this code on these inputs reproduces this number. | Exists |
| **Assumption** | A named person agreed to this value and said why. | Exists |
| **Attestation** | This is what the book says as at `effective_at`, as known at `recorded_at`, at a stated grade of evidence. | New |
| **Judgement** | A named person held this view at this time on this stated basis. **It is not evidence for anything.** | New |

**Why `Attestation` and not `Observation`.** "Observation" is taken twice already —
`RawFact.period_key` and the `uq_financial_facts_observation` index (ADR 0058), and the
`macro_observations` table. This repository is careful about vocabulary and the word is
spent.

**Attestation has two grades**, and the grade propagates:

- *documented* — extracted from a hashed `USER_SUPPLIED` artefact (a contract note, a
  custodian statement). Full chain: artefact → extraction → locator → citation.
- *attested* — typed by the operator, self-certified, no artefact behind it.

The containment is that **a lineage containing any attested node cannot reach a shareable
rendering**, enforced by a return type with no field for the figure — the shape ADR 0034
used for `WithheldComps` and ADR 0029 for `ValuationMandate`. A flag would be argued with;
a type cannot be.

**The single most important rule in this document:**

> **A Judgement may never be a `SourceRef`.**

A conviction score that can be multiplied by a weight is a judgement laundered into a
number. Expressing this in the schema — the `claims` XOR admits an `attestation_id` and
has no column for a `judgement_id` — is what stops the whole audit story dissolving at the
point where money is involved.

### `Event` is not a sixth class

A fill, a dividend receipt and a corporate action are all Attestations with an effective
time; a sixth class would overlap on every axis except intuition. But there is real
Event-shaped work: `audit_events` correlates only by `job_id` and `request_id`, so trade
entries, position corrections and thesis edits would land *outside* the hash chain. That
would make the most consequential records in the system the least tamper-evident — an
exact inversion of the current design. `audit_events` needs a generic
`subject_kind`/`subject_id` correlation, and it needs it before the first ledger row.

### The UX provenance chip is lossy — use two

The specification's five-way chip (Suggested / Approved / User entered / Calculated /
Source fact) collapses two orthogonal axes into one control, and loses three things:

1. Suggested and Approved are *lifecycle states*, not record classes. One chip cannot say
   "a Calculation nobody has confirmed".
2. "Approved" means two different things: `Assumption.approved` (a person agreed to a
   value) and an `approvals` row carrying a `payload_hash` (a person agreed to *exactly
   this page*). The second is far the stronger guarantee and must not be hidden behind the
   same word.
3. "User entered" collapses Assumption, attested Attestation and Judgement — which have
   materially different guarantees, and are precisely the distinction an auditor needs.

**Proposal: two independent chips.**

- Provenance — `Source fact` · `Calculated` · `Attested` · `Assumed` · `Judged`
- Confirmation — `Suggested` · `Unconfirmed` · `Confirmed by <name> at <time>`

## 6. What must be fixed before any of this

Four prerequisites, in dependency order. Three of them improve the research tool on their
own, and one repairs a defect that is written down but not yet firing.

### 6.1 The lineage resolver is wrong, and it is loaded rather than firing

`SourceKind.FACT` is documented as generic — "a reported figure, traced to a filing and a
hashed artefact" — and implemented as *a row in `financial_facts`*: `_load_fact` in
`services/calculations.py` is `session.get(FinancialFact, parsed)`. Meanwhile
`services/macro.py:201` mints `SourceRef.fact(observation.id)` over a `macro_observations`
row. Those two cannot both be right, and when they meet, `_resolve_fact` gets `None` back
and the provenance viewer draws a dangling node.

**They have not met yet.** `aer.services.macro` is imported by nothing in `src/` and
nothing in `tests/` — the module is finished and unwired, so no dangling node is rendering
in production and none has been. The defect is armed, not firing, and that is a materially
weaker claim than an earlier draft of this note made.

It is still the first thing to fix, for three reasons. The wrong assumption is already
written down in two independent places — `_load_fact`, and
`services/exhibits.py::_fact_input_ids`, which treats `kind == "fact"` as `financial_facts`
and silently omits a chart point when it is not. The module that would fire it is complete
and waiting to be wired. And the fourth source kind (§5) turns a three-way branch into a
four-way one that rots the same way. Fixing it now is a migration over an empty problem;
fixing it later is a migration over rows.

### 6.2 FX is written and unwired, and NAV is blocked on it

`aer/calc/fx.py` is complete. `MacroClient.fetch_reference_rates` is defined and has zero
callers. There is no `fx_rates` table — the `fx_rate` column on `costs` is USD→GBP for
model spend, not a rate store.

`Quantity.__add__` raises on USD + GBP by design and `convert()` requires a rate carrying a
`SourceRef`, so a multi-currency NAV cannot be computed at all until a rate store exists.
Shaped like `macro_observations`, with a `services/fx.py` shaped like `services/macro.py`
and one call to the already-written client.

Two cautions. ADR 0045 makes every non-EUR pair a derived cross, so one NAV on a
twenty-position multi-currency book is roughly twenty conversions, each two source
documents plus a traced cross — recomputed daily. And ECB reference rates are, in the
ECB's own words, "not intended to be used in any market transaction": fine for translating
a book, wrong for marking one.

### 6.3 Every model call requires an equity research mandate

`Agent._refuse_what_cannot_be_afforded` walks `job_step → Job → ResearchRequest` and raises
`BrokenRecordError` if the request is absent; `jobs.request_id` is `NOT NULL`. No model
call anywhere in the platform can happen outside a research run.

Nothing that monitors a thesis overnight can call a model until the per-run budget lives on
a tool-agnostic row. This is the load-bearing migration: a `work_orders` supertype carrying
`user_id`, `tool`, `subject_kind`, `subject_id`, `as_of_date`, `point_in_time`,
`max_cost_gbp`, `status`, `archived_at`, with `research_requests` demoted to a 1:1 detail
row holding the equity mandate.

It is also risky: `tests/test_migrations.py` compares the migrated schema against the
models, so this must be a real four-step sequence — add nullable, backfill, set `NOT NULL`,
drop the old column in a later revision — with working downgrades.

### 6.4 Cost changes shape, not just amount

A `BudgetExceededError` *pauses a run for a human decision*. That is correct when a person
is at the console and meaningless at 03:00 on an unattended monitor across forty names.
Continuous monitoring turns a bounded per-report expense into a standing subscription where
the monthly cap is the only real control. ADR 0052's lesson — a step with no estimate is a
step with no cap — applies with more force to a step nobody watches. This needs deciding
before code.

## 7. The kernel and the tool boundary

**A modular monolith with an explicit tool registry, and no package move.** The boundary
this codebase needs is a runtime one, not a directory one.

A full `aer.kernel.*` / `aer.tools.*` split would touch 279 source and 183 test files,
invalidate file references across 66 ADRs and `docs/knowledge-map.md`, change the
mypy-strict globs in `pyproject.toml`, and collide with the active report-quality work. It
buys a boundary that an AST test can enforce for nothing.

**Enforce the boundary with a test** that fails if a kernel module imports from a tool
package — the same shape as ADR 0018's single-writer test and ADR 0013's section-key test.

### Registration

Written in the exact idiom of `agents/registry.py::RoleDefinition`: frozen dataclasses,
lazy `"module:Attribute"` references, an `adr` field that refuses to be empty, and a test
that walks the references to files in `docs/adr/`.

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    key: str
    title: str
    adr: str                       # refuses to be empty
    subject_kinds: frozenset[str]
    mandate_model_ref: str         # "module:Attribute"
    workflows: tuple[WorkflowDefinition, ...]
    roles: frozenset[str]
    api_routers: tuple[str, ...]
    page_routers: tuple[str, ...]
    nav: tuple[NavEntry, ...]
    subject_resolvers: Mapping[str, str]   # subject_kind -> "module:function"
```

`subject_resolvers` is what keeps the `(subject_kind, subject_id)` pair from rotting the way
`_load_fact` did: a subject is resolved by the tool that owns its kind, through a registered
reference, not by an `if` chain somebody must remember to extend.

**One rule is not optional.** `__post_init__` must *intersect* a tool's declared `roles`
against `registered_roles()`, never union. Invariant 7 makes skill files additive-only and
proves it with an adversarial corpus; nothing equivalent guards a tool descriptor. If
`roles` unions, a tool can hand itself an agent capability the registry never admitted, and
the injection suite's assertion that no role holds a network-shaped tool would be scoped to
the wrong set.

Discovery is an explicit `INSTALLED_TOOLS` tuple. Never entry points, never
`pkgutil.walk_packages` — dynamic discovery would make `Base.metadata` depend on what is
pip-installed, which breaks the autogenerate comparison in `tests/test_migrations.py`
non-deterministically.

### Database

One database, one `public` schema, one linear Alembic chain. `migrations/env.py` sets
`include_schemas=False`, `schema_check.py` inspects only the default schema, and
`test_migrations.py` runs the real chain then compares against one `Base.metadata`. Per-tool
schemas or branches would mean editing all three plus every `__table_args__`, and cross-tool
foreign keys are the point of the design. The only honest cost of staying linear is an
occasional one-line `down_revision` merge conflict.

### Object model

```text
User → WorkOrder → {ResearchMandate | PortfolioMandate}  (1:1)
       WorkOrder → Job → {JobStep, Approval, Cost, AuditEvent,
                          Calculation, ReportSection → Claim → Citation}

Company, Security, PriceBar, FinancialFact   — unowned shared reference layer
Portfolio → Position → Security              — current state
Portfolio → TradeEvent                       — append-only, corrections are new rows
```

**No foreign key between a Position and a Report.** The join between what you hold and what
you think is `(subject_kind='company', subject_id)` plus the existing themes graph — a query,
not an FK. That is the only shape consistent with ADR 0064: prior research may shape the
questions, never the answers.

Also repoint `plan_skill_pins.plan_id` from `research_plans.id` to the work order. It is a
small migration and it is what makes the skills subsystem usable by anything other than an
equity report.

## 8. The shell

ADR 0006 stands. The specification does not force a SPA anywhere: drawers are `hx-get` into
a mount, split-screen review is two independently refreshable panes, and a sticky wizard is
what this codebase already calls a gate — each step a URL, a 303, and a `payload_hash`
proving what was shown.

Write a **successor ADR that narrows 0006 rather than reversing it**, naming the two things
it left implicit: JS islands with a declared JSON contract, and a client-owned chrome layer
for overlays. The governing rule, in the repo's own idiom: **JavaScript may own chrome,
never a figure.**

Multi-tool makes 0006's argument *stronger*. Five tools sharing one server-rendered truth
beats five tools each holding a stale mirror of approval state.

### Order of work

1. **Nav as data, before any colour changes.** `_nav.html` is eight hand-written anchors
   with no active state and nothing that detects drift from the actual routes. Replace with
   frozen `NavItem`/`NavSection` dataclasses composed from one import per tool, rendering
   pixel-identically at first. Then the test that gives it teeth: walk `app.routes`, assert
   every `NavItem.href` resolves, and require every unlisted page route to be named in an
   explicit `UNLISTED` tuple. Nav drift becomes a red build instead of a silent lie.

2. **Inject `shell` inside `templating.render()`.** That function is the single chokepoint
   that already makes the disclaimer impossible to forget. Ten lines gets nav tree, active
   key, guidance flag, current tool and portfolio scope onto every page, with no handler
   able to omit it. **Caveat:** `StrictUndefined` is on, so this must live in `render()` and
   never in a handler — and the shell context must be constructible with no database, or the
   landing page designed to render with Postgres down becomes a 500.

3. **Design tokens as Tailwind v4 `@theme`.** `styles/app.css` today is fourteen lines with
   no customisation at all. The trap to avoid: `@theme` values are static and do not respond
   to media queries, so the dark variant goes through a flipping custom-property layer
   aliased back with `@theme inline` — never by redefining `@theme` inside `@media`.

4. **`_ui/` macros, one per file.** Every macro takes **data, never classes**, so a page has
   no way to render a provenance badge in the wrong colour.

5. **Provenance badge requires a ref.** `ProvenanceRef(kind, identifier, href)` is mandatory,
   so every badge is also a link to its drill-down. A badge reading "Calculated" that links
   nowhere is precisely the confidently-wrong surface this platform exists to prevent.
   Enforce with a test that greps every template for the label strings and fails if any
   appears outside `_ui/provenance.html`.

6. **Guidance mode is one attribute and pure CSS.** `data-guidance` on `<body>` from the
   injected shell; callouts are `{{ ui.guide(3, "…") }}` with an explicit integer, never a
   CSS counter — a counter renumbers silently when a conditional block is hidden. Guidance
   text lives beside the markup it explains, so it moves when that markup moves.

7. **Badge counts never on the critical render path.** A `BadgeProvider` per tool, assembled
   into a separate `GET /_shell/badges` fragment fetched with `hx-trigger="load"`, cached in
   Redis. Otherwise one slow count in a future tool makes every page in the product slow.

### Two constraints that are already tests

- **Inter cannot come from Google Fonts.** `test_loads_no_third_party_asset` scans the
  rendered page for `googleapis` and fails the build. Vendor the woff2 under `static/fonts/`
  with version and SHA-256 recorded in the commit, exactly as `htmx.min.js` is — or fall back
  to the system stack.
- **`max-w-5xl` is asserted by a test.** Widening to 1240px with a sidebar is a knowing
  change; update the sampled-class assertion in the same commit so a forgotten `just css`
  still fails loudly.

## 9. New agent roles

ADR 0035 requires an ADR per role, and the registry refuses a definition without one.

| Role | Shape |
|---|---|
| `thesis_monitor` | Output: a premise id, a status from a closed enum `{unchanged, weakened, strengthened, contradicted, unobservable}`, a justification naming `source_document` ids only. **No field for a rating, an action, a revised target or a conviction.** No tools. Its output is a question raised, never a conclusion reached. |
| `risk_analyst` | Barely a model role. Volatility, beta, exposure, concentration, drawdown and scenario P&L are all `@traced` calculations under ADR 0003. The role gets **commentary only** over platform-filled numeric fields it cannot represent — the shape ADR 0063 used. It does not size, does not set limits, does not choose scenarios. Cheapest and safest to build first. |
| `post_trade_reviewer` | Per-premise verdict from a closed enum, with `process_quality` a **separate field** from `outcome`. No field recommending a methodology change — that is a skill edit and a human act. |

**Reserved output fields grow in two tranches, on two different clocks.**
`RESERVED_OUTPUT_FIELDS` is currently `{rating, recommendation, target_price, price_target,
valuation_range, fair_value}`.

`conviction` is due **now**, with ADR 0070 rather than with any sizing work: a skill that can
declare an output field named `conviction` is a skill that can put a judgement where a number
goes, which is precisely what that record forbids. The six sizing names — `position_size`,
`weight`, `recommended_weight`, `action`, `order_quantity`, `stop_loss` — are ADR 0076's, and
land in the same commit as any sizing concept, never after.

Each name needs its own attack file in the `fx_skill_adversarial` corpus, which is a
directory of skill files rather than a list. ADR 0040's guarantee is that the corpus must all
fail forever; a gap opened before the corpus grows is a gap nobody will notice closing.

## 10. The methodology library mostly does not exist yet

`SkillKind.METHODOLOGY`, `PREFERENCE` and `HOUSE_VIEW` are validated, saved, versioned and
pinned to plans — and **read by nothing**. Only `skill_dry_run` branches on kind, to refuse
them.

A thesis or sizing methodology library is mostly those three kinds, so §8.17 of the UX
specification is not a rendering job on top of an existing feature. Building prose
composition is where invariant 7 faces its hardest test: `compose_policy` clamps numbers and
intersects tool sets, and has nothing whatever to say about a prose fragment. It needs its
own additive-only rule, a precedence order when two methodology skills conflict, and a
per-agent token budget for the added text.

## 11. New ADRs required

In the repository's naming style — a claim, not a topic:

```text
0067  a tool is a registered capability, not a package
0068  a work order is the run root and a research request is a detail of one
0069  an attestation is what the book says, at two times and one grade of evidence
0070  a judgement is never a source reference
0071  the portfolio clock is not the research clock
0072  a lineage node resolves by table, not by hope        (repairs the macro seam)
0073  javascript may own chrome and never a figure          (amends 0006)
0074  a monitor finding is not a gated decision
0075  the thesis monitor raises questions and answers none
0076  the risk analyst comments on numbers it cannot write
0077  the post-trade reviewer scores the process, not the outcome
0078  a rate is a dated observation with a source, not a number in a column
```

0078 was not in the first draft of this list. It was added because three of the others name
an FX rate store as a prerequisite and none of them could decide it — and an ADR that rests
on a draft design note is not a decision.

## 12. Build sequence

**Stage A — prerequisites that pay for themselves inside the research tool**

1. Polymorphic lineage resolver (§6.1). Repairs a latent defect while it is still cheap.
2. Workflow registry — roughly 50 lines, removes four hard imports of
   `vertical_slice_v1`, and fixes the run console blanking on an unrecognised
   `workflow_version`.
3. `work_orders` supertype and the budget-guard generalisation (§6.3).
4. `EvidenceScope(work_order_id, as_of_date, point_in_time, subject_kind, subject_id)`
   replacing the
   `ResearchRequest` argument in `visible_facts`, `visible_sources` and
   `verify.citations._refuse_if_out_of_time`, preserving ADR 0061's one-predicate rule.
5. Generic subject correlation on `audit_events`.

**Stage B — the shell**

6. Nav as data plus the drift test.
7. `shell` injection in `render()`.
8. Design tokens, dark variant, `_ui/` macros, provenance badge and its test.
9. Guidance mode.
10. **Overview as a genuinely second tool**, built only from data that already exists —
    runs awaiting approval, spend this month, recent reports, schema drift. One screen with
    no new tables exercises nav-as-data, a second tool contributing entries, badge counts,
    KPI tiles, attention items, empty states, a provenance badge, guidance mode and a
    drawer. **If that screen works, the remaining sixteen are content rather than
    architecture.**

**Stage C — the first real domain**

11. FX rate store (§6.2).
12. Attestation and Judgement tables, with the two clocks.
13. Watchlist and Research Queue — already the last unbuilt Phase 6 item, and it exercises
    the standing-budget and two-clock questions at low stakes.

Positions, NAV and risk follow Stage C, once the scope question in §3 is settled.

## 13. Questions since settled

1. **A thesis item is a note that may carry a predicate.** The statement is free text; the
   predicate is optional. An item with one ("Azure revenue YoY ≥ 25%") is compared to a new
   Fact by deterministic code, and the model writes only the interpretation. An item without
   one gets a scheduled review prompt instead, and is not thereby second-class — forcing
   every item into a threshold produces fake precision, and "management allocates capital
   well" has no metric. Price is not the test: outcome testing is ADR 0077's, and it never
   touches thesis status. Decided in ADR 0075.
2. **The thesis monitor is tiered.** Only `contradicted` opens a gate; everything else
   accumulates as findings carrying no approval semantics, labelled as findings on every
   surface. Decided in ADR 0074.
3. **A trade may be documented or attested**, with the grade propagating and any lineage
   containing an attested node barred from a shareable rendering by type. Decided in
   ADR 0069.
4. **`approvals.gate` becomes a `tool_gates` reference table** with a real composite foreign
   key, seeded from the registry, with a test asserting the two agree. The vocabulary is
   already owned by the registry in Python, so a Postgres enum would be a second source of
   truth that can only fail at INSERT. Decided in ADR 0067 — and this is the one decided
   without the operator, so it is the one most worth overturning.

### Still open

1. **One shell in one commit, or two shells temporarily?** Recommendation: one. Two nav
   implementations is the exact drift problem nav-as-data exists to kill.
2. **May the knowledge graph become a JS island?** ADR 0073 admits islands by name and
   leaves this particular one undecided. The existing server-drawn SVG is accessible and
   printable, and needs nothing an inline-script policy would forbid.
3. **What does a lapsed market-data subscription do to NAV history?** ADR 0071 decides it;
   the operator should confirm the answer, because it is the one place where a permanent
   record rests on a temporary licence.

## 14. Risks

- **Provenance dilution.** An attested Attestation is a number backed only by the
  operator's word. Once one exists it becomes the path of least resistance for every
  awkward figure. Mitigation must be a type, not a check.
- **Silent bitemporal collapse.** Store one date on a trade and NAV history stops being
  reproducible — and a research claim can rest on a NAV restated after the as-of date, with
  ADR 0021's look-ahead guard sailing past because it keys on a source document's
  publication date and a ledger row has none.
- **Circular evidence at scale.** ADR 0064 was careful about three prior reports feeding one
  planner. An Investment OS is a loop by construction: thesis → monitor → decision → outcome
  → review → next thesis. `INTERNAL_PRIOR_RUN` blocks citation and does nothing about
  influence, and 0064 says those are different problems. The end state is a book of
  positions whose theses are mutually reinforcing with nothing recording the convergence.
  **No existing mechanism addresses this.**
- **Approval fatigue.** A degraded gate is worse than no gate, because the audit trail
  asserts a human judgement that did not happen.
- **Licence contamination of a permanent record.** Per ADR 0030 and 0031, EODHD artefacts
  are LICENSED and must be purged within a month of the subscription lapsing, and a citation
  into a purged artefact can never be re-verified. A portfolio's NAV history rests on those
  marks: a permanent record with a temporary evidence base, and nobody discovers it until
  every historical valuation becomes unverifiable at once.
- **Erosion of the single-writer doors.** ADR 0018's teeth are a test that reads the source
  tree. A ledger verifier will want to write a verified flag; a custodian feed is egress.
  Each is a small, reasonable-looking edit that removes the property the test protected, and
  the test gets edited to accommodate it rather than the design.
- **The plausibility gap repeating, larger.** ADR 0066's lesson was that traceable and
  possible are different properties — learned by publishing a 172.1% net margin with every
  guard green. Portfolio figures have far more impossible states: weights not summing to
  one, negative quantities under a long-only mandate, a NAV that moves with no trade and no
  mark change, realised P&L inconsistent with recorded cost basis. None exists as a relation
  today.

---

## 15. To-do

Live status. Tick an item only when it is committed, not when it is drafted.

### Decisions and documents

- [x] Survey the codebase for the kernel/tool boundary and the invariant pressure points
- [x] §1–§14 of this design note
- [x] `PLAN.md` §2.1 amended — portfolio management moves from MVP non-goal to a named
      later stage; trade execution, multi-user and optimisation stay out
- [x] `gap-analysis.md` B12 marked revisited rather than silently outgrown
- [x] `knowledge-map.md` §6 — a theme for the expansion in the ADR index
- [x] `knowledge-map.md` §7 — a tool is a different class of change, not a sixth recipe
- [x] ADRs 0067–0077 drafted
- [x] ADRs 0067–0077 repaired — nine blocking issues from the first review, then five more
      from the second, including two records that still claimed the macro defect was firing
- [x] ADR 0078 — a rate is a dated observation with a source. Added during review, because
      three of the others named an FX rate store as a prerequisite and none could decide it
- [x] `knowledge-map.md` §6 cites 0078, now that the record exists
- [x] Design note realigned where the ADRs moved past it — `subject_resolvers` on
      `ToolDefinition`, the run identity on `EvidenceScope`, `conviction` split from the
      sizing names
- [x] Operator sign-off: `tool_gates` reference table confirmed (§13)
- [x] Operator sign-off: ADR 0071's answer on a lapsed subscription accepted — bars go,
      recorded NAVs survive as derived output, nothing is recomputed or interpolated
- [x] All twelve records moved from Proposed to **Accepted**, and are therefore immutable
      under ADR 0001 — a change from here needs a superseding record

**"Decisions and documents" is complete.** Everything below this line is code.

### Stage A — prerequisites that pay for themselves inside the research tool

- [x] Polymorphic lineage resolver — a source reference carries a table discriminator
      (ADR 0072). `SourceRef.fact()` is gone; one constructor per relation. Two relations
      that had no loader at all — `macro_observations` and `securities` — now resolve, and
      `services/exhibits.py::_fact_input_ids` is narrowed to its own. **The price half was
      live, not latent:** six minting sites over `securities.id` feed the traced GBX→GBP
      conversion, so every LSE run with a licence key had been writing dangling nodes
- [x] Workflow registry — `WorkflowDefinition` keyed by version, replacing the hard import
      at `services/runs.py:38`. The engine now runs the steps the *job* recorded rather
      than whichever workflow was imported, and an unregistered version is a logged
      decision rather than a silently blank console timeline
- [ ] ~~the four `vertical_slice_v1` gate-payload imports~~ — **not folded in, and not a
      refactor.** The three payload builders take different arguments deliberately:
      `unmapped_gate_payload` reads the extract step's frozen output "not a re-derivation
      that might differ", and ADR 0046's amendment has the assumptions gate assemble from
      rows because it approves work that has not happened yet. One signature would reverse
      both. Needs a decision about what each gate hashes
- [x] `work_orders` supertype; `research_requests` demoted to a 1:1 detail row sharing its
      key; `jobs` gains `work_order_id` (ADR 0068). Migration 0051 — steps 1–3 of four,
      with a working downgrade proved by the round-trip test
- [x] `approvals.request_id` repointed to `work_orders` — ADR 0074's gate cannot be written
      until this lands
- [x] `Agent._refuse_what_cannot_be_afforded` reads the tool-agnostic run root. **A model
      call no longer requires an equity mandate, and still requires a cap**
- [x] `EvidenceScope` replaces the `ResearchRequest` argument in `visible_facts`,
      `visible_sources` and `verify.citations._refuse_if_out_of_time`, carrying the run
      identity so ADR 0061's asymmetry survives
- [x] `audit_events` gains a generic subject correlation, before the first ledger row
- [x] `plan_skill_pins` repointed off `research_plans`, unblocking skills platform-wide

      These six landed in one commit because ADR 0068 says splitting them would be worse
      rather than safer: all five tables repoint at the *same* new row through one 1:1
      backfill, so one revision holds one correspondence to check where four would hold
      four chances for it to drift while `compare_metadata` is red in between.

- [ ] **Step 4 of the migration** — drop `jobs.request_id`, `approvals.request_id`,
      `source_documents.request_id` and the columns duplicated on `research_requests`.
      Blocked on the ~20 remaining `session.get(ResearchRequest, job.request_id)` lookups,
      which are mandate reads and legitimately want the mandate

### Two things the migration surfaced that ADR 0068 did not anticipate

- [x] **A dry run needs its own work order.** Pins are unique per (work order, skill), and
      a rehearsal reused the source run's request — so it could not rehearse any skill that
      run had already pinned, which is every skill worth rehearsing. A rehearsal has its own
      job, step and spend, so under ADR 0068's own definition it is its own unit of work
- [x] **A re-plan must not silently reuse a stale pin set.** A terminal run with no report
      can be superseded, which re-runs the plan step on the same work order. Pins keyed to
      the run root would then be found and reused — so an operator who fixed a skill and
      restarted a failed run would get the version they had just replaced, with the pin
      still asserting it was deliberate. Resolution now compares the set against the enabled
      skills' current versions: a retry reuses, a re-plan over changed skills replaces

### Stage B — the shell

- [ ] Nav as data — frozen `NavItem`/`NavSection`, an explicit `NAV` tuple, `_nav.html` a loop
- [ ] The nav drift test — every `NavItem.href` resolves; every unlisted page route is named
- [ ] `shell` injected inside `templating.render()`, constructible with no database
- [ ] Design tokens in `@theme`; dark variant through a flipping custom-property layer
- [ ] `_ui/` macros — card, KPI tile, provenance badge, empty state, drawer. Data, never classes
- [ ] Provenance badge requires a `ProvenanceRef`; two chips, not five (ADR 0073)
- [ ] The provenance label test — no label string outside the macro
- [ ] `drawer.js` — focus trap, Escape, scroll lock, `aria-modal`. Written once
- [ ] Guidance mode — `data-guidance` on `<body>`, callouts in pure CSS
- [ ] Badge counts off the critical render path, behind `GET /_shell/badges`
- [ ] Inter vendored under `static/fonts/`, or the system stack accepted
- [ ] **Overview as a genuinely second tool**, built only from data that already exists.
      If this screen works, the remaining sixteen are content rather than architecture

### Stage C — the first real domain

- [ ] `fx_rates` table and `services/fx.py`; `aer.calc.fx` gets its first caller (ADR 0078)
- [ ] `Attestation` and `Judgement` tables, both clocks, the grade that propagates
- [ ] `SourceKind` gains `ATTESTATION`; the `claims` XOR widens; invariant 3 restated
- [ ] `RESERVED_OUTPUT_FIELDS` gains `conviction` (ADR 0070), with its attack file
- [ ] Watchlist and Research Queue — the last unbuilt Phase 6 item, and the cheapest place
      to exercise the standing-budget and two-clock questions

### Deferred until Stage C settles

- [ ] Positions, executions and NAV
- [ ] Thesis items, predicates and the monitor (ADRs 0074, 0075)
- [ ] Portfolio risk and scenarios (ADR 0076)
- [ ] Trade journal, post-trade review and decision analytics (ADR 0077)
- [ ] `RESERVED_OUTPUT_FIELDS` gains the six sizing names, in the same commit as any
      sizing concept and never after
- [ ] The methodology library — the three `SkillKind`s that are versioned, pinned and
      read by nothing
