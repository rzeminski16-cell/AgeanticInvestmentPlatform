# The interface overhaul — testing plan

*What each tranche of [`interface-overhaul.md`](interface-overhaul.md) must prove, the machinery
that does not exist yet, and the two rules that stop this suite lying to you.*

**Read [`../developers/testing.md`](../developers/testing.md) §The interface first.** It names
the four things the current suite structurally cannot say about a screen. This plan builds the
machinery that closes them, and the handoff's own checklist
([`../redesign/03-claude-implementation-handoff.md`](../redesign/03-claude-implementation-handoff.md)
§11) is adopted wholesale as the per-page content. What follows is what that checklist needs in
order to run here.

---

## The two rules, restated because this plan strains both

**One pytest process per database.** The suite empties tables between tests. Every concurrent
run needs its own `AER_TEST_DATABASE_URL`.

**The full suite is two processes.** `just test` then `just test-e2e`. Playwright's synchronous
API leaves a running loop on the main thread that wedges every pytest-asyncio test after it.

**This plan roughly doubles the browser suite** — a theme axis, a viewport axis and an
accessibility pass over 115 existing tests. Two consequences, both to be designed for rather
than discovered:

- **Parameterise, do not duplicate.** A theme × viewport matrix over every existing test is
  460 browser tests and twenty minutes of wall clock. Apply the matrix to *one representative
  page per family* and keep single-axis coverage everywhere else.
- **The accessibility pass is its own file**, `tests/e2e/test_accessibility.py`, so a red build
  says "accessibility" on the summary line rather than in the middle of the run console's
  tests.

---

## Machinery

Four pieces were planned. **Two landed in tranche 0, along with two nobody had thought to
ask for**; the other two are tranche 2's, because they need the new tokens to measure.

| Piece | State | Where |
|---|---|---|
| axe-core, vendored | **Built** | `tests/a11y.py`, `tests/e2e/test_a11y_harness.py` |
| The ramp ratchet | **Built** | `tests/test_palette_migration.py` |
| The script/DOM contract | **Built** — not planned, found while inventorying the swap ids | `tests/test_script_dom_contract.py` |
| Every page renders | **Built** — not planned, found because nothing opened a page | `tests/test_every_page_renders.py` |
| The contrast harness | Tranche 2 | `tests/e2e/test_contrast.py` |
| The viewport and theme matrix | Tranche 2 | folded into each family's e2e file |

### The two that were not planned

**The script/DOM contract.** The four scripts reach into markup by name and **nothing would
have noticed a rename.** A console whose `#run-spend` became `#spend` keeps rendering, keeps
its server-side value, and stops updating — correct on load and quietly wrong four minutes
later. Out-of-band swaps are worse: htmx targets an id, and a swap that finds no target is
not an error. The inventory the plan asked for is better as an assertion than as a note.

**Every page renders.** `test_shell_nav.py` proves every page is *reachable* and never opens
one, so the suite knew the map was honest and nothing knew whether the places on it rendered.
Under `StrictUndefined` that is the single most likely failure of this overhaul, and the one
that surfaces on one page in one state nothing else visits.

---

## What the built machinery looks like

### 1. axe-core, vendored — built

The handoff requires axe-core on every page family in both themes. **The suite runs with no
network and `package.json` had Tailwind and htmx and nothing else** (correction D8). Now
`axe-core` 4.13.0, 568 kB, hash-pinned in `tests/a11y.py`, injected from disk and asserted
never to reach the served tree. Twelve tests cover the harness itself — including a
deliberately unlabelled image, because a harness nobody has watched catch something is a
harness whose green means nothing.

- Add `axe-core` as an npm devDependency.
- Commit `axe.min.js` under `tests/fixtures/axe/` with its SHA-256 recorded and asserted,
  exactly as `htmx.min.js` is.
- Inject it from the local file with `page.add_script_tag(path=...)`. **Never serve it from
  `/static`** — it is a test asset, and a page that shipped it would be a page loading a
  testing library in production.
- Treat violations as build failures, not a report. Start with `serious` and `critical`; add
  `moderate` when the baseline is clean.

**Automated checking finds perhaps half of what is wrong.** The rest is the by-hand pass.

### 2. The contrast harness — tranche 2

The one test that would have caught roadmap §2.5, and the one that catches correction D1.

```python
# tests/e2e/test_contrast.py — sketch
def ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """WCAG relative luminance. Read from computed style, never from a class name."""
```

Rules that make it worth having:

- **Assert computed colours, never class strings.** A class string is what is already wrong.
- **Walk the rendered page** rather than a list of token pairs: every text node's computed
  colour against its effective background, every control boundary, every focus ring against
  *both* the component and the surface behind it.
- **Both schemes, and both ways of choosing one** — `color_scheme` on the browser context, and
  the in-app control. They are different code paths.
- **Include fixed-scheme regions explicitly** (ADR 0088). The navigation rail keeps dark colours
  on a light page, and it is precisely the region a page-level sweep gets wrong.

Thresholds: 4.5:1 normal text, 3:1 text ≥ 18.66px bold or 24px regular, 3:1 control boundaries
and meaningful graphics.

