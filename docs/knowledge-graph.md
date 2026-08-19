# The knowledge graph: what exists, what does not, and how to finish it

This is the design and build plan for the platform's knowledge layer — the thing whose
purpose is not to produce one report but to make the *next* report better: market
awareness that accumulates, connections a single run cannot see, and a record of what
past research claimed measured against what actually happened.

`docs/PLAN.md` §2.8 specifies the vault. This document is wider: it covers the graph the
vault projects, the ontology underneath it, how information enters and leaves, how the
thing is monitored, and the six tasks that would finish it. Where the two disagree,
`docs/PLAN.md` is the authority on the vault's file layout and `docs/adr/` on any
decision that changes an invariant.

---

## 1. Where it stands

| Capability | State | Where |
|---|---|---|
| Vault projection (notes, links, frontmatter) | **Built** | `aer/obsidian/export.py` |
| Link graph over confirmed state | **Built** | `aer/obsidian/graph.py` |
| Symmetric competitor edges across the component | **Built** | `graph.py:peer_edges`, `reachable_from` |
| Company ↔ industry membership, both directions | **Built** | `IndustryNoteMeta.companies` |
| Catalyst nodes aggregated across runs | **Built** | `graph.py:CatalystView` |
| Source nodes with provenance | **Built** | `export.py:_write_source_notes` |
| Look-back: prior vs current rating, confidence, valuation | **Built** | `services/history.py` |
| Look-back rendered into every report | **Built** | `prior_research_comparison` section |
| Methodology-drift attribution (skill version pins) | **Built** | `RunNoteMeta.custom_sections` |
| Anti-contamination (prior runs are never evidence) | **Built** | `verify/citations.py:237` |
| Idempotent regeneration, personal half preserved | **Built** | `vault.py`, `SENTINEL` |
| **Themes — cross-company connective tissue** | **Not built** | — |
| Feed-forward: prior research informing a new run | **Built** (K2) | `history.prior_digest_for`, ADR 0064 |
| **Assumption outcomes measured across runs** | **Not built** | — |
| **Catalyst resolution beyond the calendar** | **Partial** | `CatalystView.resolved_by` |
| Statistics and monitoring of the graph | **Built** (K5) | `services/knowledge.py` |
| **In-app graph view** | **Not built** | Obsidian's own view only |
| `obsidian_linker` model route | **Configured, unbuilt** | `config.py:179` — no agent role |

The honest summary: **the projection, the look-back, the measurement and the feed-forward
work; the connective and evaluative halves do not.** The map records what you have
researched, lets you compare a company against its own past, reports its own size and
decay, and puts prior conclusions in front of a new run's planner as questions to ask. It
does not yet connect companies through anything other than the peer sets of individual
runs, and it does not evaluate whether what you believed turned out to be true.

---

## 2. The ontology

### 2.1 Node kinds

Six exist; a seventh is proposed in §6.1.

| Node | Identity | Source of truth | Grows when |
|---|---|---|---|
| **Company** | `companies.id`, titled `TICKER — Name` | `companies` row | A run resolves it, or a confirmed peer set names it |
| **Run** | `report_id`, titled `<as-of> <TICKER> <mode>` | `reports` row, immutable only | A report is approved and exported |
| **Industry** | `sector_key` | Confirmed classification gate | A run's sector gate is approved |
| **Catalyst** | `(company, label)` | `catalysts` in an approved run's section content | Any approved run proposes one |
| **Source** | `src-<digest12> — <title>` | `source_documents` row | A run cites it in an approved report |
| **MOC / README** | fixed | Derived index | Every export |
| *(proposed)* **Theme** | `theme.key` | Confirmed theme gate | §6.1 |

Two identity rules matter more than the rest. **A catalyst is `(company, label)`, not
`(run, label)`** — two runs naming the same expected event are refining one expectation,
not creating two, so the catalyst node accumulates `thesis_refs` across runs. And **a
company node may exist with no research of its own**: a peer named by a confirmed set
becomes a stub node, which is what lets the graph show you the shape of a neighbourhood
you have only partly explored.

### 2.2 Edge kinds

| Edge | Direction | Confirmed by | Stored as |
|---|---|---|---|
| Company ↔ Company (competitor) | Symmetric | PEER_SET gate | `competitors` on both notes |
| Company → Industry | With back-link | SECTOR_SPECIALIST gate | `industry_note` / `companies` |
| Company → Run | One-to-many | Report approval | `run_notes` / `company_note` |
| Run → Catalyst | Many-to-many | Report approval | `catalyst_notes` / `thesis_refs` |
| Run → Source | Many-to-many | Citation in an approved report | `source_notes` |
| *(proposed)* Company ↔ Theme | Many-to-many | THEME_SET gate | §6.1 |

