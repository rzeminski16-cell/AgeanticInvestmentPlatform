# Tracework — Claude implementation handoff

> Build contract for implementing the approved redesign in the production application. Read
> this with `00-design-direction.md`, the visual-system specification delivered beside it,
> and the original documents under `../design/`.

## 1. What this document is for

Implement Tracework as a server-rendered decision workstation whose hierarchy is:

> **Verdict first. Evidence beside it. Proof on demand.**

This is a template, presentation-model and CSS migration. It is not a front-end rewrite. The
finished application must still be Jinja rendered by FastAPI, styled with Tailwind v4, and
progressively enhanced with the existing htmx and small-script vocabulary.

The implementation is successful when the operator can:

1. see what needs attention, how long it has waited and what it has cost;
2. understand where they are in a research run and what decision may come next;
3. approve a gate knowing exactly what is bound to that approval;
4. trace a material figure to its calculation or archived source in two clicks; and
5. use every consequential path with scripting disabled.

### Authority order

When two artefacts appear to disagree, use this order:

1. `../design/00-the-product.md`, `../design/01-constraints.md` and the page specifications
   define domain truth, safety and required behaviour.
2. The redesign direction and visual-system documents define hierarchy, appearance and
   responsive behaviour.
3. This handoff defines the recommended production structure and migration order.
4. `prototype/index.html` and the pages linked from it are **visual and interaction
   references**, not an application architecture or source of domain truth.

The prototype entry point is `prototype/index.html`; its shared visual assets are
`prototype/assets/tracework.css` and `prototype/assets/tracework.js`. The latter may switch
fixtures and demonstrate the theme, drawer, sample states or elapsed time. **Its theme and
drawer code are demonstrations, and all of its JavaScript is a presentation harness rather
than production domain state. Do not copy it as production behaviour.** In production,
statuses, approvals, costs, counts, formatted figures, gate progress, evidence verdicts and
portfolio totals all arrive from the server. Reuse visual CSS and accessible chrome patterns
where suitable; implement production theme and drawer behaviour through the established shell
contracts and rebuild domain behaviour as pages and server fragments.

## 2. Scope

### In scope

- The shared shell, persistent wide-screen navigation, compact narrow-screen disclosure,
  breadcrumbs, preferences, badges, disclaimer, footer and single drawer.
- `/`: attention-first returning state, first-run state, database failure state, counts and
  the nine-tool launcher.
- Requests: list, create, edit, detail, archive, not-found, immutable and page-based removal.
- Run console: every run state, phase/gate journey, liveness, spend and evidence availability.
- All seven gates, including one shared gate frame and the full review-gate hierarchy.
- Evidence, claims, footnotes, calculations, valuation and replay results.
- Report history, report reading surface and browser preview. The rendered PDF's page layout
  remains out of scope.
- Skills, imports, dry runs, Knowledge, the server-rendered graph and company history.
- Portfolio, including its empty, partial, refused and broken states and its transaction form.
- Light, dark and system themes; responsive behaviour; guidance mode; accessibility states.
- The server-side presentation data needed for the approved hierarchy, prioritised in
  section 9.

### Out of scope

- A SPA, client router, client store, React, Vue or a runtime component library.
- Redesigning the PDF or print pagination defects.
- Designing the seven planned tools beyond their honest launcher placeholders.
- Redesigning Settings, Costs, Health or API documentation beyond inheriting the new shell.
- New portfolio return/exposure calculations in the first visual migration. Reserve their
  intended places, but do not invent values.
- A pan/zoom/drag knowledge-graph island without its separate ADR and declared script
  contract.
- Changing domain, validation, approval, security or accounting rules to make a layout easier.

## 3. Non-negotiable production rules

These are acceptance criteria, not preferences.

| Rule | Production consequence |
|---|---|
| The server owns every domain state | Render a full page or fragment after every change. No client-side source of truth. |
| Every form works without JavaScript | Every consequential control has a real `method`, `action`, CSRF field and server response. htmx enhances the same request. |
| JavaScript may own chrome, never a figure | It may manage focus, scroll locking, a drawer, a running heartbeat and filtering of rows already present. It may not calculate, format, fetch or persist domain figures. |
| No optimistic approval | A gate remains pending until the server validates its payload hash, records the decision and redirects. |
| Figures arrive presentation-ready | Currency symbols, separators, rounding, dates, percentages, signs and unavailable phrases are generated by the server. |
| A provenance badge is a link | `Source fact`, `Calculated`, `Attested`, `Assumed` and `Judged` always resolve to an evidence destination. Do not emit one without `href`. |
| Provenance and confirmation remain separate | Never merge class with `Suggested`, `Unconfirmed` or `Confirmed by … at …`. Portfolio grade (`Typed` or `Documented`) is a third, distinct axis. |
| Refusal is not failure | A rule correctly withholding an unsafe result uses refusal language and treatment; a broken worker or dependency uses failure language and treatment. |
| No status relies on colour | Every state has a visible word or phrase. Icons, where present, are supplementary. |
| Navigation remains data | Render registry sections/items through a loop. Do not introduce page-specific navigation markup. |
| One navigation DOM | Do not render separate desktop and mobile navigation trees. The badge target appears exactly once. |
| One drawer | Keep one shell-owned dialog and one focus trap. Every trigger is a working link before enhancement. |
| Deep views have URLs | Gates, claims, calculations, footnotes, report previews, portfolio dates and destructive confirmations remain addressable pages. |
| GET reads; POST changes | A returnable view puts state in the URL. Destructive or state-changing actions are forms, never links. POSTs redirect after success. |
| The disclaimer is shell-owned | Render `This is not investment advice.` exactly once on every application page. Do not make individual pages responsible for it. |
| Theme is stamped by the server | `light` and `dark` are explicit `data-theme` values; `system` is the absence of the attribute. No head script and no flash workaround. |
| Everything is local | Vendor and hash fonts, scripts and any SVG assets. No CDN or third-party runtime request. |
| Reduced motion loses no meaning | The running heartbeat may become static. No value, status or ordering depends on animation. |
| UK English | Use `colour`, `organisation`, `recognise`, `behaviour`, and the established product vocabulary. |
| WCAG 2.2 AA is the floor | Designed focus, contrast, reflow, target size, headings, labels, field associations and keyboard operation ship with the component, not later. |

