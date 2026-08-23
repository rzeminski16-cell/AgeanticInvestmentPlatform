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
    adr: str  # refuses to be empty
    subject_kinds: frozenset[str]
    mandate_model_ref: str  # "module:Attribute"
    workflows: tuple[WorkflowDefinition, ...]
    roles: frozenset[str]
    api_routers: tuple[str, ...]
    page_routers: tuple[str, ...]
    nav: tuple[NavEntry, ...]
    subject_resolvers: Mapping[str, str]  # subject_kind -> "module:function"
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

**Stage C — replanned, 23 August.** The original Stage C led with a watchlist, on the
argument that it exercised the standing-budget and two-clock questions at low stakes. The
operator has redirected: one tool works, one more is built next, and everything else is a
button that says so. Three phases, in this order.

**Phase 1 — Overview becomes the main menu.** It is a work dashboard that happens to be
first in the nav; it becomes the landing page. `/` leads with a tool launcher — Equity
Research (working), Portfolio (under construction), the rest as placeholders — and the
attention feed sits below it, because that is the reason to come back rather than the
reason to arrive. The sidebar becomes a dropdown in the same slice.

**Phase 2 — prove the research tool still works.** A green suite is not a working product,
and the shell has been rebuilt underneath it four times. An end-to-end walkthrough:
commission a request, drive it through the gates, read the report.

**Phase 3 — Portfolio.** Its prerequisites are smaller than this document assumed, because
the substrate is already here: `securities`, `price_bars` with `adjusted_close` and a
`source_document_id`, `corporate_actions`, and `aer/calc/fx.py` — finished, tested, and
still with no caller. What is missing is `fx_rates`, the attestation record class, and the
portfolio tables. Settled with the operator on 23 August, recorded in ADR 0079:

11. `fx_rates` and `services/fx.py`; `aer.calc.fx` gets its first caller (ADR 0078).
12. `attestations` and `transactions`; the grade that propagates (ADR 0069).
13. `aer/calc/portfolio.py` — quantity, cost basis, market value, cash, NAV, weight, each a
    recorded calculation. **No `positions` table** (ADR 0079).
14. The Portfolio screen: holdings as at a date, defaulting to the latest close.

