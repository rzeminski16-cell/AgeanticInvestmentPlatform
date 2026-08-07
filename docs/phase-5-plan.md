# Phase 5 — task sequence (tasks 44–52)

Continues from `docs/phase-4-plan.md`. The phase specification — objective, deliverables,
acceptance criteria — is `docs/PLAN.md` → Stage 3 → Phase 5, and it remains the authority.
This file is the dependency-ordered breakdown of it.

**Objective, restated.** The deliverable people actually see. Four phases built a platform
in which every fact traces to a hashed artefact, every number to a recorded calculation and
every agent to a registered contract — and what a finished run produces today is a Markdown
file. Phase 5 turns the same content model into the surfaces that were the point all along:
an institutional-quality PDF frozen at approval, a chart pack whose every figure carries
provenance, a report and company history that shows how a view changed over time, an
Obsidian export that turns approved runs into a research journal, and a review experience
where any number can be walked back to the bytes it came from.

**The rule this phase is mostly about.** What is approved is what exists. The preview shown
at Gate 2, the HTML stored at approval, the PDF derived from it and the Markdown beside it
are one assembly serialised, never four renderings that could drift; the Obsidian vault is a
one-directional projection of *approved* data and nothing else. Nothing in this phase may
weaken the licence discipline either: EODHD-derived figures stay off every exportable
surface (ADR 0030 route 2, ADR 0034), and a chart is a figure.

---

## What Phases 1–4 already banked

| Deliverable | Where it landed |
|---|---|
| Data-driven section registry; a third section is an INSERT, not code | Task 10, `aer/sections/registry.py` |
| Generic contract-driven section renderer with citation-by-key | Task 10, `aer/sections/render.py` |
| Markdown report: global footnote numbering, source appendix, disclaimers | Task 10, `aer/render/markdown.py` |
| `reports` table: content JSONB, `content_hash`, `immutable`, artefact FKs for Markdown and PDF | Task 10, migration 0006 |
| Content-addressed artefact store, retention classes, erasure as appended events | Tasks 6 and 29, ADR 0031 |
| Approval flow: report frozen `immutable` only behind a recorded approval | Task 10 → 41, `vertical_slice_v1._render` |
| `WithheldComps` — the type with no field for licensed figures | Task 30, ADR 0034 |
| Sector enforcement as a type-level block, `SectorNote` disclosure | Task 28, ADR 0029 |
| Prices, corporate actions, adjusted-close series (licensed, internal-only) | Task 29, ADRs 0030/0032 |
| Statements, ratios, WACC, DCF, scenarios, sensitivity grids, valuation view | Tasks 22–31 |
| Disagreements and the resolution ladder; evaluations written per run | Tasks 19, 21, 32, 39 |
| Escalation triggers sealed into the Gate 2 payload; the review dashboard | Task 41 |
| Custom sections inside the user-skill boundary; authoring, dry run, containment corpus | Tasks 38, 42, 43 |
| Obsidian vault/personal-root containment validation in `Settings` | Task 2, `aer/config.py` |
| Claims, citations, deterministic excerpt verification, provenance read-side | Tasks 12, 15, 20, `aer/services/provenance.py` |

Phase 0's risk check also holds in this environment: WeasyPrint's native dependencies
(pango, cairo, gdk-pixbuf, harfbuzz) and the DejaVu font family are present.

---

## What must be decided before the tasks that need it

**1. The eighteen-section spine.** `docs/PLAN.md` commits to eighteen built-in sections
("the 18-section institutional spine", acceptance criterion 4) but never enumerates them;
naming them is this phase's first act and is recorded here so every later task builds
against the same list. Two are already seeded (task 10); sixteen arrive in task 44. Two of
the eighteen are **deterministic** — filled by code from database state, spending no
tokens — because their content is a record of what the platform did, not a judgement:

| Position | Key | Title | Filled by |
|---|---|---|---|
| 100 | `executive_summary` | Executive summary | model *(seeded, task 10)* |
| 110 | `investment_thesis` | Investment thesis | model |
| 120 | `business_overview` | Business overview | model |
| 130 | `segment_analysis` | Segment analysis | model |
| 140 | `industry_landscape` | Industry & competitive positioning | model |
| 150 | `management_governance` | Management & governance | model |
| 200 | `historical_financial_analysis` | Historical financial analysis | model *(seeded, task 10)* |
| 210 | `earnings_quality` | Earnings quality | model |
| 220 | `balance_sheet_liquidity` | Balance sheet & liquidity | model |
| 230 | `cash_flow_analysis` | Cash flow analysis | model |
| 240 | `capital_allocation` | Capital allocation | model |
| 250 | `growth_outlook` | Growth outlook | model |
| 300 | `valuation_dcf` | Valuation — discounted cash flow | model |
| 310 | `scenarios_sensitivities` | Scenarios & sensitivities | model |
| 400 | `key_risks` | Key risks | model |
| 410 | `catalysts` | Catalysts | model |
| 900 | `prior_research_comparison` | Prior research comparison | code (task 49) |
| 910 | `validation_disagreements` | Validation & disagreements | code (task 44) |

Deliberately absent: a comparables section — the comps disclosure is already the renderer's
`WithheldComps` block and a section would either duplicate it or leak what ADR 0034
withholds; a macro section — macro context feeds the industry and growth sections rather
than standing alone; and a moat section — competitive positioning covers the built-in need,
and moat durability is the canonical *custom-section* example precisely because it is a
personal methodology. Custom sections slot between built-ins by their declared position,
exactly as today. The seeded `token_budget > 0` check constraint relaxes to `>= 0` so a
deterministic section can honestly declare that it spends nothing.

