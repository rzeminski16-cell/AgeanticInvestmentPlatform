# The interface overhaul — implementation plan

*Roadmap [§3.12](ROADMAP.md). How the Tracework design becomes the running application, in
what order, and what has to be decided before each step.*

**Inputs, in authority order:** the invariants in `CLAUDE.md` and the ADRs · the requirements
in [`../design/`](../design/README.md) · the corrections in
[`../redesign/05-review-and-corrections.md`](../redesign/05-review-and-corrections.md) · the
design in [`../redesign/`](../redesign/README.md). The prototype is a visual reference and
never a source of domain truth.

**Testing is in its own document**, [`interface-overhaul-testing.md`](interface-overhaul-testing.md),
because the exit criteria below are stated in terms of it.

---

## The shape of the work

**Forty-two templates carry 1,837 raw ramp classes. Twelve carry none.** The clean twelve are
the shell, the main menu and the component macros — everything that arrived with the design
tokens. The debt is the research tool, and it is not spread evenly:

| Family | Templates | Raw ramps | Tranche |
|---|---:|---:|---|
| Shell, main menu, component macros | 12 | **0** | 4, 5 |
| Requests | 10 | 243 | 5 |
| Run console, seven gates, assumptions | 11 | **806** | 6 |
| Evidence, valuation, replay | 7 | 308 | 7 |
| Skills | 4 | 175 | 8 |
| Knowledge and company history | 3 | 118 | 8 |
| Reports | 2 | 102 | 7 |
| Platform (settings, costs) | 2 | 67 | 9 |
| Portfolio | 3 | 18 | 8 |
| **Total** | **54** | **1,837** | |

**Two facts set the sequence.** The run console and the gates are 44% of the debt and are also
where the design changes most, so they are neither first nor last — they follow the shared
machinery and precede the long tail. And the shell is already clean, so it moves first at
almost no migration cost: it is the tranche that proves the new system before anything
expensive depends on it.

---

## The decisions, made 2026-08-25

All cleared on the operator's direction. Nothing below is open.

| # | Question | Decision | Record |
|---|---|---|---|
| A1 | Where verdict prose comes from | **Model-authored**, over a frozen subject, stored as a step output. Split into an authored half and a composed half | [ADR 0087](../adr/0087-a-verdict-has-two-halves-one-composed-and-one-authored.md) |
| A2 | The product name | **Tracework Invest** | This document |
| A3 | One badge or two | **One**, on Requests | This document |
| B4 | The navigation rail's palette | **A fixed-scheme region declares its own family and is measured against it** | [ADR 0088](../adr/0088-a-fixed-scheme-region-carries-its-own-measured-palette.md) |
| B5 | "Active run" in the navigation | **`GET /runs/active` redirects** | [ADR 0089](../adr/0089-the-run-you-are-watching-has-an-address.md) |
| B6 | The Components page | **Never a route.** A test fixture and the visual baseline | This document |
| B7 | Navigation without scripting | **Ships `<details open>`**, closed by script at narrow widths | This document |
| C | axe-core and the fonts | **Vendored during the build.** Both reachable | Tranches 0 and 2 |
| D | Sequencing | **§2.1, then §3.1, then this** | ROADMAP |

### A1 — what the choice actually buys, and what it costs

**Chosen: model-authored.** ADR 0087 is how that is built without the three problems the
plain reading has, and it is worth carrying the shape into every page.

**A verdict is two halves.** The **composed** half is counts, states and figures, assembled
in Python on every render — live by construction, so it cannot go stale. The **authored**
half is one or two sentences of interpretation, written once by a model over a subject that
has stopped changing, and stored.

**Frozen is the test, and it decides the scope.** The review gate, the evidence pages and a
finished report have an authored half. The run console does not — its subject is in motion.
**The main menu never does**, because its verdict aggregates live state across runs: there is
no moment to write it once about, and it must keep rendering with no provider configured.

**Cost:** one cheap model call per run per authored surface, routed, metered and capped like
every other. **It is never evidence** — no claim may name it, no citation may resolve to it,
and the type carrying it cannot construct a source reference.

### A2 — Tracework Invest

Rendered in the shell in the pattern the current wordmark already uses — the name, then the
qualifier in the accent colour:

```
Tracework Invest        →   Tracework <span class="text-verification">Invest</span>
```

**Fifty files say "Ageiantic" today.** Most are documentation. The rename is mechanical and
belongs to tranche 4, with the archive left alone: `docs/archive/` is a record of what was
written at the time and renaming it would falsify it.