`StrictUndefined` remains enabled. Any new property named in a template must be supplied by
every handler and test fixture that renders it.

## 4. Production architecture

### 4.1 Think in pages, fragments and presentation models

Use three layers:

1. **Domain/service layer** — remains authoritative and unchanged unless a named data-contract
   addition requires a query or deterministic calculation.
2. **Presentation-model layer** — maps domain enums and records into human labels, formatted
   figures, semantic tones, evidence links and page-specific summaries.
3. **Jinja layer** — chooses semantic HTML and composition. It does not translate raw enums,
   format values, perform arithmetic or infer whether something is safe to show.

Prefer explicit presentation objects over passing ORM records deep into macros. The names may
follow the codebase's conventions, but keep these semantic shapes:

```text
HumanState
  label                  # "Waiting for you", never AWAITING_APPROVAL
  tone                   # neutral | live | attention | success | refusal | failure | muted
  explanation            # optional, already written in UK English

RenderedFigure
  display                # complete server-rendered string, or "—"
  sign                   # positive | negative | zero | none; supplied, never inferred in JS
  unavailable_reason     # required when display is withheld
  provenance             # ProvenanceRef when the figure needs lineage
  confirmation           # optional and distinct from provenance
  grade                  # optional portfolio evidence grade

ProvenanceRef
  kind                   # source_fact | calculated | attested | assumed | judged
  label                  # human label
  href                   # required

LineageNode
  key
  kind
  label
  summary
  href                    # required
  is_current

CostContext
  spent_display
  ceiling_display
  remaining_display
  used_fraction           # calculated on the server if a visual meter needs it
  estimate_display        # optional; never fabricate an estimate
  limit_scope             # run | monthly | none
```

Treat formatted display strings and accessibility labels as part of the server contract. If a
visual needs a shortened value, do not truncate the canonical string in CSS or JavaScript;
request a second server-rendered display field.

### 4.2 Recommended template structure

Keep existing route template names where practical. Introduce a small, legible macro library
rather than a large generic component engine:

```text
src/aer/web/templates/
├── base.html
├── _shell/
│   ├── navigation.html       # the only primary-nav rendering
│   ├── badges.html           # OOB targets; each id exactly once
│   ├── breadcrumbs.html
│   ├── drawer.html           # the only dialog/focus trap
│   ├── preferences.html
│   └── footer.html
├── _ui/
│   ├── page.html             # page_header, object_header, section_header
│   ├── state.html            # status, notice, refusal, empty
│   ├── figures.html          # figure, cost_context, grade
│   ├── evidence.html         # provenance, confirmation, evidence_spine, proof_statement
│   ├── forms.html            # field, choice_group, error_summary, form_actions
│   ├── tables.html           # table_shell, filter, empty_row
│   └── disclosure.html       # native details/summary treatment
├── runs/
│   ├── _gate_frame.html      # shared structure, sequence, cost and decision form
│   ├── _gate_sequence.html
│   ├── _decision_form.html
│   ├── _step_list.html
│   └── ...existing route templates
└── ...existing page families
```

If the repository already groups files differently, preserve its route organisation and use
the same separation of responsibilities. Do not perform a mass rename merely to match this
tree.

### 4.3 Macro rules and recommended signatures

The existing rule remains: **a macro takes data, never caller-supplied classes**. A caller may
use a Jinja `call` block for page-specific content, but it may not mutate the component's
visual contract.

Recommended public macros:

```text
page.page_header(context)
page.object_header(object, state=None, actions=())
state.status(value: HumanState)
state.notice(kind, title, body, action=None)
state.empty(title, explanation, action=None)

figures.figure(label, value: RenderedFigure, note=None)
figures.cost(context: CostContext)
figures.grade(grade)

evidence.provenance(ref: ProvenanceRef)
evidence.confirmation(ref)
evidence.spine(nodes, compact_label=None)
evidence.proof_statement(title, explanation, href=None)

forms.field(field)
forms.choice_group(field)
forms.error_summary(errors, round=None)
forms.actions(primary, secondary=None)

tables.shell(caption, columns, filter=None)
disclosure.section(summary, count=None, open=False)
```