**Only confirmed state produces an edge.** A proposed-but-unapproved peer set or
classification contributes nothing — not even a line in a journal. This is the same
refusal the comps and sector services make, applied to links, and it is why the graph
cannot quietly fill with the model's suggestions.

**The competitor relation is symmetric by construction.** An approved run of A naming B
creates the edge in both notes whether or not B has ever named A back, and the export
covers the whole connected component of that relation — which is precisely what makes
every `[[link]]` resolve to a file that also gets written.

### 2.3 What a node carries

Every note has `aer_id`, `aer_kind`, `aer_schema`, `generated_at`, `generator`, `tags`.
Beyond that, a run note carries the report and job ids, the workflow version, company
identity, as-of date, base currency, the point-in-time flag, rating, confidence,
valuation range, horizon, the four link arrays, a `content_hash`, the pinned custom
sections, and an `evidence_policy` string stating that claims require re-sourcing before
reuse. Fields the platform cannot source — ISIN, analysis mode — are **absent rather than
invented**; frontmatter that guesses is frontmatter nobody can trust.

---

## 3. How the map grows

**The unit of growth is one approved report.** `export_report(session, settings=…,
report_id=…)` refuses anything that is not immutable, builds the link graph around it,
and writes the closed set of notes that graph implies.

**Growth is currently manual.** The only triggers are a button on the report page
(`POST /reports/{id}/export-obsidian`) and the CLI. Nothing exports on approval. That is
a defensible default — the vault is the operator's, and writing to somebody's notes
without being asked is rude — but it means *the map only grows when you push it*, and a
forgotten export is invisible. §6.5 proposes making the omission visible rather than
changing the default.

**One export writes more than one node.** Exporting a run of A writes A's company note,
the run note, A's industry, A's catalysts, A's cited sources — and then every company in
the connected component of the competitor relation, so that the links resolve. Exporting
your fifth company can therefore rewrite the first four's notes, which is correct: their
`competitors` arrays may now include the new company.

**Re-export is idempotent.** A second export of the same report produces byte-identical
files: frontmatter keys are sorted, `None` fields dropped, and the serialisation is a
function of the values alone. A test holds this.

---

## 4. How it is structured and maintained on disk

```
<VAULT_ROOT>/
├── 00-Meta/          MOC-Companies.md, README-generated.md
├── 10-Companies/     TICKER — Name.md          (evergreen, regenerated)
├── 20-Runs/          <as-of> <TICKER> <mode>.md (immutable once written)
├── 30-Industries/    <label>.md                 (evergreen, regenerated)
├── 40-Themes/        — NOT IMPLEMENTED (§6.1)
├── 50-Catalysts/     <TICKER> <label>.md        (evergreen, regenerated)
├── 90-Sources/       src-<digest12> — <title>.md
└── 99-Personal/      NEVER written by the application
```

Three maintenance rules are enforced in code and tested:

1. **Containment.** `VaultWriter` resolves every relative path against the vault root and
   refuses anything that escapes it; `99-Personal/` is refused explicitly as defence in
   depth; and configuration rejects a vault root that nests with the personal root in
   *either* direction, at startup.
2. **The sentinel.** Evergreen notes regenerate only above `<!-- AER:END-GENERATED -->`.
   Everything below it is yours and is preserved byte for byte — the test asserts exactly
   that.
3. **Closure.** The exporter writes the connected component, so a link never points at a
   file that does not exist.

---

## 5. How information is extracted and utilised

### 5.1 Outward — what the map is built from

Everything in the vault is a deterministic read of committed, approved rows:

```
confirmed peer sets   → competitor edges
confirmed sectors     → industry membership
approved reports      → run notes, ratings, valuations, confidence
section content       → catalysts, risks
cited source docs     → source notes with provenance and licence
skill version pins    → methodology attribution
```

No model call takes part in building the graph. The `obsidian_linker` route sits in the
model routing table with no agent behind it — a slot reserved by `docs/PLAN.md` §1.8 and
never filled. §6.6 decides its fate.

### 5.2 Inward — what the system reads back

**From the vault: nothing, ever.** The projection is one-directional and the application
never reads vault content as evidence.

**From the database's own history: one thing.** `prior_comparison_content` runs at draft
time and produces the `prior_research_comparison` section: prior view and confidence
against current, prior valuation range against this run's, then every prior report's
catalysts dated against this run's as-of, and every prior key risk — each row carrying
the `prior_report_id`.