### B7 — the navigation fails open

The markup ships `<details open>`; the script closes it at narrow widths. Measured: with
scripting off at 320px the panel is expanded over the content on load and every link is
reachable. **That is the safe direction to fail** — a navigation that is open when it should
be closed is untidy; one that is closed with no way to open it is a dead end.

The wide-width reveal still has to be verified per engine in tranche 4, and the prototype's
`matchMedia` approach is not the implementation: the handoff forbids it and is right.

---

## Sequencing against the rest of the roadmap

**Decided: §2.1, then §3.1, then this.** Both are in flight, both touch templates this
overhaul rewrites, and both are ahead of it on the roadmap because each puts a wrong number
or a missing answer in front of somebody while this is a product that looks like two designs.

**§2.1 — five sections fail to draft.** More than a quarter of the last report was a coverage
notice. Its diagnosis surfaces on the gate-3 review page, which tranche 6 rebuilds — so
finishing it first means tranche 6 rebuilds a surface whose content is settled rather than one
still being instrumented.

**§3.1 — the portfolio's third door.** A ticker no research run has priced cannot be dealt at
all, so the tool is unusable on a fresh database. It adds a control to the transaction form,
which tranche 8 rebuilds. Landing it first is a two-line saving in tranche 8 and an unblocked
operator immediately.

**Tranche 0 may run in parallel with either.** It touches no template and no service — it
holds current behaviour under test, builds fixtures and commits the ratchet ceiling — so it
costs §2.1 nothing and makes it safer.

**§2.5 — the palette migration is not a separate item any more.** It is tranches 2 and 4–9 of
this plan, and the roadmap entry already says so. It ends when the ratchet reaches zero in
tranche 9.

**§2.4 — the report document's layout stays out.** It is a WeasyPrint print-stylesheet problem.
The one crossing point is the in-browser report preview, which tranche 7 restyles as a reading
surface without touching the print rules.

**§3.2 — portfolio return and exposure stays out**, in `Later — reserve space, do not
simulate`. Tranche 8 must leave room for it and must not ship a placeholder chart.

---

## The tranches

Each is independently reviewable and independently releasable. **A tranche is done when its
exit criteria pass, not when its templates look right.**

### Tranche 0 — Hold the current behaviour

**Do first, and do not skip.** Nothing visual moves here.

1. Run both suites and record the baseline: `pytest --ignore=tests/e2e`, then `pytest tests/e2e`.
2. Record the ramp census per template — the command is in roadmap §2.5 — and commit it as the
   ratchet's opening ceiling.
3. Build fixtures for every state the design names and the suite does not yet have: database
   down, first run, stale approval, budget refusal at both scopes, failed run, unverified
   claim, incomplete portfolio, a section that did not generate.
4. Inventory the ids that out-of-band swaps and scripts depend on.

**Exit:** the suite is green, the ceiling is committed, and every state in the design has a
fixture that renders.

**Delivered, 2026-08-25.** Four guards rather than the two this section asked for, each
watched failing before it was trusted:

| Guard | What it stops |
|---|---|
| `tests/test_palette_migration.py` | A ramp coming back, a migrated template not recording it, a new template starting in the old dialect |
| `tests/e2e/test_a11y_harness.py` | axe unavailable, unpinned, or shipped to an operator |
| `tests/test_script_dom_contract.py` | A renamed id or attribute that leaves a script querying nothing — silent, and the shape a rewrite most easily produces |
| `tests/test_every_page_renders.py` | A template naming a context key its handler stopped supplying. Fifty pages, one driven run |

The last two were not planned. The inventory this section asked for turned out to be worth
more as an assertion than as a note, and nothing in the suite opened a page at all.

**The baseline, measured on a real database and a real browser.**

| Suite | Result |
|---|---|
| Default (`pytest --ignore=tests/e2e`) | **5,659 passed, 0 failed** |
| Browser (`pytest tests/e2e`) | **127 passed, 0 failed** — after the fix below |

Getting there took a local PostgreSQL and Redis, because there is no Docker daemon in this
environment. **That mattered more than it sounds:** without a database the default suite skips
1,849 tests, and two real failures were hiding in them — a stale assertion in
`test_comps_service.py`, and the one below.

**Tranche 0 found a serious regression and it is fixed** (roadmap §4.16). Forty of 124 browser
tests failed, every one on a form submission: `render()` had been giving every response a fresh
CSRF cookie, and the badge fragment htmx fetches on every page load was replacing the token
every form on the page carried. **Only the scripting-on path was broken**, which is why the
in-process suite never saw it — an HTTP client does not run htmx.