Use a page-local include rather than adding flags to a macro when only one page needs a
variation. Avoid signatures such as `card(..., compact=True, blue=True, border=False,
classes="...")`; that is bespoke markup hidden behind a function.

### 4.4 The shared gate frame

All seven gate templates must implement this semantic sequence:

```text
breadcrumb and object identity
gate question + pending state
gate sequence
cost context
what you are deciding
why it matters / consequence of approval
evidence specific to this gate
decision form
```

At wide widths, CSS may place the decision form in a sticky right column while the evidence
occupies the reading column. **In DOM and focus order, the form comes after the evidence.** At
narrow widths it naturally follows the evidence.

The assumptions gate contains row-level forms. Do not wrap the evidence area in the final gate
form and do not create nested forms. Render each row action as its own form, then render the
gate decision as a separate form after the evidence. The decision form contains its own CSRF
token, payload hash, optional notes and submit buttons.

The visible hash guarantee belongs next to the decision:

> You are approving exactly what is shown here. If it changes before your decision is
> recorded, the platform will stop and ask you to review it again.

Do not display the raw payload digest as the reassurance. Make the full digest available in a
proof disclosure or audit link.

## 5. The one-navigation-DOM strategy

Render the registry exactly once in `_shell/navigation.html`:

```html
<aside class="shell-index" aria-label="Application navigation">
  <details class="nav-disclosure">
    <summary>Menu <span class="current-location">Research</span></summary>
    <nav id="primary-navigation" aria-label="Primary">
      {# one loop over shell.nav; one badge slot per registered item #}
    </nav>
  </details>
</aside>
```

Implementation requirements:

- At the workbench breakpoint, hide the `summary` and force the single `nav` content visible
  with CSS; place it as the persistent index rail. Do not add `open` based on guessed viewport
  width and do not clone the items into a second sidebar.
- Below the breakpoint, restore native `<details>/<summary>` behaviour. Closed is the default;
  the same links and preference forms become the compact navigation panel.
- Verify in target browsers that author CSS makes the closed details content visible at the
  wide breakpoint and that it remains in the accessibility tree. If a browser does not, use a
  single CSS-layout variant around the same `nav`; do not solve it by duplicating the tree.
- Put the badge target inside this one `nav`. `id` values used by OOB swaps appear once.
- Put current location outside the closed part as a breadcrumb or compact location label, so
  the operator always knows where they are without opening the disclosure.
- Keep preferences as real forms at the end of the same navigation rendering.
- Keep active matching in Python. Use `aria-current="page"`; do not derive it from the URL in
  JavaScript.
- Keep navigation and authorisation separate. A visible item never grants route access.

Recommended responsive thresholds:

- `>= 60rem`: persistent 232px navigation rail and one-action access to every top-level destination.
- `< 60rem`: native disclosure; main content uses the full width, and the evidence-spine margin joins the reading flow as a compact sequence.

Use named CSS custom properties or documented component rules for these thresholds rather than
scattering unrelated media-query values.

## 6. CSS, tokens and type migration

### 6.1 Token contract

Implement the revised visual-system tokens as semantic custom properties. The core direction
is fixed:

- Paper `#F4F7F8`
- Sheet `#FFFFFF`
- Graphite `#15252E`
- Verification ink `#0F6673`
- Ledger line `#CDD8DC`
- Decision amber `#7A4B00`
- Dark paper `#07171D`
- Dark sheet `#0C222B`
- Dark verification ink `#B5ECF0`

The full visual-system file should supply the accessible wash/ink pairs, focus colour,
control-boundary colour, type scale and spacing scale. Keep these token families separate:

```text
structure: canvas, surface, surface-sunken, line, line-strong, control-boundary
type:      ink, ink-muted, ink-subtle, ink-on-accent
brand:     verification, verification-strong, verification-wash
decision:  decision, decision-strong, decision-wash
state:     success, live, attention, refusal, failure, information, muted
focus:     focus-ring, focus-offset
layout:    shell, rail, reading, decision, evidence-margin, drawer
```

Do not reuse a decorative line token as an input boundary. Do not use a small-text colour
unless it clears 4.5:1 against every surface on which the component permits it. Every control
boundary and focus indicator must clear 3:1 against adjacent colours in both themes.

### 6.2 Type contract

Vendor and hash:

- Barlow Semi Condensed, weights 600–700, for display and object identity;
- Source Sans 3, weights 400–650, for interface and long-form reading; and
- IBM Plex Mono, weights 450–600, for figures, formulas, dates, hashes and compact utility
  labels only.

Include latin and latin-ext coverage. Use `font-display: swap` and local fallbacks. Do not
preload every weight. Use tabular numerals for numeric columns and cost contexts. A status,
step name or entire paragraph is not made technical by setting it in monospace.

