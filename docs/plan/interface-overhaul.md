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

## Before anything: four decisions

Three need an ADR. None is large; all three are cheap now and expensive at tranche 6.

### ADR-A — The interface states a verdict, and a verdict is composed, never inferred

**The decision:** every `*_verdict`, `*_summary`, `*_copy` and `*_label` field is composed
deterministically in Python from stored rows. No page load calls a model. A sentence a page
wants is a template over counts, states and server-rendered figures.

**Why it needs a record:** the design's central move is to lead each page with a plain-language
verdict, and the prototype's own review copy — *"complete, traceable, and cautious"* — cannot
be produced that way (see correction D2). Without a record, the first person who cannot compose
a sentence deterministically will reach for a model call at a gate, and that is a new agent
role, a per-view cost, and a judgement masquerading as a summary.

**What it must say:** the composition rule; that a model-authored summary, if ever wanted, is
**stored as a judged statement and never generated on load**; and that a verdict line is a
presentation of the record, so it carries no figure the record does not already hold.

### ADR-B — A fixed-scheme region carries its own measured palette

**The decision:** a region that keeps one scheme's colours while the page changes scheme — the
navigation rail today — declares its own token family, and every accent painted on it is
measured against it.

**Why it needs a record:** correction D1. Three light-theme accents fail 3:1 on the rail, the
focus ring among them at 2.04:1, and they failed *because the rail was not in the table*. The
rule generalises beyond the rail and beyond this redesign.

### ADR-C — `/runs/active` resolves the current run

**The decision:** navigation stays a tuple of static hrefs. One-action access to "the run I am
watching" is a real route that redirects — 303 to the current run, or to `/requests` when there
is none.

**Why it needs a record:** correction D4. It is small, but it is the first nav item whose
destination is computed, and the alternative — a dynamic `href` on `NavItem` — would silently
retire the drift test that guarantees every page is reachable.

### The fourth decision needs no ADR

**One badge or two.** The design shows counts on Overview and Requests; one provider is
registered. Register a second `BadgeProvider` with its own key, label and ADR reference, or
show one badge. **Recommended: one, on Requests.** The Overview count duplicates what the
attention verdict already says in words, and the deliverable's own rule is not to announce the
same count twice.

---

## Sequencing against the rest of the roadmap

Two items are in flight and both touch templates this overhaul rewrites.

**§2.1 — five sections fail to draft.** Its diagnosis surfaces on the gate-3 review page, which
tranche 6 rebuilds. **§2.1 goes first and is not blocked by this plan.** It is a wrong number
in front of somebody; this is a page that looks like two designs. If §2.1 is still open when
tranche 6 begins, tranche 6 takes its surface as it finds it and rebuilds around it — the
"Sections in this draft" record is a data contract either way.

**§3.1 — the portfolio's third door.** Adds a control to the transaction form, which tranche 8
rebuilds. **Land §3.1 first.** Portfolio is 18 ramps across three templates — the smallest
migration in the plan — so there is no case for blocking a functional fix behind it.

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

### Tranche 1 — Presentation vocabulary

Python only. No template changes.

1. Central mappings from every `JobStatus`, `StepStatus`, `GateKind`, request state, report
   state and skill state to a `HumanState` carrying a label and a semantic tone key.
2. `RenderedFigure`, `CostContext`, `LineageNode`, `PageContext` — the shapes handlers assemble
   before rendering. A field ending `_display` is a complete server-rendered string.
3. The verdict composers, under ADR-A.
4. Adapt handlers family by family; existing templates keep rendering.

**Exit:** every enum member has a human label and a valid tone, asserted by a completeness test
— a new gate or status without one is a red build. No redesigned template will need to
interpret raw domain data.

### Tranche 2 — Assets and tokens

1. Vendor the three families, latin and latin-ext, variable cuts where they exist, no italic.
   Record six SHA-256 pins in `tests/test_fonts.py` and three OFL files.
2. Implement the token system in `src/aer/web/styles/app.css`: the full §2.2–2.6 set, **plus
   the navigation-rail family under ADR-B**, plus focus, control-boundary, type and spacing
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
4. The rail's own palette (ADR-B). Selection carried by the teal rule and `aria-current`, never
   by the 1.21:1 fill.
5. `/runs/active` (ADR-C). No "Components" item, ever (D5).
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

**Do not flatten the gates into one another.** The financials gate sorts by what decides the
question; the peer gate renders rationales at full length; the assumptions gate reads live rows
and carries three forms per row that must **not** nest inside the decision form. The frame is
shared; the evidence is not.

**Exit:** liveness is answerable in two seconds; any gate is recognisable as a gate; approval
stays hash-bound, non-optimistic and refused when stale; the assumptions gate still renders
from current rows.

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
| **Composed verdicts and summaries** | ~12 | **Adopt under ADR-A**, deterministic only. Any that cannot be composed from rows is **declined**, not faked |
| **Counts and readiness labels** — sources, claims, coverage | ~8 | **Adopt** where the query is cheap; a count that costs a slow join goes behind the badge mechanism, off the render path |
| **Structural regrouping** — gate journey, attention index, lineage nodes, typed replay findings | ~10 | **Adopt.** These are reshapes of data already assembled, and they are what stops templates doing positional indexing |
| **Typical-cost guidance on the request form** | 1 | **Adopt, with the `unavailable` case designed.** Guidance from history, never a promise, never client-computed |
| **`first_run` / provider-failure typing** | 2 | **Adopt.** Explicit state; never inferred in Jinja from an empty list that may also mean a provider failed |
| **Deterministic figure assets** — sensitivity heatmap, valuation chart, exposure | 3 | **Adopt for the two that have data** (heatmap, valuation history). **Defer exposure to §3.2** |
| **Server GET filter/sort** | 3 | **Adopt** where it makes a returnable URL. Client filtering stays for rows already on the page |
| **Point-in-time as two radios** | 1 | **Adopt.** Presentation only — the boolean and its default-on meaning do not change |
| **Stale-confirmation token for removal** | 1 | **Adopt** if removal does not already re-check eligibility; otherwise decline as duplicate |
| **`/runs/active`** | — | **Adopt (ADR-C)** |
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