**2. One assembly, three serialisations — and the preview *is* the PDF's HTML.** The plan's
"single Jinja template set → HTML preview + PDF + Markdown" is realised as: one assembler
(extracted from today's `aer/render/markdown.py`) produces a `ReportDocument` — header
metadata, sector block, sections in position order with globally numbered citations, comps
disclosure, footnotes, source appendix — and two serialisers render it: the existing
Markdown one, and a new Jinja HTML one. The PDF is WeasyPrint over the **exact HTML the
preview showed**, not a third rendering. The Markdown serialiser is not rewritten through
Jinja: it is heavily tested, and what the approve-what-you-see property actually requires
is that preview and PDF are the same bytes-in, which the shared-HTML route guarantees
directly. Footnote-numbering identity between the Markdown and HTML serialisations is held
by test instead.

**3. A chart is a figure (ADR 0043, task 47).** The withheld-figures rule applies to
pixels: a price line is a price series and a comps band is a set of multiples, whether they
arrive as digits or as geometry. So the chart pack splits into an **exportable set**
(revenue & margin history, segment mix, scenario bridge, sensitivity heatmap, and the
football field showing only bands derived from our own calculations) and an
**internal-only set** (price/relative performance; the football field variant with the
comps band), which render solely on the internal valuation surface that already shows
licensed figures. Exportable surfaces carry the existing licence note where a band is
withheld.

**4. The section writer is Phase 5 work, and it is a new agent role.** The draft step
still fills built-in sections with the Phase 1 placeholder (`_content_for`), whose own
docstring promises its replacement by a section-writer agent. No phase plan ever claimed
that replacement, but §1.8 commits to the `report_writer` role ("18 sections from
structured facts"), its model route has been configured since task 2, and a report phase
that shipped sixteen new sections of placeholder prose would be sixteen sections nobody
could read. So the spine seed (task 44) is immediately followed by the section writer
(task 45), registered under ADR 0042 as ADR 0035 requires. It gets **no tools**: evidence
arrives as a structured pack assembled by code from what the run already recorded, which
is both the §1.8 design ("from structured facts") and the reason its containment story is
short.

**5. The Obsidian exporter is deterministic, and no `obsidian_linker` agent ships in
Phase 5.** Every §2.8 link is derivable from the database — industry from the confirmed
sector classification, competitors from the approved peer set, sources from the run's
citations, catalysts from the catalysts section's structured output — so link-building is
a query, not a judgement. The `obsidian_linker` model route stays configured but unused;
adding the role later requires an ADR (ADR 0035) and nothing in this phase justifies one.

**6. Reproducible bytes.** The plan requires a byte-reproducible PDF (§1 tooling table).
Three sources of nondeterminism are pinned: Matplotlib SVG ids (`svg.hashsalt` set from the
report id, date metadata stripped), WeasyPrint's PDF creation metadata (overwritten in the
pikepdf pass from `approved_at`, never the clock), and pikepdf's document id (deterministic
save). The test is blunt: render twice, hash twice, equal.

**7. Migration numbering.** The plan file's `0007_reports_obsidian` label is long
superseded by the real sequence. Phase 5 lands three migrations, each in the task that
needs it: `0023` (section spine seed + token-budget constraint, task 44), `0024` (report
HTML artefact column, task 48), `0025` (Obsidian export records, task 50).

---

## Why this order

Content before rendering: the spine (44) comes first because everything else renders,
writes or exports it, and the section writer (45) follows immediately so the spine holds
real analysis before any surface shows it. The HTML snapshot test is only worth writing
against full-width content, so the template set (46) follows; charts (47) precede the PDF
(48) because the PDF embeds them, and the HTML set precedes it because WeasyPrint consumes
exactly that HTML. History (49) precedes Obsidian (50–51) because the run note's "Prior
research comparison" and the company page consume the same deterministic builder, and
building it twice would guarantee drift. The exporter splits into the vault writer with
its anti-contamination guards (50) — the part where a bug touches the user's personal
notes — and the link graph and journal features (51), so the safety-critical part is
tested alone before anything interesting is layered on it. Provenance drill-down (52)
comes last because it reads every surface the phase built.

---

## Task 44 — The eighteen-section spine and the deterministic appendix

**Objective.** The registry-driven content model at full width: eighteen built-in
sections seeded, two of them filled by code, every one of them planned, budgeted and
rendered with no code change beyond the seed.