### 6.3 Theme contract

Define three paths:

1. `html[data-theme="light"]` applies light values.
2. `html[data-theme="dark"]` applies dark values.
3. `html` with no theme attribute follows `prefers-color-scheme`.

An explicit choice wins in both directions. Test explicit light on a dark operating system and
explicit dark on a light operating system. Do not add a client script to race the first paint.

### 6.4 Migration sequence

1. Add new variables and Tailwind v4 theme mappings while the legacy aliases still resolve.
2. Add font files, `@font-face` rules and the recorded SHA-256 values.
3. Build the new primitives and focus rules from semantic tokens only.
4. Migrate shell and Overview; verify both schemes and database-down rendering.
5. Migrate one page family at a time in the order in section 10.
6. Rebuild and commit the compiled stylesheet in every style-changing change.
7. Remove legacy aliases only after no template or script references them.
8. Add a regression check that rejects stock ramp utilities in production templates.

Never compose Tailwind class names at runtime. Every emitted class must be statically visible
to Tailwind's `@source` scan. Prefer a closed mapping in Python to semantic state keys and one
static class per key.

## 7. htmx and no-JavaScript contracts

The redesign should fit the existing four enhancement patterns. A fifth pattern requires a
named contract and a no-script design before code is added.

| Surface | Base HTML path | Enhanced path | Invariant |
|---|---|---|---|
| Form validation | `POST action` returns the full page with all values and errors | The same URL returns/swaps the error fragment on 422 | Validation code is identical. Swap `innerHTML`; the `aria-live` node survives. |
| Drawer preview | A real `href` opens the full destination page | `hx-get` loads a preview fragment into the one shell drawer | Content arrival opens the drawer. Escape and Close restore focus to the trigger. |
| Navigation badges | Navigation is complete with empty badge slots | One hidden load trigger receives OOB badge fragments | Failure leaves slots empty; it never blocks navigation. Each target id exists once. |
| Table filtering | The complete table is present and usable | A hidden filter is revealed and filters only rows already in markup | It fetches nothing, calculates nothing and never changes totals. |
| Run liveness | Full server snapshot plus `<noscript><meta refresh>` | Existing event stream updates allowed liveness chrome and reloads when domain state changes | The script never invents a row, banner, approval, cost or completed state. |
| Theme/guidance | CSRF-protected forms set server-read cookies and redirect back | The same forms may receive busy styling only | No save-on-change toggle and no client persistence. |
| Gate decisions | Form POST validates hash, records, queues and redirects | A busy/disabled treatment may prevent double submission | Never paint `Approved` before the response. Stale payload remains pending/refused. |
| Native disclosures | `<details>/<summary>` opens sections | Optional styling only | No scripted accordion state. Content remains in the document. |

Rules for all htmx responses:

- A full-page URL must remain meaningful when opened directly.
- A fragment must not be the only representation of important content.
- Preserve focus and live-region nodes deliberately across swaps.
- Use server responses for error, refused, empty and success states; do not build them in JS.
- A disabled submit state may say `Recording decision…`; it may not say `Approved`.
- Any new request must be documented with endpoint, trigger, target, swap, focus outcome,
  failure outcome and no-script outcome.

No new production script is required for the first implementation pass. Reuse `drawer.js`,
`tables.js` and the conservative `console.js` contract. If a design detail appears to need a
new script, first attempt native HTML and CSS, then write an ADR-sized component contract.

## 8. State vocabulary

Map enums to presentation states in Python. Templates receive the human result. Use these
labels consistently unless the page specification provides a more precise sentence.

| Domain state | Visible label | Tone | Required explanation or action |
|---|---|---|---|
| Draft request/report | Draft | neutral | Say what has not happened or been spent. |
| Queued run | Queued | neutral | The worker should pick it up shortly. |
| Running run/step | Running | live | Name the current work in plain English, elapsed time and last server contact. |
| Awaiting approval | Waiting for you | attention | Name the gate and say nothing further happens or is spent until a decision. |
| Per-run budget stop | Stopped before spending | refusal | Raise this request's ceiling or stop. |
| Monthly budget stop | Stopped before spending | refusal | Change the monthly budget or wait; do not point at the request ceiling. |
| Stop requested | Stopping after the current step | attention | Cancellation is acknowledged but not complete. |
| Succeeded | Complete | success | Link to the result/evidence. |
| Failed | Failed | failure | Name the failed step, stable code and recovery path. |
| Cancelled | Cancelled | muted | Include time and reason when present. |
| Gate pending | Decision needed | attention | Show the live decision form. |
| Gate approved | Approved | success | Name who and when; no form. |
| Gate rejected | Rejected; run stopped | refusal | No form. Explain that a new run is needed to change it. |
| Conditional gate unknown | May be needed | neutral | Do not count it as a certain future step. |
| Conditional gate skipped | Not needed | muted | State why when it helps. |
| Gate not reached | Not reached | muted | Do not show an active form. |
| Stale payload | Changed since you opened it | refusal | Refuse the decision and link/reload to review the current payload. |
| Verified evidence | Confirmed | success | Say what code checked and link to the artefact. |
| Evidence not yet checked | Unconfirmed | attention | Never imply verification. |
| Evidence check failed | Verification failed | failure | Show the reason and the exact stored excerpt. |
| Rule-withheld output | Not produced | refusal | Name the rule, remedy and why the refusal protects the record. |
| Dependency unavailable | Unavailable | failure | Name the dependency and concrete recovery. |
| Archived | Archived | muted | Offer restoration where supported. |
| Disabled skill | Disabled | muted | Say runs will not pick it up. |

