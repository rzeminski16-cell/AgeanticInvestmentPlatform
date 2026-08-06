# Phase 5 — task sequence (tasks 44–51)

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
| 900 | `prior_research_comparison` | Prior research comparison | code (task 48) |
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

**3. A chart is a figure (ADR 0042, task 46).** The withheld-figures rule applies to
pixels: a price line is a price series and a comps band is a set of multiples, whether they
arrive as digits or as geometry. So the chart pack splits into an **exportable set**
(revenue & margin history, segment mix, scenario bridge, sensitivity heatmap, and the
football field showing only bands derived from our own calculations) and an
**internal-only set** (price/relative performance; the football field variant with the
comps band), which render solely on the internal valuation surface that already shows
licensed figures. Exportable surfaces carry the existing licence note where a band is
withheld.

**4. The Obsidian exporter is deterministic, and no `obsidian_linker` agent ships in
Phase 5.** Every §2.8 link is derivable from the database — industry from the confirmed
sector classification, competitors from the approved peer set, sources from the run's
citations, catalysts from the catalysts section's structured output — so link-building is
a query, not a judgement. The `obsidian_linker` model route stays configured but unused;
adding the role later requires an ADR (ADR 0035) and nothing in this phase justifies one.

**5. Reproducible bytes.** The plan requires a byte-reproducible PDF (§1 tooling table).
Three sources of nondeterminism are pinned: Matplotlib SVG ids (`svg.hashsalt` set from the
report id, date metadata stripped), WeasyPrint's PDF creation metadata (overwritten in the
pikepdf pass from `approved_at`, never the clock), and pikepdf's document id (deterministic
save). The test is blunt: render twice, hash twice, equal.

**6. Migration numbering.** The plan file's `0007_reports_obsidian` label is long
superseded by the real sequence. Phase 5 lands three migrations, each in the task that
needs it: `0023` (section spine seed + token-budget constraint, task 44), `0024` (report
HTML artefact column, task 47), `0025` (Obsidian export records, task 49).

---

## Why this order

Content before rendering: the HTML snapshot test is only worth writing once the section
spine is complete, so the spine (44) precedes the template set (45). Charts (46) precede
the PDF (47) because the PDF embeds them; the HTML set (45) also precedes the PDF because
WeasyPrint consumes exactly that HTML. History (48) precedes Obsidian (49–50) because the
run note's "Prior research comparison" and the company page consume the same deterministic
builder, and building it twice would guarantee drift. The exporter splits into the vault
writer with its anti-contamination guards (49) — the part where a bug touches the user's
personal notes — and the link graph and journal features (50), so the safety-critical part
is tested alone before anything interesting is layered on it. Provenance drill-down (51)
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
deterministic service from the run's evaluations, disagreements and fired escalation
triggers, written before Gate 2 so the preview the operator approves already contains it;
`prior_research_comparison` seeds now but renders its honest empty state ("first run — no
prior research to compare") until task 48 supplies the builder. Per-section cost estimates
join the Gate 1 plan view, which already displays them for custom sections.

**Tests.** Registry resolves exactly eighteen built-in keys in position order; a
FakeProvider full run generates every model section and both deterministic sections; the
validation section's content matches the run's evaluation and disagreement rows; the
deterministic sections record zero token cost; the existing third-section-is-data proof
still passes untouched.

**Acceptance.** `SELECT count(*) FROM section_definitions WHERE origin = 'builtin'`
(latest versions) is 18; a run's Markdown report shows all eighteen in spine order with
custom sections interleaved by position.

---

## Task 45 — One assembly, three serialisations: the HTML report and institutional CSS

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

---

## Task 46 — The chart pack: deterministic Matplotlib, and a chart is a figure

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
keeping the stored HTML artefact self-contained. ADR 0042 records the pixels-are-figures
rule. `matplotlib` joins runtime dependencies.

**Tests.** Rendering the same chart twice yields identical bytes; the exportable football
field contains no comps-derived geometry and carries the licence note; the internal
variant is reachable only from the valuation page; empty inputs produce the placeholder,
never an invented axis; caption citations resolve to real calculations.

**Acceptance.** The HTML report shows the exportable charts inline with resolvable
provenance; nothing price- or comps-derived appears in any exportable artefact.

---

## Task 47 — The PDF: WeasyPrint, bookmarks, and the pikepdf immutability pass

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

---

## Task 48 — Report history, company history and the prior-run comparison

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

---

## Task 49 — The Obsidian exporter: vault writer and anti-contamination

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

---

## Task 50 — The link graph and the research journal

**Objective.** The §2.8 features that make the vault a journal rather than a folder of
exports: links, industries, catalysts with outcomes, and the historical comparison in the
run note.

**Build.** Industry notes derived from the confirmed sector classification with symmetric
company back-links; competitor links from the approved peer set, maintained symmetrically;
catalyst notes from the catalysts section's structured output, each carrying
`thesis_refs`; on a later export for the same company, catalyst notes whose date has
passed get their `resolution` field pointed at the newer run. The run note gains the
`## Prior research comparison` section from the task 48 builder, so the journal and the
report tell one story. The company note records the valuation-range history.

**Tests.** Competitor links are symmetric after exporting two peers; every `[[link]]`
written resolves to a file in the vault; a catalyst whose date passed before the second
export carries its resolution; the run note's comparison matches the report section's
rows; frontmatter for every note kind validates.

**Acceptance.** The §2.8 link conventions hold in a real vault across two runs of the
same company; methodology drift is visible — a run note pins the skill versions its
custom sections used.

---

## Task 51 — Provenance drill-down and the phase close-out

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