**What is still owed.** The state fixtures this section asks for are partly covered: the render
harness drives one complete run and a book, and the database-down and refusal states are held
elsewhere. **First run, stale approval, budget refusal at both scopes, a failed run, an
unverified claim, an incomplete portfolio and a section that did not generate still have no
rendered fixture.** They are carried into tranche 1, where the presentation vocabulary needs
every one of them anyway to prove a verdict composes from a thin run as well as a rich one.

### Tranche 1 — Presentation vocabulary

Python only. No template changes.

1. Central mappings from every `JobStatus`, `StepStatus`, `GateKind`, request state, report
   state and skill state to a `HumanState` carrying a label and a semantic tone key.
2. `RenderedFigure`, `CostContext`, `LineageNode`, `PageContext` — the shapes handlers assemble
   before rendering. A field ending `_display` is a complete server-rendered string.
3. **The composed half of every verdict** (ADR 0087) — counts, states and figures, assembled
   from stored rows. This is the half that ships everywhere, including the surfaces that never
   gain an authored half.
4. Adapt handlers family by family; existing templates keep rendering.

**Exit:** every enum member has a human label and a valid tone, asserted by a completeness test
— a new gate or status without one is a red build. No redesigned template will need to
interpret raw domain data.

**Part done, 2026-08-25.** The first half of the exit criterion is met and the second is not.

| | |
|---|---|
| **Done** — `web/vocabulary.py` | Eight enums, fifty-one states, each with a label and a tone. Gate words for all eight gates, with an honest `certainty` so a journey cannot present five conditional gates as certain. Completeness is a red build, watched failing |
| **Done** — `web/figures.py` | `pounds` and `CostContext`, so "£6.40 of £8.00" is one object every surface shows identically, and the console stops formatting currency in a template |
| **Still owed** | `RenderedFigure`, `LineageNode`, `PageContext`; **the composed half of every verdict** (ADR 0087); adapting the handlers family by family |

**Two duplications were closed rather than added to.** `GATE_ASKS` and the portfolio's
`GRADE_LABELS` are now derived from the vocabulary. Both were written before it, and a second
copy of a label is a second answer to what a thing is called.

**The tone work turned out to be the valuable half.** A label makes a state readable; the tone
decides whether the reader thinks the platform is broken. `BUDGET_EXCEEDED` was rendering in
the same red as a crash while the console's own prose argued the opposite two inches below it.

### Tranche 2 — Assets and tokens

1. Vendor the three families, latin and latin-ext, variable cuts where they exist, no italic.
   Record six SHA-256 pins in `tests/test_fonts.py` and three OFL files.
2. Implement the token system in `src/aer/web/styles/app.css`: the full §2.2–2.6 set, **plus
   the navigation-rail family under ADR 0088**, plus focus, control-boundary, type and spacing
   scales. No `faint` token at any point (D3).
3. Keep the existing semantic aliases resolving so the clean twelve keep working.
4. Compile and commit the stylesheet.

**Exit:** contrast is asserted from **computed colours** for every sanctioned pairing including
the rail, in both schemes; the font chain is verified in a browser; nothing renders differently
yet.

### Tranche 3 — Shared macros

The component set the research tool never had, which is why it has 1,837 ramps.

Page header · verdict block · working sheet · status label · callout (notice, refusal, failure,
success) · button · form field · table · definition list · consequential figure · record list ·
native disclosure · provenance · confirmation · grade · **evidence spine** · **decision panel**.

**The rule stands: a macro takes data and never classes.**

**Exit:** every macro renders in both schemes across default, hover, focus, disabled, error,
loading and empty; each has its no-script behaviour written beside it; a new page needs no raw
ramp and no repeated form or table class block.

### Tranche 4 — Shell and navigation

Twelve templates, zero ramps — so this is design work rather than migration.

1. One navigation DOM: persistent rail at the workbench breakpoint, native disclosure below it.
   **Verify the wide-width CSS reveal in Chromium and Firefox in this tranche** (D7); if an
   engine will not reveal a closed `<details>`, use one CSS layout variant around the same
   `nav` — never a second tree.
2. `<details open>` in the markup, closed by script at narrow widths. Fail open, deliberately.
3. Visible location: breadcrumb or compact label, outside the closed part.
4. The rail's own palette (ADR 0088). Selection carried by the teal rule and `aria-current`, never
   by the 1.21:1 fill.
5. `/runs/active` (ADR 0089). No "Components" item, ever (D5).
6. One badge target, one drawer, server-stamped preferences, disclaimer once.

