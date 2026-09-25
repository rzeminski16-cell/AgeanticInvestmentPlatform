# Feature specifications

*What each feature of V1.0 is, why it exists, how it works, what it touches, and how we will
know it is done. Twenty features. Compiled 14 September 2026 from the decisions taken across
the design conversation, and normative where it and a page specification disagree about
behaviour — the page specification wins on layout, this wins on mechanism.*

**How to read an entry.** *What it is* and *why* are the argument. *How it works* is the
mechanism a developer implements. *Touches* names the real modules. *Needs an ADR* means the
change alters a recorded decision or an invariant and must be argued in `docs/adr/` before the
code lands. *Done when* is testable.

**The single rule every feature obeys.** Deterministic Python owns every number and every fact;
the model owns planning, interpretation, challenge and writing. No feature below moves an
arithmetic operation into a prompt, and several exist specifically to move one back out.

---

# Part A — The research tool

## F1 · Remove point-in-time

**What it is.** The deletion of the as-of-date enforcement, the point-in-time mode, the
`temporal_compliance` and `look_ahead_recall` metrics, the gate that reads them, and every
mention in the documentation except the record of the removal.

**Why.** The as-of date is always *now* for somebody deciding whether to buy today, and the
audit measured the machinery as never having fired: `look_ahead_recall` read *"not exercised"*
on all five runs because nothing published after the as-of date was ever offered to a claim. It
is complexity with a zero in front of it.

**How it works.** Acquisition stops filtering by date. The request form loses the as-of field.
The evaluation suite loses two metrics and the blocking set shrinks by two. The planner, the
section writer and the plan critic lose the clause that describes the rule.

**The one thing that stays.** Every artefact keeps its **retrieval timestamp**. That is
provenance, not point-in-time; it already exists; and F11 cannot work without it, because *"was
this a good decision or a lucky one"* is unanswerable without knowing what was in front of the
operator at the time.

**Touches.** 143 files under `src/` and 137 test modules (counted 16 September 2026; an
earlier draft said 189 without saying how). Principally `aer.workflow.workflows.vertical_slice_v1`,
`aer.services.requests`, `aer.services.evaluations`, `aer.eval.metrics`, `aer.agents.planner`,
`aer.agents.section_writer`, `aer.agents.plan_critic`, and the report renderers' header block.

**Needs an ADR.** Yes — it removes invariant 4 from `CLAUDE.md` and supersedes ADR 0110 and
ADR 0111. The ADR must say what was given up: the ability to reconstruct a research position as
it stood on a past date, which nothing else in the system now provides.

**Done when.** A full run produces a report with no as-of concept anywhere on it; the
structural-absence scan in ADR 0113 — the enforcement's own names, with the surviving
`as_of_date` readers allowlisted by name — returns only the ADR and the records that cite it;
the blocking metric set is eight; every archived run still replays.