**And that is the whole of it.** The planner never sees what you concluded last time. The
section writers never see it. The research workers never see it. Knowledge accumulates
and is compared *after* the fact, but never steers the questions a new run asks. That
asymmetry is the single biggest gap in the current design, and it is §6.2.

The containment for closing it already exists: `Provider.INTERNAL_PRIOR_RUN` is defined,
the citation verifier **hard-rejects** any claim whose only support carries that provider,
and `wrap_untrusted` plus `CONTAINMENT_RULE` are the machinery for injecting labelled
material into a prompt. What is missing is the retrieval and the injection, not the guard.

---

## 6. The build plan

Six tasks. K1 and K2 are the substantive ones; K5 is the cheapest and pays for itself
immediately; K6 is a decision rather than a build.

### K1 — Themes: the connective tissue

**Why.** Today the only company-to-company edge is "a run of A named B as a comparable".
That is a real relation but a narrow one: it cannot express *AI capital expenditure*
linking a hyperscaler, a fab, a utility and a REIT, which is the kind of connection that
makes a research library worth more than the sum of its reports.

**Where themes come from is the design question**, and the answer must respect the
platform's division of labour. Three options were considered:

- *Operator-authored.* You write a theme note and tag runs. No model, no gate, but no
  discovery either — the platform notices nothing.
- *Derived.* Cluster on shared concepts or excerpt keywords. Cheap and nearly meaningless:
  there is no signal in the data that distinguishes a theme from a coincidence.
- **Model-proposed, human-confirmed** — the peer pattern (ADR 0059). A `theme_proposal`
  role is handed the subject's identity, classification and its approved sections'
  headline points, and returns a bounded list of `{key, label, rationale}`. A THEME_SET
  gate shows them; only confirmed themes become edges.

**Take the third.** It is the pattern this codebase already uses for exactly this shape of
judgement, it puts a person between the model and the graph, and it fails safe — an
unconfirmed theme contributes nothing, like an unconfirmed peer.

**The change.**
- Migration: `themes` (`key` unique, `label`, `created_at`) and `theme_memberships`
  (`theme_id`, `company_id`, `report_id`, `rationale`, unique on the triple).
- `GateKind.THEME_SET`; a `propose_themes` step after `classify`, conditional and
  cost-estimated like `propose_peers`.
- `aer/services/themes.py`: `propose`, `confirmed_theme_set`, `theme_gate_payload` —
  mirroring `services/comps.py`.
- `graph.py`: theme nodes and `Company ↔ Theme` edges from confirmed memberships only.
- `export.py`: `40-Themes/<label>.md` with a `ThemeNoteMeta` carrying `companies`,
  `runs` and each membership's rationale; the back-link array on company notes.

**Tests.** An unconfirmed theme produces no edge and no note. A theme spanning two
companies appears in both company notes and lists both. Closure holds — the theme note's
links all resolve. Re-export is idempotent.

**ADR.** *Themes are proposed by a model, confirmed by a person, and only then are they
edges* — recording that the third option was chosen over the other two and why.

### K2 — Feed-forward: prior research as labelled hypothesis material

**Why.** The library is currently write-only from the run's point of view. Closing this
is what turns an archive into a memory.

**The change, scoped deliberately narrowly.** Inject prior research into **the planner
only**, not into the section writers. The planner's output goes through gate 1, where a
human reads the plan before a penny is spent on drafting — so the containment is not only
the wrapper and the verifier, it is the gate that already exists. Extending this to the
writers later is a separate decision with a separate risk profile.

- `services/history.py` gains `prior_digest_for(company_id, before=as_of)`: the last N
  approved reports as a compact structure — as-of date, rating, confidence, valuation
  range, key risks, catalysts and their calendar status. Rows only.
- `_plan` passes it to the planner as `prior_research`, rendered through `wrap_untrusted`
  under a `<prior_research trust="NOT_EVIDENCE">` label, with the system prompt stating
  that it may shape which questions the plan asks and may **never** support a claim.
- The plan's gate payload includes a one-line note that prior research informed it, so
  the operator knows what the planner had in front of it.

**Tests.** A first run injects nothing. A second run injects the prior digest, and the
composed prompt carries the wrapper and the label. A claim whose only source is a prior
run still fails the verifier (this already passes; pin it against the new path). The
planner's gate payload changes when a prior report exists — so approving one plan is not
approving a different one.

**ADR.** *Prior research may shape the questions, never the answers.*