Judgements, theses, the monitor, risk and the review tools follow, once a book exists for
them to be about.

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
- [x] The `vertical_slice_v1` gate-payload imports — **and an earlier note here was
      wrong.** It said one signature would force every gate to re-derive from the session
      and so reverse two decisions. Five of the seven builders already share a signature,
      and their argument is the step's own frozen output read back out of `job_steps` —
      reading what a step wrote is not recomputing it. The two that genuinely differ keep
      their own branch: `plan` assembles from the plan row and its pins, and `assumptions`
      assembles from the rows as they stand, alone among the gates, because it approves
      work that has not happened yet (ADR 0046's amendment). `WorkflowDefinition` now
      carries a `gate_payload_ref`, so a page renders an approval without knowing which
      workflow raised it
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
      **Deliberately not done in the same sitting as step 3.** ADR 0068 stages it as a
      later revision, and the staging is the whole reason the downgrade is lossless rather
      than declared: dropping `work_orders` discards nothing while those columns still
      hold it. Running both at once would spend that guarantee for nothing. It also needs
      the ~20 `session.get(ResearchRequest, job.request_id)` lookups to become optional
      mandate reads, since a monitor run will have none

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

- [x] Nav as data — frozen `NavItem`/`NavSection`, an explicit `NAV` tuple, `_nav.html` a loop.
      Renders identically to the eight hand-written anchors it replaced; what changed is that
      a second tool can contribute a section and an item knows when it is the current one
- [x] The nav drift test — every `NavItem.href` resolves; every one of the 40 page routes is
      either in the nav or named in `UNLISTED`. Verified to fail when an entry is removed
- [x] `shell` injected inside `templating.render()`, constructible with no database — asserted
      against the broken-engine client, because `StrictUndefined` would otherwise turn the one
      page an operator opens when Postgres is down into a 500
- [x] Design tokens in `@theme`; dark variant through a flipping custom-property layer.
      Added *beside* Tailwind's stock ramps rather than overriding `sky` and `slate` —
      that would have re-skinned all 37 templates for free and left `text-sky-700`
      rendering navy, a small permanent lie in a repository whose habit is to refuse
      exactly that. Verified the utilities compile to `var(--aer-…)` so the flip works
- [x] `_ui/` macros — card, KPI tile, provenance badge, empty state, guidance callout.
      Data, never classes. **The drawer is not here**: it needs the focus trap, and that
      is its own slice rather than a macro with a gap in it
- [x] Provenance badge requires a `ProvenanceRef`; two chips, not five (ADR 0073). A ref
      with no `href` raises, and `Confirmed` with nobody attached raises
- [x] The provenance label test — no label string outside the macro, plus a companion
      asserting the macro file does contain them, so the grep cannot pass by finding
      nothing anywhere
- [x] `drawer.js` — focus trap, Escape, scroll lock, `aria-modal`. Written once, and
      landed with its first user rather than ahead of one, which is the lesson
      `_ui/index.html` had just taught: a component nobody imports is a component nobody
      has run.

      **It opens because content arrived, not because anything told it to.** There is no
      `data-drawer-open`; the trigger's whole contract is `hx-target="#aer-drawer-body"`,
      so a trigger cannot open a drawer it then fails to fill, and there is no moment
      between "opened" and "filled" for a failed request to leave an empty panel visible.

      The semantics are markup — `role`, `aria-modal`, `aria-labelledby`, `tabindex="-1"`
      are in `_shell/drawer.html` and the script sets none of them, so the panel is a
      dialogue in the DOM a reader inspects and not only in the one a script got round to
      editing. What a server cannot send is the *behaviour*, and that is all the script
      owns. A grep asserts no page grows a second focus trap (ADR 0073), and the trap
      itself is proved in a browser: with the `Tab` branch removed, focus escapes on the
      first press.

      The trigger is a link before it is anything else. Its `href` is the run console, so
      with scripting off the same click is a page — asserted with JavaScript disabled
      entirely. **A live browser also caught the one real defect:** `detail.elt` on
      `htmx:afterSwap` is the element that was *swapped*, not the one that asked, so the
      panel opened with an empty heading. Nothing server-side could have seen it.

      Its first user is an attention row: `Attention.preview_href`, optional, set by the
      research tool for rows backed by a run and left empty for an unrun draft, which has
      nothing to show that the row does not already say
- [ ] Guidance mode — the flag, the route and `data-guidance` on `<body>` are done: server
      state under ADR 0073, a form POST that redirects so it works with scripting off, and a
      checked destination so it cannot become an open redirect. `ui.guide()` and its CSS
      landed with the macros. **Two things are left, and the second is why this is still
      open:** no page calls the macro yet, and nothing renders a control for the toggle — a
      form in the shell needs a CSRF token in the shell, which means `render()` minting one
      and every response setting the cookie. That is its own slice, not a line in this one
- [x] Badge counts off the critical render path, behind `GET /_shell/badges`. A
      `BadgeProvider` per counted thing, in the registry idiom — a lazy `"module:function"`
      counter and an `adr` that refuses to be empty — joined to `NavItem.badge_key` by a
      test rather than an import, so the shell keeps no cycle. Two drift guards: a slot no
      provider fills, and a provider no slot renders. Cached per operator for ten seconds,
      best effort in both directions, because Redis being down must not cost the sidebar.
      **The swap is `hx-swap-oob="innerHTML"`, not the default**, and that is the whole
      accessibility question ADR 0073 named: `"true"` replaces the element, so the
      `aria-live` region a screen reader is watching is thrown away and the number that
      lands is never announced. Proved in a browser — with the default the attribute is
      simply gone from the live DOM. A zero is swapped and renders as nothing, so a count
      the operator has acted on clears rather than lingering
- [x] **Inter vendored under `static/fonts/`.** Not the system-stack fallback: ADR 0006
      says every stylesheet, script *and font* is served from this origin, and the spec
      named the typeface — accepting the fallback would have been narrowing the ask rather
      than making a decision. `@fontsource-variable/inter@5.3.0`, upstream Inter v20, SIL
      Open Font License 1.1 with the notice committed beside the files.

      One variable file per range, weights 100–900, so a page using four weights makes one
      request rather than four and a semibold heading is a real weight rather than a smear
      of the regular. No italic — nothing in thirty-nine templates is italic, and a face
      nobody renders is 52 kB committed for the look of completeness. `latin-ext` is here
      because of what this tool renders: issuer names come out of filings and an LSE
      listing is routinely a European domicile, so a report about Škoda would otherwise set
      those letters in whatever the system supplies, mid-word. `unicode-range` means it is
      fetched only on a page that contains one, so the common case is still the 48 kB latin
      file alone — which is also why only that one is preloaded.

      **Almost every way this can fail looks exactly like success.** A `src` that 404s, a
      break in the three-link chain from Preflight to `--font-sans`, a swapped binary — all
      three render a perfectly reasonable page in the fallback stack. So the SHA-256 of
      each file is pinned in a test rather than described in a commit message nobody diffs;
      the chain is followed through the compiled stylesheet rather than assumed; and a
      browser reads the face off a rendered page, which is the only check a file cannot
      make. Verified by pointing the `@font-face` at a missing file: the computed
      `font-family` still names Inter and only `document.fonts.check` notices.

      `test_loads_no_third_party_asset` gained four hosts in the same commit. It scanned
      for `googleapis`, which catches the Google Fonts *stylesheet* — the woff2 it then
      asks for comes from `fonts.gstatic.com`, which it did not
- [x] **Overview as a genuinely second tool**, built only from data that already exists.
      The claim held: the screen owns no query. Its counts are the registered badges and
      its feed is a registered `AttentionProvider` per tool, so a second tool appears on it
      by adding a row in Python rather than a branch in a template. Its `NavSection` comes
      from `web/overview/nav.py` — the first section `shell/registry.py` did not declare
      itself — and that module holds data and imports nothing heavy, because the router
      imports `render`, which imports the shell, which composes the nav.

      **The attention registry deliberately differs from the badge one in its failure
      mode.** A badge is a hint, so a provider that raises drops its number. This feed is
      the answer to "is anything waiting for me", and an empty one is a claim — so a
      provider that raises becomes an item saying it could not be asked and that the rest
      of the list is incomplete. Every listing is bounded at eight and says how many more
      there are, because a feed showing the first eight would describe a smaller problem
      than the operator has.

      **Two deviations from the plan above, both deliberate.** There is no provenance
      badge: ADR 0054 defines a figure as a numeral denoting a quantity that invariant 3
      requires to be a stored fact or a recorded calculation, and nothing on this screen is
      one — a count of stopped runs is a count of rows, and the month's spend is a sum of
      charges the platform metered itself. A "Calculated" badge beside either would spend
      the word that means "this traces to a formula" on something that does not. And there
      is no recent-reports list: reports are the research tool's, and a hard-coded section
      for them is exactly the coupling this screen exists to avoid — `/reports` is one nav
      item away
- [x] **`_ui/index.html` exported nothing at all**, found by the first page to import it.
      Jinja does not re-export a name brought in with `{% from %}`, so `ui.card` raised
      `UndefinedError` on Overview's first render — a whole macro package that would have
      failed for whoever reached for it first. Each name is now assigned at the top level,
      and `test_the_ui_aggregator_exports_every_macro` compares the two lists so a macro
      added and forgotten is a red build. The macros shipping before any page used them is
      what hid it, which is the argument for the drawer landing with its first user

### Stage B, after the fact — the shape of the product, made visible

- [x] **The sidebar.** The header strip became the left sidebar the specification asked
      for, at the widths the tokens already carried (`--spacing-sidebar`,
      `--container-shell`) rather than at a number typed into a template. Sections render
      their labels now that there are five of them: an eighteen-item flat list is a list
      nobody reads. The `max-w-5xl` widening the note above flagged as "a knowing change"
      is this change, and the sampled-class assertion it warned about no longer exists —
      the stylesheet guard covers every class the templates use.

      **One nav element, not two.** The obvious responsive shape is a sidebar on a wide
      screen and a disclosure on a narrow one, and it is wrong here: rendering the nav
      twice would put two nodes with `id="aer-badge-approvals"` on every page, and an
      out-of-band swap targets an id — the first would fill and the second would show
      nothing for ever. So there is one DOM and CSS moves it, and a browser test asserts
      the sidebar is beside the content at 1400px and above it at 400px, read off the
      rendered boxes rather than the class list. The honest cost is that the narrow layout
      is a scrolling strip, so the current item can start off-screen; the page heading
      carries "where am I" there
- [x] **Eight planned tools, each a registered row with a real page.** Watchlist, Theses,
      Decisions, Positions, Monitor, Risk, Post-trade review, Decision analytics — a
      sidebar listing only what is finished describes a research tool, which is what this
      codebase is in the middle of not being.

      Each occupies the URL it will keep, so nothing linking to `/watchlist` ever has to
      move, and each href is a literal route rather than a `/tools/{key}` catch-all —
      which is also what lets the nav drift test go on comparing hrefs to routes. Each page
      answers three questions from its row: what the tool will do, what has to exist first,
      and which record decided it. `needs` is the field that stops it being a "coming
      soon" page. **200, not 404 and not 501:** the page exists and is correct, and it says
      truthfully that the tool does not

- [x] **Two defects the screenshot found**, neither visible from any test that passed.
      `/_shell/badges` answered 500 with Postgres down — `CurrentUser` raises before a
      handler can decide anything, so the fragment that now fires on *every* page took the
      landing page, the one built to render in that state, from degrading gracefully to
      logging an unhandled exception on each load. The operator is looked up in the handler
      now, through one shared query rather than a second `select(User)`. And both registries
      caught `SQLAlchemyError` where asyncpg raises the operating system's error directly:
      a bare `ConnectionRefusedError` went straight past them

### Phase 1 — Overview becomes the main menu

- [x] **ADR 0079 accepted**, 23 August. A position is a calculation, not a row
- [x] **`/` is the main menu.** The launcher leads — every tool from
      `web/tools/registry.py`, with its state — and the work list follows, because that is
      the reason to come *back* rather than the reason to arrive. `/overview` is a 308 to
      it: the URL was in the navigation and in whatever the operator bookmarked, and
      404ing it would be a lie about a page that is right there.

      **The launcher needs no database and the work list does**, and that split is the
      whole design of the handler. The front page of a local tool is the page you open
      *because* something is not working, so the tools always render and a failure becomes
      a notice naming which failure it was. The work list is then absent rather than empty:
      "nothing is waiting" is a claim, and with the database down it is a claim nobody
      checked
- [x] **One registry for tools, three states.** `WORKING` earns a navigation section,
      `UNDER_CONSTRUCTION` earns one too because watching it arrive is the point, and
      `PLANNED` appears on the launcher and nowhere else — written down in `UNLISTED`, so
      it is a decision somebody can argue with rather than an omission. Research is
      working, Portfolio is under construction and absorbs Positions, and seven are
      planned. This is ADR 0067's `ToolDefinition` in the only shape that is useful yet;
      the fields for tables, workflows and subject resolvers arrive with the tool that
      needs them
- [x] **The sidebar became a dropdown.** `<details>`/`<summary>` and **no JavaScript at
      all** — focusable summary, Enter and Space to toggle, Escape to close, all from the
      browser. A scripted one would be a second focus-managing control beside `drawer.js`,
      which ADR 0073 spends a paragraph refusing, and it would be dead with scripting off.
      Proved open in a browser with scripting disabled entirely. **The cost, as stated
      before it was built:** navigation is now a click on a wide screen, where the sidebar
      gave it for nothing
- [x] **A third way for the badge fragment to answer 500**, found by a screenshot again.
      It caught `OSError` — nothing listening — and not `SQLAlchemyError`, so a database
      that *is* listening two migrations behind took the front page from degrading to an
      unhandled exception, on exactly the machine that page exists to help
- [x] **An import cycle that worked only because of import order**, and had been there
      since the Overview slice. `shell/registry.py` imports each contributing tool's
      module; those modules imported `NavItem` from `aer.web.shell.nav`, which runs the
      shell package's `__init__` first, which imports `context.py`, which imports
      `registry.py` — back to a package half-way through initialising. `import
      aer.web.tools.registry` in a fresh interpreter raised `ImportError` and nothing ever
      noticed, because something always imported `aer.web.shell` first. The types moved to
      `aer/web/nav.py`, beside an `__init__` that imports nothing, and the rule is now a
      test that reads the contributor set off the registry's own imports: **composition
      points down**
- [x] **The launcher took away the landing page's "Start a research request" button**, and
      an existing browser test noticed — which is what that test is for. It is back as
      `action_label`/`action_href` on the working tool's row rather than as a line in the
      template, refused at construction for a tool that is not built, so the second tool's
      action appears when its row grows one

### Phase 2 — prove the research tool still works

- [x] **It works.** Front page, launcher, form, request, run, plan gate, the conditional
      gates, final gate, publish, report — one browser session, no URL typed that an
      operator could not have clicked their way to. The report comes out with its figures
      cited, its calculations footnoted with formula and code version, ten validator
      metrics scored and the disclaimer on every surface.

      Every other browser test in that directory owns one surface, and none of them owned
      the *seam*. Split into eight, each step would pass against a product where the steps
      no longer lead to one another, which is the only failure this was written to catch
- [x] The fake provider, as the operator chose. The browser does everything a person does
      and `tests/e2e/worker.py` does what the worker would — which is the split that makes
      this worth anything: a journey that drove the workflow from the test would prove the
      test can run a workflow, not that the product can
- [x] The advancer moved out of `test_run_console.py` into `worker.py`, shared. Two of them
      would drift into two ideas of what "advance" means, and the one that stopped clearing
      interim gates would be the one nobody noticed
- [x] **Three things the journey corrected in the writing of it**, none a product defect
      and all worth knowing: the report is headed with the name the *filing* registers
      ("MICROSOFT CORP"), not the one typed into the form — `acquire` resolves against the
      regulator's registry and that answer wins from then on; approving the final gate does
      not publish, because the report is written by the step *after* the gate and a
      decision and its consequence are separate rows; and a run needs a ceiling the draft
      step's estimate does not exceed, or the journey tests the cap rather than the tool
- [x] Looked at, not only asserted: the console, the plan review, the final review and the
      report render coherently under the new shell. They still use the stock `slate` and
      `sky` ramps rather than the semantic tokens, which is exactly what ADR-aligned
      restraint asked for — the tokens were added *beside* the ramps so that nothing
      re-skinned itself for free — and the two palettes are close enough to read as one

### Phase 3 — Portfolio

Settled with the operator, 23 August: holdings typed by hand first at the *attested* grade,
with a broker-statement importer as a second door into the same table later; cash and NAV
from the start, because a weight over securities alone silently overstates every holding; an
as-of date defaulting to the latest close, because reconciling against a dated statement is
the only external check this tool has; and a `portfolios` table from day one with one row in
it, so separating an ISA from a SIPP is a setting rather than a migration.

- [x] `fx_rates` and `services/fx.py`; `aer.calc.fx` gets its first caller (ADR 0078)
- [x] ADR 0080 — a rate outlives the request that fetched it. Written during the slice, not
      before it: ADR 0078's `NOT NULL` source document met `purge_request` and made a
      request that had acquired rates unpurgeable, permanently. The pointer goes nullable
      with `SET NULL`, as on a macro observation, and the guarantee moves to a `NOT NULL`
      `artefact_sha256` — which a rate nobody fetched cannot produce, and which unlike the
      pointer still cannot be produced after a purge
- [x] `Provider.ECB` reached the Postgres enum, which ADR 0045 needed and never got: the
      Python value had been there since the ECB adapter was written and no source document
      for a rate response could be inserted at all. Alembic's autogenerate does not compare
      enum labels, so nothing had ever reported drift — `tests/test_migrations.py` now
      compares them, in the one direction that is a fault
- [x] `attestations` and `transactions`, with the grade that propagates (ADR 0069), and
      `portfolios` from the first day with one row in it
- [x] `SourceKind` gains `ATTESTATION` — pulled forward into the slice above, because the
      grade cannot travel a lineage until a `SourceRef` can carry one. It is on the
      reference rather than looked up per node, so `aer/calc/attestation.py` can state the
      property with no session: a module that had to ask a database which grade a leaf
      carried would put the containment in the service layer, one caller away from being
      forgotten
- [x] A currency exchange is deliberately **not** a `TransactionKind`. It is one event
      touching two currencies, and the row shape holds one — so it needs either a second
      currency column used by nothing else or a pair of rows whose "these two are one
      event" invariant no Postgres check can see. Getting it wrong double-counts cash,
      silently, in the direction that flatters. Recorded as a withdrawal and a deposit
      until it has a shape of its own, which is `CorporateActionKind`'s reasoning reused
- [x] The `claims` XOR widens to admit an `attestation_id`; invariant 3 restated in
      `CLAUDE.md`, in `db/models/claim.py`, in `ClaimKind.NUMERIC` and in
      `provenance.FigureView`, in the same change as the constraint
- [x] `provenance._figure_view` resolves the third arm. A trade carries three numbers and a
      claim names a row, so something had to choose which the sentence is about: the signed
      quantity, because it is the only one true of every kind — a dividend has no price and
      a deposit has no security. The grade rides in the detail, where a renderer must read
      it
- [x] **Macro is still a seam and this did not close it.** A `macro_observations` row is
      neither a financial fact nor an attestation, so a gilt yield still reaches a report
      only wrapped in a calculation. ADR 0069 named the question and picked neither answer;
      widening the constraint has not picked one either, and a test asserts the arms are
      exactly three so a fourth cannot arrive quietly
- [x] `aer/calc/portfolio.py` — quantity, cost basis, market value, cash, NAV, weight. Pure,
      `mypy --strict`, property-tested. **No `positions` table** (ADR 0079)
- [x] ADR 0081 — cost basis is a pooled average, and not a tax computation. ADR 0079 named
      cost basis and left the convention open, and the three candidates disagree by a third
      on the same three trades. Pooled, because the operator is a UK investor and it is the
      shape of a Section 104 holding — **without** the same-day rule, the thirty-day rule or
      share reorganisations, so it answers what was paid for what is held and never what is
      owed. One function, so a second convention is a second function and a setting rather
      than a rewrite
- [x] ADR 0081's worked example is in the golden corpus as well as the unit tests: it is the
      one case where pooling and first-in-first-out disagree, so replacing the convention
      fails in two independent places
- [ ] The Portfolio screen: holdings as at a date, every figure carrying its grade, and an
      export path that refuses an attested lineage by return type rather than by a flag
- [ ] A split arrives as a transaction. Deriving it from `corporate_actions` is worth doing
      and is worth doing as a written transaction, never as a quantity that changed with
      nothing behind it

### Later, once a book exists to be about

- [ ] Judgements and theses (ADRs 0070, 0075)
- [ ] The thesis monitor (ADRs 0074, 0075)
- [ ] Portfolio risk and scenarios (ADR 0076)
- [ ] Trade journal, post-trade review and decision analytics (ADR 0077)
- [ ] Watchlist and Research Queue
- [ ] `RESERVED_OUTPUT_FIELDS` gains `conviction` (ADR 0070), with its attack file, and the
      six sizing names in the same commit as any sizing concept and never after
- [ ] The methodology library — the three `SkillKind`s that are versioned, pinned and
      read by nothing