Do not use a generic `warning` state as a substitute for this distinction. In particular,
budget stops, inadmissible sources, incomplete totals and inappropriate valuation methods are
refusals working as intended, not failures.

Keep the exact provenance vocabulary:

- `Source fact`
- `Calculated`
- `Attested`
- `Assumed`
- `Judged`

Keep confirmation separate: `Suggested`, `Unconfirmed`, `Confirmed by {name} at {time}`.
Keep portfolio grade separate again: `Typed`, `Documented`.

Humanise all nineteen run-step keys in a single tested mapping. Show the technical key only as
secondary audit text, because it must still match worker logs. A missing mapping is a build
failure, not a fallback to `red_team` or another raw identifier.

## 9. Data-contract additions

These are presentation requirements, not permission for templates to query the database.
Handlers or query services assemble them before rendering. A field ending in `_display` is a
complete server-rendered string.

### Must — required for the redesigned hierarchy

Implement these before calling the matching page family complete.

#### Shared shell and presentation vocabulary

- `page_context.section_label`, `section_href`, `page_label` and optional object trail, so
  location is visible with the menu closed. The database-down `/` path must construct its
  context without a database.
- Central mappings for run, step, gate, report, skill and request states to `HumanState`.
- A stable semantic `tone` key; templates never inspect raw enums.
- Server-composed accessible badge labels, including pluralisation.

#### Overview

- For each attention item: `waiting_since_display`, `age_display`, `spent_display`, optional
  `ceiling_display`, server-calculated `cost_fraction`, gate-specific `detail`, full-page
  `href` and optional preview endpoint.
- `is_first_run` or an equivalent explicit state. Do not infer it in Jinja from a convenient
  empty list that may also mean a provider failed.
- Provider failures as typed attention results, distinct from a valid empty result.
- Bounded-section overflow text/counts supplied by the server.

#### Requests

- List rows: `created_at_display`, `last_run_at_display`, `spend_display` and human state.
- Commission form: a server-provided historical/typical cost range per depth, with a clear
  `unavailable` case. It is guidance, not a promise and not client-calculated.
- The two validation rounds identified in the returned error model so copy can say what was
  checked and what remains to be checked without implying piecemeal validation.
- Destructive-confirmation counts and an explicit list of audit/spend/artefact records that
  survive removal.

#### Run console

- `cost: CostContext`, including the run ceiling and limit scope.
- `current_work.label`, `technical_key`, `elapsed_display`, `last_seen_display` and a
  server-owned liveness state.
- A `gate_journey` list containing label, URL, required/conditional status and the human state
  `passed`, `current`, `may_be_needed`, `not_needed` or `to_come`. Do not pretend five
  conditional gates are certain.
- Every step: human label, secondary technical key, state, elapsed display, cost display,
  message and stable failure code.
- Evidence destinations with server-owned readiness/count labels, for example `12 sources`,
  `31 claims`, `Valuation not produced yet`.
- Failure recovery copy and destination, including whether recovery requires a new run.

#### Gates

- One `gate_context` on all seven pages: question, consequence, state, sequence, cost context,
  estimated additional cost when legitimately available, payload-hash explanation, form
  action and decision record.
- Gate 1 source list and risk summary promoted in its presentation model rather than found by
  positional indexing in the template.
- Review attention summary: failed validations, unresolved source disagreements, non-generated
  sections, challenge count and cost context, each linked to its section anchor.
- Assumption rows read from current saved rows and carry separate provenance and confirmation
  objects; never render the gate from frozen step output.
- Stale-payload errors return the current page with an explicit refusal state and fresh hash.

#### Evidence and valuation

- A page-level evidence `verdict` with human explanation and semantic tone.
- `lineage_nodes` for the visible evidence spine, each with a real destination and current
  position. Build the chain server-side from stored provenance.
- `hash_short_display` and `hash_full_display`; short is for scanning, full remains available
  in a disclosure/audit surface.
- Exact excerpt, verifier verdict and located context kept together in one presentation model.
- Sensitivity data identifies axis labels and the central/base case. If rendered as an SVG,
  the server generates deterministic bytes and accessible text/table equivalence.
- Replay result with an overall verdict and typed findings rather than an unlabelled list.

#### Reports

- History rows: conclusion/rating display, valuation display, as-of and produced dates, cost
  display, approval/draft state and prior-report reference where one exists.
- Proof statement data: approval actor/time, report hash and archived-bytes link.
- The report document comes before export controls in template order.

#### Portfolio

- `book_grade` summary and explanation, separate from row-level provenance.
- A typed completeness/refusal object controlling all four headline figures together. The
  four values must be withheld as one decision, never independently inferred in Jinja.