**Exit:** every top-level destination is one action away at wide width; the whole shell works
with scripting off at 320px and 1440px; the shell survives Redis down, database down and a
badge request that never returns.

### Tranche 5 — Overview and requests

10 templates, 243 ramps.

1. Attention leads; the launcher follows, still database-free.
2. Age, spend and ceiling on every attention row. A first-run state distinct from caught-up.
3. Provider failure as a typed attention item, never silence.
4. The request form: four decisions, then optional refinement behind a native disclosure.
   Point-in-time promoted to a two-option choice beside the as-of date. Cost ceiling beside
   depth, with a server-supplied typical range and an honest `unavailable` case.
5. Dates and spend on list rows. One page-based destructive confirmation for both delete and
   remove — the `confirm()` dialogue goes.

**Exit:** a new operator commissions a run having made four decisions; a returning operator
triages without scrolling; both validation rounds read as one problem list.

### Tranche 6 — Run console and the seven gates

11 templates, **806 ramps — 44% of the debt** — and the largest design change.

1. Human step names, with the technical key retained as secondary. The console explainer sends
   the operator to the worker terminal, so the key must stay findable.
2. The gate journey, with conditional gates visibly conditional and `decisions_remaining` as a
   range.
3. Liveness as the lead while running: current step, elapsed, last seen, and what is normal.
4. Spend against ceiling, identically, on the console and all seven gates.
5. The shared gate frame: question, consequence, evidence, decision. Sticky decision panel at
   wide width, **after the evidence in DOM and focus order**.
6. The review gate rebuilt around a linked attention index. Nothing removed; everything ranked.
7. The payload-hash guarantee written as a reassurance, not a footnote.
8. **The `verdict` role and its step** (ADR 0087): registered in `agents/registry.py`, no
   tools, a closed output schema, routed and capped in pounds with a step estimate. It writes
   the review gate's authored half once, when the draft freezes. A run that fails before it
   falls back to the composed half, which is a complete sentence on its own.

**Do not flatten the gates into one another.** The financials gate sorts by what decides the
question; the peer gate renders rationales at full length; the assumptions gate reads live rows
and carries three forms per row that must **not** nest inside the decision form. The frame is
shared; the evidence is not.

**Exit:** liveness is answerable in two seconds; any gate is recognisable as a gate; approval
stays hash-bound, non-optimistic and refused when stale; the assumptions gate still renders
from current rows; **the authored verdict is never citable and its absence never reads as a
defect.**

### Tranche 7 — Evidence and reports

9 templates, 410 ramps.

1. Verdict first on every evidence surface; the evidence spine rendered from server-built
   lineage.
2. The excerpt promoted to the visual centre, verbatim, with its verdict beside it.
3. Hashes: short for scanning, full retained in a disclosure.
4. The sensitivity grid as a deterministic server-generated figure with full table equivalence.
5. Replay given a typed overall verdict and grouped findings.
6. Report history rows carry the conclusion, valuation, dates and cost. The document leads the
   report page; export becomes an action.

**Exit:** the two-click proof path is visually continuous; a report is findable by what it
concluded; the grid produces identical bytes for identical rows.

### Tranche 8 — Portfolio, Skills, Knowledge

10 templates, 311 ramps.

1. Portfolio: as-of context, book grade stated once, four totals coupled as one typed decision,
   per-row reasons, kind-specific transaction entry via native disclosure. Exact to the penny.
   **Leave room for §3.2; ship no placeholder chart.**
2. Skills: containment stated before it fires, composed policy promoted beside the source, dry
   run made a first-class action, byte-for-byte round-tripping preserved.
3. Knowledge: actions lead, graph nodes become links, empty and sparse states distinguished
   from dependency failure.

**Exit:** each side-tool has a clear hierarchy; no new client state; portfolio totals are all
present or all withheld.

### Tranche 9 — Removal and hardening

1. Settings and costs (67 ramps) onto the system.
2. Remove the legacy aliases and the last ramps. **The ratchet reaches zero and becomes a hard
   assertion.**
3. Recompile and commit the stylesheet; verify every vendored hash.
4. The full manual pass: keyboard, 320px, 200% zoom, both schemes, scripting off.
5. Update `docs/design/` and `docs/developers/` to describe what shipped, and close roadmap
   §2.5 and §3.12.

**Exit:** every check in the testing plan passes and no legacy visual dialect remains.

---

## The 84 server proposals, resolved