**Build.** Migration `0023`: sixteen new `section_definitions` rows per the table above —
each with an output contract (JSON Schema with declared field order), evidence policy,
token budget, allowed tools and applicability — and the token-budget check relaxed to
`>= 0` for the two deterministic sections. `validation_disagreements` is filled by a small
deterministic service from the run's evaluation rows and disagreement records, written at
the end of the validate step — after the metrics are measured, before the red team — so
the preview the operator approves already contains it. Escalation triggers stay out of the
section deliberately: they already ride inside the sealed payload (task 41), and embedding
them in content the trigger engine's scenes are computed over would be a circular flow for
no new information. Escalated disagreements are described as escalated for human decision
at approval — a statement about what the run did, which stays true after the human
decides;
`prior_research_comparison` seeds now but renders its honest empty state ("first run — no
prior research to compare") until task 49 supplies the builder. The Gate 1 plan payload
gains a per-section listing (key, title, origin, position, token budget); per-section
*cost* estimates join it in task 45, because until the writer exists the built-in draft
spends nothing and an estimate the gate showed for spending that cannot occur would be a
number nobody could reconcile.

**Tests.** Registry resolves exactly eighteen built-in keys in position order; a
FakeProvider full run generates every model section and both deterministic sections; the
validation section's content matches the run's evaluation and disagreement rows; the
deterministic sections record zero token cost; the existing third-section-is-data proof
still passes untouched.

**Acceptance.** `SELECT count(*) FROM section_definitions WHERE origin = 'builtin'`
(latest versions) is 18; a run's Markdown report shows all eighteen in spine order with
custom sections interleaved by position.

**Delivered (2026-08-06).** Migration 0023 (sixteen seed rows, the token-budget check
relaxed to non-negative, and `executive_summary` **v2** — its own carrier test caught that
the 0006 contract had no citation-carrying field, so a summary was structurally unable to
cite a headline figure; a new version row, never an edit). One trap re-found and dodged:
the seed's table construct initially bound `output_contract` as JSONB, which normalised the
authors' declared field order before the `json` column ever saw it — the exact bug
migration 0007 fixed, reintroduced at seed time and caught by the declared-order tests.

`aer/sections/deterministic.py`: the builders registry, routed to **by budget, not by
key** — the draft step asks "is this section deterministic?" (a column) and the registry
binds keys to code, which is why the no-key-in-code scan gained exactly one scoped
exception (the registry itself, held to the rule for the sixteen model keys).
`prior_research_comparison` fills at the DRAFT stage with the honest first-run sentence;
`validation_disagreements` fills at the **end of validate** — after the metric rows are
written, before the red team seals the hash — mirroring the evaluation rows verdict for
verdict and describing escalated disagreements in words that stay true after the human
decides. A zero-budget row with no registered builder fails loudly at draft. Gate 1's
payload gained `section_listing`, written by code from the resolved definitions and stored
on the plan row so a definition published after planning cannot change what the gate
hashes (held by test).

One §2.4 correction the spine forced: `SectionScene` now carries `requires_primary` from
the section's own evidence policy, and the low-source-coverage trigger only names a
required section that *owes* a primary source — without that, the deterministic pair
raised the banner on every clean run. The escalation fixtures that previously relied on
executive summary v1 citing nothing now seed a starved probe (a required, prose-only
section) so both engineered triggers genuinely hold; the sealed payload pins its evidence
to exactly the probe. Sabotage: thirteen mutations, thirteen caught. Suite: 3,451 unit
tests green; browser suite green.

---

## Task 45 — The section writer: structured facts in, contract-shaped analysis out

**Objective.** The §1.8 `report_writer` role, replacing the Phase 1 placeholder: every
built-in model section written from the run's recorded evidence, under the same claim,
citation and failure rules a custom section already lives by.

**Build.** ADR 0042 registers the role (ADR 0035 makes that a precondition, not
paperwork): typed input carrying the section's contract, its evidence policy and a
structured evidence pack — facts, calculations, research-worker findings, all assembled
by deterministic code from what the run stored; **no tools**, because a writer that could
fetch would be a researcher with a second identity. One structured-output call per
section, validated against the definition's `output_contract`; a schema violation is
retried once, then the section fails with its reason recorded — never silently dropped,
never fabricated to fill space. Evidence policy unmet renders the insufficiency banner
with findings marked low-confidence. Figures name `calculation_id` /
`source_document_id`, exactly what the renderer and citation resolver already expect —
which is why this lands as a change to *how* content is produced, not to what it is.
Token budgets come from the definition rows; cost is metered per section, per-section
cost estimates join the Gate 1 section listing, and the plan's estimated total now covers
the spine. `_content_for` and its helpers are deleted.

**Tests.** Against the FakeProvider: every built-in model section generated against its
real contract; a schema-violating response is retried once then failed with the reason
recorded; figures cite calculations that exist on the run; an evidence-policy failure
produces the banner rather than invented content; the deterministic sections are never
sent to the writer; a cost row exists per written section; the registry refuses the role
any tool.

**Acceptance.** No placeholder sentence survives in `vertical_slice_v1`; a full
FakeProvider run yields eighteen sections whose content derives from that run's recorded
evidence, every figure resolving to a stored calculation or fact.

**Delivered (2026-08-06).** ADR 0042 and the `report_writer` registry row (no tools, the
one-assertion test). The drafting discipline moved to `aer/sections/evidence.py` —
`SectionPolicy`, gathering, budget truncation, validation, claim recording, shortfalls —
and `aer/skills/execution.py` became a caller of it deriving policy from the pin, while
`aer/sections/writing.py` derives it from the definition row: one implementation, two
boundaries, held together by construction. `aer/agents/section_writer.py` composes with
the planner's approved *focus* line and no `<user_skill>` block (both held by test). The
placeholder `_content_for` is deleted; its contract-walking survives only in the test
fake (`ScriptedSectionBrain`), which answers each section from the prompt it was just
sent — parsing the real contract and the real evidence ids back off the provider's own
call log. Gate 1's section listing gained per-section cost estimates at the writer's
routed model, zero for the deterministic pair, with the spine's total folded into the
plan estimate the operator approves against.

Two rules reached their final shape under the writer (recorded in ADR 0042). **A figure
row is lineage**: a numeral inside an object naming its `calculation_id` or
`financial_fact_id` carries provenance without a claim — and, tightening as it opened,
content ids came under the closed world for the first time (a fabricated row id now
fails validation; previously only claim ids were checked). The citation keys also became
numeral-exempt: a UUID with an all-digit group was tripping the rule — a latent flake for
custom sections. **Structured API responses became citable**: a `json` extractor joined
the sandbox roster (the decoded source verbatim, the same injection scan), and the
extract step now records one extraction per persisted fact, located in the archived
bytes, so a claim naming a fact can carry a citation the verifier re-reads — held
end-to-end by the writer suite, where a scripted draft's citation is confirmed by the
real verifier against real artefact bytes.

The slice's exact spend roll is pinned: planner, five workers, sixteen writer calls, one
date-adjudication assist (the undated aggregate now has readable text, so the assist
genuinely fires), and no red team — the slice's only source is a quarantined undated
aggregate, so its sections cite through figure rows rather than claims, and an adversary
with no claims honestly skips. Sabotage: fourteen mutations, fourteen caught. Suite:
3,464 unit tests green; browser suite green.

---

## Task 46 — One assembly, three serialisations: the HTML report and institutional CSS

**Objective.** The single template set the plan promises: a `ReportDocument` assembler,
the Jinja HTML serialisation, and print-grade CSS — with the custom-section default
template polished enough that a user-authored section looks native.

**Build.** Extract the assembly walk from `aer/render/markdown.py` into
`aer/render/document.py` (sections in position order, global footnote numbering, footnote
and appendix resolution, sector/comps blocks); the Markdown module becomes a serialiser of
`ReportDocument` with byte-identical output, held by test. New `aer/render/html.py` +
`templates/report/`: cover page, contents page with the **"Custom analysis" grouping**
(body order stays position order; the contents page groups `origin='skill'` sections under
their own heading, attributed to the operator's methodology), running headers, page
numbers, table styles, footnotes, disclosures block and the non-optional disclaimer — all
via CSS paged media so the same stylesheet drives preview and PDF. The contract-driven
default template renders custom-section output natively in HTML (same walk as
`sections/render.py`, emitting markup rather than Markdown). Jinja autoescape everywhere:
model output is data, never markup. The report web page serves this HTML as the preview.

**Tests.** Snapshot test on the rendered HTML of a fixed document; footnote numbering
identical between Markdown and HTML serialisations of the same document; a report with two
custom sections renders them at the correct positions with the default template and
correct footnote numbering across the built-in/custom boundary; a section whose content
contains `<script>` renders escaped; the contents page groups custom sections and the body
does not.

**Acceptance.** The Gate 2 preview and the report page show the HTML document; a custom
section is visually indistinguishable in quality from a built-in one without a line of
user-authored HTML.

**Delivered (2026-08-06).** The assembly walk moved out of `aer/render/markdown.py` into
`aer/render/document.py` (`assemble_document` → `ReportDocument`: header view, sector
note, section views with fragments and origin, global footnote numbering, resolved
footnote and appendix rows), and `sections/render.py` now produces format-neutral
fragments (`Heading`/`Banner`/`StatusLine`/`Paragraph`/`Bullet`/`Table`, markers as
pre-assigned global integers) that each notation only transcribes. The Markdown module
became `serialise_markdown(document)`, held **byte-identical to the pre-refactor
renderer** by `tests/fixtures/fx_report/golden.md` — recorded before the split, so the
identity is against the old code's actual output, not against itself. The HTML notation
is `aer/render/html.py` plus `templates/report.html`: one self-contained page (cover with
masthead and disclaimer, contents page grouping `origin='skill'` sections under "Custom
analysis" with the attribution note while the body keeps position order, sector aside,
footnotes with back-references, sources appendix with digest prefixes) under one
stylesheet written for both screen and CSS paged media (`@page` running headers, page
counters, `string-set` company name), so the browser preview and task 48's WeasyPrint
input are the same bytes. Every interpolation passes through `markupsafe.escape` (S704
scoped ignore recorded in `pyproject.toml`, held by test rather than lint). Preview
routes `/runs/{job_id}/preview` and `/reports/{report_id}/preview` serve the document
itself — assembled with the workflow's own `sector_note_for`/`comps_note_for`, made
public for exactly that parity — linked from the Gate 2 review page and the report page;
the report preview stamps the row's `rating`, `confidence` and `created_at`. Tests: both
goldens (`UPDATE_GOLDEN=1` re-records then fails, so an update can't pass silently),
marker-sequence identity between the notations, escaping planted in every tag path,
two skill-origin sections between built-ins (body order, contents grouping, numbering
unbroken across the boundary), notation edge cases (dangling `**`, heading cap), and the
preview pages' content, no-sections refusal and ownership. A 27-mutation sabotage pass
over the walk, assembler, both serialisers, template and routes found three escapes —
the marker-carrying table cell escapes on a separate branch nobody had planted markup in,
the ownership 404 was indistinguishable from the no-sections 404, and a report viewed
moments after creation cannot tell "stamped with created_at" from "stamped with now" —
each fixed by strengthening the test it exposed, after which all 27 were caught. The e2e
sweep then exposed a real console bug: the no-JavaScript meta refresh was emitted bare
and deleted by `console.js`, but a declarative refresh is scheduled at parse time and
removal does not cancel it (verified empirically against the bundled Chromium), so every
scripted console view reloaded underneath its own event stream every five seconds — now
emitted inside `noscript`, with the containment pinned by test. No new
migration and no ADR: no schema change, no new agent role, and the one-assembly rule is
the plan's own (storage of the HTML artefact is task 48's migration 0024, as planned).

---

## Task 47 — The chart pack: deterministic Matplotlib, and a chart is a figure

**Objective.** Six charts, every one rendered by deterministic code from recorded rows,
byte-stable, provenance-marked, and licence-clean.

**Build.** New `aer/charts/`: pinned style sheet and DejaVu fonts, fixed DPI,
`svg.hashsalt` from the report id, date metadata stripped. Exportable set: revenue &
margin history (statements/ratio calculations), segment mix (rendered only when structured
segment facts exist; an honest placeholder otherwise), scenario bridge (scenarios),
sensitivity heatmap (the ADR 0028 grid), valuation football field from own-calculation
bands only. Internal-only set: price/relative performance and the football-field variant
with the comps band, rendered solely on the valuation surface. Every chart carries a
caption with footnote markers citing the calculation ids it was drawn from, so a chart
figure resolves exactly as a text figure does. Charts embed into the HTML as data-URI SVG,
keeping the stored HTML artefact self-contained. ADR 0043 records the pixels-are-figures
rule. `matplotlib` joins runtime dependencies.

**Tests.** Rendering the same chart twice yields identical bytes; the exportable football
field contains no comps-derived geometry and carries the licence note; the internal
variant is reachable only from the valuation page; empty inputs produce the placeholder,
never an invented axis; caption citations resolve to real calculations.

**Acceptance.** The HTML report shows the exportable charts inline with resolvable
provenance; nothing price- or comps-derived appears in any exportable artefact.

**Delivered (2026-08-06).** ADR 0043 (a chart is a figure) and `aer/charts/`: frozen
`Decimal` input types whose every point carries a `CitationRef` (`model.py`), the pinned
style and the one route from figure to bytes (`style.py` — bundled DejaVu, fixed DPI,
`svg.fonttype: none`, no date metadata, `svg.hashsalt` from the job id, one typed wrapper
around `rc_context`), and seven pure builders (`builders.py`): the five exportable
exhibits, plus the internal-only price chart and comps-band football field, both born
`exportable=False` by their builders. Empty input renders the honest bordered placeholder,
never an axis. `aer/services/exhibits.py` is the only ledger-to-geometry bridge:
`exportable_charts_for` reads FY revenue facts (restatement-deduped, quarterly facts
excluded at the chart's grain), margin calculations with their periods recovered from
their recorded fact inputs (an ambiguous period stays off the chart), case-tagged
per-scenario valuations, the first stored sensitivity grid, and value-per-share bands for
the football field, whose caption carries the comps licence note; the all-placeholder run
gets no exhibits block at all, which keeps chart-less reports byte-stable.
`internal_charts_for` serves the valuation page alone. Scenario attribution needed one
recorded parameter that did not exist: the four DCF outcome calculations now carry
`case` alongside `method` (same precedent, one level up), threaded from
`run_scenarios(case=scenario.key)` with a blank-case refusal, and the exhibits service
reads it back rather than guessing positionally — untagged pre-task-47 rows honestly
cannot reach the bridge. The assembler takes `charts=`, refuses `exportable=False`
outright, and numbers exhibit citations straight on from the sections; the Markdown
notation carries captions and markers with the geometry deferred to the HTML and PDF
editions, and the HTML embeds each chart as a base64 SVG data URI in an `img` (self-
contained, and an `img` cannot script). Both preview routes and the workflow's render
step pass the pack; the valuation page renders the internal set under an explicit
licensed-data note. `matplotlib` joined runtime dependencies. Tests: builder purity
(byte-identical re-render, salt genuinely applied, no `dc:date`, placeholders on every
empty input, the licence split including the input type having no comps field),
exhibits-service read-back against a seeded ledger, document integration (markers
continue the global sequence, both notations agree, assembler refusal), the end-to-end
run whose frozen report and preview carry the exhibits, and the recorded `case` asserted
in the valuation service's own scenario test. A 17-mutation sabotage pass — the salt, the
date metadata, every placeholder branch, both born-internal flags, the dedupe, the
licence caption, the assembler's refusal and numbering, both notations' exhibit blocks,
the skip-all rule, the case filter, the period-ambiguity guard, the FY grain, the
valuation-page placeholder filter and the workflow's chart wiring — was caught in full on
the first run, three of those catches resting on tests strengthened pre-emptively while
planning the pass (the ambiguity guard, the FY grain and the workflow wiring had no test
before it). No migration: `case` is a parameter, not a column.

---

## Task 48 — The PDF: WeasyPrint, bookmarks, and the pikepdf immutability pass

**Objective.** The immutable PDF the sequence diagram promises: rendered from the approved
HTML at approval time, frozen as a content-addressed artefact, tamper-evident.

**Build.** `aer/render/pdf.py`: WeasyPrint over the stored preview HTML, one bookmark per
section from the heading structure. The pikepdf pass sets owner-password permissions (no
modification, no form filling), strips forms, embeds XMP metadata carrying the report id
and `content_hash`, overwrites creation metadata from `approved_at`, and saves with a
deterministic id — honest in the ADR-standing note that this is tamper-*evident*, not
tamper-*proof*. Migration `0024` adds `reports.html_artefact_id`; the workflow's render
step stores HTML, PDF and Markdown artefacts and links all three. Download grows to
`GET /api/reports/{id}/download/{fmt}` for `md | html | pdf`, always serving archived
bytes, never a re-render; the report page gains the download buttons. `weasyprint` and
`pikepdf` join runtime dependencies.

**Tests.** PDF renders in under 30 s and contains a bookmark for every row in
`report_sections`; pikepdf asserts the permission set; XMP carries the id and hash;
rendering twice produces identical bytes; each download format serves bytes whose SHA-256
equals the artefact record; an unapproved report has no PDF to download and says so.

**Acceptance.** Approving a run yields a PDF containing all eighteen built-in sections
plus every enabled custom section, at institutional visual quality, provably identical to
what was previewed.

**Delivered (2026-08-06).** `aer/render/pdf.py`: `render_pdf` (WeasyPrint over the stored
preview HTML) and `finish_pdf` (the pikepdf pass — forms stripped, owner-password
permission bits refusing modification/assembly/form-filling with reading unrestricted,
XMP carrying `dc:identifier` and the content hash with the non-advice disclaimer, every
date the approval's, docinfo synced from the XMP as the single source). Byte-stability
was won empirically, and three measured findings shaped the module, each documented in
its docstring: qpdf refuses its content-derived deterministic `/ID` for any encrypted
output; AES can never be byte-stable (random stream IVs), so the pass uses **RC4 R4 with
unencrypted metadata and a static `/ID`** — the cipher is not confidentiality (the user
password is empty by design), only the carrier for permission bits any tool could strip,
and the document's real identity is the XMP hash plus the artefact digest; and
WeasyPrint's font *subsetter* emits differently ordered tables across renders in one
process (measured five renders, five byte streams), so the PDF embeds **full fonts** —
about two megabytes for reproducibility. Migration 0024 adds `reports.html_artefact_id`;
the render step now assembles once and freezes three notations — Markdown, HTML, and,
strictly behind an approval, the PDF rendered from the *stored* HTML bytes and stamped
with `approved_at` — linking all three artefacts and reporting all three digests in its
step output. `GET /api/reports/{id}/download/{fmt}` serves `md | html | pdf` from the
content-addressed store with the digest in `X-Artefact-SHA256` (the bare `/download`
stays as Markdown); the PDF's absence message names the actual rule — no approval, no
PDF. The report page gains the HTML and PDF download links and an honest no-PDF note.
Tests (`tests/test_report_pdf.py`, on the golden HTML fixture — the real template, not a
toy page): double render byte-identical inside the 30-second budget, permission bits,
form stripping via an injected AcroForm, XMP id/hash/date, a bookmark for every golden
section plus Notes and Sources; integration: an approved run freezes all three notations
with body-hash = header = artefact row for each format and a bookmark for every one of
the run's 18+ section rows, and an unapproved report's PDF download and page both say
why there is none. An 11-mutation sabotage pass — the full-fonts pin, the static id,
form stripping, the permission bits, both XMP fields, the two artefact links, the format
map, the absence message and the page's PDF conditional — was caught in full on the
first run. `weasyprint` and `pikepdf` joined runtime dependencies.

---

## Task 49 — Report history, company history and the prior-run comparison

**Objective.** The platform starts remembering: what was concluded about a company before,
and what changed.

**Build.** `aer/services/history.py`: a deterministic builder that, given a company and a
new run, assembles prior approved reports' rating, confidence and valuation range against
the new ones, prior catalysts with their dates marked passed or pending, and prior key
risks — every row carrying the prior `report_id`. It fills `prior_research_comparison` on
any run of a company with prior approved reports (seeded honest-empty in task 44). New
surfaces: `/reports` (filterable history, per-company grouping), `/companies/{id}`
(timeline of approved reports, valuation-range history as a server-rendered exportable
chart), and `GET /api/companies/{id}/history`. All server-rendered, no JavaScript
required, consistent with every existing page.

**Tests.** A second run of the same company generates the comparison section from the
database with the prior report id on every row; a first run renders the honest empty
state; drafts and rejected runs never appear in history; the history API refuses another
user's company; the valuation-history chart is byte-stable.

**Acceptance.** The §2.7 company page exists: timeline, valuation history, prior
catalysts and what happened; the comparison section appears in the report itself.

**Delivered (2026-08-07).** `aer/services/history.py`: approved reports only — the
`immutable` flag, which only an approval sets, is the whole filter, so a draft or a
rejected run can never quietly become "what we used to think"; `company_for_user` makes a
company visible only to an account that researched it; `approved_reports_for` takes a
`before` bound so a comparison can never read the future. `prior_comparison_content`
fills the position-900 section from rows alone: a first run keeps the one-sentence honest
state, and a later run gets the most recent prior's view, confidence and valuation range
(the current side read from this run's own base-case `value_per_share` rows — the same
read the football field makes — or an honest "not computed"), then every prior report's
catalysts and key risks, each row carrying its `prior_report_id`. Catalyst timings are
the prior analyst's free text and become dates only where an unambiguous shape matches
(ISO, year, quarter, half); "passed" is a statement about the calendar, never about
whether the event occurred. The deterministic builder in `sections/deterministic.py` now
delegates to the service, with the delegation itself held by test. **The
no-hardcoded-section-key scan caught this task's first design** — the reader named
`catalysts` and `key_risks`, making two rows a code dependency — so prior items are now
collected by the *fields they carry* (`CATALYST_FIELDS`, `RISK_FIELDS`), the same
convention as the renderer's citation keys: a custom section whose items carry those
fields joins history exactly as it gains citations, with no code change. New surfaces:
`/reports` (grouped by company, drafts badged, filterable), `/companies/{id}` (the §2.7
page: approved-report timeline, prior catalysts dated against today, and the
`valuation_history` chart — a new exportable builder salted with the company id, byte-
stable by the same pinning as the rest of the pack), and `GET
/api/companies/{id}/history` (approved reports oldest-first; another user's company
answers 404 exactly as a missing one). Numeric display trims the storage scale — a
`Numeric(18,4)` round-trip is not a precision claim. Tests: only-approved history, the
before bound, company invisibility, catalyst dating including the parser's refusals, the
comparison rows' prior-report ids, scenario rows staying out of the base-case range,
builder delegation, first-run honesty, chart byte-stability and placeholder, and the
three surfaces (grouping and filtering, timeline-without-drafts with the chart, API
mine-versus-theirs). A 12-mutation sabotage pass — the immutable filter, the before
bound, both ownership checks, catalyst dating and the quarter parser, the row ids, the
base-case filter, the builder delegation, the chart placeholder, the draft badge and the
page filter — was caught in full on the first run; two of those catches rest on tests
strengthened pre-emptively while planning the pass (a scenario row that must not widen
the base-case range, and the builder delegation itself). No migration and no ADR: rows
already existed, and no invariant moved.

---

## Task 50 — The Obsidian exporter: vault writer and anti-contamination

**Objective.** The vault as a derived, one-directional projection of approved data — with
the §2.8 anti-contamination rules enforced in code before a single interesting feature is
layered on.

**Build.** New `aer/obsidian/`: vault layout (§2.8 tree), frontmatter schemas as Pydantic
models serialised through `python-frontmatter`, and writers for the run note, company
note, source notes, `MOC-Companies` and `README-generated`. Guards, each enforced
structurally: only `immutable` reports export; every write path is resolved and refused
unless inside the configured vault root (the personal root is not merely avoided — it is
unreachable); evergreen notes regenerate only above `<!-- AER:END-GENERATED -->` and
user content below survives byte-for-byte; every exported numeric claim carries a
`^claim-<id>` block reference and a source link. Rule 4 lands in the verifier: `Provider`
gains `INTERNAL_PRIOR_RUN` and the citation verifier hard-rejects any claim whose only
support is a prior-run artefact. Custom sections export tagged `aer/custom-section` with
skill key and version in frontmatter. Migration `0025`: `obsidian_exports` records
(report, timestamp, files written, generator version), shown on the report page. Export
is triggered from the report page and an `aer export-obsidian` CLI command; nothing
exports automatically.

**Tests.** Frontmatter validates against the §2.8 schema; a draft report refuses to
export; on a temp vault the personal directory is never written (asserted on the
filesystem); regenerating a company note preserves user content below the sentinel;
the verifier rejects an `internal_prior_run`-only citation as a hard failure; a second
export is idempotent.

**Acceptance.** An approved run exports; the vault opens cleanly in Obsidian; nothing
below any sentinel is ever touched; no draft data exists anywhere in the vault.

**Delivered (2026-08-07).** `aer/obsidian/`: `notes.py` (the §2.8 frontmatter as Pydantic
models — run, company and source notes — serialised through `python-frontmatter` with
sorted keys and `None` dropped; fields the platform cannot source are absent, never
invented), `vault.py` (`VaultWriter`, the one door: every path resolves and is refused
outside the vault root, inside the personal root — checked even when that root sits
inside the vault, so the guard does not lean on `Settings` — or under the reserved
`99-Personal/` subtree; `regenerate` rewrites only above the sentinel and carries the
personal half byte for byte), and `export.py` (`export_report`: rule 1 first — only
`immutable` reports with an `approved_at`; run note with every verified numeric/factual
claim carrying `^claim-<id>` and a source wiki-link, the stored prior-research-comparison
transcribed, custom sections pinned as `key@version` with the `aer/custom-section` tag;
company note and MOC evergreen behind the sentinel; one source note per admissible source
— quarantined sources leave no trace; README stating the vault's rules). **A second
export is the first export**: every byte derives from database state and the approval's
own timestamp, so idempotence is a property, not a hope, and each act is recorded in
`obsidian_exports` (migration 0025, which also adds the `internal_prior_run` provider
value). Rule 4 landed in the verifier: `Provider.INTERNAL_PRIOR_RUN` is deliberately
uncitable — no tier mapping (the exhaustiveness test carries the documented exemption)
and a hard rejection in the citation verifier's refusal ladder, isolated from
admissibility so the test proves the provider rule itself. Nothing exports automatically:
the report page's form (with export history and honest disabled states) and
`aer export-obsidian <report-id>` are the only doors. Tests: containment including the
inside-the-vault personal case, the draft refusal, the untouched personal directory
asserted on the filesystem, sentinel preservation byte for byte, schema-validated
frontmatter with the approval's date, block references with the unverified claim staying
home, quarantined sources absent, idempotence by file digest with both acts on record,
and the verifier's hard rejection. An 11-mutation sabotage pass — all three containment
checks, the sentinel carry-over, the approval gate, the approval-time stamp, the block
reference, the quarantine filter, the verified-citation filter, the provider rule and
the page's vault conditional — was caught in full on the first run; three of those
catches rest on tests strengthened pre-emptively while planning the pass (the
personal-root check isolated from the outside-vault check, the quarantined source, and
the unverified claim staying home). No ADR: every rule here is §2.8's own, no invariant
moved, and the deterministic exporter needs no agent role (the `obsidian_linker` route
stays unused by design, as the phase plan decided).

---

## Task 51 — The link graph and the research journal

**Objective.** The §2.8 features that make the vault a journal rather than a folder of
exports: links, industries, catalysts with outcomes, and the historical comparison in the
run note.

**Build.** Industry notes derived from the confirmed sector classification with symmetric
company back-links; competitor links from the approved peer set, maintained symmetrically;
catalyst notes from the catalysts section's structured output, each carrying
`thesis_refs`; on a later export for the same company, catalyst notes whose date has
passed get their `resolution` field pointed at the newer run. The run note gains the
`## Prior research comparison` section from the task 49 builder, so the journal and the
report tell one story. The company note records the valuation-range history.

**Tests.** Competitor links are symmetric after exporting two peers; every `[[link]]`
written resolves to a file in the vault; a catalyst whose date passed before the second
export carries its resolution; the run note's comparison matches the report section's
rows; frontmatter for every note kind validates.

**Acceptance.** The §2.8 link conventions hold in a real vault across two runs of the
same company; methodology drift is visible — a run note pins the skill versions its
custom sections used.

**Delivered (2026-08-07).** `aer/obsidian/graph.py`: the link graph as a deterministic
read of approved runs and their *confirmed* gates — an unconfirmed peer set or sector
classification contributes no edge, logged, because a link in a journal is still a use of
unapproved state. The competitor relation is symmetric by construction (an approved run
of A naming B links both directions, whether or not B ever named A back), and the export
covers the whole connected component of it: run and source notes for every approved run
of every company in the component — each dated by its own report's approval — a company
note per company (an honestly-labelled stub where a company was named as a comparable but
never researched), catalyst notes, industry notes. That closure is what makes **every
`[[link]]` written resolve to a file**, which a vault-wide sweep test asserts. Catalysts
aggregate by (company, label) across runs — every proposer in `thesis_refs`, the latest
proposer's timing — and resolve by calendar and rows alone: the earliest approved run
whose as-of date lies *strictly* past the deadline parsed by the (now public)
`history.timing_deadline`, its run note becoming the `resolution` link; a deadline on the
as-of date itself stays `pending`, and an unparseable timing stays `undated` forever.
Industry notes (evergreen, `30-Industries/`) list companies by their *latest* confirmed
classification — each run note still links its own run's sector, which is how
classification drift stays visible — and membership unions companies from prior exports
so one component's export cannot silently drop another's back-link; the MOC unions
previously exported companies the same way. The company note gains `industry_note`,
symmetric `competitors` and a `## Valuation history` section (one line per approved run,
range and run link); the run note gains `industry_note`, `competitors` and
`catalyst_notes` frontmatter — the §2.8 illustrative key for that field collides with a
seeded section key, and the no-hardcoded-section-key scan holds the exporter to the same
rule it taught task 49, so the field is named for what it holds instead;
`IndustryNoteMeta` and `CatalystNoteMeta` join the validated schemas. Tests
(12 new, in `tests/test_obsidian_links.py`): symmetry after exporting two peers including
the reverse-only direction and the stub, unconfirmed proposals leaving no trace, the
vault-wide link sweep, catalyst resolution arriving with the newer run (with the strict
boundary and the undated case), cross-run catalyst aggregation, latest-classification
membership with the empty prior-sector note, disjoint-export membership healing, the
run-note comparison matching the stored section rows, valuation history, frontmatter
validation across all five note kinds, whole-graph idempotence by byte, and the
company-less degenerate path. A 24-mutation sabotage pass (reverse edge, strict
resolution boundary, freshest-timing and aggregation identity, component filtering,
membership and MOC unions, evergreen-vs-whole writes, the rule-1 conjunction, both new
frontmatter defaults, the year-deadline parse) finished with one escape on the first
run — weakening rule 1's `or` to `and` survived because the draft test set both halves
false together — fixed by strengthening the guard test with the reachable half-state (a
draft carrying a stray `approved_at`; the inverse half-state turned out to be
unrepresentable, owned by the `ck_reports_immutable_reports_were_approved` check
constraint), after which the honest full re-run caught all 24. Several other catches rest
on scene state seeded pre-emptively while planning the pass: the unresolvable peer
identifiers (a ticker and a foreign UUID inside a confirmed set), the catalyst restated
across both runs with a fresher timing, and the sentinel assertions on regenerated
industry and MOC notes.

---

## Task 52 — Provenance drill-down and the phase close-out

**Objective.** Any figure on any surface walks back to bytes: the hover/drill-down UI,
and the phase's acceptance sweep.

**Build.** In the HTML preview and report page, every footnote marker links to a claim
drill-down (the existing provenance read-side): the excerpt, its verification state and
ratio, the artefact hash, tier and licence note. Calculation footnotes link to a
calculation walk rendering the DAG to its leaves — every input with its unit and source,
the formula, the code version. Chart captions link the same way. Hover previews are
CSS-only (`title` attributes and a no-JS-friendly footnote return link); the drill-down
pages are server-rendered. Close-out: the Phase 5 acceptance criteria in `docs/PLAN.md`
are re-run end-to-end against a FakeProvider run and recorded in this file's delivered
notes; README and `docs/` updated for the new surfaces and dependencies.

**Tests.** Every marker in a rendered report resolves to a drill-down page that answers
with the claim's evidence; a calculation walk reaches only leaves that are facts or
assumptions; an unresolvable citation renders its honest unresolved state in the
drill-down exactly as in the document; e2e — a browser walks from a figure in the preview
to the artefact hash that supports it.

**Acceptance.** Phase 5's acceptance line holds: all sections in the PDF, every figure
resolvable regardless of section origin, custom sections at institutional quality with no
user HTML, a vault that opens cleanly with working links, user content under sentinels
preserved.