### 3. The viewport and theme matrix — tranche 2

Nothing in `tests/e2e/` sets a viewport today. Two widths, held:

| Width | Represents | Asserts |
|---:|---|---|
| 320px | The narrow floor | Native disclosure nav; no document-level horizontal scroll; every control reachable |
| 1440px | The workbench | Persistent rail; one-action navigation; evidence spine in its margin |

**The assertion that matters at both:** `document.documentElement.scrollWidth <= clientWidth`.
A wide table scrolls inside its own bounded region; the page body never scrolls sideways. Add
200% zoom as a third case on the three widest pages — the console, the assumptions gate and the
review gate.

### 4. The ramp ratchet — built

Roadmap §2.5 asks the palette migration to end with a test that fails when a template
reintroduces a raw ramp. **A test asserting zero is a test somebody deletes in week one of a
multi-week migration.** So it ratchets:

```python
# tests/test_palette_migration.py — sketch
# A per-template ceiling that may only ever fall. Zero for the twelve already clean,
# and zero for every template a tranche has migrated. A file above its ceiling is a
# red build; a file below it fails too, with "lower the ceiling" as the remedy.
```

- **Committed in tranche 0** with the current census as the opening ceiling.
- **Lowered to zero by each tranche** for the templates that tranche migrates.
- **Becomes a hard zero assertion in tranche 9**, plus a ban on any `faint` token name (D3) and
  on runtime-composed Tailwind class names, which the scanner cannot see and which therefore
  render with no colour at all.

---

## What each tranche must prove

Exit criteria in testable form. **A tranche is not done until its row is green.**

| Tranche | Must prove | Where |
|---|---|---|
| **0 — Hold** | **Done.** Both suites baselined. Ceiling committed and watched failing in three shapes. axe vendored and watched catching a real violation. Every script-and-swap id asserted. Fifty pages opened against a driven run | `tests/test_palette_migration.py`, `tests/test_script_dom_contract.py`, `tests/test_every_page_renders.py`, `tests/e2e/test_a11y_harness.py` |
| **1 — Vocabulary** | **Done.** Every member of eight mapped enums has a label and a tone; a new one without either is a red build, watched failing. A guardrail and a fault can never share a tone. Cost renders identically everywhere. A composed verdict drops a zero, refuses a breakdown that does not sum, is never empty, and **cannot report a partial count as the all-clear** — all four watched failing. A figure carries its lineage or the reason it has none. `Authored` names no evidence type, asserted from the syntax tree | `tests/test_presentation_vocabulary.py`, `tests/test_web_figures.py`, `tests/test_verdict.py` |
| **2 — Tokens** | **Done.** 136 pairings measured from `getComputedStyle` in both schemes, **including the navigation rail** — removing ADR 0088's scope reproduces 2.04, 2.23, 2.00 and 1.06, watched failing. Ledger rules asserted to stay *below* 3:1. Font chain verified in Chromium; **eight** hashes pinned, not six; every scale weight proved real, including the 450 and 550 that decided the supplier. No `faint` token in any template or in the stylesheet. Type scale checked against the design system's own table. A literal ink on a fill that flips is a red build | `tests/e2e/test_contrast.py`, `tests/test_fonts.py`, `tests/test_type_scale.py`, `tests/test_palette_migration.py` |
| **3 — Macros** | **Done.** Nineteen macros render in both schemes; axe passes over the whole set in each. Focus outline, hover fill, disabled fill and sheet geometry read from `getComputedStyle`. Nothing animates; 320px causes no sideways scroll. Every provenance badge has a required `href`. **No macro accepts a class, style, html or attrs argument, and none may be declared without being re-exported** — both watched failing | `tests/test_components.py`, `tests/e2e/test_component_states.py` |
| **4 — Shell** | One nav DOM; one badge target; one drawer. Wide-width reveal verified in Chromium **and** Firefox. Scripting off at 320px and 1440px: every link reachable, both preference forms submit. Shell survives Redis down, database down, badge timeout | `tests/e2e/test_shell.py`, `tests/test_shell_nav.py` |
| **5 — Overview + requests** | Launcher renders with no database. First-run distinguished from caught-up. Provider failure appears as an item, never silence. Rejected form returns every value across both validation rounds. One page-based destructive confirmation; **no `confirm()` anywhere** | `tests/test_overview.py`, `tests/e2e/test_request_form.py` |
| **6 — Console + gates** | All declared steps render; none invented by the stream. Budget refusal distinguishes run from monthly. Conditional gates never presented as certain. **Stale hash refused; decided gate renders no form; approval never optimistic.** Assumptions gate renders from live rows, and its row forms do not nest in the decision form. Peer and theme rationales full length. **The authored verdict cannot be cited — no `SourceRef` constructs from it — and a run that never wrote one still renders a complete composed verdict** | `tests/e2e/test_run_console.py`, `tests/e2e/test_gates.py`, `tests/test_verdict.py` |
| **7 — Evidence + reports** | Excerpts verbatim with verdicts beside them. Valuation read from the ledger, not recomputed on GET. **Heatmap byte-identical for identical rows.** Replay stays a POST with typed findings. Drafts appear only in the operator's list | `tests/e2e/test_evidence_surfaces.py`, `tests/test_charts.py` |
| **8 — Portfolio, skills, knowledge** | **All four portfolio totals present or all withheld.** Cash is a position; unpriceable rows carry a reason; penny-exact; a typed transaction stays `Typed`. Skill export round-trips source bytes; import shows a diff and rechecks the base hash. Graph coordinates deterministic | `tests/e2e/test_portfolio_screen.py`, `tests/test_portfolio_service.py`, `tests/e2e/test_skills_editor.py` |
| **9 — Hardening** | Ratchet at zero and hard. No runtime-composed class. Stylesheet recompiled and committed. Every vendored hash verified. Full manual pass recorded | `tests/test_palette_migration.py`, `tests/test_web_pages.py` |