- As-of context: requested date, effective last-close date and complete server-rendered label.
- Per-row reason text when a price or conversion is unavailable; never pass an empty numeric
  cell and expect the template to explain it.
- Transaction form schema by transaction kind, even if the first implementation renders the
  groups with native disclosures rather than a new route.

#### Skills and Knowledge

- Skill editor: stored source, validation issues and composed policy as explicit parallel
  presentation objects.
- Knowledge: a server-derived lead verdict/action summary using existing freshness and
  catalyst data; an empty/sparse state distinguished from dependency failure.
- Graph nodes include full-page company `href` values. Layout coordinates remain server-owned.

### Should — high-value follow-up contracts

These may follow the base migration in small server-and-template changes. Do not fake them in
the production UI while missing.

- Evidence drawer fragments for a source excerpt, a calculation's inputs and an assumption's
  justification. Each retains a full-page `href` and uses the one shell drawer.
- Holding detail URL and preview fragment with transaction count, transaction rows, pooled
  cost explanation and grade lineage.
- A transaction-history surface with date in the URL where filtering creates a returnable
  view.
- Deterministic report-to-prior-report deltas: rating, valuation and a concise, sourced change
  summary. If the summary is model-authored, store it as a judged statement; do not generate
  it on page load.
- Skill usage history: which completed runs used a version and a link to their outcomes.
- Knowledge action counts for stale companies and closed catalyst windows, with direct links.
- Additional server-rendered drawer previews where they reduce back-and-forth without hiding
  the canonical URL.

### Later — reserve space, do not simulate

- Time-weighted and money-weighted portfolio return, with deposits and withdrawals treated as
  flows rather than gains.
- Exposure by holding, sector, currency and listing country, plus top-five concentration.
  Unknown sectors are named as unknown, never folded into `Other`.
- Holding price-history visualisation if the underlying historical data contract is approved.
- Rich knowledge-graph pan, zoom, drag or live filtering. This is a named JavaScript island;
  server-computed node positions remain authoritative and the script owns only viewport
  transforms.
- Counts and attention providers for the seven planned tools when those tools acquire real
  domain tables.

For later fields, use honest absence. Do not ship sample charts, zero totals, disabled controls
or synthetic deltas that could be mistaken for records.

## 10. Phased file/change sequence

Keep every phase independently reviewable and releasable. Preserve unrelated work in a dirty
tree. Suggested commits are deliberately narrow.

### Phase 0 — inventory and fixtures

1. Run the existing test suite and capture baseline screenshots for representative light/dark
   states.
2. Inventory templates, raw ramp classes, inline component patterns, IDs used by OOB swaps and
   scripts that query CSS selectors.
3. Add or update fixtures for every state required in section 8, especially database down,
   first run, stale approval, budget refusal, failed run, unverified claim and incomplete
   portfolio.
4. Record existing routes and their navigation/reachable-only classification.

**Exit:** existing behaviour is held by tests before visual movement begins.

### Phase 1 — presentation vocabulary and view models

1. Add central state/step/gate mappings in the web presentation layer.
2. Add `RenderedFigure`, `CostContext`, evidence-lineage and page-context shapes, using the
   repository's established Python model style.
3. Adapt handlers one family at a time; keep old templates rendering until their phase.
4. Add completeness tests: every enum/step/gate has a human label and valid tone.

**Exit:** no redesigned template needs to interpret raw domain data.

### Phase 2 — assets and tokens

1. Vendor the three font families and record hashes.
2. Implement revised light/dark/system variables, focus tokens, control boundary and scales in
   `src/aer/web/styles/app.css`.
3. Preserve temporary aliases for clean existing templates.
4. Compile and commit the stylesheet; add contrast assertions against computed colours.

**Exit:** the visual system can be consumed without changing page structure.

### Phase 3 — shared macros

1. Build status, notice/refusal, page header, figure, provenance, confirmation, evidence spine,
   form, table and disclosure macros.
2. Document each macro's allowed states and no-script behaviour beside the macro or in tests.
3. Add component-render tests in both schemes, including focus, disabled, error, loading and
   empty states.

**Exit:** new page templates require no raw colour ramp or repeated form/table class blocks.

### Phase 4 — shell and navigation

1. Refactor `base.html` and `_shell/` around the single navigation DOM.
2. Add visible location context, persistent wide rail, narrow native disclosure and the
   restrained disclaimer placement.
3. Preserve the one drawer, one badge request and server-stamped preferences.
4. Test with Redis down, database down, badge timeout, JS disabled and both explicit themes.

**Exit:** any top-level destination is one action away at wide width; shell remains complete
when optional services fail.

### Phase 5 — Overview and requests

1. Make `Your attention` the returning-page lead and build a distinct first-run state.
2. Add age/cost/ceiling context and preserve bounded severity groups.
3. Separate working tools from planned tools while preserving all nine registry entries and
   database-free launcher rendering.
4. Reshape the request form into the four-decision core plus native optional refinement.
5. Promote point-in-time and cost ceiling; add cost guidance, list dates/spend and unified
   page-based destructive confirmation.