**Delivered (2026-08-19).** ADR 0064. `history.prior_digest_for(company_id, before, limit=3)`
renders the last three approved reports before the run's as-of date into strings — view,
confidence, valuation range, named risks, catalysts with their calendar status already
judged — and never an excerpt of evidence, because an excerpt is what a citation quotes.
The planner declares the digests through ``untrusted_sources``, so the base class does the
wrapping and the delimiter neutralisation; the blocks carry ``tier="not_evidence"`` where
a filing would carry ``regulatory`` (the sketched ``<prior_research trust=…>`` label
became the existing wrapper's vocabulary rather than a second wrapper). The system prompt
gains the may-shape-questions-never-support-claims rule only on calls that carry priors —
two variants, hashing to two ``prompts`` rows, each describing the run it served —
and ``prompt_version`` moved to 4. The plan's stored body and ``plan_gate_payload`` carry
the one-line note, inside the hash, shown on the review page: a plan informed by history
and one planned blind are different proposals.

Tested at three depths: the digest against seeded rows (newest first, the as-of bound and
the limit hold, a draft is not history, the dataclass carries nothing citable), the
planner's composition (a first run says nothing about history; a repeat quotes it inside
the wrapper under the rule; a prior containing ``</untrusted_source>`` cannot close its
own quotation), and the workflow end to end — where the gate note and the provider's
*recorded prompt* are asserted to agree, because a note claiming the planner saw history
while the prompt carried none would be the platform lying to its operator. A seven-mutation
sabotage pass (rule dropped, wrapper bypassed, before-bound ignored, limit ignored, note
out of the body, note out of the payload, digests never passed) was caught in full — the
last only after the workflow test learned to read the fake provider's record, which is
what it exists for. The verifier's ``INTERNAL_PRIOR_RUN`` hard rejection stays pinned in
``tests/test_obsidian.py``; the feed-forward tests add that the digest gives a model
nothing that could reach it. No migration: nothing new is stored.

### K3 — Assumption outcomes measured across runs

**Why.** "Evaluate assumptions and see if they held" is the requirement's own wording, and
this is the part that can be done deterministically. A prior run assumed revenue growth of
9% for FY2026; the later run holds the filed FY2026 revenue. The comparison is arithmetic.

**The change.**
- `services/history.py`: `assumption_outcomes_for(prior, as_of)` — for each confirmed
  assumption of the prior run that names a forecast year, find the actual in the later
  run's facts for that fiscal year, and record `(name, assumed, actual, delta, basis)`.
  Only where the concept maps and the period is closed; everything else is "not yet
  observable" and says so.
- The rows join `prior_research_comparison`, so they appear in the report.
- A `driver_accuracy` figure per driver, over all measurable prior runs, on the company
  note above the sentinel.

**Tests.** A driver with a closed period and a filed actual produces a delta. A driver
whose period has not closed produces "not yet observable", never a zero. A prior run whose
concept the map cannot place is skipped with a stated reason rather than silently.

### K4 — Catalyst resolution beyond the calendar

**Why.** `resolved_by` currently means "an approved run's as-of date is past the parsed
deadline" — a statement about the calendar, honestly labelled. Whether the event *happened*
is not knowable from rows.

**The change, kept cheap.** An operator-recorded outcome: on the catalyst's row in the
report or a small surface, record `outcome ∈ {occurred, did not occur, superseded}` with a
free-text reason and the recording user and time. Stored in the database — never edited
into the vault, which is a projection — and projected into `CatalystNoteMeta.resolution`.

**Explicitly not.** A model deciding whether an event occurred. That is a factual claim,
and a factual claim needs a citation; if it ever becomes automatic it goes through the
normal evidence path with a source, not through the graph layer.

### K5 — Statistics and monitoring

**Why.** There is currently *no* way to ask how big the map is, how connected, how stale
or how complete — and a knowledge base you cannot measure is one you cannot tell is
decaying. This is the cheapest task here and the one that makes the others legible.

**The change.** `aer/services/knowledge.py`, computing from the database (never from the
vault):

*Size* — companies, of which researched vs stub; runs; industries; catalysts; sources;
themes.
*Shape* — competitor edges; connected components; largest component; isolated companies;
mean degree.
*Coverage* — proportion of graph companies with research of their own; industries with
only one member; companies with no confirmed classification.
*Freshness* — newest and oldest approved run per company; companies whose newest research
is older than a configured horizon; catalysts **passed but unresolved**, which is the one
list that is genuinely a to-do.
*Accuracy* (once K3) — count of measured assumptions and mean absolute error per driver.
*Vault health* — last export per report; reports approved but never exported; files under
the vault root that no current export would produce, i.e. drift.

Surfaced three ways: a `/knowledge` page, a `aer knowledge stats` CLI command printing the
same numbers, and a JSON endpoint so the figures can be watched over time.