---

## The four standing layers

Applied continuously from the tranche that introduces them, not at the end.

**Accessibility** — `tests/e2e/test_accessibility.py`. axe-core per page family per theme;
keyboard traversal in DOM order through shell, drawer, every gate and every form; **gate
decision controls after the evidence in focus order even when visually sticky**; one non-empty
`h1` per page, no skipped levels; every input labelled and every error associated; 24×24
minimum pointer targets.

**Contrast** — `tests/e2e/test_contrast.py`. As above.

**Responsive** — folded into each family's existing e2e file at the two widths, so a table that
breaks at 320px fails in the file that owns that table.

**No-JavaScript** — extend the existing pattern. Ten tests use
`browser.new_context(java_script_enabled=False)` today; every consequential path added by this
overhaul gets one. **This is the binding rule of ADR 0006 and the cheapest guard in the suite.**

---

## Visual regression

**Baseline components, not pages.** A full-page screenshot per surface fails on every
legitimate change, and a suite whose failures are usually noise is a suite whose failures stop
being read.

- Baseline **the component inventory** — card, verdict block, status label, callout in four
  severities, provenance badge, grade chip, empty state, evidence spine, decision panel, nav
  rail — each in both schemes.
- **This is what correction D5 turns "Components" into.** The design-reference page does not
  ship as a route; it becomes a test fixture rendered by the suite and captured as the baseline.
- **Treat a pixel mismatch as less important than domain truth, accessibility and progressive
  enhancement.** The handoff says so and it is right. A screenshot diff is a prompt to look,
  not a failure in itself.
- The four prototype captures in `docs/redesign/previews/` are the *character* reference for
  hierarchy and tone. They are not a pixel target.

---

## By hand

Extend [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md) §8.3, which
already carries the keyboard, narrow-window, contrast and zoom passes. Three additions this
overhaul needs:

**The dialect boundary, deliberately.** Between tranches 4 and 9 the product has two visual
languages. Walk the operator's common path — `/` → `/requests` → a run console → a gate — and
confirm the boundary sits where the plan says it does. A boundary in an unexpected place means
a tranche is not finished.

**The verdict lines, against a real run.** Every composed verdict reads well against rich
fixtures. Read them against a thin run — five sections that did not generate, no peers, no
prices. A verdict that only works when the data is good is a verdict that fails when it matters.

**The fonts, on the target machine.** Three families, six files, `font-display: swap`. Watch a
cold load: the fallback must be legible and the reflow must not move a decision button under
the cursor. **A green Linux suite says nothing about behaviour a host supplies** — two of the
three defects the by-hand sheet has found were invisible to CI by construction.

---

## Two order-dependent failures, found and left alone

Both predate this overhaul and both are invisible in a full run, because pytest collects
alphabetically and the alphabetical order happens to be the working one. Anybody running a
subset in a different order meets them, so they are written down here rather than rediscovered.

**`test_overview.py` before `test_every_page_renders.py`** errors the render harness. The
overview tests commit seed rows for real — they have to, because the browser is a separate
client — and the harness then drives its own run over an estate it did not create. Reversing
the two files passes. Verified as pre-existing by running the same pair at the commit before
tranche 5 touched either.

**`test_backup.py`** fails twelve tests when PostgreSQL is down but `pg_dump` is installed,
where every other database test skips.

Neither is in any tranche's scope. Both are real, and a suite whose result depends on
invocation order is a suite that will eventually be wrong in the other direction.

## What this plan does not prove

Stated so nobody reads a green build as more than it is.

- **That the design is right.** These checks prove a screen is usable, legible and honest. They
  do not prove it is the right screen. That is what a real operator using it for a week proves.
- **That the verdict lines are true.** A composed sentence is testable for composition, not for
  judgement. If the composer says "no validation failure blocks approval" and something does,
  the test that catches it is a domain test, not an interface one.
- **That anything here is investment advice.** It is not, and every surface says so.

---

**See also:** [the implementation plan](interface-overhaul.md) · [the testing
layers](../developers/testing.md) · [the by-hand
sheet](../developers/testing-by-hand.md) · [the handoff
checklist](../redesign/03-claude-implementation-handoff.md)