**Exit:** a new operator can commission with four clear decisions; a returning operator can
triage without scrolling.

### Phase 6 — console and gates

1. Introduce human step names, liveness lead, cost against ceiling and evidence counts.
2. Render the honest gate journey, with conditional gates visibly conditional.
3. Move all seven gates onto the shared frame without flattening their specific evidence.
4. Add sticky wide-screen decision placement with post-evidence DOM order.
5. Rebuild the review gate around its linked attention summary and evidence sections.
6. Preserve console event-stream and no-script refresh tests before changing styling.

**Exit:** liveness is answerable in two seconds; any gate is recognisable; approval remains
hash-bound and non-optimistic.

### Phase 7 — evidence and reports

1. Lead every evidence page with its verdict and render the evidence spine.
2. Promote the exact excerpt; demote, but retain, full hashes.
3. Render the sensitivity grid deterministically with table equivalence.
4. Give replay a typed overall result and exception list.
5. Put conclusions on report-history rows and make the report the lead reading surface.
6. Express approval/hash as a proof statement; keep export explicit and secondary.

**Exit:** the two-click proof path is visually continuous and every report is findable by
conclusion.

### Phase 8 — Portfolio, Skills and Knowledge

1. Recompose Portfolio around as-of context, completeness, book grade, holdings and the
   kind-specific transaction entry path.
2. Keep all four totals coupled and exact to the penny.
3. Promote skill containment, composed policy and dry run while preserving byte-for-byte
   source round-tripping.
4. Lead Knowledge with actions, link graph nodes and implement honest empty/sparse states.
5. Add only the approved `Should` data-contract work that has real server backing.

**Exit:** each side-tool has a clear decision or reading hierarchy without new client state.

### Phase 9 — removal and hardening

1. Remove legacy raw ramp classes and temporary aliases.
2. Recompile and commit the final stylesheet.
3. Run the complete automated, no-script, keyboard, zoom, responsive, theme and manual passes.
4. Compare representative screenshots with the prototype for hierarchy and character, not
   pixel identity.
5. Update route and component documentation to match the shipped system.

**Exit:** all checks in section 11 pass and no legacy visual dialect remains in the research
templates.

## 11. Testing and acceptance checklist

### Structural and server truth

- [ ] Every page enters through `render()` and receives shell, disclaimer, theme, guidance and
  CSRF context.
- [ ] `StrictUndefined` renders every fixture without missing fields.
- [ ] Every route is in navigation or the reachable-only registry.
- [ ] Exactly one primary navigation and one badge target per registered badge exist per page.
- [ ] Exactly one shell drawer exists per page.
- [ ] No template formats money, percentages, dates or scalar values.
- [ ] No production JavaScript uses arithmetic or `Intl`/locale formatting for domain figures.
- [ ] No approval, status, total or count is stored only in browser memory.
- [ ] GET controls encode returnable state in their URL; state-changing actions are POSTs.
- [ ] Successful POSTs redirect; refresh never resubmits a decision.
- [ ] A stale gate hash is refused and the current payload is shown for review.
- [ ] A decided gate renders no decision form.
- [ ] Every provenance object has a working `href`.
- [ ] Provenance, confirmation and portfolio grade remain independent.
- [ ] No runtime asset loads from a third-party origin.

### htmx and scripting-off

- [ ] Disable scripting and complete every create/edit/decision/remove/portfolio form.
- [ ] Disable scripting and traverse every drawer trigger as a full-page link.
- [ ] Disable scripting on a running console and verify the meta refresh remains inside
  `<noscript>` and the page stays usable.
- [ ] With scripting enabled, 422 validation swaps only the live region's contents and is
  announced.
- [ ] Badge failure leaves navigation complete and does not show a misleading zero.
- [ ] Filters are absent without scripting; the complete table remains.
- [ ] Drawer focus is trapped, Escape closes it and focus returns to the exact trigger.
- [ ] A busy gate button says only that the request is being recorded, never that it succeeded.

### Accessibility

- [ ] Run axe-core on every page family and material state in light and dark themes.
- [ ] Verify small text at 4.5:1, large text at 3:1, and control boundaries/meaningful graphics
  at 3:1 using computed colours.
- [ ] Verify the designed focus indicator against the component and its adjacent surface in
  both schemes.
- [ ] Keyboard through the shell, drawer, every gate, long tables and every form in DOM order.
- [ ] Gate decision controls follow evidence in focus order even when visually sticky.
- [ ] Reflow at 320 CSS pixels and 200% zoom with no horizontal page scroll.
- [ ] Wide tables scroll only inside labelled bounded regions.
- [ ] Pointer targets are at least 24 by 24 CSS pixels, including row actions and disclosure
  summaries.
- [ ] One non-empty `h1` exists per page and heading levels do not skip.
- [ ] Every input has a label; every field error is associated with its field and the summary
  links to it.
- [ ] Every icon-only control has an accessible name; no information is hover-only.
- [ ] Status and data sign remain understandable in monochrome.
- [ ] Reduced motion removes pulses/transitions without removing `Running` or elapsed text.