**Status, 17 September 2026.** Landed in code, £0. The report carries the run's date and no
mode; the scan (`tests/test_point_in_time_is_gone.py`) returns only the records, over `src/`,
`tests/`, `audit/`, `migrations/` and the behaviour documents; the blocking set is eight
(both temporal metrics were blocking — the earlier "nine" here and in the ADR assumed one
was not) and the run-time set nine; migration 0076 dropped the column. Three pieces the spec did not name
went with it (the validator's date-adjudication assist, a period bound in peer discovery, the
full-text search's split of hits at the date — ADR 0113 records them). *Every archived run
still replays* waits on the corpus, which is not in this container; ADR 0113 says so and
stays Accepted-in-code until the re-seeded runs replay.

**Risk.** This is the largest purely-deletion change in the plan and it touches the drafting
prompts. Do it early, on its own, with the full suite green before anything else lands.

---

## F2 · The adversary argues the opposite case

**What it is.** The red team stops re-checking sources and arithmetic and starts constructing
the strongest case *against the report's conclusion*.

**Why.** Two reasons, and the second is the bigger one. First, it is duplicated work:
citation verification, numerical consistency and figure plausibility are deterministic checks
that code already performs better and for nothing, so paying a frontier model to re-check
arithmetic spends tokens on the one job it is worst at. Second, the audit's judges read eight
published challenges on AstraZeneca that were **arithmetically false** — the adversary had been
handed FY2021 figures with no period labels and accused the draft of contradicting itself on
every headline number. The report shipped the accusations, and all three judges reconstructed
the truth from the footnotes and marked the document down for it.

**How it works.**

1. The adversary's brief changes. It is told explicitly: *the figures are already verified by
   code; auditing arithmetic is not your job*. It is given the draft's **conclusion** and the
   full evidence base, and asked to build the opposing investment case. Long thesis → argue the
   short. Short → argue the long. No thesis → it argues that the evidence does not support one.
2. Its output is a **case with a number attached**, not a list of objections: a claim, the
   evidence for it, and what it would do to the valuation if true.
3. Every challenge is **resolved before publication** — accepted (the draft changes), rejected
   with a recorded reason, or carried as an open question the operator acknowledged. Today every
   challenge prints *"Escalated for human decision"* whatever became of it, because the appendix
   is written at `validate` and never rewritten after a settle.
4. The published section is a **bear case or a bull case**, named as such, in the body.

**Touches.** `aer.services.red_team`, `aer.agents.red_team`, the disagreements renderer, and the
appendix assembly in `aer.render`.

**Needs an ADR.** Yes — amends ADR 0095 (*an escalated challenge is briefed, not decided*),
because a challenge must now be resolved before the document is frozen.

**Depends on.** F13. You cannot attack a thesis that does not exist.

**Done when.** A run's disagreements section contains no unresolved item; a seeded false
challenge (a figure the calculation record refutes) is caught and dropped before drafting; and
three judges reading the output name the bear case rather than naming the appendix as a defect.

---

## F3 · The closing section reads the operator's own book

**What it is.** A final report section that takes the current portfolio and the request's own
context fields — planned weight, horizon, purpose — and states what this position would do to
the book that already exists.

**Why.** It is the one thing a chat cannot do at all: it does not know what you hold. And it is
arithmetic over the operator's own data, so it sits squarely on the code side of the rule.

**How it works.** The request form gains three fields (§6 of the page specifications). At
render, a deterministic composer computes, from the position store and the proposed weight:
weight after, top-five concentration before and after, sector exposure before and after, and the
stated horizon against the model's own payback year. Each is a recorded calculation with its own
footnote. The model writes the prose around figures it did not produce.

**What it must never be.** A recommendation. It computes consequences; it does not issue
instructions. There is no rating, no target price and no expected return — those are four of the
six strings in `RESERVED_OUTPUT_FIELDS` and remain forbidden.

> At your planned 5% weight this takes your top-five concentration from 41% to 44% and your
> energy exposure from 3% to 8%. Your stated horizon is three years; this discounted cash flow's
> payback lands in year six.

**The legal note, recorded here so it is not forgotten.** A recommendation tailored to a
specific person's portfolio, horizon and planned weight is much closer to a personal
recommendation than a research note is, and advising a specific person on a specific investment
is a regulated activity in the United Kingdom. Advising yourself is not. **Before this ships to
any user who is not its author, take advice.** The consequences-not-instructions design is what
keeps it on the right side of that line, and it is not a substitute for the advice.

**Touches.** `aer.services.requests` (three fields), a new composer in `aer.calc`, the section
spine, `aer.render.document`.

**Depends on.** Portfolio becoming a dependency of Research — which reorders the build.

**Done when.** A run commissioned with a planned weight renders the section with every figure
footnoted to a calculation, and a run commissioned without one renders the section absent rather
than empty.

**Corrected 23 September 2026, on building it** (ADR 0129, migration 0084). Five lines above did
not survive contact with the code, and one question the specification cannot answer alone is
answered by an ADR. *The request form gains three fields*: two — the horizon has been on the form
since it was built, as `investment_horizon_months` and its label; the planned weight and the
purpose join it in a *Your context* sheet of their own. *The section renders every figure*: it
does, on the operator's own copy — the report reader and the final-gate preview — and the figures
rest on the operator's book, whose typed entries are attested (ADR 0073), so the stored HTML, the
PDF and the Markdown carry ADR 0073's disclosure in the section's place whenever any figure's
lineage is attested, and the section in full only where every entry is documented. *"The model's
own payback year"*: nothing computed one; the value step now strikes how much of today's
enterprise value the explicit forecast recovers, and the payback year only where the forecast
reaches it — the example's *year six* lies beyond a five-year forecast and is stated as beyond.
*"Four of the six strings in `RESERVED_OUTPUT_FIELDS`"*: the set holds thirteen, since ADR 0104,
and `weight` is one of them; the section's own fields are named so as not to be. *The weight
after*: it is the planned weight itself, which the section states as the operator's input rather
than computing; what it computes are the cash after, the five largest holdings' share after and
the sector's share after, each a fraction of the book, and never a money amount for the position
— ADR 0104's rule that a decision's size is a sentence stands, and the section multiplies the
planned weight by nothing. A run with a planned weight and no book on record renders one honest
sentence and no figure.

---

## F4 · The refresh

**What it is.** Updating a company already researched, without paying for a full run.

**Why.** It is the mechanic that turns a report into a subscription. A full run at £7.19 is a
purchase; a £1–2 refresh on a cadence is a relationship.

**How it works, and the part that is counter-intuitive.**

1. **Acquire only what is new.** New filings since the last run, new price data, new exhibits.
2. **Recompute every calculation.** All of them, not just the affected ones. They are
   deterministic and nearly free, and recomputing the lot is what guarantees the document cannot
   contradict itself — the failure mode the audit found repeatedly.
3. **Re-draft only what moved.** A section whose inputs are unchanged keeps its prose. This is
   where the money is, and where the saving comes from.
4. **Lead with the change summary.** The headline deliverable is not the report; it is *what
   changed* — which figures moved, by how much, which premises are affected, and what the
   valuation did.
5. **Supersede, never overwrite.** The previous report is archived, immutable, and marked
   `superseded_by`. It stays readable for good.

**Touches.** A new orchestration alongside `vertical_slice_v1`; `aer.services.run_replay` for
the recompute; a `superseded_by` column on `reports`; a new change-summary composer; the report
library's grouping.

**Needs an ADR.** Yes — report supersession is a new concept in the data model, and the
"which report is current" question has no answer today.

**Done when.** A refresh of a stored run costs under £2, completes in under ten minutes,
produces a change summary naming every material move, and leaves the prior report readable at
its own address.

**Corrected 23 September 2026, on building it** (ADR 0131, migration 0086). Four lines above
did not survive the code. *Touches* named `aer.services.run_replay` for the recompute and a
`superseded_by` column: the recompute is the slice's own free steps run again under the slice's
keys (`refresh_v1` declares `acquire`, `extract`, `calculate`, `comps`, `value` and the rest
as the same functions), and `superseded_by` had already landed with ADR 0116, so the refresh
supersedes through `reports_service.supersede` with a reason that leads with *Refreshed*.
*Needs an ADR* was answered by ADR 0131, not by a supersession ADR. Two decisions the
specification left open are now fixed: a refresh is a **second job on the same request** —
the record's documents and the confirmed assumptions are the request's, so a new request
could not see them — and **the priced go-ahead is the plan gate**, recorded on the new job
with the carried plan and the estimate hashed, with the classification, peer set and theme
set re-asserted on the new job from the prior run's decisions. The price on the control is
six sections at the draft step's per-section figure plus the validators, rounded up to the
penny (£1.69 on the shipped estimates); the ceiling is `AER_REFRESH_BUDGET_GBP`, £2 by
default, checked before every section the draft would pay for. Step 3's "inputs" are the
figures a section's **recorded claims** name — the evidence pack is never persisted — and a
carried section keeps its prose word for word with only the calculation ids inside its figure
rows re-pointed at the new ledger. When nothing is new the run finishes at £0 with no model
call, no document, and the prior report current: the change summary, filled from its rows, is
the whole result.

---

## F5 · The model workbook

**What it is.** A spreadsheet shipped with every report, carrying the valuation as **live
formulas** rather than values, so changing an input recomputes the model in the sheet.

**Why.** The target user lives in Excel. And it is a quiet distribution channel: an analyst
emails the model to a colleague and the provenance tab travels with it.

**How it works.**

- One sheet per model: the discounted cash flow, the comparables, the scenarios, the sensitivity
  grid.
- **Real formulas.** `=B12*(1+C4)` rather than a pasted number, generated from the calculation
  registry, which already stores formula, inputs, units and sources.
- **The modelling convention**, because the audience knows it: input cells blue, computed cells
  black, no exceptions.
- **A provenance tab** listing every input with its source document and its retrieval date, and
  stamping the run identifier, the code version and the hash of the report it came from.
- Inputs are unlocked; computed cells are locked but not password-protected — the point is to
  mark the boundary, not to prevent anybody crossing it.

**The honest limit, stated on the provenance tab.** The moment a figure is changed in Excel, the
chain is broken and the sheet is the operator's model rather than the platform's record. The tab
says so in one sentence.

**Touches.** A new `aer.export.workbook` module; `openpyxl` as a dependency (nothing in the
project writes spreadsheets today); the report's action bar; the run export.

**Done when.** Opening the workbook, changing the revenue growth rate and seeing the value per
share move to the figure the platform's own sensitivity grid predicts for that input.

*Corrected 25 September 2026, when F5 landed under ADR 0134.*
- **The modules.** They are `aer.render.workbook` (the pure builder) and
  `aer.services.workbook` (what goes in), not `aer.export.workbook`.
- **The formulas.** They are `aer.calc.dcf`'s own, written in the sheet's syntax and held to
  the ledger by a LibreOffice recompute. They are not generated from the calculation registry:
  its formulas are prose for a reader, not expressions a spreadsheet can evaluate.
- **The colours.** The report's own recorded figures sit beside the live ones in grey. They are
  neither inputs nor computed cells, so the convention's two colours do not fit them.
- **The done-when.** No grid varies revenue growth, so no stored record predicts that figure.
  The test holds terminal growth to the report's own grid cell, and revenue growth to the
  calculator's strike (ADR 0133).
- **The scenarios sheet.** It was not built: nothing in the product creates a scenario.
- **The run export.** It carries the render step's record of the workbook: its digest, or the
  reason there is none.

---

## F6 · Ask, in three tiers

**What it is.** A question box over a company's record that resolves each question into one of
three tiers, **states the tier and the price before it runs**, and never guesses.

**Why.** Refusing everything outside the stored record was too narrow — the question an investor
actually asks is answerable, it just costs something. And the third tier does something no chat
can: it leaves the record larger than it found it.

**How it works.**

| Tier | What it does | Cost | Approval |
|---|---|---|---|
| **1 · Recompute** | Re-runs a stored model with a changed input, using the scenario engine and the margin bridge — both of which exist and have no callers today | Free, sub-second | None; it runs |
| **2 · Re-read** | A model pass over everything already fetched and hashed for this company. No fetching | Pennies, metered | None; the cost is shown after |
| **3 · Research** | Fetches new material — filings, investor-relations pages, competitors' filings, the open web — hashes it, cites it, and **adds it to the company's record** | Priced, typically £1–3 | **Always, before it runs** |

**Tier resolution** is a classification step, and it is allowed to be wrong in one direction
only: when unsure, it resolves *upward* and asks. A question mis-resolved down would answer from
stale material and not say so.

**The sentence that makes tier 3 honest**, shown before anything is spent:

> This needs new material. About £1.40, and eleven new documents will be added to Microsoft's
> record. Go ahead?

**Every answer carries the report's evidence drawer.** A tier-1 answer names the inputs it
changed and the model it re-ran; a tier-3 answer lists the documents it added, and those
documents are in the company's record afterwards.

**Touches.** A new `aer.services.ask`; the scenario engine and `aer.calc.bridge` (wiring
existing code); the acquisition path for tier 3; the cost ledger.

**Done when.** A tier-1 question answers in under five seconds for nothing; a tier-3 question
cannot run without an explicit approval carrying a price; and an adversarial corpus of questions
outside the record produces no answer generated from the model's own knowledge.

**Corrected 23 September 2026, on building tiers 1 and 2** (ADR 0130, migration 0085). Three
lines above did not survive the code. *"The scenario engine and the margin bridge — both of
which exist and have no callers today"*: the bridge gained its caller in Phase 6.1
(`services/analysis.py` strikes it per period), and the scenario engine's callers are the value
step's scenarios and grids. What tier 1 actually re-runs is the run's **base case**, struck
again on the question's own ledger with one input changed, through the value step's own input
assembly (`base_case_inputs`) — there is no stored `DcfInputs` to reload, and the scenario table
is a diff over confirmed assumptions rather than a model to re-run. *"A tier-1 answer names the
inputs it changed and the model it re-ran"*: it does, and every figure is a calculation row a
reader walks. *"Tier resolution is a classification step"*: it is a deterministic match over
what the record holds, and spends nothing (ADR 0130 §2; mechanisms §2.2 corrected). The tier-3
acquisition is the one line not yet built: it lands after F4, whose fetch-for-a-company path it
shares, and until then a tier-3 question is resolved, priced and recorded, and the page says
researching it is not yet available here.

---

**Corrected 23 September 2026, on building tier 3** (ADR 0130 §5, amended). The third tier
runs. Its acquisition is rooted on the company's current report's own request — the run's
established hosts, exclusions and held-document checks — with the question's job as the
fetcher of record; the research worker takes the question as its brief within the bounds the
estimate priced; what it fetched is excerpted and the reader answers over the grown record. A
question that finds nothing useful is answered honestly, with the documents kept and the cost
reported. The go-ahead carries the hash of the price shown, happens once, and queues the run
to the worker. *Done when*'s "a question outside the record is refused with a price rather
than guessed" still holds for the ask; what follows the price is now a control.

## F7 · Depth in primary sources

**What it is.** Reading further into free primary material rather than licensing secondary
commentary.

**Why.** The console's breadth advantage came from its *weakest* sourcing — the judges caught it
using a blog and an aggregator for load-bearing balance-sheet figures — and the material that
actually beat us was never exotic. Microsoft's segment table came from **Exhibit 99.1 inside an
accession the platform had already opened**, and AstraZeneca's patent-cliff answer came from a
6-K, the 20-F and a press release. `Filing.url()` builds only the primary document, so the
exhibit is never fetched.

**How it works, in priority order.**

1. **Exhibits inside accessions already opened.** `Filing.url()` gains the ability to enumerate
   an accession's documents and fetch EX-99 and its siblings. This is the single highest-value
   change in this feature and it is small.
2. **Earnings-call transcripts and prepared remarks**, frequently furnished as those same
   exhibits. This is where guidance lives.
3. **The narrative already fetched and barely read** — management's discussion, risk factors,
   the competition section, legal proceedings. Held, hashed, and scarcely cited.
4. **Investor-relations material**, through the issuer adapter that exists and is constructed
   nowhere.
5. **Competitors' filings describing the subject.** Rivals characterise each other; free from
   the same archive; and nothing else does it systematically.
6. **Regulatory primaries** — the drug-approval register would have answered the patent question
   that beat us, for nothing.

**Touches.** `aer.sources.sec.filings` and `Filing.url()`; `aer.sources.issuer` (wiring);
the research workers; the extraction path for narrative text; the concept map for the new
dimensioned facts.

**Done when.** A Microsoft run cites a figure from an 8-K exhibit; an AstraZeneca run states
revenue by geography from the 20-F's own note — the breakdown the platform already stores and
no writer has ever seen; and the checklist rows for segment revenue and guidance move from
*absent* to *present-sourced* under three judges.

---

## F8 · Print what the run already computed

**What it is.** The audit's own finding, as a feature: the platform computes, stores and hashes
far more than it prints.

**Why.** Every item here is a clear loss on the judges' checklist that costs nothing to win
because the work is already done and thrown away.

| What exists | Where it stops | The fix |
|---|---|---|
| The subject's own P/E of 27.43× and EV/EBITDA of 18.93×, computed every run | `assemble_document`'s comps parameter is typed so no multiple can pass — enforcing a licence position **the operator reversed on 2026-08-09** | Widen the type; the permission already exists in `fetch/policy.py`. **Default: publish** (open question 2, answered 14 September) |
| 626 dimensioned facts, mapped and stored — AstraZeneca's revenue by geography, Microsoft's by segment and by product | `visible_facts` bars every dimensioned row from every section's evidence pack | A carve-out for the section that is about segments |
| The WACC, terminal value and value per share | They carry `period = None`, sort last under `ORDER BY period_end DESC NULLS LAST`, and fall off a forty-row cap before any writer sees them | Rank and pool by name, as `red_team.py` already does |
| 259 verified excerpts | None is printed in the exported document | Print the excerpt, with F16's boundary decided first |
| A tested margin-decomposition bridge | Zero production callers | Wire it; it is what the console won on |

**Touches.** `aer.render.document`, `aer.services.facts`, `aer.sections.evidence`,
`aer.calc.bridge`.

**Needs an ADR.** One, amending ADR 0058 for the dimensioned-facts carve-out.

**Done when.** Re-rendering the five stored audit runs from their own records produces
multiples, segment figures, a market capitalisation and an implied range, with no sentence
denying any of them.

---

# Part B — The judgement layer

## F9 · The thesis, as premises with tests

**What it is.** What the operator believes about a company, written as sentences, each with the
test that would defeat it.

**Why.** It is the load-bearing idea of the whole product. Without it there is nothing for the
monitor to watch, nothing for the portfolio to validate, and nothing for the review to judge.

**How it works.** Mostly built, and unused. `aer.services.theses` already has `add_premise`,
`withdraw_premise`, and predicates defined as *a metric against a threshold*; it already refuses
to delete a premise and refuses to invent a predicate the operator did not choose. What is
missing is the interface, and two rules:

- **At least one premise must carry a test**, or the monitor has nothing to do. Saving without
  one is allowed and warns, naming the consequence rather than scolding.
- **A premise nothing can test gets a review date**, not a fabricated predicate.

**Revision, not replacement.** When a premise breaks, three doors: revise (the old wording is
kept), withdraw with a reason (it stops being tested, stays in the history), or keep it and
record why you think the miss is temporary. **Nothing is ever deleted**, and the history reads
as a narrative rather than a diff.

**Touches.** `aer.services.theses` (interface only); the thesis editor; the company page.

**Done when.** A thesis is written in under ten minutes, carries at least one resolvable
predicate, and its history reads as sentences rather than a changelog.

---

## F10 · Decisions, including the pass

**What it is.** The record of what was decided, why, how much — and, as a first-class outcome,
the decision **not** to act.

**Why.** The pass is the record nobody else keeps. When a company you declined halves, *"you
passed at 62× sales because X, and X is now false"* is the most valuable thing the system can
say.

**How it works.** Five kinds: open, add, trim, exit, **pass**. Each requires a thesis and a
report — neither can be blank, because a decision resting on nothing recorded cannot be reviewed
later. Each carries intended size (absent for a pass) and a reason with no minimum length. An
optional *"what would make this wrong"* field becomes a premise on the thesis, which is the
fastest route to a testable belief.

**One thesis, many decisions.** Open, add, trim, exit, re-enter — the belief outlives the trades
that express it, so the model is thesis → many decisions → many transactions.

**The pre-trade check** (F12) runs before the form can be submitted.

**Touches.** `aer.services.decisions` (largely built); the decision form; the company page.

**Done when.** A decision is recorded in under three minutes; a pass is as easy to record as a
buy; and no decision exists without a thesis and a report behind it.

**Corrected 24 September 2026, on verifying it against the code.** *No decision exists without
a thesis and a report behind it* is half true by design. A decision requires a thesis
(`decisions.thesis_id`, not null); the report is the thesis's own link, nullable and severed if
the report goes (ADR 0102's shape), because a view may be formed before the platform has
researched the company — Today's *held with no thesis* card leads to writing exactly such a
thesis. Requiring a report at decision time would forbid recording a decision about a holding
nobody has researched, which is the decision most worth recording. The "done when" reads: no
decision exists without a thesis behind it, and the thesis names its report where one exists.

---

## F11 · The monitor

**What it is.** The service that watches on the operator's behalf: it tests their premises
against what has been filed, and it watches the close for moves worth knowing about.

**Why.** This is the feature the product is for, and it is the largest piece of finished, unused
code in the system. `aer.services.thesis_monitor` already resolves a metric, measures it against
the fiscal years filed since a premise was last read, and decides whether the predicate holds.
It has no caller, no schedule and no screen.

**How it works — two kinds of alert, deliberately different.**

| Kind | Cadence | Mechanism | Cost |
|---|---|---|---|
| **A premise broke** | Monthly or quarterly, per company | Resolve each predicate's metric; measure against filings since the last read; decide — then ask the model what the new facts do to the premise | The arithmetic is free; the reading is a model call (corrected below) |
| **The price moved** | Daily, after the close | Compare the move over the listing's window against a threshold **the operator set**, never a default nobody chose | Nothing — arithmetic on stored bars |

**A price move is never shipped alone.** It arrives with what the record says about it: whether
anything has been filed since the last check, whether the premises hold, and whether the market
moved too.

> Down 12.4% over 7 days, from 410.00 USD to 359.16 USD. Nothing has been filed since your last
> check; all 4 premises still hold; the S&P 500 moved -6.1% over the same period.

That sentence is the product in miniature, and no chat can produce it, because no chat remembers
what you believed last quarter.

**Dismissals carry a reason**, and the threshold panel says why: too many dismissals mean the
threshold is wrong, not the market.

**Touches.** `aer.services.thesis_monitor` (wiring, schedule, screen); a scheduled job — which
the platform does not have, having only a queue; the alert surfaces; Today's queue.

**Done when.** Every broken premise surfaces within one filing cycle; a price move above the
operator's threshold surfaces the morning after the close with the thesis state beside it; and
zero alerts are dismissed as noise across a quarter.

**Corrected 23 September 2026, on building the price kind.** Two lines above did not survive
contact with the code. *Premise checks "cost nothing"*: the arithmetic does, and then
`aer.services.thesis_monitor` asks the model what the new facts do to the premise — a model
call, so the premise kind is not a free nightly job and is not on the daily pass (F15's own
correction says the same; the cadence and `next_check_at` columns are stored and unread until
the operator decides about standing model spend). *"The sector moved too"*: the platform holds
one market proxy per exchange (`aer.sources.eodhd.proxies`) and no sector series, so the
sentence names the exchange's index — *the S&P 500 moved*, *the FTSE All-Share moved* — and
never calls an index a sector; a sector series is work found, not work done. The rest stands and
is built: the threshold is the operator's (per listing where it is followed, or the account's
default on Settings); a move is a finding of its own kind, `price_move`, that carries no premise
status and opens no gate (ADR 0079: a price is an outcome, not evidence); the premises beside it
are the monitor's latest readings, read and never re-measured; and a dismissal takes a reason as
an appended resolution (ADR 0078), which is what the threshold band counts.

---

## F12 · Risk, and the pre-trade check

**What it is.** One implementation of the book's exposure arithmetic, surfaced twice: as a page,
and as the check that runs before a decision is recorded.

**Why.** They must never disagree, and the only way to guarantee that is one implementation.

**How it works.** Concentration (top-five, single-position, top-ten), exposure by the filer's own
classification code, and an **operator-stated shock** — a percentage applied to a named set. The
platform proposes neither the percentage nor the set. Every figure links to the holdings beneath
it.

**The check never blocks.** If a ceiling would be breached it says so and the submit control
reads *"Record it anyway"*. The operator's book is their own; the check exists to make sure they
knew.

**What it will not compute.** No value-at-risk, no correlation matrix, no beta-adjusted
anything. If the method is not on the page, the number is not on the page.

**Touches.** `aer.calc.portfolio`, a new exposure module, the risk page, the decision form.

**Done when.** The same shock produces the same figure on both surfaces, and every figure on the
risk page reaches its positions in one click.

---

## F13 · The stated view, in two halves

**What it is.** A conclusion the report can carry — composed by code where it is arithmetic, and
authored by the operator where it is judgement.

**Why.** `report.rating` is assigned `None` in one place and written nowhere, so *"no view
reached"* is a hardcoded default rather than a judgement. But the audit also showed that
**stating a view is not sufficient**: AstraZeneca's second run stated one, argued it, and changed
no verdict. So the two halves ship separately, and the composed half ships first.

**How it works.**

- **The composed half** — deterministic: the base-case range, the method, and, once a price
  exists, the implied upside or downside. No adjectives.
- **The authored half** — the operator's own view, recorded at the final gate as an ADR 0102
  judgement, with a basis that cannot be blank and at least one falsifier tied to a stored
  identifier.
- **The model writes neither.** It writes the prose around both.

Shipping them together makes the measurement unattributable, which is why the composed half is
judged alone first.

**Touches.** `vertical_slice_v1`'s report assembly; `aer.render.markdown`'s header;
`aer.services.theses`; the final gate.

**Needs an ADR.** Yes — whose view the report states is a decision, and ADR 0087 defines a
verdict as having two halves. This extends it to the report itself.

**Done when.** A run with a valuation never prints *"no view reached"*; a run the operator
declines to judge prints the composed half alone; and no rating string is ever model-written.

---

## F14 · Post-trade review and decision analytics

**What it is.** The loop closing: after a position is closed, was the reasoning sound *and*,
separately, did it work out.

**Why.** A profitable trade on a broken thesis is a bad decision that paid, and it teaches the
wrong lesson if nobody names it.

**How it works.** Two questions the form will not let you conflate, answered against what was
knowable at the time — which is why F1 keeps the retrieval timestamps. The four combinations are
named on the page: *right for the right reasons · right anyway · wrong for good reasons · wrong
for bad reasons*.

**Analytics stays silent until the sample earns it.** Below about twenty reviewed decisions the
page shows the count, the shortfall and a link to the review queue, and nothing else. Above it:
calibration, which premise kinds break most often, and whether the process is improving — each
finding linking to the decisions behind it.

**Touches.** `aer.services.decisions`; a new review record; the two Review surfaces.

**Done when.** Every closed position is reviewed or explicitly deferred with a date, and the
analytics page shows no statistic on a sample that cannot support one.

**Verified 24 September 2026, against the code.** The analytics half held: every proportion
below the minimum sample is withheld. The deferral half did not exist — no service, no control,
no record — so the queue could not distinguish a position nobody was ready to review from one
nobody had decided about. Built as `review_deferrals` (a date to review by and a reason,
append-only, and not a judgement: it asserts nothing about the company), a control on each
unreviewed row, a *Deferred* group on the page, and Today reading the state (page specification
§15's correction of the same date; ROADMAP §3.19 item 62).

---

# Part C — Platform

## F15 · Scheduling

**What it is.** A scheduled job runner, which the platform does not have — it has a queue, which
is a different thing.

**Why.** F11 needs a daily price pass and a monthly premise pass; the portfolio needs a daily
valuation. None is a queued run.

**How it works.** A single daily job after the close: value the book, read prices for every
watched company, ~~evaluate price thresholds, and run any premise check whose cadence is due~~.
One job, one schedule, one place to look when it does not run.

**No standing budget is needed.** The price subscription's ceiling is high and permits several
reads a day; the daily pass runs inside it. ~~Premise checks are arithmetic and cost nothing.~~

**Corrected 23 September 2026, against the monitor as built** (roadmap §3.19 item 51). A
premise check is *not* arithmetic alone: `aer.services.thesis_monitor` measures the metric and
decides the predicate in code, and then **asks the model what the new facts do to the premise**
— that reading is the finding, and it is a model call. So the two struck sentences contradict
each other: a nightly job that runs premise checks is a nightly job that spends, unattended,
under a line saying no budget is needed. The pass as landed reads prices and only prices, and
records every run as a job with a spend of `0.00`. **Premise checks on the schedule wait for
F11, and for the operator's decision on standing spend** — whether the platform may spend on a
timer with nobody watching is a budget decision (invariant 6), not a wiring detail. Price
thresholds are F11's price-move alert kind and go with it.

*"Valued at yesterday's close"* means having yesterday's bars: there is no `positions` table
to value into (ADR 0083), so the book is computed from bars on the way to the screen, and
reading the closes *is* valuing it.

**Touches.** A scheduler beside `aer.worker`; the health page.

**Done when.** The book is valued at yesterday's close without intervention for a fortnight, and
a missed run is visible on the health page rather than silent.

---

## F16 · The evidence boundary, when excerpts leave

**What it is.** A decision, taken before F8 prints verified excerpts into the exported document.

**Why.** Invariant 8 says untrusted content is data, never instruction, enforced by wrapping and
labelling at the point a model sees it. An excerpt printed into a report can re-enter the next
run's planner prompt through the prior-research path. That is a route from fetched bytes to a
future prompt, and it must be closed deliberately rather than discovered.

**How it works.** Excerpts print only for tiers whose licence permits it and whose source
carries no injection signal; the prior-research reader treats a printed excerpt as data with the
same wrapping any fetched document gets.

**Needs an ADR.** Yes.

**Done when.** An excerpt containing an instruction-shaped sentence is carried into a later run's
planning context and provably changes nothing about what that run does.

---

## F17 · Authentication, sharing and the evidence pack

**What it is.** The work that lets somebody who is not the author use the system, and lets the
author hand the work to somebody who must scrutinise it.

**Why.** The buyer most likely to pay is one who has to defend their research to somebody. Today
`get_current_user` returns the first row of `users`, there is no inbound rate limiting and there
is no deployment story — recorded in the roadmap as *"before this leaves one machine"*, and
treated there as hygiene. If the strategy is right, it is not hygiene; it is the product for
that buyer.

**How it works.** Real authentication; per-user data; a share that produces a **read-only
evidence pack** — the report, its sources, its calculations and its verification record, as a
single artefact somebody else can check without an account.

**Needs an ADR.** Yes, and it is the largest architectural change in this plan.

**Done when.** Two accounts exist with separate books, and a shared pack opens for somebody with
no account and resolves every footnote.

---

## F18 · Model portability

**What it is.** Making the language model a configured choice rather than an assumption.

**Why.** Because the arithmetic lives in Python, the model is swappable — which is a hedge
against a supplier's price or policy and a margin that improves as models commoditise. It is also
the cheapest insurance in the plan.

**How it works.** The provider boundary already exists and only one module may import a vendor
SDK. What is missing is a second implementation, a routing table that is not free text on a
settings page, and a documented per-role cost table so a swap is priced before it is made.

**Touches.** `aer.providers`, the router, the settings surface.

**Done when.** A full run completes on a second provider with the same gates and the same
verification results, and the cost table prices both.

---

## F19 · The UK path

**What it is.** Acquisition, extraction and classification for a company listed in London that
files only with Companies House — so that "UK or US" stops being a claim and becomes a fact.

**Why.** The product documentation has said "UK or US" since the first plan. A domestic London
listing cannot be researched at all: `acquire` resolves every subject against EDGAR's ticker
list, so a company with no SEC filings is refused before anything else happens. The operator's
decision on 14 September was to build the path rather than narrow the sentence.

**How it works.** Four pieces, and the second is the one that is not a wiring job.

1. **Dispatch on the resolved registry.** `acquire` stops naming `sec_client` and asks which
   registry can identify the subject. A subject that resolves in both is refused with both
   choices named — the dual-listing rule ADR 0093 already applies to the portfolio.
2. **`CompaniesHouseClient.fetch_facts`, which does not exist**, because Companies House
   publishes no equivalent of EDGAR's companyfacts. A UK filer's numbers live only inside its
   accounts, as inline XBRL, one period at a time. So `fetch_facts` is: accounts filings newest
   first, fetch and hash each, `extract_ixbrl` over each, union the facts. **Depth defaults to
   four filings** — four years on an annual filer — so the cost is predictable.

   > **Corrected 18 September 2026.** `fetch_facts` was built as written and the register was
   > then asked what it holds. Twelve accounts filings across Tesco, Barclays, AstraZeneca and
   > Greggs are PDF-only and paper-filed; none carries inline XBRL, so this route returns no
   > facts for a listed company. Inline XBRL at Companies House is what small and medium
   > companies file through software. See ADR 0121's appended section and `ROADMAP.md` §3.19
   > items 21 and 22; where a London-listed company's tagged numbers come from is an open
   > question, and **F19's "done when" below cannot be met until it is answered**.
3. **A second classification scheme.** Every `SectorProfile`'s `sic_prefixes` are US SIC codes
   (`602` banks, `6798` REITs, `737` software). UK SIC 2007 is a different scheme. Without
   seeding it, a UK bank matches nothing, the gate does not fire, and it takes the standard
   model — which is precisely the hole that produced M&T's 172.1% net margin.
4. **A sterling risk-free rate, as an operator-owned assumption.** `RISK_FREE_SERIES` holds USD
   only and `risk_free_series_for` refuses rather than defaulting, because the Bank of
   England's `robots.txt` disallows the CSV handler it documents. The gilt yield is supplied,
   sourced and confirmed at the assumption gate — the same mechanism the audit used for the US
   rate. An automated series is a follow-up with a named candidate and an unverified one.

**What is already done, and is why this is affordable.** The Companies House client is complete
and has 32 tests; the fetch policy allowlists both hosts; the rate limit was verified against
the developer specifications on 2026-09-04; the credential is wired in `runtime.py`; the
offline iXBRL extractor was built for UK filings; and `companies.company_number` already
exists with a check constraint (`cik IS NOT NULL OR company_number IS NOT NULL`) written for
exactly this case. **This plan twice said that constraint fails for a UK company. It does not.**

**Touches.** `aer.sources.uk.companies_house` (`fetch_facts`), `vertical_slice_v1`'s `acquire`,
`aer.services.sectors` and the sector profiles, `aer.db.models.company` (`sic_scheme`),
`aer.services.assumption_gate`, and the product documentation's claim.

**Needs an ADR.** Yes — **ADR 0121**, drafted.

**Done when.** A domestic London filer reaches an approved, rendered report with its figures
traced to its own accounts documents; a UK bank fires the sector gate; a sterling valuation
either carries a sourced gilt yield or refuses; and the offline test that proved the refusal is
inverted to prove the resolution.

> **Corrected 18 September 2026.** The first clause is not reachable and was never about this
> platform: nine London-listed companies sampled across the FTSE 100, 250 and AIM file their
> accounts as scanned PDFs, and none publishes inline XBRL (ROADMAP §3.19 items 21 and 25). The
> subject the platform can research end to end is a UK company that *does* file tagged accounts
> — small companies filing through accounting software do — and what it owes a London listing is
> a refusal at the door, by name and with the reason, before a planning call is spent (ADR 0128).
> The restated exit is in `ROADMAP.md` §3.17, which is the authority.

**What it does not do.** No 10-Q and no 8-K stream exists for a UK filer, so a UK report is
quieter on recent developments than a US one, and says so. UK peers cost a full acquisition
each, so comparable multiples stay deferred.

---

## F20 · The knowledge map learns what you decided

**What it is.** The knowledge layer — which already grows on its own as the platform is used —
gains the judgement layer as nodes and edges, and four surfaces start reading it back.

**Why.** It is the same pattern as the judgement layer itself: **already built, barely read.**
`docs/archive/knowledge-graph.md` says so in its own words — *"the layer this document planned
is built"* — and it is right. Seven node kinds, six edge kinds, an Obsidian projection and an
in-app graph view. A run resolves a company and a node appears; a confirmed peer set makes a stub
node for a company nobody has researched; a confirmed theme links companies across industries; a
catalyst accumulates references across runs because its identity is `(company, label)` rather
than `(run, label)`.

**What it reads back is two things**: prior conclusions in front of the planner as labelled
hypothesis material (ADR 0064), and the `prior_research_comparison` section at draft time. That
is all.

And V1.0 opens a hole in it: **the map records what you researched and knows nothing about what
you decided.** A thesis, a premise and its threshold, a decision, a monitor finding, a post-trade
verdict — none is a node, none is an edge, none is read. A knowledge map that accumulates
research and forgets judgement is a library with no borrowing record.

**How it works.**

1. **Four node kinds and five edges**, under the rule the map already runs on — *only confirmed
   state produces an edge*. Thesis, premise, decision, verdict; company→thesis, thesis→premise,
   decision→**thesis version**, premise←finding, verdict→decision. A draft thesis and an unfiled
   review produce nothing.
2. **Four surfaces read it back**, each a question the operator can already ask aloud and the
   platform cannot answer:
   - **The monitor** — a finding on one premise surfaces against every other position sharing
     its metric *and* a theme or sector. *"The thing that just broke here is load-bearing in two
     other places."* The connective tissue exists; nothing traverses it on a finding.
   - **Ask tier 1** — the map **is** the record tier 1 answers from, so *"which of my holdings
     depend on the same premise?"* becomes free rather than research.
   - **The refresh** — materiality stops being only a property of the figure and becomes partly
     a property of your position: a section feeding a premise you hold is material at a smaller
     move than one feeding nothing you own.
   - **The methodology library** — `calc/outcomes.py` already measures a confirmed assumption
     against the year it forecast. With decisions and verdicts in the map, the same measurement
     runs over *methods*. That is the difference between a library of methods and one that knows
     which ones worked.
3. **It becomes evidence for nothing.** No claim may name a thesis, a premise, a decision or a
   verdict — the rule ADR 0095 applies to a challenge brief. A premise is an **attestation**
   (ADR 0073), so a lineage containing one reaches no shareable surface, which is what keeps it
   out of the evidence pack. Anything from the map that reaches a prompt is wrapped under
   ADR 0119.
4. **The vault stays one-directional.** From the vault, nothing, ever — and it matters more now:
   a vault note is a file anything can edit, and a decision record editable outside the database
   would destroy the one property a post-trade review depends on.

**Touches.** `aer.obsidian.export` and `graph.py`, `aer.services.knowledge`,
`aer.services.graph_view`, `aer.services.history`, the monitor, Ask's tier 1, the refresh's
materiality classifier, and `aer.calc.outcomes`.

**Needs an ADR.** Yes — **ADR 0122**, drafted.

**Depends on.** F9, F10 and F14. There is nothing to record until theses, decisions and reviews
exist, which is why this is last.

**Done when.** A finding on one holding's premise surfaces against another holding that shares
it; Ask answers *"which positions rest on this same belief?"* from the record for nothing; and a
seeded attempt to cite a premise as evidence is refused.

**What it does not do.** It does not backfill. The three stored reports have no theses, decisions
or reviews, and the migration invents none — a map with judgement nodes on day one would have
got them from somewhere nobody authorised.

---

## The order these want to be built in

Not a schedule — [`05-delivery-plan.md`](05-delivery-plan.md) holds that — but the dependency
graph, which the schedule must respect.

```
F1  point-in-time removal        ── first, alone, suite green before anything else
F8  print what exists            ── independent; the cheapest wins
F12 risk + pre-trade check       ── needed by F3 and F10
F9  thesis                       ── needed by F2, F11, F13, F14
      └─ F13 stated view         ── needed by F2
           └─ F2 the adversary
      └─ F11 monitor             ── needs F15 scheduling
F10 decisions                    ── needs F9, F12
      └─ F14 review + analytics  ── needs F10, and F1's retained timestamps
F7  primary-source depth         ── independent, and the largest research-side win
F4  refresh                      ── needs F7 to be worth refreshing into
F6  ask                          ── tiers 1–2 independent; tier 3 needs F7's acquisition
F5  workbook                     ── independent
F16 evidence boundary            ── before F8 prints excerpts
F17 auth and sharing             ── independent, and a change of who the product is for
F18 model portability            ── independent, any time
F19 the UK path                  ── independent; follows F7, whose acquisition work it
                                    would otherwise be done twice alongside
F20 the knowledge map           ── last. Needs F9, F10 and F14: nothing to record until
                                   theses, decisions and reviews exist
```