**Tests.** Each statistic against a seeded graph with a known shape. The stub ratio counts
a peer-only company as a stub. Drift detection notices a file the DB would not write.

**Delivered (2026-08-19).** `aer/services/knowledge.py` computes size, shape, coverage,
freshness and vault health from rows alone. Three surfaces read the one service —
`GET /api/knowledge`, the `/knowledge` page and `aer knowledge` — so a figure quoted from
the terminal and one read off the page cannot disagree. The command is `aer knowledge`
rather than the `aer knowledge stats` this section proposed: there is one thing to print,
and a group with a single member is a prompt to guess the subcommand.

`obsidian/graph.py` gained public `peer_edges` and `reachable_from`. The confirmed peer
relation *is* the graph: the vault is one projection of it and this service measures it,
so neither may own the definition privately, and components are found by the same walk the
exporter uses.

Three figures this section asks for are absent because what they measure does not exist
yet, and inventing a zero would be worse than an omission: **themes** (K1), **accuracy**
(K3), and **catalysts passed *but unresolved*** — with no resolution to record until K4,
every passed catalyst is open, so the list is the full backlog and `OpenCatalyst` says so
rather than implying a filter that is not applied. What the calendar knows is all that is
claimed; whether an event happened is not something the platform can know from its rows.

One asymmetry worth recording. `confirmed_classification` raises when a specialist sector
was proposed and nobody confirmed it — right for a caller that would act on it, since that
refusal decides which valuation models may run, and wrong for one counting it. The
measurement catches it and counts the company unclassified, as the graph does with the
same read; otherwise a single unconfirmed gate anywhere in the database would take all
three surfaces down.

Tested against a scene of four companies whose shape is known by construction — two
researched and joined by a confirmed peer set, one stub, one researched company standing
alone — plus the vault half against a temporary directory, where drift notices a file the
record does not account for and never reports the personal directory. An eight-mutation
sabotage pass (edges counted twice, stubs never counted, drift ignoring the record, drift
walking the whole vault, unexported never listed, passed catalysts ignored, the staleness
horizon ignored, the classification guard removed) was caught in full. No migration and no
ADR: every figure is a read of rows that already existed, and no invariant moved.

### K6 — Decide the `obsidian_linker` route

`config.py` routes an `obsidian_linker` role to Haiku 4.5 at low effort, and
`providers/router.py` lists it among the permitted route names — so the configuration
resolves, and a reader of the routing table would reasonably conclude the platform has a
linker. It does not: no agent declares that role, and nothing ever asks the router for it.
Either build it — its plausible job, proposing links, is exactly what K1's
`theme_proposal` does — or **delete the route and the allowlist entry**, so the
configuration stops advertising a capability that does not exist. Recommend deleting it as
part of K1, which supersedes it.

### Sequencing

K5 first: it is small, it has no dependencies, and it makes the effect of everything
after it visible. Then K2, the largest single improvement to research quality per line
changed. Both are delivered. Next K1, the biggest build. K3 after K1 (they touch the same
section). K4 and K6 whenever convenient.

---

## 7. Monitoring and viewing, today

**The measurements.** K5 is built: `/knowledge`, `GET /api/knowledge` and `aer knowledge`
report size, shape, coverage, freshness and vault health from one service. They measure
the graph rather than draw it — for the picture, the vault is still the place.

The instruments that were here before it, and remain the way to see structure:

**In Obsidian.** Open the vault; the native graph view *is* the map. Filter by
`tag:#aer/run`, `tag:#aer/company` or a sector tag to see one layer at a time. Local
graph on a company note shows its neighbourhood. Dataview-style queries over the
frontmatter work because every note carries typed, sorted frontmatter — `aer_kind`,
`as_of_date`, `rating`, `confidence` are all queryable.

**In the database.** The graph is `companies`, confirmed `job_steps` outputs for peer sets
and classifications, `reports`, and `obsidian_exports` — the same rows K5 reads, still
queryable by hand for anything it does not compute.

**From the command line.** `aer export-obsidian <report-id>` writes a report's component
and prints the files it wrote; `aer knowledge` prints the aggregate that list used to have
to stand in for.

**In the application.** The report page shows the export status for that report; the
`/knowledge` page carries the aggregate. Neither draws the graph — an in-app node-and-edge
view remains unbuilt, and Obsidian's own is better than a first attempt at one.

**A caveat worth stating plainly.** With a single approved run the graph is one node and
the comparison section correctly says "first research run". Nothing in this layer is
observable until several runs of several companies have been approved *and exported* —
which is why K5's "approved but never exported" figure was the first thing built.