### Responsive and visual behaviour

- [ ] Wide screens show one persistent navigation rail and one-action top-level navigation.
- [ ] Narrow screens show the same navigation as a native disclosure.
- [ ] The evidence spine occupies its margin at wide width and becomes a compact in-flow
  sequence below 60rem.
- [ ] Long gates preserve readable line length; the decision panel never covers evidence.
- [ ] The page body never scrolls horizontally.
- [ ] Tables use tabular numerals, right-align figures, provide captions/row headers where
  needed and state unavailable values in the cell rather than leaving blanks.
- [ ] Explicit light defeats a dark system preference and explicit dark defeats a light one.
- [ ] The system theme works with no `data-theme` attribute and has no first-paint flash.
- [ ] Fonts fall back legibly while loading and issuer names retain latin-ext glyph coverage.
- [ ] The interface uses no gradients and spends its visual emphasis on the evidence spine
  and genuine decisions.

### Page-specific truth

- [ ] Overview's launcher renders with no database and attention providers never fail silently.
- [ ] Overview distinguishes first run from caught up.
- [ ] Request rejection returns every entered value and handles both validation rounds.
- [ ] All declared run steps render; none is invented by the event stream.
- [ ] Budget refusal distinguishes run and monthly ceilings.
- [ ] The gate sequence never presents conditional gates as guaranteed.
- [ ] Peer/theme rationales remain full length; refusals remain adjacent and outside the hash.
- [ ] Review challenges read as adversarial value, not system faults.
- [ ] Exact source excerpts remain verbatim and appear with verifier verdicts.
- [ ] Valuation reads the stored ledger and does not recompute on page load.
- [ ] Replay remains a POST and findings retain their distinct types.
- [ ] Draft reports appear only in the operator's report list, not established history.
- [ ] Skill import shows a diff and rechecks the base hash; export round-trips source bytes.
- [ ] Knowledge coordinates and valuation-chart bytes are deterministic.
- [ ] Portfolio's four headline values are all present or all withheld.
- [ ] Portfolio cash remains a position; unpriceable rows show a reason; exact penny rendering
  is preserved; a typed transaction remains `Typed`.

### Migration cleanliness

- [ ] No `slate`, `sky`, `amber`, `red`, `emerald` or `rose` Tailwind ramp utility remains in
  production templates except an explicitly documented non-UI artefact.
- [ ] No runtime-composed Tailwind class is introduced.
- [ ] Compiled CSS is regenerated and committed.
- [ ] Vendored asset hashes are recorded and verified.
- [ ] Deleted macros/classes/selectors have no remaining template, Python or JavaScript users.

## 12. Explicit do-not list

Do not:

- turn the application into a SPA or introduce a client-side view layer;
- use prototype fixture-switching JavaScript as production state management;
- calculate a total, ratio, percentage, currency conversion or display abbreviation in the
  browser;
- format a server number with `Intl.NumberFormat`, `toLocaleString`, string concatenation or a
  second money helper in Jinja;
- paint approval, rejection, success, spend or progress optimistically;
- duplicate the navigation for desktop and mobile;
- duplicate a badge target id or add a second drawer/focus trap;
- build navigation with bespoke markup per destination;
- replace a gate URL with a modal or multi-step browser-memory wizard;
- place a submit-looking button outside a real form unless its `form` attribute names that
  form and tests prove it submits;
- nest the assumptions gate's row forms inside the gate-decision form;
- use a destructive link or `confirm()` dialogue;
- hide provenance to reduce density, or render a provenance label without a link;
- merge provenance, confirmation and grade into one badge;
- call a refusal an error, or use a failure style for the red team doing its job;
- show a blank or zero where the platform refused or failed to produce a figure;
- show a portfolio subtotal when any position is unresolved;
- omit cash from portfolio weight calculations or visually imply deposits are performance;
- truncate peer/theme rationales or summarise stored source excerpts;
- recompute valuation, charts or report figures during a GET;
- expose a full hash as primary reading content or hide it completely;
- use raw enum names, raw step keys or workflow versions as primary interface labels;
- create a search/filter control that is visible but dead without scripting;
- replace native disclosure, date, datalist, link or form behaviour with avoidable script;
- load fonts, scripts, styles, icons or telemetry from a CDN;
- use colour, motion, hover or an icon as the only carrier of meaning;
- use a decorative line token as a form-control boundary;
- compose Tailwind classes dynamically where its scanner cannot see them;
- add placeholder analytics that look like real records; or
- treat a pixel mismatch with the prototype as more important than domain truth, access or
  progressive enhancement.

## 13. Final definition of done

The redesign is complete only when all in-scope routes speak one visual language, all material
states have been rendered and tested, and the production implementation retains the platform's
central guarantee: the browser may help the operator move around the record, but it never
authors the record.

The result should feel like an analyst's working paper rather than a generic dashboard: quiet
paper and ledger structure, disciplined typography, decision amber used only where a decision
has consequence, and an evidence spine that makes proof available without making every page
look like debugging output.