72 `[NEW SERVER DATA]`, 7 `[NEW SERVER BEHAVIOUR]`, 5 `[NEW ROUTE]`. Resolved by class, so the
build has a rule rather than 84 arguments.

| Class | Count | Resolution |
|---|---:|---|
| **Human labels for enums, steps, gates, states** | ~20 | **Adopt, tranche 1.** This is the presentation vocabulary. No new query |
| **`_display` strings for figures already held** — spend, ceiling, dates, ages | ~18 | **Adopt, tranche 1.** Formatting moves to the server, which is where it already belongs |
| **Verdicts and summaries** | ~12 | **Adopt under ADR 0087.** Composed half always; authored half only where the subject is frozen — never on the main menu, never on the run console |
| **Counts and readiness labels** — sources, claims, coverage | ~8 | **Adopt** where the query is cheap; a count that costs a slow join goes behind the badge mechanism, off the render path |
| **Structural regrouping** — gate journey, attention index, lineage nodes, typed replay findings | ~10 | **Adopt.** These are reshapes of data already assembled, and they are what stops templates doing positional indexing |
| **Typical-cost guidance on the request form** | 1 | **Adopt, with the `unavailable` case designed.** Guidance from history, never a promise, never client-computed |
| **`first_run` / provider-failure typing** | 2 | **Adopt.** Explicit state; never inferred in Jinja from an empty list that may also mean a provider failed |
| **Deterministic figure assets** — sensitivity heatmap, valuation chart, exposure | 3 | **Adopt for the two that have data** (heatmap, valuation history). **Defer exposure to §3.2** |
| **Server GET filter/sort** | 3 | **Adopt** where it makes a returnable URL. Client filtering stays for rows already on the page |
| **Point-in-time as two radios** | 1 | **Adopt.** Presentation only — the boolean and its default-on meaning do not change |
| **Stale-confirmation token for removal** | 1 | **Adopt** if removal does not already re-check eligibility; otherwise decline as duplicate |
| **`/runs/active`** | — | **Adopt (ADR 0089)** |
| **`/portfolio/holdings/{listing}?as_of=`** + fragment | 2 | **Adopt in tranche 8.** It is the "make a holding openable" ask, and the drawer already exists |
| **Transaction index / filter route** | 1 | **Adopt.** Transactions are the only thing the tool stores and have no surface |
| **Evidence drawer fragments** (source excerpt, calculation inputs, assumption justification) | 3 | **Adopt in tranche 7**, each keeping its full-page `href` |
| **Report-to-prior-report change summary** | 1 | **Defer.** Deterministic rating and valuation deltas are adoptable; a prose change summary is a judgement and needs storing, not generating |
| **Skill usage history** | 1 | **Defer** to a later pass. Real value, no urgency |
| **Planned-tool counts and attention providers** | — | **Decline until those tools have tables.** Honest absence |

**The standing rule:** a field the server cannot supply is **not rendered**. Not as a blank,
not as a zero, not as a plausible placeholder. `StrictUndefined` will enforce the first half;
the review has to enforce the second.

---

## Risks

**The half-migrated window is the main one.** Between tranches 4 and 9 the product has two
visual dialects, which is the state it is already in — but the boundary moves each tranche, and
a boundary that moves is harder to reason about than one that sits still. Mitigation: tranche
order follows the *journey* (shell → overview → requests → console → gates → evidence), so an
operator's common path crosses the boundary once rather than repeatedly.

**Tranche 6 is 44% of the debt and the largest redesign.** If it slips, everything after it
slips. Mitigation: it depends on tranches 1–3 being genuinely finished. A macro set that is
80% done makes tranche 6 twice the size.

**The gates carry the platform's strongest guarantees.** Payload hashing, non-optimistic
approval, refusal-before-spend, full-length rationales. A visual rebuild is exactly when those
get simplified by accident. Mitigation: the page-specific truth checks in the testing plan run
per tranche, not at the end.

**Three new font families change every metric on every page.** Line lengths, table densities
and truncation points all move. Mitigation: tranche 2 lands the fonts before any page is
restyled, so the shift is observed once against the old design rather than blamed on the new
one.

**The design is better than the current data in places.** Several verdict lines read beautifully
with rich fixtures and would read thinly against a sparse real run. Mitigation: tranche 0's
fixtures include the thin cases, and every verdict is reviewed against a real run before its
tranche exits.

---

**See also:** [the testing plan](interface-overhaul-testing.md) · [the
corrections](../redesign/05-review-and-corrections.md) · [the
requirements](../design/README.md) · [ROADMAP §3.12](ROADMAP.md)
