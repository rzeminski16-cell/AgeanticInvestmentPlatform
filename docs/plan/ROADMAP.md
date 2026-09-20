# Roadmap

**The authority on scope.** Where this and any other document disagree, this one wins —
except the ADRs, which are decisions and outrank a plan.

Three buckets and nothing else. **Fixes and bugs** is what is wrong; **new additions** is
what does not exist yet; **archived** is what is finished or decided against, kept because
the *why* is the part worth having. An item moves between them by being worked, never by
being tidied away.

*Supersedes the original `PLAN.md` (Stages 1–4), the five phase plans and `investment-os.md`
§12/§15 — all now in [`../archive/`](../archive/README.md). Those remain the record of how
the platform got here and why; this is the record of where it goes.*

*Written 2026-08-23 on the merged trunk. Restructured into these three buckets 2026-08-25.*

---

## What to do next

In this order. It is the operator's order rather than the author's: each one is the next
thing that would otherwise put a wrong number, or no answer at all, in front of somebody.

**As at 2026-09-01, all five are closed in code.** Items 2 to 5 were already done; the
operator's `run-diagnosis.json` landed on 2026-09-01, §2.1 was diagnosed from the record
rather than from a hypothesis, and all five of its causes are fixed — along with §2.2,
which the same export settled. **What remains is one operator-approved confirmation run**,
which is the operator's move rather than a session's.

1. **§2.1 — sections fail to draft. Diagnosed and fixed 2026-09-01; the confirmation run
   is outstanding.** More than a quarter of the last report was a coverage notice. The
   export settled it: nothing starved, and every failure was a contract refusal — six at
   `draft` and two *destroyed by `revise`* after drafting cleanly. All five causes are
   closed (ADRs 0096, 0097, 0098, 0100 and the two eraser gaps), and §2.2 with them
   (ADR 0099). **What is left is one operator-approved live run.**
2. **§3.1 — the portfolio's third door. Done 2026-08-29, under ADR 0093.** A work order
   roots the book's own acquisitions; a typed `TICKER EXCHANGE` the platform has never seen
   is verified with the vendor once, at first sight, and either becomes dealable or is
   refused with the reason. Tranche 8's prerequisite is landed.
3. **§2.4 — the report document's layout. Done 2026-08-30.** The disagreement appendix
   reads as prose (migration 0061); the stacked pairs closed with the WeasyPrint ≥ 69 pin;
   both are held by the geometry assertions in `tests/test_report_layout.py`.
4. **§3.12 — the interface overhaul. Done 2026-08-30.** All ten tranches built and
   verified green — tranche 9, the removal-and-hardening pass, closed §2.5 and §3.12
   together; the record is the status section of
   [`interface-overhaul.md`](../archive/superseded-2026-09/interface-overhaul.md).
5. **§2.5 — the palette migration. Done 2026-08-30, inside §3.12.** The ramp ledger fell
   1,837 → 0 across tranches 2 and 4–9, the legacy aliases are gone from the stylesheet,
   and the ratchet is now the hard zero §2.5 asked for
   (`tests/test_palette_migration.py`).

*Finished 2026-08-25 and now in §4: the drafted-figure check (§4.14) and the comps
disclosure (§4.15), which were the two at the top of this list.*

**The confirmation run happened, five times over, in the readiness audit of 2026-09-12** —
[`readiness-audit-2026-09.md`](readiness-audit-2026-09.md). Item 1 is closed by it: 18 of 18
sections drafted on every run, nothing starved and no section lost.

**What the audit put in front of this list instead is two issues, and they are now §2.10,
§2.11 and §3.16.**

1. **The platform is not ready for a user.** Three of six runs reached an approved report;
   £14.41 was destroyed by two states the interface could not leave; 135 code identifiers
   reach gate pages and the terminal is load-bearing. **§2.11.**
2. **The platform is not better than a Claude console note.** Nine of nine blind comparisons
   chose the console; six of six judges would not act on what the platform produced.
   **§3.16**, and the abandonment criterion inside it is what stops that being an article of
   faith.
3. **A bank cannot produce an approvable report**, because its revenue resolves to its
   ASC 606 fee income. The one open *blocking* finding. **§2.10**, decided in ADR 0114.

**The running order is now [`../V1.0_Alpha/05-delivery-plan.md`](../V1.0_Alpha/05-delivery-plan.md)** —
seven phases, about sixty-four sessions and £64 of live spend, with the gate that would stop
the work stated before it starts. This list stays the authority on priority; that plan is the
authority on sequence within §3.16. Everything the audit decided and everything the design
conversation settled is written up in [`../V1.0_Alpha/`](../V1.0_Alpha/README.md), with eight
ADRs (0113–0120) drafted ahead of the code.

**All three of those were settled on 14 September 2026**, and are recorded in
[`../V1.0_Alpha/06-open-questions.md`](../V1.0_Alpha/06-open-questions.md):

- **The price subscription** is treated as permitting publication of derived figures, which is
  the permission `fetch/policy.py:192` already records. If the agreement turns out to restrict
  it, the figure is withheld through ADR 0034's type — which has no field for a figure it may
  not carry — rather than quietly printed or silently dropped.
- **"UK or US" is built, not narrowed.** §3.17.
- **The abandonment criterion is signed off**, which is what makes every other gate in the plan
  able to fail.

---

## 1. Where the platform actually is

**The chain is complete. The breadth is not.**

A research request becomes a costed plan you approve, filings fetched and hashed,
point-in-time facts, traced calculations, a drafted report you approve, and a frozen
document in which every figure carries a footnote resolving to either the formula that
produced it or the archived bytes it came from. That path has no gap in it, and the
evaluation gate re-derives every stored calculation from its own record on every run.

Nine of nine tools work:

| Tool | State | Waiting on |
|---|---|---|
| **Equity Research** | Working, end to end | — |
| **Portfolio** | Working | — |
| **Watchlist** | Working | — |
| **Theses** | Working | — |
| **Decisions** | Working | — |
| **Monitor** | Working | — |
| **Risk** | Working | — |
| **Post-trade review** | Working | — |
| **Decision analytics** | Working | — |

A planned tool, while there was one, was a real page saying what it would be and what it
needed — not a dead link and not a lie. None remains.

### What the merge established

The trunk merges two lines of work that ran in parallel from `7a438e8`:

- **The research line** — residual income for banks, the catalyst contract, the assumption
  gate, subject naming, section evidence, sector enforcement that blocks rather than
  footnotes.
- **The Investment OS line** — work orders as the run root, the tool registry, attestations
  and grades, the FX rate store, portfolio arithmetic, and the shell: nav as data, design
  tokens, badges, the drawer, the launcher.

Two collisions had to be resolved by hand, because git could not see either: both branches
had claimed ADRs **0067–0070**, and both had claimed alembic revisions **0051–0053**. The
research numbering was kept; the Investment OS records became **0071–0085** and its
migrations **0054–0057**. Anything written before 2026-08-23 that cites an Investment OS
ADR by its old number reads four low.

---

## 2. Fixes and bugs

Something here is wrong and should not be. Ordered by how much of a report or a screen each
one costs, worst first. **§2.10 is next** — it is the one open blocking finding of the
readiness audit, and a bank cannot produce an approvable report until it is closed.

**2.1 A63 — sections fail to draft. Diagnosed 2026-09-01 from the operator's export;
the fixes are landing, and the confirmation run is outstanding.** Eight of eighteen
sections did not generate on the MSFT run of 2026-08-31, and more than a quarter of the
report was a coverage notice.

**The standing hypothesis is refuted.** Nothing starved. Every section was dealt 17–43
facts, 3–9 excerpts and 11–29 calculations; the run held 780 calculations and 153 claims.
Neither did the retry ladder mis-fire: every failed section used both of its two attempts,
and every failure was a refusal on the *contract* — not on length, not on an empty pack.

**And they did not all fail in the same step.** Six failed at `draft`. **Two drafted
successfully and were destroyed by `revise`** — Balance Sheet & Liquidity with 24 recorded
claims and Scenarios & Sensitivities with 21, each reduced to a four-byte null when its
revision was refused.

| Cause | Sections | State |
|---|---|---|
| A numeric claim naming no figure, or naming one and citing nothing | Segment Analysis, Industry & Competitive Positioning, Capital Allocation, Balance Sheet & Liquidity | **Fixed** — ADR 0096 |
| The platform's own rendering of a figure read as an unsourced numeral | Historical Financial Analysis (`331,839`), Scenarios & Sensitivities (`$331.8 billion`), Capital Allocation | **Fixed** — ADR 0097 |
| A product name and a year naming a document read as figures | Business Overview (`Dynamics 365`), Management & Governance (`2025 proxy statement`) | **Fixed** — the erasers, `343fc3e` |
| **A failed revision discards the draft it was improving** | Balance Sheet & Liquidity, Scenarios & Sensitivities | **Fixed** — ADR 0098 |
| More than one "missing evidence" sentence | Historical Financial Analysis, Management & Governance *(a second cause on each)* | **Fixed** — ADR 0100 |

**The revise defect was the worst of the five and was not a validation rule at all.**
`revise_challenged_sections` deleted the section's claims, then redrafted over
`section.content`; a refused revision left `FAILED` with nothing. So a section that
drafted, validated and was paid for was traded for no section at all because the red team
had something to say about it — and ADR 0091's loop, which exists to *improve* a draft, was
the only way to lose one that already passed. **ADR 0098 closes it**: the claim replacement
moved to `record_draft_claims`, where it only runs for a draft that passed, the row is
snapshotted and restored, and a fourth disposition — `revision_refused`, migration 0063 —
puts the attempt and its refusal inside the gate-2 hash rather than leaving the spend
invisible.

**The `gaps` rule was the last of the five**: at most one sentence may describe missing
evidence. Both sections that tripped it tripped something else too, so neither is known to
have failed *for* it — but it refused a whole draft over a count of its own hedging and had
no salvage, which is the trade ADR 0057 exists to refuse. **ADR 0100 gives it the fourth
repair**: the surplus remarks go and the first one stays, which is what the rule asked the
writer to do in the first place. The budget itself is untouched.

**All five causes are closed. What remains is the confirmation run** — one live,
operator-approved report on the same subject, which also exercises the critique-and-revise
loop (ADR 0091) against these sections for the first time since they were fixed.

**The confirmation run, 2026-09-05 (`21d5beb6`), found two more, both fixed from its
record.** Fifteen of eighteen sections drafted; the three that did not
(`business_overview`, `cash_flow_analysis`, `capital_allocation`) and the seven reported by
`cited_figure_agreement` came down to:

| Cause | Sections | State |
|---|---|---|
| A negative figure read without its sign: "-51.8 days" scanned as `51.8` and refused against a stored `-51.79` | Cash Flow Analysis, Capital Allocation, and every section the agreement metric reported | **Fixed** — ADR 0097, amended |
| A numeric claim naming a fact row was refused for citing no prose excerpt, nine times over, on both attempts | Business Overview, and the `schema` refusals on the other two | **Fixed** — ADR 0109 |

The approved report then stalled at `gate_final` because a disagreement settled on the
page after the seal changed the payload the seal was taken over — fixed, with `aer reseal`
to recover a run already caught (`57c8138`). `aer replay-draft <job-id> [section-key]` reads
the run's archived section replies back under today's rules at no spend — the proof that a
changed rule takes, before another live run pays to find out. It runs the salvage pass on
every refused reply and rolls the sections up as the draft step reads its attempts, so a
refusal that costs an edit is told from one that costs the section: the first readout
showed eighteen refusals and could not say how many of them were lost sections, which was
the only number that mattered. Two smaller findings from the
same run: the depreciation-intensity driver was asked of the operator because the subject
files `Depreciation` and `AmortizationOfIntangibleAssets` and no combined tag — the halves
now map to their own concepts and the combined line is derived from them, as total debt is
from its maturities; and the beta the operator was asked for had its refusal only in a log
line — `acquire_prices` now records the regression's reason beside `beta_proposed`.

**The replay of that run's 37 archived replies under the fixed rules** (2026-09-05, £0):
`business_overview` and `cash_flow_analysis` pass; `capital_allocation` is still refused on
both attempts for the writer's own arithmetic in prose and two gap sentences, one of them
the depreciation line the derivation now supplies. Of the 18 refusals that remain across
the run, 11 are word-budget overruns on first attempts — the writer, told the validator's
headroom, wrote to it and past it — so the budget is now stated as the limit and the
headroom kept for miscounting; one was a 27-claim reply against a 24-claim bound the prompt
never mentioned, now asked for; one was "Proposition 5 —" read as a figure, now a reference
(ADR 0054, amended). The rest are the rules doing their job: MD&A percentages no fact row
holds, a difference the writer computed itself, a market capitalisation mentioned on a run
with no prices. Whether the budget change takes is a live-run question, read from the
`draft` step's attempt counts.

**The resealed run then sat queued for a night because no worker was running**, while the
console said it would begin within a few seconds. The worker now records its health to
Redis every thirty seconds, and the console, `aer diagnose` and `just worker-check` read
the record: a queued run with nobody listening says so, and says what to start. `aer
preflight` then folds the runbook's stage 1 into one readout at no cost — the model key,
the database and its schema, a user, the per-run ceiling against the last run's cost, the
month's room, Redis, the worker, the price feed — so the next run starts on a known footing.
And `aer rehearse-section <job-id> <section-key>` is the built-in twin of the skill dry run:
one section drafted again against a finished run's evidence under today's prompts, on a job
of its own, for about thirty pence — the way to find out whether the budget change takes
before a full run pays to, and the way to re-test a failed section after any fix.

**What the Capital Allocation refusals were, read from the replay** (2026-09-06): four
sentences of the writer's own arithmetic over figures the pack held — dividends paid *rose
by* £2.4 billion, repurchases *rose by* £3.9 billion, debt repayments *fell by* £6.0 billion,
buybacks and dividends *together* £48.7 billion — each refused as a numeral no figure stood
behind. The rule was right and the section could not do its job. `aer/calc/cash_uses.py`
now strikes those figures in the analysis pass — shareholder distributions, their share of
operating cash flow, and the year-on-year change in dividends paid, share repurchases, debt
repayments and capital expenditure — so the writer names a recorded change rather than
computing one; and the writer's first rule now says in so many words that a sum or a
difference worked out from the listing is a figure of its own. Both are measured by
rehearsing the section, not by the next full run.

**The price feed never reached a run** (2026-09-07, found by stepping the first run on a
clean slate): `acquire_prices` reported "no market-data subscription is configured" with
`AER_EODHD_API_KEY` set and preflight saying so. The client was built on every machine with
a key and asked for by the step as an optional service, but `runs.execute` never put it in
the engine's services and neither the worker nor `aer step` passed it — so every run to date
held no prices, no regressed beta, no market capitalisation and no priced multiple, however
the key was set, while the peer step (which reads the setting rather than the client) still
paid a model to propose peers nothing could price. `execute` now takes the client, and the
service bundle has one `for_execution()` both callers spread into the call, so the two
cannot forward different subsets again. A run that already recorded the step's "no
subscription" output keeps it; prices arrive on the next run.

*What was read against the tree 2026-08-28, ahead of the data, and turned out not to be the
cause: `validate_draft` checks only the 1.25× word ceiling with no minimum; a truncation
retry halves the word budget; `MAX_GENERATION_ATTEMPTS = 2`. All three still hold, and none
of them fired on this run.*

**2.2 Section confidence. Resolved 2026-09-01, ADR 0099.** Sections reporting 0.30 were
either an honest signal about a starved pack (§2.1) or a floor nobody calibrated. The
export said neither: it is a *cap*, and it was firing on edits that have nothing to do with
evidence.

Half-settled from the code, 2026-08-28: **it is not a floor.** `confidence_of` takes the
model's own declared figure — defaulting to 0.5 when it states none — and *caps* it at 0.3
when the pack was degraded (`sections/evidence.py`).

**Settled from the export, 2026-09-01, and the answer is that the cap is misfiring.** Five
of the ten sections that survived the run report exactly 0.30, and **not one of them is a
degraded pack**:

| Section | Why it was capped |
|---|---|
| Executive Summary, Earnings Quality, Cash Flow Analysis, Growth Outlook | "Shortened to fit the length allotted to this section." |
| Valuation & DCF | "One or more sentences were removed because their figures could not be traced to a recorded source." |

Four of the five were capped for being **trimmed to their word budget** — the mildest edit
the platform makes, and one that says nothing about whether the section is right. The fifth
had sentences deleted for untraceable figures, which says a great deal. Both landed on
0.30, a strong statement about reliability that neither of them earned: a complete, fully
cited section that ran long read to a person exactly like one the platform had to cut for
lineage.

**ADR 0099 gives each degradation its own ceiling and takes the lowest that applies** — an
evidence shortfall keeps §2.12's 0.3, removed unsourced material caps at the platform's own
0.5 prior, and a length trim moves the number not at all. Nothing stops being *disclosed*:
`low_confidence_reason` already told the three apart in the reader's words (gap R2), which
is how the flattening was visible at all, and it still does.

**2.3 A run that fails late cannot be resumed, only repeated. Resolved, 2026-08-28, ADR
0090.** The engine skips completed steps, and does it well — that is how a run survives the
worker dying. But it only applied to the *same* job, and the only operator-facing path was
superseding, which creates a new job precisely because the old one is a finished audit
record. So a failure at the red-team step, one step from the end, cost the entire run again:
on the 2026-08-24 MSFT run that was £8 of research and drafting to recover a £1 step.

The decision was the work, and ADR 0090 records it: `jobs.status` is where the run is *now*,
never a summary of everything it has been — the history was always in the step rows and the
hash-linked audit chain, and resuming appends a `run.resumed` event (who, when, from what
state) rather than rewriting anything. `aer resume` re-enqueues the same job;
`aer.services.resume` refuses the states that do not admit continuing, each with its reason.
The deliberate pause this settled alongside is §3.15's.

**2.4 The report document — layout. Done 2026-08-30.** The rendered PDF had two defects a
reader met immediately, and each closed differently. The disagreement appendix put a
two-hundred-word challenge in a narrow table column — one row spanned three pages —
and now reads as prose: `validation_disagreements` v4 (migration 0061) declares the
appendix in the renderer's prose-block shape, and each recorded conflict becomes a short
run of paragraphs the page can break inside. The stacked label/value pairs do not
reproduce under the engine now pinned — WeasyPrint ≥ 69 lays the cover grid and every
table out correctly, where pre-grid engines stacked each `dt` over its `dd` — so that
defect closed with the version pin and is guarded so it cannot silently return.

The layout check is a test rather than a pass: `tests/test_report_layout.py` renders the
golden document *and* a document from a full fake-provider pipeline run whose red team is
scripted to argue at the length that broke the live document, then walks WeasyPrint's own
box tree asserting the rules the defects broke — nothing paints past the page edge, no
table row outgrows a page, a row's cells share a line, a label shares its line with its
value, and the challenges reach the reader as paragraphs, never inside a cell. What no
instrument holds stays the operator's: a live-provider document on the machine the
platform actually runs on (commercial check 5), and the typographic judgement beyond
those rules.

**2.5 The palette migration. Done 2026-08-30, as tranches 2 and 4–9 of §3.12: the ratchet
reached zero and became a hard assertion.** The ledger fell 1,837 → 0 — tranches 6, 7, 8
and 9 each removed exactly what the plan predicted — the compatibility aliases are gone
from the stylesheet, and `tests/test_palette_migration.py` now asserts every template at
zero raw ramps with the retired names pinned gone. What was true when this was written,
kept for the record: the theme *control* was done first (§4.13)
and this is what it left behind: a page's colours are correct in both schemes or they are
slate grey beside navy.

`web/styles/app.css` added the semantic tokens *beside*
Tailwind's stock ramps rather than over them, deliberately, so that `text-sky-700` still
renders sky — overriding the ramp would re-skin the templates for free and leave a
codebase where a colour name is a lie. So it was a real rewrite, onto the semantic
vocabulary — those working names shipped as `canvas / surface / ink / line / verification /
decision / success / warning / refusal / failure / info / muted`, and the working aliases
themselves were removed in tranche 9 — ending with exactly the test asked for: one that
fails when a template reintroduces a raw ramp.

The measurements live in one place — **the ramp ledger in
[`interface-overhaul.md`](../archive/superseded-2026-09/interface-overhaul.md)**, with the census command and the standing
caveat that the method must be stated with the number. Opened at 1,837 over forty-one
templates; closed at zero on 2026-08-30.

Deliberately sequenced after everything above it. Those are a wrong number or a missing
answer; this is a page that looks like two designs. It is also the item most likely to go
wrong quietly, so it wants its own pass with screenshots rather than being folded into a
functional change.

**2.6 A split arrives as a transaction. Done 2026-08-30, under ADR 0094.** A recorded
split becomes a derived transaction in every book that has dealt the listing, pointing at
the corporate action behind it — never a quantity that changed with nothing behind it.
**The quantity is the ratio, not a share delta**, so the row derives from the action alone
and stays right when an earlier trade is backfilled: the walk multiplies at the split's
place in trade-date order. Units multiply, the cost pool is untouched (ADR 0085 — a split
is not a purchase), and the kind is refused on the form because a split you can type is a
share count with nothing behind it.

**2.7 R18 — the share-based-compensation risk-free rate. Done 2026-08-30.** A
`ShareBasedCompensation…RiskFreeRate` tag must never map to `risk_free_rate`. It is an
input to an option-pricing model in a footnote, not the discount-rate input, and mapping it
would put a plausible wrong number in the cost of capital.

Verified 2026-08-28: the mapping does not exist — **and neither does anything that would
refuse it.** `core/concepts.py` holds alias tables and no never-map table, so an absent tag
is indistinguishable from one nobody has looked at yet, and nothing stops the mapping
arriving later in good faith. The fix is the mechanism: a deny table carrying the reason
beside each entry, this family pinned in it, and the unmapped-concepts gate reading *refused*
as distinct from *unplaced*.

**Done 2026-08-30.** `NEVER_MAP` in `core/concepts.py` holds the five tags of the
share-based-compensation assumption family, each with the reason a reader can act on, and
`canonical_concept` returns `None` for them whatever the alias tables say — the refusal is
in the resolver rather than only in a test, so an alias added in good faith cannot take
effect while somebody argues about the table; a test then catches the contradiction rather
than being what prevents it. The distinction reaches the operator: a refused tag is
reported under its own heading with its reason, is excluded from the tags the gate asks
about, and **does not stop a run on its own** — asking about a decision already taken is
how a considered refusal gets approved away as noise.

**2.8 A55 — concept-map coverage. The mechanism is built, 2026-08-30; the curation is
still the work.** 175 concepts and 110 segment tags the map cannot place. This is judgement
over accounting semantics rather than a code change, which is why it has survived several
passes: it needs somebody who knows what a tag *means* deciding what it maps to. The gate
that names the lines a filing would lose is one half of the mechanism; the other is now
`aer curation-worksheet`, which reads every run's recorded extract rows, aggregates them,
ranks them by the largest share of a mapped line any run saw, and writes a worksheet with a
column to fill in and the canonical vocabulary listed beneath it. Refused tags (§2.7) are
listed apart and are not up for decision. **A sitting works down from the top and stops** —
the first rows are the ones that would most change a report — and turning what the operator
writes into alias-table entries stays a deliberate act by a person reading it.

**2.9 Report readability.** The register is clean — every sentence in a report that was
*about the report* is gone or moved to where disclosure belongs. Keep it that way: the
failure mode returns whenever a new refusal path gets a placeholder written in the
platform's voice rather than the report's.

**2.10 A bank's revenue resolves to its ASC 606 fee income.** The one open blocking finding
of the readiness audit. `RevenueFromContractWithCustomerExcludingAssessedTax` is a correct
fact and the wrong answer: for M&T it is $1,657m against $6,948m of net interest income and
$2,742m of non-interest income, so the published net margin was **172.1%** and every ratio
with revenue in its denominator is nonsense. A bank has no revenue caption to extract; the
figure must be derived. Every guard held — the fact was hashed, the calculation recorded, the
citations verified — which is why `calc/plausibility.py` exists and why this is a fact-layer
fix rather than a rendering one. **Decided in ADR 0114**: derive it, at the fact layer, only
for a filer the sector gate has confirmed as a bank, and raise rather than coerce on a unit
mismatch. Specified as F7 in [`../V1.0_Alpha/04-feature-specifications.md`](../V1.0_Alpha/04-feature-specifications.md).
**Done in code, 17 September 2026** (ADR 0114 Accepted): the ASC 606 caption is retagged
`revenue_from_contracts` before selection and revenue is derived from the two halves as a
fact with `basis = derived` and a `derivation` column (migration 0077) holding the formula,
both inputs by id and the code version. Found on the way, and fixed first: the turnover half
of `calc/plausibility.py` had never fired at all, because `services/evaluations.py` asked for
a concept named `total_assets` where the canonical one is `assets` — so the guard this
finding produced had caught nothing on any run. **The live M&T run followed the same day**:
FY2025 revenue of $9,690m, a 29.4% net margin against the 172.1% the September run
published, and the third refusal firing on the three years M&T stated the line itself —
where the filer's own caption equals this sum to the dollar. **The note landed on 18
September** as Phase 4.9, on Phase 4.8's printing work where the ADR said it belonged: a
derived figure's footnote says what the figure is, because no filing states it, and the
drill-down page is handed the document's own note rather than composing a second account
of the same derivation.

**2.11 A stopped run has no way forward that is not the terminal.** The audit lost **£14.41**
on two runs — MSFT #2 and M&T — that reached a state the interface could not leave: a
rejected gate leaves the job non-terminal (`services/runs.py:165`), the eleven evaluation rows
are written once inside `validate`, and the engine skips any step whose row says SUCCEEDED
(`workflow/engine.py:518`). Three of six runs reached an approved report. Alongside it, 135
distinct code identifiers reach gate pages, and the console tells the operator to watch
`just worker`. This is the whole of ISSUE 1 in the V1.0 delivery plan and is measured by the
journey harness in [`../V1.0_Alpha/11-testing-strategy.md`](../V1.0_Alpha/11-testing-strategy.md)
§3.1 — every stopped state carries a labelled forward control, zero UUIDs, zero shell
commands, and pressing the control moves the job. **Moved on 16 September 2026:** the
harness landed red and Phase 1.2 (ADR 0123) put a way forward on the three gate dead ends
and on a refused check the same day — a decision the page moved under is decided again and
superseded, a rejection ends the run and the console offers a new one, re-seal and re-measure
are controls. Phase 1.4 emptied the record the same day: the console, every pause message,
the gate pages, the problem page and the report's own validators table, footnotes and
valuation refusal speak in words, and the harness measures 33 constructible rows green on
all three assertions. What it still cannot say is anything about the 19 rows it cannot yet
construct on the fake scene — the sector and unmapped-concepts gates, the nine escalation
triggers, an unverifiable citation, a bare `AerError` — which are harness backlog, not
platform backlog.

---

## 3. New additions

Nothing here is broken; it does not exist. **§3.16 is where this bucket now leads**: the
judgement layer of §3.5–§3.11 is built and unreached, and V1.0 is what connects it to a
report. §3.5 onwards is the judgement layer, and the order there is forced by dependency —
nothing after theses can exist before them.

**3.1 The portfolio — getting a ticker in. All three doors, 2026-08-29.** `Security` rows
existed only where a priced research run created one, so on a fresh database the control held
one option reading "cash, no security" and an operator could neither type a ticker nor find
out why not.

- **Typed, over what is held.** Done. The `<select>` is an `<input list>` over a
  `<datalist>` — a native typeable combobox, no script — and what is typed is resolved on
  the server: a bare ticker, the vendor symbol a run stored (`BARC.LSE`), or
  `TICKER EXCHANGE`. A dual listing is refused with **both** choices named rather than
  resolved by picking one, because a holding priced off the wrong exchange is a book nothing
  downstream can reconcile. An empty box is a cash transaction, not a mistake.
- **From a research request.** Already true and now stated on the form: `acquire_prices`
  creates the subject's listing, so a company you have researched with a subscription
  configured is dealable with no second step. The empty state names this as the one path
  that creates a listing today, rather than reporting an absence.
- **A ticker the platform has never seen. Done 2026-08-29, under ADR 0093.** A typed
  `TICKER EXCHANGE` that resolves to nothing held is verified with the market-data vendor
  once, at first sight: `services.listings.add_listing` fetches a short window of bars and
  records them the way every acquisition is recorded — the series hashed and stored
  (invariant 1), the source document rooted on the act's own work order, the security keyed
  on the vendor's symbol — or refuses with the reason: no subscription configured, a venue
  the vendor mapping does not document, a symbol the vendor returns nothing for. A bare
  ticker is asked for its exchange rather than guessed at.

**How the decision landed** (decided 2026-08-25, recorded as
[ADR 0093](../adr/0093-a-work-order-roots-the-books-own-acquisitions.md)): a *portfolio data
acquisition* is a work order whose subject is the book — one per act, `tool` and
`subject_kind` the distinguisher with no new column, `point_in_time` off because today's
close is the point, and a cap of zero that structurally refuses any model call under it.
`record_acquisition` now reads the clock off the work order rather than the mandate row,
which was the exact coupling ADR 0072 exists to remove; every attempt, refused or not,
leaves a `COMPLETED` or `FAILED` order on the record.

**3.2 The portfolio — return and exposure. Done 2026-09-01.** Four tiles was not an
overview; the page now answers whether the book has *done well* as well as what it is worth.

- **Return over time.** Time-weighted and money-weighted, since inception and per calendar
  year, over a value series walked from the transactions and the price history. Deposits and
  withdrawals are the only external flows — a dividend is money the holdings produced and
  belongs inside the return — and the series is broken at every one of them, so a top-up
  cannot read as performance. The two figures are shown side by side because they disagree
  on purpose: the first is comparable to an index, the second credits the operator's own
  timing. A true time-weighted return needs a valuation per flow date, which is bounded at
  `MAX_VALUATION_POINTS` and refused *with its reason* above that rather than silently
  approximated by a Dietz weighting answering a different question under the same label.
- **Concentration and exposure.** Weight by holding, sector, currency and listing country,
  with a top-five figure that says how many holdings it covers. Cash is in the currency band
  because cash is a position. Sector comes from the company record behind the security and
  listing country from an explicit venue table, so both report what they know and **name
  what they do not** in a group held apart from the weighted ones, members listed.

Both are calculations under ADR 0083 like everything else on that screen: derived on the way
to the page, nothing stored, every figure carrying the grade of the weakest thing beneath it
— including the sign flip that turns the book's side of a flow into the investor's, which is
the one arithmetic step nobody would think to check.

**3.3 Step 4 of the work-order migration.** **Done 2026-09-01** — migration `0064`.
`jobs.request_id`, `approvals.request_id` and `source_documents.request_id` are gone, and so
are the six columns duplicated on `research_requests` (`user_id`, `as_of_date`,
`point_in_time`, `max_cost_gbp`, `status`, `archived_at`) and the function that kept the two
copies in step. `research_requests.id` is a foreign key to `work_orders.id` now, so the shared
key is something the database keeps rather than a convention two modules remember.

Staged as a later revision on purpose (ADR 0072): while the columns still held the data,
dropping `work_orders` discarded nothing, so the downgrade was lossless rather than merely
declared. `0064`'s own downgrade is where that stops being free, and it says so — it backfills
every column exactly, then deletes work orders with no detail row before re-imposing `NOT
NULL`, because downgrading past this point is downgrading past the existence of runs that are
not about a company.

The mandate lookups moved first: `services/mandate.py` answers "the mandate for this run, if
it has one" and the 17 `session.get(ResearchRequest, …)` sites read through it, so a monitor
run with no research request is an ordinary `None` rather than a missing row. ADR 0072 records
what step four actually cost — the purge walk and `aer reset-research` both had to move their
root, and the edit diff had to learn which of the two rows holds each field.

**3.4 Scenarios and sensitivity for the residual-income model.** **Done 2026-09-02** —
ADR 0101. The bank model shipped without them and said so in its caveats; it now runs every
authored scenario on both terminal treatments and builds two 5×5 grids of its own — cost of
equity against terminal growth under the perpetuity, and against return on equity under the
fade. Each sits under the treatment its second axis means something in, and every cell is a
complete valuation on ADR 0028's terms.

The axis question was the real obstacle, and 0101 settles it: the return on equity is a
driver path, and `aer.calc.dcf.VARIABLE_FIELDS` refuses driver axes because "revenue growth"
is five numbers. An axis may vary one **when the confirmed path is flat**, which is what a
bank's gate usually confirms; a fading path costs that grid, by name, with the reason in the
output. A perpetuity refused in any corner takes its grid whole — a hole is a cell a reader
interprets. `residual_income_value` gained a `case` so a bank's scenarios are attributable in
the ledger, and the scenario bridge and football field read the bank's per-share rows rather
than looking for a discounted cash flow's and finding nothing.

**3.5 Judgements and theses** (ADRs 0074, 0079). **Done 2026-09-02** — ADR 0102. A
thesis is a view a named person held at a time, with the evidence it rests on and the
questions that would defeat it. The record that makes it *storable without becoming evidence*
already existed — **a judgement is never a source reference** — and this is where it earned
its keep: `judgements` is the supertype (holder, two clocks, basis, withdrawal with a
reason), `premises` the first subtype keyed on the judgement's own id, and `theses` the
container. A premise is a statement plus an optional predicate, and one with no predicate
must name the date a person reviews it by, so nothing stored is a view the platform silently
stops asking about. Nothing is deleted; every act is on the audit chain with the thesis as
its subject, which is what `AuditEvent.create_linked` gained the subject correlation for.

The theses tool is the third working tool: a list, a detail and four forms. The rule is proved
off the schema — no table but `premises` references `judgements`, `claims` has no column for
one, `SourceKind` has four members — rather than remembered.

`RESERVED_OUTPUT_FIELDS` gained `conviction` in the same change, with its attack file and its
own refusal clause: not because a section owns it, but because a view somebody holds is not a
figure at all.

**3.6 The thesis monitor** (ADRs 0078, 0079). **Done 2026-09-02** — ADR 0103. What has
happened since a thesis was written that bears on it. **It raises questions and answers
none**, and a monitor finding is not a gated decision — an alert feed that decides things is
the thing that record exists to refuse.

What shipped: the `thesis_monitor` role (no tools, a status from the closed enum and a
justification naming source documents), and the rule ADR 0103 settles on top of ADR 0079 —
**code measures the crossing before the model is asked anything.** A premise's free-text metric
resolves to a growth, a ratio or a statement line; the threshold's unit is normalised once
(per cent is a convention, ADR 0027) and compared through `Quantity`, which refuses a mismatch;
and the model's status is bounded by the verdict: a defeated predicate is `contradicted`
whatever it says. A premise nothing new bears on makes no call; a premise nothing measures is
`unobservable` with the reason.

`findings` and `finding_resolutions` are the record ADR 0078 wanted kept apart from an
approval, with the tier pinned as a column. Only a contradicted finding opens
`GateKind.THESIS` — decided on the finding, through the monitor service, never through a
run's gate order — and the decision is what happens to the premise: withdrawn with the reason,
or kept despite the filing with the reason. Every other act is an appended row with a reason.
A pass that hits its cap stops with a `stopped` finding and a FAILED job; it never pauses.

The monitor is the fourth working tool: a page, the gate, an attention provider (a
contradicted premise is *waiting for you*; a stopped pass *needs diagnosis*; an unread finding
or an overdue review *not started*), a worker task, and `aer monitor` for a nightly schedule.
What it cannot measure is written on the finding: segment lines are dimensioned facts the
analysis excludes, so "Azure revenue growth" is unobservable until a later change reads them.

**3.7 Decisions and the trade journal.** **Done 2026-09-02** — ADR 0104. The entry written
*before* the outcome is known. `decisions` is the second judgement subtype, keyed on the
judgement's own id in `premises`' shape: the thesis it acts on, an action from six, the
statement and basis, and the four things a post-trade review holds the operator to — a size
**as a sentence** (the schema has no numeric size column, on purpose: ADR 0074), a horizon in
months, an exit plan, a review date. Revising writes a new entry that supersedes the old;
withdrawing records the reason; nothing is edited.

`transactions.decision_id` is the trade saying which decision it carried out — on the
attestation and pointing at the judgement, never the reverse, so a judgement still enters no
lineage and a test keeps `aer.calc` free of the word. The trade form gained *Carries out*, and
a pairing that cannot be what it claims (a sale carrying out a buy) is refused.

ADR 0080's six sizing names are reserved in this change, with their attack files, because a
decision's action and size are the first sizing concept: the adversarial corpus is nineteen.
The Decisions tool is the fifth working tool, with an attention row for a decision never
carried out and one past its review date.

**3.8 Post-trade review and decision analytics** (ADR 0081). **Done 2026-09-03** — ADR
0105. Scored against the process that was supposed to be followed, deliberately **not**
against whether it made money. A closed position is an *episode*: the walk the pooled cost
makes, asked when the holding returned to nil, so nothing is marked and an open holding is
never reviewed. Its outcome is code's — cost, proceeds and the realised return as `@traced`
functions in `calc/outcomes.py`, every flow converted at its own trade's date, the ledger
persisted against the pass — beside the holding period and the horizon the decisions stated.

The `post_trade_reviewer` role runs once per episode on its own work order and its draft
lands on the pass's step as a **proposal**; the operator confirms it, amending anything, and
*that* is the review — `reviews`, the third judgement subtype in `premises`' shape, held by
the operator on their basis, with the proposal kept beside it so that agreement is decision
data. `Statistic` cannot be built without its `n`, and below three reviewed positions every
breakdown is a tally. Post-trade review and Decision analytics are the sixth and seventh
working tools; the loop ADR 0079 named stays open by design.

**3.9 Portfolio risk and scenarios** (ADR 0080). **Done 2026-09-03** — ADR 0106. Commented
on rather than scored. Every figure ADR 0080 named is a `@traced` calculation in
`calc/risk.py` — annualised volatility, maximum drawdown, expected shortfall, a holding's
contribution as its weight times its beta to the book, scenario profit and loss — measured
*ex-ante* over the weights the book holds now and a year of each holding's daily returns in
its own currency, with the coverage stated and an unmeasured holding named rather than
filled in. Exposure and concentration are §3.2's, shown not recomputed.

A scenario is a named set of shocks the operator states (`risk_scenarios`,
`risk_scenario_shocks`), each reaching what the exposure bands say it reaches; none is
built in, for ADR 0080's reason reaching code. The `risk_analyst` role runs in the web
process on its own work order over the rendered block, and its three commentaries are
refused by the numeral check and the words of a prescription — once with the problems
carried back, then recorded. A shock is a lineage node in its own relation (ADR 0076). Risk
is the eighth working tool.

**3.10 Watchlist and research queue.** **Done 2026-09-03** — ADR 0107. Two clocks on two
tables: an entry is followed *from* the instant the database stamped, a commission is
research *as at* the date the operator chose, and nothing on the entry says *researched* —
that is read from the commission's run and its report, so a dead run puts the entry back
in the queue and a second commission after a report is the ordinary case. The standing
budget (`AER_WATCHLIST_BUDGET_GBP`) bounds what the queue may *start* in a month — the
month's spend by commissioned runs plus the caps of the ones still alive — and each run
keeps its own cap with the month's applying on top. A commission is an ordinary research
request at the form's defaults with the per-run cap, started and stopped at gate one. The
queue is walked in follow order and stops at the first refusal, by name, from the page or
from `aer queue`, which exits non-zero when it stopped short. Watchlist is the ninth
working tool, and no planned tool remains.

**3.11 The methodology library.** **Done 2026-09-03** — ADR 0108. The three prompt kinds
were versioned and pinned since task 36 and composed nowhere; now a pure role table says
which roles read which kind — methodology and house view into the planner and the
section writer, preference into the writer, and nothing into any role that judges — and
each reader composes the pinned text as the last block of its user turn, under the same
delimiter and rule a custom section's body gets, with the system prompt byte-identical
either way. The plan step pins before it plans, so the planner, gate 1 and every section
read the same rows, and gate 1 names the roles inside its hash. A prompt-kind skill is
refused every section-shaped field at authoring. The ADR 0040 corpus gains seven
prompt-kind escalations and a `roles` layer, and the starter library ships one example of
each kind.

**3.12 The interface overhaul. Done 2026-08-30: specified, designed and planned 2026-08-25;
all ten tranches built and verified green by 2026-08-30 — the record
is the top section of [`interface-overhaul.md`](../archive/superseded-2026-09/interface-overhaul.md).** Four
surfaces were in scope and the rest of the product deliberately not: **the main menu, the menu
system and shell, the Equity Research tool, and the Portfolio tool.**

**Where it stands.** The requirements are in [`../design/`](../archive/superseded-2026-09/design-requirements/README.md); the design
came back as [`../redesign/`](../archive/superseded-2026-09/redesign-delivered/README.md) — a token system, page specifications, a
production handoff and a twelve-screen prototype. It was reviewed against the invariants and
the code, and **adopted with nine corrections**, one of which is a WCAG failure the design's own
validation reported as passing: the navigation rail keeps dark colours on a light page, its
tokens were never in the normative table, and the light-theme focus ring measures 2.04:1 on it.
The review is [`../redesign/05-review-and-corrections.md`](../archive/superseded-2026-09/redesign-delivered/05-review-and-corrections.md)
and it wins where it and the design system disagree.

**The work is sequenced in [`interface-overhaul.md`](../archive/superseded-2026-09/interface-overhaul.md)** — ten tranches,
each independently releasable — with its testing in
[`interface-overhaul-testing.md`](../archive/superseded-2026-09/interface-overhaul-testing.md).

**Every blocking decision was cleared on 2026-08-25** and three became records: **0087** (a
verdict has two halves — a composed half that is live, and an authored half a model writes once
over a frozen subject and which is never evidence), **0088** (a fixed-scheme region carries its
own measured palette), **0089** (`/runs/active` resolves the current run). The product is named
**Tracework Invest**; the navigation carries one badge, on Requests.

**Sequenced behind §2.1 and §3.1**, on the operator's direction. Tranche 0 touches no template
and no service, so it may run alongside either.

What does not exist is not a screen. It is a *specification a designer can work from*: the
platform's interface grew a page at a time, each one correct in isolation, and the result is
two designs sharing a shell — the boundary §2.5 measures. A palette migration alone would
make the two halves the same colour without making them the same product.

So the deliverable is [`../design/`](../archive/superseded-2026-09/design-requirements/README.md): every surface in scope with its
purpose, its reader, its data contract, every input and how it is collected, every state it
can be in, what is wrong with it today, and what a redesign must not break. It is written for
a designer rather than for a developer, and it is the input to the work rather than the work.

**The order is: specification, then design, then §2.5, then the templates.** Migrating the
palette before the design exists would be doing the most quietly-fragile item in the roadmap
twice. **§2.5 is no longer a separate item**: it was tranches 2 and 4–9 of the plan, and it
closed with the ratchet's hard zero on 2026-08-30.

**Two items in flight go first, and neither is blocked by this.** §2.1 puts a wrong number in
front of somebody and this is a page that looks like two designs; §3.1 adds a control to a
portfolio form that is the smallest migration in the plan. Both land before the tranche that
rewrites their surface.

**The constraints are not negotiable inside the design, and are argued outside it.** ADR 0006
makes the server the only renderer; ADR 0077 draws the line — chrome may be the client's, a
figure never is — and every form works with scripting off. `design/01-constraints.md` states
them as constraints a designer can satisfy, and keeps a *challenge appendix*: what a designer
might reasonably want that a constraint forbids, with what it would cost to change. A design
that needs one changed needs an ADR, not a diff.

**What is deliberately outside this item.** The rendered report document (§2.4) is a
document-layout problem in WeasyPrint's print stylesheet, not a screen; it keeps its own
entry. The seven planned tools get their placeholder page and nothing more until each ships:
designing a screen for a tool whose tables do not exist is how a specification becomes
fiction.

**3.13 A critique-and-revise loop for drafting, with a memory of what needed revising.
Done, 2026-08-28 — ADR 0091.** The `revise` step redrafts the sections the red team's
material challenges attack — attribution by claim id, validated in code; one pass, at most
four sections, custom sections stood aside from — and seals the gate-2 hash with the
revision record inside it. The `critique_plan` step puts a separate-context critic
(`plan_critic`, the role this ADR admits) between `plan` and gate 1, with one planner
revision when a challenge clears severity 3 and the critique inside the gate-1 hash. The
memory landed on the safe default the paragraphs below asked for: `revision_notes` records
every decision, `aer lessons` counts recurrence across runs, and a lesson reaches a future
run only as an operator-authored methodology skill — invariant 7 untouched. The original
case for the item follows.

Today `red_team` (ADR 0039) already attacks the draft from a separate context, and it has
twice caught a section publishing a number that contradicts its own citation (4.14, ADR
0086) — not a hypothetical failure mode, the platform's own log. What it does not do is loop
back: a challenge reaches the disagreement ladder for a human at `gate_final`, the section
that provoked it is never redrafted, and nothing about the challenge survives past that one
run. The machinery to critique already exists; what is missing is a writer that gets a
second attempt before a human ever sees the draft, and a way for a recurring class of
challenge to be recognised as recurring.

Scope it to where an open-ended, model-written step is both expensive and wrong often
enough to matter. `draft` first — already the largest cost line per report, already the
most reader-facing, already the step §2.1, §2.2 and 4.14 are about. `plan` second — cheap on
its own, but a wrong plan sends the whole run after the wrong target, so catching it early is
the highest leverage in the workflow. `propose_assumptions` is a plausible third and a lower
priority: high-stakes, but already narrow (ADR 0046) and immediately human-gated. It does not
belong on `acquire`, `classify`, `extract`, `calculate`, `comps`, `value` or `render` — those
are correct by construction under the one rule (`CLAUDE.md`, ADR 0003), and a model's opinion
feeding back into one of them is the "calculation drifting into a prompt" failure the
architecture exists to prevent. It does not belong on the gates either, since the human is
already the critic there, nor on `validate`, which is advisory-only by design and was never
meant to have the last word (ADR 0038), nor on `red_team` itself — recursing the loop onto
the critic is diminishing returns that the disagreement ladder already covers.

**The revise loop is the easy half.** The knowledge base is not: a memory that changes what
a future agent writes is exactly the shape of thing invariant 7 exists to govern — skill
files may only add requirements, never relax them, proved by a corpus that must all fail
(ADR 0040), not assumed. An auto-written "do not repeat this" lesson has no such proof
behind it, and a critic that was wrong once would otherwise get to entrench its own mistake,
unreviewed. Whether that memory has to be a skill file, or some other mechanism that still
needs the same proof, is an architectural choice CLAUDE.md says to stop and ask about rather
than guess — the safe default is a person confirming a finding is real and recurring before
a future run inherits it, possibly through §3.11's methodology library rather than a new
mechanism. Either way this needs an ADR before any of it is built: a new step is a new agent
behaviour (ADR 0035), and this one touches invariant 7 by nature, whichever way it lands.

**3.14 A web-search tool for the qualitative sections. Done, 2026-08-28 — ADR 0092.**
The `analysis` role's allowlist now grants `web_search`: the worker asks, code executes
one bounded server-side search through the provider, and what returns is a listing —
titles, URLs, age notes — wrapped untrusted at T6, never a page and never citable.
Reading stays behind `fetch_known_url`'s host-admission rule, so no trust boundary moved;
the pricing check below was verified against the official page and the fee is metered per
search (commercial check 1, closed). Point-in-time runs with a past as-of date are
refused the tool in code. The original case for the item follows.

Nothing has a `web_search`
capability today. `fetch_known_url` reaches only a host this run has already acquired a
document from through the named adapters, and refuses anything else by design — an
unlisted host gets back "this run holds no document from that host, and a host is never
taken from a request." What is missing is not the trust model: `sources/tiering.py` and
`fetch/policy.py` already carry `Provider.WEB_SEARCH`, tiered before this item existed —
search-found news and issuer material at `T5_SECONDARY` ("never the sole support for a
numeric claim"), search-found commentary at `T6_UNVERIFIED` ("never citable evidence").
What is missing is a tool nothing calls: no role's allowlist grants it, and the commercial
check still outstanding on the Anthropic web-search tool's price is what keeps it out of
the budget guard (invariant 6).

Scope it to the qualitative workers — `research_recent_developments` and the rest of the
five parallel research agents (ADR 0036) — for what does not belong in `calc/`: business
description, sentiment, recent developments, the colour a filing does not carry. It does
not widen what can reach a figure. `calc/` consumes structured facts extracted from filings
and nothing else, so a T5 or T6 source has no route into a number whatever tool exists —
that boundary is the one rule (`CLAUDE.md`, ADR 0003), not a permission to add. Wired the
same way as any other tool request — the model asks, code executes, results return wrapped
as `<untrusted_source tier=…>` (ADR 0019) — it is contained the same way a filing already
is, and the corroboration the operator wants ("confirmed when multiple sources agree") is
`T5_SECONDARY`'s existing "never sole support" constraint, already enforced, not a new one
to write.

**Do not pre-approve named publishers.** Checked against robots.txt — the same mechanical
test ADR 0009 already treats as an absolute refusal, no ToS reading required — Seeking
Alpha disallows roughly 150 crawlers outright, `Claude-User` and `ClaudeBot` by name among
them: it appears to refuse the model this platform itself runs on. `ft.com` could not be
reached to check at all. That is the same shape of finding that put the Bank of England and
the FCA NSM (ADR 0022) in §4's "decided against" — a documented block, not a negotiable
one. So each specific always-on publisher, if wanted, is its own adapter-style decision — a
ToS/robots determination recorded in an ADR before the first request, per the existing
recipe — never a batch of sources assumed trustworthy for being well known. Needs an ADR
either way: a new tool capability is new agent behaviour (ADR 0035).

**3.15 A step-by-step developer mode for debugging a run. Done, 2026-08-28, as §2.3's
resolution — ADR 0090.** `jobs.step_mode` pauses the run (`PAUSED`, the status that was in
the vocabulary from Phase 1 and set by nothing until now) after every step that actually
executes, wherever it executes; `aer step` runs the next step in the terminal and prints its
diagnostic, `aer diagnose` prints the readout without executing, and `aer resume` hands the
job back to the worker. The diagnostic is assembled from what each step already records —
no model call. The original case for the item follows.

Pause after every step, print a
diagnostic, let the operator confirm or correct before the next one spends anything — the
point being to catch a defect at the step that caused it, not three steps and several pounds
later. The primitive already exists for the accidental case: the engine already records
each step and skips the ones already completed on resume, which is how a run survives a
worker dying today. What does not exist is the deliberate case. §2.3 already names the gap
this depends on: there is no supported way to pause and continue *the same job*, only crash
recovery and superseding into a new job — and superseding is wrong here specifically,
because it creates a fresh audit record when the whole point is reviewing one run as it
happens. Build this as §2.3's resolution, not a second mechanism beside it, and settle its
open question — what a deliberately paused, not-failed job's own status record says — once,
for both. Unlike §3.13 and §3.14, nothing here touches an invariant or adds an agent
capability, so the item itself does not obviously need its own ADR — only §2.3's decision
does, and that one already stands regardless of this.

It is not a gate. `gate_plan`, `gate_final` and the rest are domain-approval checkpoints
through `services/approvals`, each meaning something specific about a business decision;
stepping through a run is a generic, lighter thing — closer to a breakpoint than an
approval — and belongs in the engine's own execution loop instead. Unlike §3.13, scope it
to every step, not only the model-written ones: a wrong number out of `calculate` or a bad
extraction is a code bug, and catching it here matters at least as much as catching a bad
paragraph out of `draft` — arguably more, since a silent arithmetic mistake is exactly the
kind of thing nothing today stops to show anyone.

**The diagnostic is code, not a model call.** An LLM judging each step would add a paid
call to steps that cost nothing today (`extract`, `calculate`, `render`), work against the
speed this is meant to buy, and duplicate the critique agent in §3.13. Most of what it needs
is already captured and simply not surfaced — a failed section already records its attempt
count, its evidence tally and its own refusal reason (4.6), and the roadmap's own words for
the gap are "the run console still shows none of this." Assemble it from what each step
already records — timing, retries, raw versus parsed output, validation errors, cost — in
the same family as the existing `job_id`-scoped CLI commands (`replay-run`, `acceptance`)
that already print a typed readout to the console rather than a web page, which is the right
shape for something meant to be read by whoever — human or Claude — is sitting at the
terminal deciding whether to continue.

**3.16 V1.0 Alpha — the report becomes a loop.** The eighteen features that turn a report
generator into *research → decide → hold → review*, each stage writing a record the next
stage reads. Specified in full in [`../V1.0_Alpha/`](../V1.0_Alpha/README.md); this entry is
what makes them scope.

**Why it is one item and not eighteen.** They are not independent: F9 (the thesis) is the
load-bearing one and F2, F11, F13 and F14 cannot exist before it, while F1 must land first
and alone because it touches the drafting prompts. Splitting them into eighteen roadmap
numbers would invite them to be worked in an order the dependency graph forbids. The order is
in [`../V1.0_Alpha/05-delivery-plan.md`](../V1.0_Alpha/05-delivery-plan.md) and it is the
authority on sequencing within this item.

| | Feature | Needs |
|---|---|---|
| F1 | Remove point-in-time | ADR 0113 — **landed 17 September 2026**, Accepted outright: all four re-seeded runs replay |
| F2 | The adversary argues the opposite case | ADR 0115, F13 |
| F3 | The closing section reads the operator's own book | F12 |
| F4 | The refresh | ADR 0116, F7 |
| F5 | The model workbook | — |
| F6 | Ask, in three tiers | F7 for tier 3 |
| F7 | Primary-source depth, and a bank's revenue | ADR 0114 (= §2.10) |
| F8 | Print what the run already computed | ADR 0118, F16 |
| F9 | The thesis, as premises with tests | — |
| F10 | Decisions | F9, F12 |
| F11 | The monitor | F9, F15 |
| F12 | Risk and the pre-trade check | — |
| F13 | The stated view, in two halves | ADR 0117, F9 |
| F14 | Post-trade review and decision analytics | F10 |
| F15 | Scheduling | — |
| F16 | The evidence boundary | ADR 0119 — **landed 18 September 2026**, Accepted: the passage prints behind three gates, and a poisoned prior moves no plan |
| F17 | Authentication, sharing and the evidence pack | ADR 0120 — **deferred** |
| F18 | Model portability | — |
| F19 | The UK path | ADR 0121 — and it has its own number, **§3.17** |
| F20 | The knowledge map learns what you decided | ADR 0122 — **§3.18** |

**Sequencing decided on 16 September 2026**, where the delivery plan left two things
unplaced. **F1 is Phase 1½**: after Phase 1's instruments (CI green, the order dependence
verified, the journey harness red) and before Phase 2, alone, with the suite green before
anything else lands — and beside it the corpus, which the delivery plan costed at £0 on stored
runs that no longer exist (§3.19, item 2). F1 landed on 17 September 2026 at £0; the corpus
half is open until the keys arrive. **The loop — F3, F4, F5, F6, F9–F15 and the nineteen
surfaces of `03-page-specifications.md` — is Phase 6a**, estimated at 25–35 sessions and £0
live, after Phase 6 and before Phase 6b (which has nothing to record until F9, F10 and F14
exist), in the dependency order of `04-feature-specifications.md`: risk and the pre-trade
check, the thesis editor, decisions, the scheduler, the monitor, review and analytics, the six
destinations, the closing section, Ask, the refresh. Conditional on Phase 5 like everything
after it; movable after Phase 7 with 6b if the verdict is wanted sooner.

**What this item does not commit to.** Phase 5 of the delivery plan carries an abandonment
criterion: if a measured round moves no verdict and changes no judge's stated reason, the
work stops and the product's claim narrows to what the audit already scores as true — an
evidence base and a checking instrument. That gate is real, it is pre-registered before the
round, and it needs the operator's sign-off to be worth anything. **F17 is deferred** on the
operator's decision of 14 September 2026, until a solicitor has read the
consequences-not-instructions design; ADR 0120 is drafted anyway so that V1.0's schema does
not foreclose it.

**3.17 A London listing can be researched.** The product documentation has said "UK or US"
since the first plan, and a company listed in London that files only with Companies House
cannot get past `acquire` — every subject is resolved against EDGAR's ticker list. **The
operator decided on 14 September 2026 to build the path rather than narrow the claim.**
Argued in **ADR 0121**, specified as **F19**, delivered as **Phase 4a** (8–11 sessions, £8
live).

Most of it exists and has never been called: a complete `CompaniesHouseClient` with 32 tests,
both hosts allowlisted in the fetch policy, the rate limit verified on 2026-09-04 (§commercial
check 2), the credential wired in `runtime.py`, an offline iXBRL extractor built for UK filings,
and a `companies.company_number` column whose check constraint — `cik IS NOT NULL OR
company_number IS NOT NULL` — was written for exactly a CIK-less UK company. Earlier plans said
that constraint fails. It does not.

Four things are genuinely missing, and only the second is large:

1. `acquire` names `sec_client` directly rather than dispatching on the resolved registry.
   **Landed 18 September 2026.** Both `acquire` and `extract` split on `registry_of(exchange)`:
   a London listing resolves at Companies House, is classified from the profile's UK SIC 2007
   codes, and has its accounts parsed one per accounting period; a US listing is unchanged.
   `research_requests.register` records which register answered. The pre-run check (ADR 0128)
   is why this had to land rather than being dropped with the rest of the phase: the check
   asks Companies House whether a UK subject's accounts are tagged and admits the run when
   they are, so a run admitted on that answer and then resolved against EDGAR would be
   precisely the defect the check exists to prevent.
2. **`CompaniesHouseClient` has no `fetch_facts`, because Companies House publishes no
   companyfacts equivalent.** A UK filer's numbers exist only inside its accounts, as inline
   XBRL, one period at a time — so a UK acquisition is *n* fetches and *n* parses, and every UK
   fact is this platform's own parse rather than a registry's aggregation. **Overtaken on 18
   September 2026 — see §3.19 item 21 and ADR 0121's appended section.** `fetch_facts` landed;
   the premise did not survive contact with the register, because a listed company's filed
   accounts are a PDF. Where a London-listed company's tagged numbers come from is now an open
   question for the operator, and item 22 is a second one about the fetch policy.
3. Every `SectorProfile.sic_prefixes` is a US SIC code. UK SIC 2007 is a different scheme, so a
   UK bank matches nothing, the gate does not fire, and it takes the standard model — the ADR
   0029 hole that produced §2.10's 172.1%. **Landed 18 September 2026** as migration 0081, and
   it is two columns rather than the one this list said: `companies.sic_scheme` records which
   register issued the code, and `sector_profiles.uk_sic_prefixes` gives the code something to
   match — seeded from the Companies House condensed SIC list, read rather than recalled. Every
   writer states its scheme and every reader takes it; `631` reaching the insurers under one
   scheme and early-stage technology under the other is the test that would have to be deleted
   for the column to become decoration.
4. No GBP risk-free series: `risk_free_series_for` refuses rather than defaulting, because the
   Bank of England's `robots.txt` disallows the CSV handler it documents (ADR 0026's
   Resolution). The gilt yield ships as an operator-confirmed assumption; an automated series is
   commercial check 6 below. **Closed 18 September 2026**, and the working part was already
   built: the refusal becomes the assumption gate's own prompt, so the operator enters the rate
   and it is recorded as theirs. What was missing was the sentence — *no risk-free series is
   documented for GBP* tells nobody what to do. It now names the ten-year gilt yield, its
   publisher, why this platform may not fetch it, and that the date must come with the figure.
   Two refusals, kept apart in `SETTLED_BUT_UNFETCHABLE`, because "the platform does not know
   what to use here" and "it knows exactly and cannot fetch it" lead to different actions.

**The phase's stated exit is not reachable, and is restated here — 18 September 2026.** It read
*a domestic London filer reaches an approved, rendered report with every figure traced to its
own accounts documents*, and it needs a domestic London filer that publishes tagged accounts.
Nine sampled across the FTSE 100, 250 and AIM publish none (item 25), so the exit as written is
a statement about the London market rather than about this platform, and no amount of code
reaches it. What the phase can be held to instead, and is:

- **A UK subject that files tagged accounts is researched end to end**, its figures parsed from
  its own filings, each traced to the filing that stated it. Small UK companies filing through
  accounting software do publish inline XBRL — four were confirmed on 18 September 2026 — so
  this is a real subject rather than a fixture.
- **A UK subject that does not is refused at the door**, by name and with the reason, before a
  planning call is spent (ADR 0128).
- **A UK bank fires the sector gate**, because the scheme travels with the code (item 3 above).
- **A sterling valuation carries a sourced gilt yield or refuses** (item 4 above).

The two halves together are the honest form of "a London listing can be researched": the
platform either does it properly or says why it cannot, and never produces a report on a scan.

**3.18 The knowledge map learns what you decided.** The knowledge layer is built and
`../archive/knowledge-graph.md` says so in its own words — seven node kinds, six edge kinds, an
Obsidian projection and an in-app graph view, growing on its own as the platform is used. It
reads back into exactly two places: the planner's prior digests (ADR 0064) and the
`prior_research_comparison` section.

V1.0 opens a hole in it. The map records what you *researched* and knows nothing about what you
*decided* — no thesis, no premise, no decision, no finding, no post-trade verdict. So it gains
four node kinds and five edges under the rule it already runs on (only confirmed state produces
an edge), and four surfaces start reading it: the monitor surfaces a broken premise against every
other position that shares it; Ask's tier 1 answers from it for nothing; the refresh's
materiality becomes partly a property of what you hold; and the methodology library measures
which *methods* worked, the way `calc/outcomes.py` already measures assumptions.

**And it becomes evidence for nothing.** No claim may name a thesis, a premise, a decision or a
verdict; a premise is an attestation under ADR 0073, so a lineage containing one reaches no
shareable surface; and the vault stays one-directional, which matters more once a decision record
exists, because a vault note is a file anything can edit. Argued in **ADR 0122**, specified as
**F20**. Last in the dependency order — there is nothing to record until F9, F10 and F14 exist.

**§3.19 is deliberately unallocated.** Anything this phase turns up gets a number here rather
than being folded into §3.16, §3.17 or §3.18, so that work found during V1.0 is visible as work
found rather than as scope that was always there.

1. **CI was red on a collection error nobody had read, 14–16 September 2026.** A test pinned
   a document the folder tidy moved (`docs/redesign/01-design-system.md` →
   `docs/design-system.md`), the whole default suite stopped collecting, and the browser job's
   green kept the summary line looking half right. Fixed with the path and with
   `tests/test_pinned_paths.py`, which asserts every documentation path a test pins exists.
   The lesson is §2.11's: a build nobody reads is a build that is not run.
2. **The audit's run corpus does not survive a container.** The 6 jobs, 3,417 calculations
   and 832 artefacts every "£0 re-render" proof in the delivery plan rests on lived in the
   audit session's container, which was reclaimed; no `aer backup` was taken. What is committed
   is the run exports, the rendered documents and every gate payload — enough to read, not to
   replay. The corpus is re-seeded once (three runs, ~£23) in Phase 1½ and backed up at once,
   and Phase 1.8's backup-and-restore exercise is no longer optional hygiene.
3. **The journey harness's first measurement, 16 September 2026.** Built red as §2.11 asks,
   and the red is wider than §2.11 counted: the console prints the workflow's step keys on
   every run, so every constructible stopped state fails the vocabulary assertion, not only
   the gate pages with their 135 identifiers; the two budget ceilings name their remedy in a
   sentence whose only control is a bare *settings* link; and the failed step's remedy is
   stated with nothing leading to it. Phase 1.4's ratchet therefore starts at the console.
   Narrower than expected in one place: the three gates whose approval is checked against the
   live payload (assumptions, peers, themes) have no seal to drift, so F-16's seal-drift case
   exists only at the plan and final gates, which is where Phase 1.2's re-seal control
   belongs. The measured lists are the record (`tests/journey_inventory.py`).
4. **A stored calculation's `name` is the name of its *function*, not of its figure, 17
   September 2026.** Found by measuring ADR 0125's cross-section check against the committed
   records before letting it record anything. `days_outstanding` is one traced function serving
   days sales outstanding, days inventory outstanding and days payable outstanding, so three
   genuinely different figures share one `calculations.name` and are told apart only by their
   inputs — on the MSFT #2 record they read 115.2, 90.6 and 3.9 days for FY2025. A discount
   factor is the same shape in the other direction: its year is a *parameter*, not an input, so
   ten forecast years are ten rows with one name, one absent period and identical inputs.
   Neither is a defect in the ledger, which records exactly what it should; both are a trap for
   anything that reads a run *by name*. The consistency check is fixed by keying on
   `aer.calc.engine`'s own identity minus the output. **What has not been checked** is every
   other by-name reader — the footnote resolver, the evidence index the adversary is handed,
   the report's calculation appendix — and a reader that groups by name alone will show a
   person three figures under one heading. A number here because it is a finding about the
   platform rather than about the check that found it.
5. **The eight false AstraZeneca challenges quoted nothing false, 17 September 2026.** ADR
   0115 argued that a challenge asserting a value the record contradicts should be dropped
   before drafting, and called that "the direct fix" for them. Measured: **33 of 33** numerals
   across the three severity-4-and-5 challenges are real recorded values, resolved by
   `reads_as` against that run's own ledger. The record refuted nothing, so the rule would
   have dropped none of them. What they actually do is quote FY2025 *and* FY2021 of one
   figure — interest cover 8.11 beside 0.812, gross margin 81.9 beside 0.668, return on
   invested capital 15.6 beside 0.0823 — and call the pair a contradiction. **The adversary
   was doing by hand what `services/consistency.py` forbids the platform's own document**,
   because the pack it was handed carried one name at several periods with no labels; Phase
   3.1 closed that at source. Recorded here because it is a fact about how this platform's
   adversary fails, and because it is the second plan-level claim this programme has had to
   correct by reading rows instead of prose.
6. **A test stub that returns a shape the real client cannot, 17 September 2026.** The price
   stub in `tests/test_price_acquisition.py` generated a monthly ladder running past the run's
   as-of date; the real adapter drops exactly those rows and counts them in
   `discarded_after_as_of` (`sources/eodhd/client.py`). Every assertion in that module was
   therefore made against a response shape production never produces — and when a re-run test
   was finally written, the stub's own surplus rows crashed it on `uq_price_bars_day` rather
   than exercising the bug it was written for. The stub now filters as the adapter does.
   Recorded because the failure mode generalises: a stub looser than the thing it stands in
   for does not merely fail to catch bugs, it manufactures ones that cannot happen, and both
   cost the same to debug. Worth a sweep of the other adapter stubs; not done here.
7. **Reading another step's output without declaring the dependency fails silently, 17
   September 2026.** `context.outputs.get(SOME_STEP, {})` returns an empty mapping for a step
   that has not run, and the engine places an undeclared node wherever it likes: a step that
   reads a sibling's output without naming it in `needs` gets a correct answer whenever the
   ordering happens to favour it and an empty one otherwise. Found while wiring the market
   capitalisation into the WACC (Phase 4.2) — `acquire_prices` and the assumptions chain both
   descend from `gate_unmapped_concepts` and neither waited for the other, so the discount
   rate's *basis* would have depended on which finished first, with nothing in the report
   saying which. The dependency is now declared, and `_comps` had always declared its own.
   **What does not exist is the guard**: `StepContext.output_of` could refuse a key the
   running step did not declare, which would make the whole class unrepresentable rather than
   something each author has to remember. Not built here; it touches every step.
8. **A step's record kept the figure and threw away the arithmetic behind it, 17 September
   2026.** `CompsOutcome.as_dict` recorded each multiple's key, label, value and reason and
   not the calculation that produced it, though `@traced` had already put that id on the
   quantity — so the record's own reader said, in a docstring, that "the ledger holds the
   calculation behind it, and the record does not carry that id". The report could therefore
   have printed MSFT's 27.6× P/E only with a footnote naming the *step*, which resolves to no
   `calculations` row and renders as the document's own broken-citation warning against a
   figure that is perfectly sound. One field fixed it. **The class is what is worth the
   number**: a step output is the only thing a re-render sees, so anything it drops is
   permanently unrecoverable for every run already recorded — and this one also dropped
   `MultipleResult.missing`, which is the whole distinction between a figure nobody filed and
   a figure whose denominator makes it meaningless, so every absence a stored record could
   describe came back as the second kind. Neither loss was visible while nothing read the
   record. **Not swept**: the other step serialisers were not audited for the same shape.
9. **Phase 4's exit criterion assumes every item is render-only, 17 September 2026.** It
   reads "re-render MSFT #1 and AZN #2 from stored rows", and 4.1, 4.2 and 4.3 each change
   what a *step* records rather than how a record is drawn — a second run's bar count, the
   capital structure the WACC weighs, the calculation id beside a multiple. None of the three
   can be proved by re-rendering a run seeded before it, and 4.3's is the sharpest case: a
   stored run re-rendered today states, correctly, that its multiples cannot be cited. The
   criterion is not wrong so much as split, and the split is worth naming before Phase 4's
   exit is claimed: what the render path does is provable on the corpus, what a step records
   is provable only on a run made after the change. The offline full-run fixture is where the
   second half is proved for £0, and Phase 7's verdict round is where it is proved on real
   filings.
10. **The journey harness could not run on a subscribed machine, 17 September 2026.** Its
    worker advances a run through the interim gates to reach the final one, bounded at four
    iterations because "the conditional gates are few". Four is the gate count of a machine
    with **no market-data key**: plan, theme set, assumptions, then the final gate on the
    fourth pass. Configure a key and the peer-set gate joins them — ADR 0059's second
    amendment proposes peers only where a peer's multiple could be computed — so the loop
    ran out exactly one gate short, and all four `gate.FINAL.*` rows and all three
    `budget.*` rows came back "no path constructed" rather than measured. CI has no key and
    has been green on this step throughout; the operator's own machine, which is the machine
    the platform is built for, could not run the shape half at all. The bound is now six.
    **The class**: a harness constant sized to the environment the harness usually runs in
    is a harness that stops measuring the moment the environment it was built for is
    configured properly — and it fails by reporting *nothing*, which reads like silence
    rather than like red. Found by running the instrument rather than by reading it, which
    is the third time in this programme that has been the difference.
11. **Every audited report said no comparable figure was computed, over figures it had, 17
    September 2026.** The comparables disclosure has two branches, and the one the audit
    never looked at is the one every run took. ADR 0059 acquires no peer's prices, so every
    confirmed peer is excluded and the report prints: "every one of the eight proposed peers
    was excluded … **No comparable figure was computed**, and there is no fuller version
    elsewhere." Measured across the eight committed run exports: **every one holds at least
    one of the subject's own multiples behind that sentence** — MSFT #2 four of them
    (EV/EBITDA 19.0×, EV/Sales 11.1×, P/E 27.6×, P/B 8.3×), its re-run two, and AZN and M&T
    the P/E each. The sentence is defensible as a
    statement about *peer* comparables and is not how a reader takes it, and the delivery
    plan's own diagnosis had recorded the licence as the reason the figures were missing,
    which was true of the other branch and not of this one. Fixed in §4.3: the paragraph
    states the peer position, the table prints what was computed. **The class, and it
    generalises past comps**: a disclosure written for one state is the cheapest thing in a
    codebase to reuse for a neighbouring state, and reuse is invisible — the second state
    never gets a sentence of its own, so nothing reads wrong until somebody checks the
    figures it is standing in front of. Worth noting too that ADR 0125's denial scan could
    not have caught this: it reads model-written sections, and the comparables block is
    written by the platform.

12. **The price step computed a price, used it, and threw it away, 18 September 2026.**
    `_market_capitalisation` took the close, multiplied it by the share count, recorded the
    product and discarded the multiplicand — so a company whose share count this build
    cannot map held *no price either*, though the two fail for different reasons and an
    implied upside needs only the price. The peer path had kept both since it was written;
    the subject path had not, and nothing noticed because nothing downstream wanted a
    price until ADR 0117's composed half did. Beside it, the capitalisation's recorded
    source was the literal string `"market_capitalisation"`: the reader looked for a
    `security_id` the record has never carried and fell back to a label that is not an id,
    resolves to no row, and would have rendered as the document's own broken-citation
    warning the moment anything footnoted it. **The class is §3.19.8's, twice**, and what
    is new is the second half of it: recording *where* a figure came from is not enough if
    the kind is assumed. A capitalisation is struck in the ledger and a dollar-quoted close
    is a stored fact; recording both as calculations would have reproduced the defect one
    layer along, invisibly, because a uuid-shaped id that resolves to nothing looks exactly
    like one that resolves.
13. **No run this platform has ever made had a scenario, 18 September 2026.** The
    `scenarios` table is empty across the whole stored corpus, and 180 of the 186 per-share
    rows are sensitivity cells tagged `case: "sensitivity"`. ADR 0117's composed half lists
    "the scenario spread — bear and bull, as figures" as one of its four parts, so on
    today's corpus the block a judge will read has three: a range, a distance and the
    levers. Not a defect — a scenario is the operator's to define and none has been —
    but it is a fact about what the composed half will be judged on, and the measurement
    round should know it before it runs rather than after. It also nearly became a defect:
    the composer's first draft read untagged rows as cases, and had the cells been untagged
    it would have printed ninety "scenarios" per method.

14. **Fixing the `WHERE` clause would not have fed a bank's segment section, 18 September
    2026.** ADR 0118 diagnoses the segment failure as one exclusion in `visible_facts`, and
    it is right about that; what it does not say is that the exclusion is not the only thing
    between the store and the section. The evidence pack ranks from a pool of the newest
    four hundred rows by period, and a filer's segmentation is an *annual* disclosure
    competing against every quarter since. Measured: M&T's 182 dimensioned facts begin at
    rank 930 of its 23,416, so the carve-out alone would have delivered the bank exactly
    what the blanket exclusion did. Fixed in §4.5 by drawing the breakdown as a pool of its
    own and fetching the consolidated line for the spans it covers. **The class**: a cap
    written as a bound on cost is a *selector* whenever the thing it cuts is rarer than the
    thing it keeps — which is gap A39's lesson a second time, now about periodicity rather
    than the alphabet. A39 was found by reading a report; this was found by counting rows
    before writing the code, which is the only reason it did not ship.
15. **The segment and revenue exhibits emptied on every second run of a company, 18
    September 2026.** Both inputs reached their facts by joining to the run's *own* source
    documents. The store deduplicates a fact it already holds under ADR 0058's identity
    rule, so a second run re-fetches the filing and the rows keep the *first* run's
    `source_document_id`: the join then matches nothing. Measured on the stored corpus —
    Microsoft's second run reached 0 of the 55 segment rows and 0 of the consolidated
    revenue rows the store held for it. It did not show as a blank chart, because
    `RevenueMarginInput.is_empty` was satisfied by the margins alone: the exhibit rendered
    an empty bar area under one margin line, titled "Revenue and margin history" and
    captioned *"Every bar and point is a stored figure."* Fixed in §4.5 by scoping both at
    company grain through `visible_facts`, and by making a revenue chart with no revenue
    empty. **The class**: `job_id` reads as the safe scope because it is the narrowest one,
    and it is wrong precisely where deduplication works — the better a store is at not
    storing a fact twice, the more often the second run's join finds nothing. ADR 0061
    already decided this for evidence and the exhibits were never brought across. It had a
    second cost worth naming: two test scenes — `tests/test_exhibits.py` and
    `tests/provenance_fixtures.py` — never set `request.company_id`, and their charts drew
    anyway, because the `job_id` join did not need the subject resolved. A scope that works
    without the thing it is supposed to be scoped by lets a fixture model a run that cannot
    exist, and the fixture then guards nothing.
16. **A geography axis is not a partition, and the subtotal check stops at twelve members,
    18 September 2026.** `_without_subtotals` recognises an aggregate by arithmetic, which
    is right, but declines entirely above `_SUBTOTAL_MEMBER_LIMIT = 12` because it
    enumerates subsets. AstraZeneca tags 18 members on `ifrs-full:GeographicalAreasAxis`,
    so the check never ran, and the exhibit draws *Outside United Kingdom* (54.4bn) beside
    *United States* (24.0bn) and *United Kingdom* (4.36bn) as if they were parts of one
    whole — three granularities on one axis. Pre-existing, and made legible rather than
    caused by §4.5, which printed the values onto the bars. **The fix is identified**: both
    overlaps are exact subset sums (54,380 = 27,557 + 13,455 + 13,368; 27,557 = 23,970 +
    2,633 + 954), so raising the bound would catch them — it needs a subset-sum reachability
    pass in place of `itertools.combinations`, and a decision about what the check does when
    it cannot decide. Left for the backlog's own
    `segments-guidance/segment-axis-preference-is-us-gaap-only` row rather than folded into
    §4.5. **The class**: a guard that declines silently above a threshold is indistinguishable
    from a guard that passed, and the threshold was set from what US filers do.

17. **`presentation_integrity` is red on half the stored corpus, and was before §4.6, 18
    September 2026.** Measured while checking that naming the peers cost the register
    nothing: re-rendering the four stored runs and scoring each gives Microsoft #1 one
    defect and M&T four, all of them `unformatted integer` — eight-digit runs inside
    *model-written* section prose, not in anything the platform composes. AstraZeneca and
    Microsoft #2 are clean. This matters beyond the two runs: several rows in the delivery
    plan name "`presentation_integrity` still green" as an acceptance criterion, and on
    today's corpus that criterion cannot be met by leaving the metric alone. The right
    reading is that it measures the *document*, model prose included, and a check whose
    baseline is red tells a later change nothing. **The class**: an acceptance criterion
    phrased as "still green" assumes a green it never verified, which is §3.19.1 in a
    different costume — a signal nobody had read.

18. **The endpoint the plan named for §4.7 does not carry what the plan said, 18 September
    2026.** The audit's recommendation, repeated into ADR 0126's first draft, was to read an
    accession's `index.json` and select the documents whose *type* begins `EX-99`. Fetching
    one settles it: that field is an icon name — `text.gif`, `compressed.gif`, `image2.gif`
    — and the string `EX-99` appears nowhere in the response. A rule written against it
    would have selected on a picture of a file, and the only other route from that endpoint
    is inferring the type from `dex991` or `ex99_1`, which is the guessing `core/concepts.py`
    refuses for XBRL tags. The real type is in `{accession}-index-headers.html`, EDGAR's own
    dissemination header. **The class is the handover's own §7**: every error in this
    programme has been a plan describing something nobody had read. What made this one cheap
    is that the sandbox can now reach `sec.gov`, so the endpoint was read before the code was
    written and two *recorded* fixtures replaced two that would have been constructed around
    the assumption — `tests/fixtures/sec/README.md` had said for a year that the fixtures
    were hand-written because the sandbox could not reach EDGAR, and that is no longer true.
19. **A worker had already fetched the folder and could not open it, 18 September 2026.**
    Three archive URLs in the store came from a research worker guessing at paths, all at
    tier 5 because they arrived through `fetch_known_url` rather than an index. Two are
    EDGAR's 404 page at 307 and 326 bytes. The third is 17,901 bytes — the **directory
    listing of the exact accession** whose Exhibit 99.1 the console's note was built from —
    fetched, hashed, stored, and extracted into *zero* excerpts. The worker found the door
    and the platform had nothing that could read it. Fixed by §4.7 at the deterministic
    layer, where it belongs. **The class**: when a model starts improvising around a
    structural gap, the improvisation is the measurement — it says exactly which door is
    shut, and it is cheaper to read than the report that came out of it.
20. **A footnote is about a document, and there are only four or five of them, 18 September
    2026.** §4.8 was written as "print the passage behind a footnote", which reads as one
    passage per marker. Three of the exported reports were counted and they carry **22 to
    37 source markers each, resolving to four or five distinct documents** — Microsoft's
    second run, 37 markers over 4 URLs — and the database says it from the other end: one
    run holds 75 verified citations over 3 documents. Printing per marker would have put
    the same four paragraphs in the notes seven to nine times, so the passage prints once
    per document and the document's later markers name the note that carries it. The
    selection rule came out of the same numbers: the most-leaned-on passage carries 4 to 12
    claims, where "first by row order" would have been arbitrary. Cost
    measured rather than estimated — 1.5 to 7.7 KB on a report of about 100 KB, and all
    four runs' cited documents pass all three of ADR 0119's gates. **The class**: a feature
    phrased against the reader's gesture ("following a footnote") silently assumes a
    cardinality, and the corpus is where the cardinality actually is.
21. **A listed UK company's filed accounts are a PDF, and ADR 0121's fact path assumes
    inline XBRL, 18 September 2026.** The ADR's load-bearing sentence is that "a UK filer's
    numbers exist only inside its accounts, as inline XBRL, one accounting period at a
    time", and `CompaniesHouseClient.fetch_facts` is built on it: four filings deep, arelle
    over each. Asking the register settles it. The newest three accounts filings of **Tesco
    (00445790), Barclays (00048839), AstraZeneca (02723534) and Greggs (00502851)** — twelve
    filings across the FTSE 100 and 250, two sectors and four filing agents — are every one
    of them `paper_filed: true` with a single resource, `application/pdf`, 8 to 36 MB. Not
    one offers inline XBRL. A UK acquisition as specified would fetch ~60 MB of scanned
    annual reports per run and extract **no facts at all**, four times logging a document it
    could not read. Inline XBRL at this register is real and it belongs to the companies
    nobody researches: four small active companies, picked out of a name search, all offer
    `application/xhtml+xml` beside the PDF and all are `paper_filed: false` — filed through
    accounting software, which is what produces the tagged copy. `fetch_facts` is not wrong;
    it is right about the wrong companies. (The endpoint content-negotiates and the client
    asks for neither type, which is a smaller fix worth making either way.)
    Phase 4a's exit — "every figure traced to its own accounts documents" — is therefore not
    reachable from this register, and the alternatives are a decision rather than a fix: the
    issuer's own ESEF report under `ISSUER_IR`, the licensed feed's fundamentals, a PDF
    statement parser (which would put arithmetic behind a heuristic), or reopening ADR 0022's
    refusal of the FCA's National Storage Mechanism with written consent. **The class is the
    handover's §7 again, and this time the plan and the ADR agreed with each other** — which
    is how a premise survives three documents without anyone asking the register.
22. **The register's own document endpoint redirects somewhere the fetch policy refuses, 18
    September 2026.** `document-api.company-information.service.gov.uk/document/{id}/content`
    answers 302 to a pre-signed `s3.eu-west-2.amazonaws.com` URL, and the fetcher checks the
    allowlist on every redirect hop — so `UrlNotAllowedError` on the first real UK document,
    where 32 offline tests pass because `respx` returns the body without the redirect. The
    fix is not a one-line allowlist entry: that host is every AWS customer's bucket in
    eu-west-2, so admitting it for `COMPANIES_HOUSE` admits any URL that redirects there
    under this platform's most trusted provider. What is wanted is narrower — a redirect
    admitted because of where it came *from* — and it is a security control, so it is the
    operator's to approve. **Approved and landed the same day as ADR 0127**, with the origin
    check, the one-hop property and the standing refusals all tested. **The class**: a mocked
    transport tests the parser and cannot test the policy, and the failures it hides are the
    ones that only appear in production — which is the theme of the next two items, both
    found by the first request that got through.
23. **The credential followed the redirect, 18 September 2026.** With the hop admitted, the
    Companies House API key went to Amazon S3 — which answered 400, `Only one auth mechanism
    allowed`, **quoting the `Authorization` header back in its error body**, which the fetcher
    hashed and archived as it archives every failure. The header had always been attached by
    provider, under a comment stating that a key for one publisher could never travel to
    another's host: true while every admitted host was one of the provider's own, false the
    moment one was not. A credential now goes only to a host on the provider's *standing*
    allowlist. **The class**: a safety property held by a coincidence of the data — here "the
    only hosts we fetch are the provider's own" — is a property nothing is testing, and it
    expires without a sound when the data changes. The comment asserting it was the only
    thing guarding it.
24. **The register serves two copies of a filing and hands over the wrong one, 18 September
    2026.** The document endpoint content-negotiates, and `fetch_facts` asked for nothing: a
    small company's accounts came back as a 20 KB PDF with no extractable text by default and
    as 19.6 KB of inline XBRL carrying **eight facts** when asked for by type. So the UK fact
    path would have reported every filing as untagged — including the ones that are tagged —
    and the sample in item 21 would have read as "no UK company files inline XBRL" rather
    than "listed companies do not". A filing with no tagged copy answers **406**, which is a
    better answer than the 14 MB scan behind the other representation: it settles "is this
    filing tagged?" in one round trip and no megabytes. **The class**: when a publisher offers
    a choice and the code expresses no preference, the default it gets is the one that suits
    the publisher, and nothing in the code says which one that was.
25. **No London-listed company files tagged accounts, and the issuer's own site is not a way
    round it, 18 September 2026.** Item 21 sampled four FTSE names; five more were added down
    to AIM — Cranswick, Chemring, Gamma, Judges Scientific, Nichols — and **not one of the
    nine** offers inline XBRL, Cranswick's being electronically filed and still untagged. The
    operator then chose the issuer's own ESEF report, on a recommendation made before anybody
    probed whether it could be fetched; probing it: three of five issuer sites (Tesco,
    AstraZeneca, Unilever) answer **403** to a User-Agent that identifies the operator and
    must not pretend to be a browser, and of the two that answer, Tesco's own results page
    serves 52 KB of HTML with **zero anchors** because the document list is built client-side.
    There is no web search in this platform to find a document URL with. So the operator's
    second decision is the one that landed, and it is better than either source: **check at
    commission time whether this run can succeed, and refuse it with the reason if not** —
    ADR 0128. **The class**: three sources, three different obstacles, and the thing worth
    building was not a fourth source but an honest answer at the door. Also the class of my
    own error — recommending the ESEF route before measuring it, which is the same mistake
    this list keeps recording about the plans.
26. **A UK figure is stated scaled, so the aggregate path's fact locator can never match it,
    18 September 2026.** The extract step records, beside each persisted figure, an excerpt
    located at that figure in the document it came from — which is what lets a numeric claim
    carry a citation the verifier re-reads. It was written for EDGAR's aggregate, where the
    value appears in the JSON verbatim. An inline accounts document presents its figures
    scaled: `198270` in the prose, tagged with a scale of three, stored as 198,270,000. A
    search of the extracted text for the stored value therefore finds nothing on **every** UK
    filing, by construction rather than by luck. The UK path records no fact-level excerpt
    and says why; what a claim cites there is the paragraph, already recorded when the
    document was acquired, which contains the figure as the company printed it. **The class**
    is a mechanism ported to a second source on the assumption that it means the same thing
    there — the same class as item 24, and the reason the test that found it asserts the
    presented figure is in the text and the stored one is not.
27. **A scene that kept passing while proving something else, and the third caller it found,
    18 September 2026.** `audit/smoke.py`'s second scene drove Tesco with the real EDGAR client
    and passed when the run reached `FAILED` — "because EDGAR's ticker list has no such
    company". After the dispatch, Tesco goes to Companies House, the scene carried no client
    for it, and the run failed on a missing credential instead: same verdict, different proof,
    and nothing would have said so. Fixing it surfaced the larger point. ADR 0128 put the
    pre-run check at the API route and the web page; the **audit driver is a third caller** and
    went straight to `start_run`, so the harness whose purpose is to meet what an operator
    meets could commission runs the product refuses at its own door — and that harness is the
    one that spends money. `drive` now checks, against the real registers unless a scene
    supplies its own, and the scene is offline and deterministic: the register answers from the
    recorded documents and the 406 that produced ADR 0128, and the verdict asserts no job
    exists and that the refusal names the filing. **The class**: a check placed in its callers
    rather than in the thing it guards is a check that a new caller silently opts out of.
28. **A failure no page expected reached the operator as JSON, 19 September 2026.** Found while
    settling a journey row rather than by meeting it: the row `problem.aer_error` had sat
    unconstructed since the harness landed, recorded as *"no route on the run's pages catches a
    bare `AerError`"*. Reading the routes to decide whether the branch was dead showed the
    recorded reason was exactly true — `web/pages.py` refuses the two failures it expects, a
    page that moved under a form and a rule the approval service refused, and catches nothing
    else — so **the row asserted nothing and is deleted**, which is what the harness's own rule
    prescribes for a branch nothing can reach. But the reading also showed where such a failure
    *did* go: the application's handler answered it with an RFC 9457 problem document whatever
    the caller asked for, so an `IntegrityError` or a `ConfigError` raised inside a page handler
    arrived in a browser window as JSON with nothing to press — a worse dead end than the one
    §2.11 spent a phase removing. A caller that says it renders HTML now gets the refusal page,
    with a way back to the run derived from the path, since the raising code did not expect to
    be there and cannot be asked to supply one. **The class**: a row that cannot be constructed
    is worth reading the code to settle rather than leaving on a list — the row was wrong and
    what it was pointing at was real.

29. **The escalation banner was written in the wrong language, 19 September 2026.** The last
    eight unconstructed journey rows were the §2.4 triggers at the final gate, and no test had
    ever rendered that state: a clean fake run fires no trigger, and the fake run is the only
    run the suite has. Constructing them found no defect in the platform's reasoning and a
    vocabulary leak on every surface the state touches. The console named the fired conditions
    as `low_source_coverage, material_missing_section` — *the one message written to tell an
    operator what had gone wrong with their run*. The review page's evidence named sections and
    metrics by key; a failed check listed `balance_sheet_liquidity/cagr#4`; the disagreements
    table printed `source_conflict · same_tier_same_date · escalated`; and the conflict
    ladder's own rationale said "both are T4_LICENSED_MARKET, both as_reported" — on the review
    page **and in the report's published appendix**, the section whose whole job is to make the
    rest of the report trustworthy. `TriggerKind`, `SourceTier` and `FactBasis` gained `spoken`,
    on the pattern `GateKind.spoken` and `ResolutionRule.spoken` already set, and the trigger
    scenes now carry a section's title, a metric's label and a skill's title so the pure engine
    words its evidence without importing a registry. **The class is §3.19.8's**: a surface
    nothing could reach is a surface nobody has read, and the ratchet Phase 1.4 ran could only
    ratchet what a test could render.

30. **The instrument had a hole the shape of a real identifier, and a tier code behind it, 19
    September 2026.** Found inside item 29. The harness's shouted-enum pattern required two
    leading letters, so `T4_LICENSED_MARKET` — letter, then digit — passed it, and every
    `SourceTier` value had been passing since the harness landed. Closing the hole turned
    **seventeen green rows red**, all on one word: the plan gate printed `T1_REGULATORY` in
    its planned-sources table, the review page printed it in the draft preview's footnotes,
    and `position_figure` printed it beside both sides of a conflict. The renderer's version
    is the one that matters most — *"Form 10-K, fiscal 2022, published 15 June 2022,
    retrieved 1 July 2022, tier T1_REGULATORY"* is a footnote in the **published report**,
    under a figure the reader is being asked to trust. `SourceTier.spoken` now says "a
    regulatory filing" there and on the four other surfaces that printed the code; the tier
    *number* is the ladder's ordering rule and stays on the row. The golden report fixtures
    moved with it.

    A second instrument fault, same session: a row met at a gate read only the console, so the
    trigger *summaries* were asserted clean while the evidence under them was not.

    **The class**: an assertion that passes for the wrong reason is worse than no assertion,
    because the second is visible. Seventeen rows had been reporting green on a page carrying
    a code identifier, and the ratchet Phase 1.4 ran could not have caught it.

31. **A cleanup could not empty a table it was half-preserving, 19 September 2026.** The
    journey row that enables a skill was the first test to run a skill through a real run and
    then reset. `tests/db_cleanup.py` preserved `section_definitions` whole, because migration
    0023 seeds the eighteen-section spine into it — but a run with an enabled custom-section
    skill writes its own row there too, with a `RESTRICT` reference to `skills`. So the sweep
    left that row behind and then failed on `DELETE FROM "skills"` with a foreign-key
    violation. The table is now visited in its proper place and emptied of everything that is
    not `origin = 'builtin'`, which is what a fresh database has. **The class**: "reference
    data" was a property of *rows*, recorded against a table.

    **And the rule lived in two places, one of them now wrong.** `tests/e2e/conftest.py` has
    a reset of its own — a hand-maintained `TRUNCATE` list plus three deletes, the one reset
    in the suite that still truncates — and it restated the skill half of the same predicate.
    So it was missing the probe half the moment a row seeded one: the *browser* half of the
    journey harness, which had been green on the shape half, met a unique-key violation on
    the second row to seed the probe and, by the third, a review page naming both. It now
    reads `PARTLY_SEEDED` rather than repeating it. That reset is the remaining instance of
    the hand-maintained-`TRUNCATE` backlog (Phase 1.1c), and its own comment already argued
    for replacing it with `delete_all`; this is the second time the duplication has cost
    something. **And the wider class**: the two halves of the harness share their builders
    on purpose, so that they cannot drift into two ideas of what a way forward is — but they
    do not share their *scene reset*, and that is where they drifted.

32. **September's judges were told they were reading blind, and were not, 19 September 2026.**
    Found while building row 7's blinding as code, and measured over the recorded texts rather
    than argued. The platform's MSFT report carries 316 Markdown footnote markers and the
    console note carries none; the console note carries 451 `[S12]` sources and the report
    carries none; the console numbers its sections and the eighteen-section spine never does;
    the report carries a six-line header block and a standing disclaimer the console has no
    equivalent of; the console says "my calculation" thirty-nine times and the report never
    says "my". **And all three console notes open with the assistant's own working** — *"I'll
    research this thoroughly before writing. Let me begin with the primary sources."* — two of
    them running straight into the title with no newline between.

    Every one of those separates the two sides completely, and a judge needed one. So the
    September comparison was between two documents whose authorship was legible in the first
    line, and **ISSUE 2's number inherits that caveat whatever Phase 5 finds**. It is not a
    reason to discard September: the judges' *reasons* are detailed, specific and mostly about
    substance, and the pre-registration already decided in advance that a high identity-guess
    rate attaches a caveat rather than voiding a comparison. It is a reason the round must be
    blinded properly and must record the rate, which is what row 7 is.

    `audit/judges/blinding.py` takes both sides through one shape and
    `tests/test_blinding.py` asserts no tell survives on any of the six documents. **The class**:
    a control nobody measured is a control nobody has. The panel was *described* as blind in
    its own system prompt, and the description was the only thing holding it up.

33. **A tell that fires on both sides is not a tell, 19 September 2026.** Found inside item 32,
    by the test written to hold the tells honest rather than by reading them. §3.5 names *"the
    word 'as at'"* as a platform-ism to remove, and it is — the renderer says it — but the AZN
    console note says it twice as well, once inside a source's own title. So it distinguishes
    nothing, and a blinding test that counted it as evidence of authorship would have been
    reporting a leak where there was a shared habit. It is still normalised on both sides,
    because the strategy is right that the habit is worth losing; what it is not is a tell.

    Two smaller ones from the same test: the AZN console note cites through Markdown links and
    a numbered table rather than inline markers, so "every document renumbers its markers from
    one" was wrong for a citation style it never used; and `AGREEMENT_KEYS` was written off a
    truncated print and was missing three of its ten keys, which the both-directions key diff
    named on its first run. **The class**: a constant derived from a partial read of a record
    is a guess wearing the record's authority, and the cheap defence is to diff against the
    record rather than to read it carefully.

34. **The two documents cannot be blinded to each other, and the reason is not presentation,
    19 September 2026.** The live half of §5's row 7: fifteen judge reads over the blinded
    pairs, across three rounds, **fifteen correct and every one stated as *certain*.** £4.27.
    The measurement is in [`phase-5-gate.md`](phase-5-gate.md); what it found splits in two.

    *The instrument had been built by eye.* The tell list read the documents' surface —
    markers, headings, the header block, the disclaimer — and never looked inside the prose,
    where the renderer prints the machinery that made each figure. Three judges out of three
    named `aer.calc.ratios:gross_margin` before anything else, and counting afterwards found
    367 code identifiers across the three reports against none in any console note, 248 dotted
    module paths against none, 135 shouted enum codes against none. Six tells went in. **The
    class is item 30's, one layer further in**: an instrument written from the examples its
    author was already looking at closes those examples. What broke it open was asking a
    reader rather than re-reading the list.

    *And then the rate did not move.* With every machine artefact gone the judges named the
    next thing down — the validator's scoreboard, the red-team log escalated for human
    decision, and, in every one of the fifteen reads as the second reason, the evidence base:
    one XBRL feed against thirty heterogeneous web sources. Those are the product, not its
    wrapping. **So row 7's "hit rate at chance" is a condition written before anyone had
    measured the thing it was about, and it cannot be met at any price** — the same shape as
    row 6. The pre-registration, hashed before any of this ran, already decided what a high
    rate means: a caveat on the six comparisons, never omitted, rather than a void.

    **The operator restated the row on the measurement, the same day and the same way as row
    6**: the blinding must remove the presentation and the rate must be *measured and
    recorded* rather than assumed, with the caveat attached above chance. The round proceeds,
    and its report leads with the finding rather than footnoting it — because **ISSUE 2's own
    question needs reading with care now**: a panel that knows which document is the
    platform's is a panel whose preference may follow identity. That was already true of
    September's number, unmeasured. It is now measured.

35. **The renderer prints a stored Decimal to a reader, 19 September 2026.** Found as the one
    thing the blinding refuses to fix: `beta quoted as 1.064553313698`, which a judge named
    unprompted, and `0E-8` in the validator's table where a reader expects zero. Removing it in
    the blinder would mean rewriting a digit, and a figure rounded wrongly is invisible in a way
    a mis-numbered marker is not — so the remedy is in the renderer. The same class as the
    `tier T1_REGULATORY` footnote in item 30, and it reaches the round's own documents.

    **Half fixed, and the half that was broken is the interesting one.** The validator's table
    in the *document* built its cells with `str(Decimal)` — while the review page beside it had
    been fixing the identical column through a `trimmed` filter since Phase 1.4, with a
    docstring explaining exactly why eight decimal places is the wrong thing to show a person.
    One rule, written twice, and the copy nobody looked at stayed wrong. It is now
    `aer.render.display.stored`, which the filter calls and the section builder calls: **the
    same duplication `568ed6d` removed from the test cleanup this morning, one layer up, and
    the second time in one day that a rule stated twice had one statement rot.**

    *What remained, and what it turned out to be.* `display.scalar`'s fallback for a
    dimensionless number whose label states no meaning passed the value through at full
    stored precision — deliberately, because guessing a unit is worse, but twelve decimal
    places is not the only alternative to guessing. **Closed on 19 September, and scanning
    the stored corpus first changed what the work was.** 909 of 5,128 recorded calculations
    reached that fallback, and 410 of them should never have got there: `_pure_reading`
    matches phrases a person writes — *value share*, *to* — against a label, and the caller
    was handing it a calculation *name*, so `terminal_value_share` matched none of them
    while the table cell beside it read `79.0%` off its heading. Underscores read as spaces
    now, which is one line and 410 rows.

    The 499 that genuinely state no meaning take a precision policy: **four significant
    figures or four decimal places, whichever says more.** One alone is wrong at each end of
    the range — four places make a covariance of `0.000542746561` read `0.0005`, four figures
    make an interest cover of `40.418322830829` read `40.42` — so the more generous of the
    two is taken. `beta quoted as 1.064553313698`, the figure a judge named unprompted, now
    reads `1.0646`.

36. **One question, two functions, and a discount rate the document had to apologise for,
    19 September 2026.** The first thing the Phase 5 round bought, and it was bought with
    evidence: all three AZN judges quoted the same sentence back at the platform — *"a 5.6%
    WACC built on book equity weights it admits makes every valuation discounted at it
    correspondingly too high"*.

    *The seam.* "How many shares are there" was answered in two places with two lists.
    `valuation_run.share_count` tries `diluted_`, then plain, then `basic_shares_outstanding`;
    `_filed_share_count`, which feeds the market capitalisation, tried **only** plain
    `shares_outstanding` and only undimensioned. AstraZeneca tags its cover-page count *per
    share class* — five dimensioned rows, no plain one — while its basic and diluted counts
    are undimensioned. So the same filings gave AZN a per-share value and no market
    capitalisation; with no capitalisation the capital structure fell back to book equity;
    and Phase 4.2, *prefer market equity in the WACC*, was never reached. MSFT, whose
    cover-page count is undimensioned, took the market path and came out at 9.70 %.

    *Measured, not asserted.* Book equity $48.7bn against a market capitalisation of
    $257.4bn on the round's own price moves the equity weight from 0.622 to 0.897 and the
    WACC from 5.62 % to 6.11 % — **49 basis points**. The first draft of this entry said four
    hundred, which was wrong by a factor of eight and is corrected here rather than quietly:
    the rest of AZN's low discount rate is its 0.27 beta, which is a different argument. What
    the fix removes is not mainly the basis points but **a document that discredits its own
    valuation in a footnote**, which is the part the judges actually quoted.

    *The class, for the fourth time in one day.* A rule stated twice, where one statement
    rotted: the test-cleanup predicate (`568ed6d`), the `trimmed` filter beside the document's
    own validator table, the display formatter's two doors, and now this. The two lists here
    still differ, because the two questions differ — a per-share value divides by the diluted
    count, a capitalisation multiplies by the shares that exist — but neither may now be empty
    where the other is full, and `tests/test_price_acquisition.py` holds that.

37. **The consistency check could not see the first thing a reader sees, 19 September 2026.**
    The round's second defect, and the sharper one, because the check that should have
    caught it was built for this exact case and shipped three days earlier.

    MSFT's Historical Financial Analysis said *"no cash flow line — neither operating cash
    flow nor capital expenditure — is present, so free cash flow cannot be stated at all"*,
    while **line 61**, the first table in the document, read `Free cash flow | FY2026 |
    $66,987m`. The judge reading it wrote that the document *"cannot be trusted on its own
    numbers"* and handed the comparison to the console.

    *Why ADR 0125's third pass missed it, traced rather than guessed.* The denial was
    detected perfectly — `denies=True`, names `free_cash_flow`, periods overlap, no numeral
    escape, the section is not platform-filled. It had **nothing to contradict**: the
    check's *published* set is built from `report_sections` rows — a claim naming a figure,
    or a section figure row carrying its id — and the at-a-glance block is assembled by
    `aer.render.glance` at render time from stored rows. It is never a section. So the
    document's front page publishes seven curated figures that the validate-step check
    cannot see, and a section may freely deny any of them.

    *Measured on the round's own run, read-only so the finished record stayed finished*:
    sections only, 1 denial; with the front page, 2 — the second being `free_cash_flow`
    denied in Historical Financial Analysis. The platform would have raised, at its own
    validate step and for £0, the defect a judge raised about it after £7.06.

    *The fix asks the renderer instead of restating it.* `_published_ids` gains the front
    page as a third channel, via `glance_content` — a `services` module importing a
    `render` one, which is the wrong direction and still the right call: a second copy of
    the curated seven in `consistency.py` would be **the fifth instance in one day** of one
    rule written twice with one copy rotting.

    *The precision half, closed the same day.* The same run produced a **false positive**:
    *"Absent that comparison, the capital allocation posture … with capital expenditure
    roughly five times the combined shareholder distribution"* was logged as denying capital
    expenditure, in a clause that states it is five times the shareholder distribution.

    The cause is one word in two lists. `absent` is a negator **and** a word about the
    record, so `denial_span` took its self-negating branch — written for *"the evidence is
    silent on interest cover"*, where the subject follows the word and nothing stands
    between them — and carried the span to the end of the clause, sweeping up a positive
    statement twenty words later. What the negation governs is *that comparison*, and what
    says so is the comma: an absolute phrase opening a clause is closed by one. The branch
    stops there now, and only when the word **opens** the clause, because "the evidence is
    silent on…" has no comma anywhere.

    A comma cannot end a span in general, and msft1's own sentence is why — *"No discounted
    cash flow, cost of equity, weighted average cost of capital, terminal value, value per
    share or peer multiple sits on this record"* is an enumeration held together by commas,
    and ending at the first would drop five of its six figures. Its negator is not a word
    about the record, so the rule cannot reach it, and a test says so.

    *Measured on the round's own MSFT run, read-only both ways:* **2 denials before, 1
    after** — the false positive gone, the true one (free cash flow denied and printed)
    standing, because that one is caught by `opens_with` rather than by the span.

38. **The two terminal methods contradicted each other and the document called it a width,
    19 September 2026.** The measurement round's largest finding, and the one all three AZN
    judges led with: *"its two terminal methods produce $357.62 versus $158.58"*. MSFT's pair
    was 2.9x apart and its reader wrote that they *"do not bracket a range; they disagree"*.

    *Three defects, one cause — the platform had no figure for the distance.*

    **The caveat knew a threshold and nothing else.** `METHOD_DISAGREEMENT` is 0.25, and past
    it the run said *"The two terminal methods disagree by more than a quarter. That is
    information, not an error … the distance between them is the honest width of the answer."*
    AZN's pair was 125% apart. The sentence was true, useless and reassuring, in that order,
    and a caveat that reassures a reader about a contradiction is the platform arguing for its
    own output. Banded: `METHOD_CONTRADICTION` at 1.00 — where the lower figure is less than
    half the higher and no single view holds both — takes a different sentence, which says
    they contradict and points at the parameter that separates them.

    **The distance was not a figure, on a rule that was right.** `_terminal_rows` had left it
    out because *"a figure needs a recorded calculation, and the comparison already exists as
    words"*. So every reader of that document did the division themselves, which is exactly
    what the judges did. The answer was to satisfy the rule rather than stay silent:
    `aer.calc.dcf.method_disagreement` is a traced calculation now, and it and the exit
    multiple's already-recorded implied perpetual growth are both rows in the method table
    with their own lineage. An unexplained 2.25x is something a reader can only distrust; the
    same gap beside *the exit multiple implies -1.97% perpetual growth, the perpetuity was
    given 2.5%* is something they can argue with.

    **And the front page printed one of the two as though it were the answer** — the sharpest
    form of it, found while tracing the first two. `aer.render.glance` curates
    `value_per_share` and took the *last* base-case row of each curated name. A discounted
    cash flow strikes it twice, both stamped `case="base"` because both are the base case, so
    a `reversed()` decided which of two contradictory numbers led the document. AZN's line 61
    read `Value per share (base) | — | $158.58` over the $357.62 on line 457, with nothing
    naming the method. The picker now takes the last row *per terminal method* and labels each
    where there is more than one; a name struck once is unlabelled, because a discriminator on
    a single row invites a reader to look for the row beside it.

    *Nothing asserted the front page's per-share row*, which is why this shipped. Three tests
    do now, one of them checked against the unfixed picker.

    *Two smaller things found inside it.* `implied_terminal_growth` and
    `implied_exit_multiple` carried no `case` stamp, so a grid's fifty and the base case's one
    were told apart by ledger order — tolerable while nobody printed them, not once one is a
    row a reader meets. And `TerminalMethod`'s two words were written out in three places;
    they are `TerminalMethod.spoken` now, on the pattern of `TriggerKind.spoken`. **The class
    is §3.19's recurring one**, for the sixth and seventh time in two days.

    *What this did not do.* ADR 0125's second pass still does not compare the two, and should
    not: it groups by function *and inputs*, and two terminal methods genuinely are two
    questions. A contradiction between them is not one figure published twice.

39. **The report said no market prices were used, in a document whose header measured
    against one, 19 September 2026.** The round's second AZN defect, and the one a reader
    needs no expertise to catch: line 22 of that document is a section headed *Against the
    market price*, stating a 115.3% upside on the perpetuity value and −4.5% on the exit
    multiple, both struck against a close of $166.08. Line 424 says the capital was weighted
    at *"book values from the filed balance sheet — no market prices were used"*, and lines
    437 and 438 each append *"(book values — this run holds no market prices)"*.

    *The claim was about the weights and was written as a claim about the run.* The equity
    weight fell back to book because no market capitalisation could be formed — §3.19.36's
    share-count defect, fixed the same day — not because the run held no price. The price
    step had one, and the header used it. `valuation_method` sees a rendered block and the
    value step's record; it was never in a position to say what the whole run holds, and the
    caveats below the table already said the true thing, which is that no *capitalisation*
    was available.

    *Fixed by saying what is true of the weights.* The note says book or market; the weight
    rows carry the basis in the **label** — `Equity weight, at book value` — rather than in a
    parenthesis, because two things read it: a reader, and the commentary guard.

    **And Phase 4.2 had left that guard refusing something the platform now does.** The WACC
    prefers market equity since 4.2, so on a market-weighted run the equity weight *is* a
    market capitalisation — while `_NEVER_HELD` still refused any commentary naming one, as
    *"a methodology the run never executed"*. A writer describing the run correctly was
    refused, and the refusal's own message (*"this run holds no such input — no prices, no
    traded debt, no return series"*) was the same falsehood a third time. The market terms
    moved to the block-conditional list, keyed on the new label, so they are admitted exactly
    where the block shows them; `_NEVER_HELD` keeps only what no discount rate in this build
    is ever computed from — traded debt, a per-share quote, a return regression — and its
    message now describes the block it actually checks.

    *The class.* A guard written against one state of the code and not revisited when the
    code gained the capability it was guarding against. Distinct from §3.19's recurring
    duplicate-rule class, and worth watching for separately: every refusal in this tree is a
    claim about what the platform cannot do, and each one ages.

40. **The figure and its unit travelled in separate fields, and three renderers joined
    them, 19 September 2026.** MSFT's only failing validator in the measurement round, and
    the check named it exactly: *unformatted integer `66987000000`* and *unformatted integer
    `19359000000`*, where a reader should have seen `$66,987m` and `$19,359m`.

    *Where they were.* Not in a section's prose — in the **calculation provenance
    footnotes**. Line 710 of that document read ``Calculated: `free cash flow = operating
    cash flow - capital expenditure` = 66987000000 USD for FY2026``, four rows under a table
    cell reading `$66,987m`. The same figure, twice, in two notations, with only one of them
    chosen.

    *Why.* `CalculationFootnote` carried `value` and `unit` as separate fields, and the
    footnote's formatter governed **precision only** — it trimmed twelve stored decimal
    places to four and had nothing to say about magnitude or currency, so an eleven-digit
    integer went through untouched. The two fields were then joined by
    ``" ".join(piece for piece in (footnote.value, footnote.unit) if piece)`` in *three*
    places — the Markdown note, the HTML note and the HTML hover text — none of which could
    apply the house style, because none of them had it. **The recurring §3.19 class again**,
    this time in triplicate.

    *Fixed by giving prose the door tables already had.* `aer.render.display.figure` puts a
    figure and its unit together for a sentence, reusing the same `_unit_reading` dispatch a
    table cell uses and differing from it in one decision: a unit the formatter cannot
    restate is **kept**, because a sentence has no column heading to say what the number is.
    The footnote now carries one formatted phrase and the three renderers print it.
    Verified read-only against the round's own MSFT ledger: `66987000000 USD` → `$67.0bn`,
    `19359000000 USD` → `$19.4bn`. Billions in prose and millions in a table are two
    registers of one house style (ADR 0056), and a footnote is prose.

    *The golden fixture had been recording the defect since it was recorded.*
    `tests/fixtures/fx_report/golden.md` carried ``= 0.18 ratio`` in a footnote whose own
    table cell said `18%`, byte-identical-asserted on every run. A fixture that pins output
    pins whatever the output was, and nobody had read that line as a contradiction.

    *Two things it was not.* The rounding marker — *(rounded; full precision stored)* — does
    not fire on `$67.0bn`, which has dropped eight significant figures, and that is correct:
    it answers the narrower question a decimal raises, *does this look more precise than it
    is?*, and a scaled billion does not. And the same change closed §3.19.35's open half,
    which is why that item now reads as finished.


41. **The journey harness is fragile at full-suite scale, 19 September 2026.** Not a
    product defect, and worth a number because the harness is ISSUE 1's measuring
    instrument and an instrument that fails for its own reasons reports noise.

    A full `tests/e2e` run failed one row —
    `test_every_stopped_state_offers_a_way_forward[chromium-gate.UNMAPPED_CONCEPTS.rejected]`
    — with `CancelledError: Task cancelled, timeout graceful shutdown exceeded` inside
    uvicorn's `listen_for_disconnect`, which is the console's event stream still open when
    the per-row server was torn down. It passes alone in nine seconds and passes with its
    whole module, 50 of 50, in five minutes; the failure did not reproduce at either scale.

    The design note that predicted it is the plan's own: *"each row starts its own live
    server, so the browser job grows by several minutes (a module-scoped server with the
    existing per-test `_reset` is the fallback)"*. The cost has arrived as flakiness rather
    than as minutes. The fallback is the fix and it is not urgent — but a row that fails for
    a reason unrelated to what it asserts is exactly what teaches an operator to read red as
    noise, which is the failure mode §2.4's own trigger note describes.

    *And a second finding, which is mine rather than the harness's.* The same suite first
    reported 10 failures and 15 errors, including a Postgres deadlock, because I left it
    running and started other suites against the same `AER_TEST_DATABASE_URL`. CLAUDE.md
    says one pytest process per database and this is what ignoring it looks like: 24 of 25
    results were fabricated by the contention, and the only way to tell was to run it again
    alone. A test run that shares a database is not a slower test run, it is a different and
    untrue one.


42. **A decomposition whose rows share one name is invisible to the writer's index, 19
    September 2026.** Found by measuring Phase 6.1's own wiring rather than assuming it
    landed: the producer is live and the consumer still cannot read it.

    `aer.services.calculations.indexed_calculations` keeps **one row per `(name, case)`**,
    newest period — the right rule for the ratio suite, where `gross_margin` is one figure
    a run strikes once per year. A margin bridge is the opposite shape: every component is
    a `bridge_contribution`, they share one name by construction, and *the set of them is
    the finding*. "Operating margin fell 240bp, of which 180 was gross margin and 90 was
    R&D" is the sentence the console won on, and it needs every row.

    *Measured on a two-year scene:* 11 bridge rows struck, **3 survive the index** — one
    `bridge_contribution` at `-0.1`, one `bridge_residual`, one `margin_of` — each with
    **empty parameters**, so a writer cannot tell which expense line the contribution
    belongs to, which of the two margins it decomposes, or that there were others.

    *The cause is the one `enterprise_value` documents against itself.* That calculation
    records `method` and `case` precisely so "the ledger holds two rows with the same name
    and different answers and nothing saying why" cannot happen; `contribution_of`,
    `residual_of` and `margin_of` record nothing of the kind. It is the same defect fixed
    for `implied_terminal_growth` in §3.19.38 earlier the same day, one module along.

    *Fixed, 20 September, and it was three changes rather than two.* Parameters on the four
    bridge calculations — the driver concept and the margin key; an index keyed on a row's
    recorded choices rather than on its name alone; and **the evidence unit carrying those
    choices**, which measuring turned up as mandatory rather than optional. The unit had
    held name, value, unit and period and nothing else, so widening the index alone would
    have handed a writer two `bridge_contribution` rows reading `-0.1` with nothing
    whatever to tell them apart — strictly worse than showing one.

    *What the widening costs, measured on all six stored runs:* 7 to 9 extra rows each
    (65→73, 70→78, 68→77, 37→44, 67→75, 71→80) against a cap of 120. Nothing is pushed
    out, and the extra rows are **the second terminal method's** — `enterprise_value`,
    `equity_value`, `value_per_share`, `implied_upside`. A writer had been shown one of
    two contradictory per-share figures with nothing saying which, which is §3.19.38's
    front-page defect one layer along, found because this item made it visible.

    *Gap R14's guard was narrowed, not relaxed, and the distinction is the point.* It keys
    on `(name, value, period)` and so forbade two `bridge_contribution` rows of equal value
    — but what R14 found was rows with *"nothing to say which of the two a citation
    meant"*, and cost of revenue's contribution to the gross margin and to the operating
    margin are two claims that happen to be equal, each saying which it is. The key gains
    the parameters. Checked on all three cases: R14's own defect still fails, the bridge's
    rows are allowed, and a bridge row repeating another down to its parameters still
    fails.

    *Also measured, and not a defect:* four of the operating-margin bridge's five drivers
    went `unattributed` because the opening year's statements did not carry them, and the
    two bridges' shared `cost_of_revenue` contribution collapsed to one ledger row because
    the inputs and the stamp were identical. Both are the machinery working.


43. **The 40-row keyhole is not the keyhole, and the flag that said so ranks nothing, 20
    September 2026.** The delivery plan's Phase 6 asks to *"widen the 40-row keyhole per
    section"* on the evidence that *"all 48 section executions across three logged runs
    reported `evidence_truncated: true`"*. Measured against the committed run records
    before changing anything, and **both halves of that are wrong**.

    *It is 98 of 98, not 48 of 48* — eight runs, not three. And **not one section execution
    ever reached the 40-row cap in any category**: facts run 7 to 31 with a median of 24,
    calculations 5 to 20, excerpts 0 to 7. `EVIDENCE_ITEM_CAP` bounds the *query*; what
    binds is the section's **token budget**, which is consulted afterwards. Raising the cap
    from 40 would have changed precisely nothing, and would have looked like progress.

    *What the budgets actually are.* The built-in sections carry 2,000 to 4,000 evidence
    tokens — `executive_summary`, the section a reader meets first, has the lowest at
    2,000 — against a configured per-section ceiling of 12,000. So the built-ins run at a
    sixth of what the platform already permits. **Raising them is a spend decision and is
    the operator's**, not a refactor: it is roughly £0.20 to £0.50 a run for a doubling, on
    a £7.50 run, derived from the round's own 1.32M tokens at £7.56.

    *What was £0 and is done.* `evidence_truncated` is a boolean, and it is `True` on all
    98 — so it separates nothing. A section that lost one low-ranked excerpt from the tail
    recorded exactly what a starved one did, and the readout printed the same sentence
    eighteen times a run. **A signal that is always on is one its reader learns to skip**,
    which is the argument `aer.core.escalation` makes about the trigger it deleted for
    precisely this reason. The pack now counts the units the budget refused, the count
    travels to the step record, the rehearsal, the dry run and the console, and the readout
    says *"3 more did not fit"* or *"everything gathered fitted"*.

    *Why that had to come first.* Without it, nobody can tell whether raising a budget
    helped: the before and after both read `truncated: true`. The measurement is the
    prerequisite for the spend decision, not a consolation for not having made it.

    *Also seen, and left alone:* at a small enough budget a section is dealt nothing at all
    — 0 facts, 0 calculations, 0 excerpts — and the run continues. One of the 98, a QUICK
    run's `executive_summary`, was dealt zero excerpts. `insufficient_evidence` is the flag
    for that and it is a different question from this one.


44. **A measurement whose resolution an unrelated change consumed, 20 September 2026.** The
    second instance of §3.19.41's class in two days, and worth its own number because the
    cause is different: 41 was an instrument failing for its own reasons, this is an
    instrument whose *resolution* another change quietly spent.

    `tests/test_draft_fanout.py` asserts `peak == DRAFT_FAN_OUT` — the section fan-out
    reaches four in flight, exactly. It measures that with a gauged provider that sleeps
    while a call is in flight, and four calls overlap only if the spread of their arrival
    times fits inside the sleep. The sleep was 50ms.

    Item 42 put seven to nine more rows in the evidence index and a `choices` key on every
    unit. The fan-out's semaphore is held across the **whole** section —
    `async with bound, factory() as session` at `vertical_slice_v1.py:3605` — so each task
    gathers its evidence *inside* the bound, before it ever reaches the provider. A longer
    gather widens the spread. On CI's loaded runner the peak read 3; on an idle machine it
    read 4, three times out of three.

    *Diagnosed rather than dismissed.* "Flaky on CI" was available and would have been
    wrong: there is a causal path from the change to the reading, and the reading was
    right — four calls no longer overlapped. The window is the instrument's resolution and
    the claim is `peak == DRAFT_FAN_OUT`, so the window widened to 250ms rather than the
    assertion weakening to `>= 3`. Verified at 38s idle and 52s with four CPU burners
    saturating the box, against 31s before.

    **The class, for the next person.** A test that measures a race by sleeping has a
    resolution, and nothing tells you when a change elsewhere has spent it — it reports as
    a flake, on the machine with the least slack. Where the sleep is the instrument, say so
    in the test and give the number a name.


45. **The monitor cannot watch what the view says would change it, 20 September 2026.**
    The delivery plan's Phase 6 asks to *"read `thesis_monitor.py` before wiring the view
    into it — one session. Half the product downstream of a stated view is undiagnosed."*
    Read, and measured against the real corpus rather than the prose.

    *The monitor is not the undiagnosed half.* 1,303 lines, 48 tests across twelve classes,
    and the whole loop is there: a pass per thesis, a step per premise, code measures and
    the model interprets inside the crossing code made (ADR 0103), findings never
    decisions, a contradicted premise opens the thesis gate, the gate withdraws or
    dismisses with a reason, every act appended and chained. A commit per premise so a
    pass that dies has still metered what it spent. A premise whose metric code cannot
    resolve costs **no model call** — `_measure` raises before the agent runs.

    *What is undiagnosed is the seam, and the measurement is stark.* **The five axes the
    stored sensitivity grids vary are `wacc`, `terminal_growth`, `exit_multiple`,
    `cost_of_equity` and `return_on_equity`. The monitor can read one of the five.** Across
    the eight stored grids: seven vary two axes it cannot read at all, and the eighth —
    M&T's `return_on_equity` against `cost_of_equity` — gets one of two. So *"what would
    change the view"*, which is the composed half's own phrase for its levers, names
    almost nothing the monitor can watch.

    That is not a defect in either piece. A lever is an **assumption**; the monitor reads
    **filed facts** and nothing else, because ADR 0079 says so. The defect is in ADR 0117's
    sentence *"The monitor gets its first premise for free"*, which reads as though the
    levers carry over and they do not. **A falsifier must be an observable.** Corrected in
    a dated section on the ADR.

    *And the seam has no door.* `Thesis.report_id` exists, `write_thesis` takes it, the
    form offers every approved report — and there are four entrances to writing a thesis
    (the theses page, the monitor's empty state, the decisions page, the tools menu) and
    **the report is not one of them**. The final gate's template does not contain the word.
    So the ADR's *"the seam closes at the moment the operator states a view, rather than in
    a separate sitting they may never have"* describes a sitting that is, today, separate.

    *Three traps a falsifier can fall into, all measured on the corpus and all silent until
    a pass has run:*
    - **A metric the resolver does not know** (`wacc`) reads `unobservable` on every pass,
      for ever. The form's hint says so honestly; nothing refuses it at write time, and
      `_last_reading` deliberately ignores unobservable readings, so the finding re-issues
      on every pass that sees a new filing. §3.19.43's class in a new place: a signal that
      is always on.
    - **A level carries a currency.** `free_cash_flow` resolves and measures — $11.8bn for
      AZN, $67.0bn for MSFT — and a threshold typed as a bare ratio raises a unit mismatch,
      so a perfectly sound falsifier reads unobservable because of the unit box.
    - **A bank cannot be asked most of them.** On M&T, `operating_margin`, `gross_margin`
      and `free_cash_flow` are all unmeasurable — the filing reports no `operating_income`,
      no `gross_profit`, no `capital_expenditure`. A falsifier that works on Microsoft is
      permanently unobservable on the bank, which is Phase 2's shape one layer along.

    *And the layer has never held a row.* Across the whole restored corpus: 4 approved
    reports, 12 sensitivity grids, 300 cells — and **0 theses, 0 premises, 0 judgements, 0
    findings**. Everything above is true of a subsystem that has only ever run in tests.
    Which is what the delivery plan's sentence was for, and why it asked for the read first.

    *What this does not do.* No wiring, no form, no door — those are F9's and Phase 6a's,
    and folding them in here is what the roadmap's own rule forbids. What lands is the
    diagnosis, the ADR correction, and three named traps for whoever builds the door.


46. **Every challenge in the corpus froze in the state an ADR says does not exist, 20
    September 2026.** Found while closing Phase 6 by checking which of the measurement
    round's four judge criticisms were still open. Two were: this is the first.

    ADR 0115's decision 4 is unambiguous: *"A challenge reaches a published report in
    exactly one of three states"* — accepted, rejected, or carried — and *"there is no
    fourth state. 'Escalated for human decision' is not a resolution; it is a note that the
    document was frozen in the middle of a conversation."*

    *Measured across the whole corpus:* **33 of 33 disagreements in four published,
    immutable reports are `escalated`, and `resolved_by` is `rule` on every one.** Not a
    single `chose_a`, `chose_b` or human settle exists anywhere. The state the ADR says
    does not exist is the only state any published report has ever used.

    *And it is what a judge marked the platform down for.* AZN's beta objection — the red
    team's own severity 4/5 finding that a beta of 0.274 gives a 5.62% WACC for a business
    the draft calls exposed to binary trial outcomes — is printed under **"Resolution: Open
    at approval: no rule settled this and nobody preferred either side, so both are
    published here and the reader decides."** The panel cited *"a beta of 0.27 that its own
    red team calls implausible"* against the platform.

    *The control is not missing and it is not hidden.* `settle_by_hand` exists, refuses an
    empty rationale, and the review page renders a Settle form per unresolved challenge
    *"in reach rather than behind a disclosure"* with the brief's lean marked. It was
    offered 33 times and used 0. Both round runs were then published by operator override.

    *So what is missing is the precondition, not the button.* Nothing refuses to freeze.
    ADR 0115's status line says decision 4 *"has landed"*, and what landed is its appendix
    half — the appendix is assembled after the settle and honestly reports the non-answer.
    The rule itself, *the document does not freeze until somebody has settled*, is enforced
    nowhere.

    *Deliberately not fixed here, and the reason is ADR 0018's.* A freeze that refuses on
    an open challenge is a **new blocking check**, and this repository's rule is that every
    one lands advisory at gate 2 before it blocks, with a written override. It would also
    have stopped both of the round's runs from publishing at all — so it is a product
    decision with a measured cost, not a refactor. It belongs with F2 in Phase 6a, where
    ADR 0115's remaining decisions (1, 2 and 5, which wait on the authored view) sit
    already. What lands today is the measurement, which is what the decision needs.

    *Distinct from the deleted `THESIS_DISAGREEMENT` trigger*, and the distinction matters:
    that was removed because alarming on the red team doing its job taught an operator to
    read red as noise. This is not a banner. It is whether a document may freeze mid-
    argument, which is a different question and the one ADR 0115 answered.


47. **A restated segment structure reads as the document contradicting itself, and the
    consistency check cannot see it, 20 September 2026.** The round's second still-open
    judge criticism: *"describes two reportable segments in one section and three in
    another"*. Traced to the blinded document rather than taken on trust, and the judge is
    right, though not for the reason the sentence gives.

    *No section states three reportable segments as a present fact.* Segment Analysis is
    careful and correct: Microsoft's FY2026 10-K restated to **two** reportable segments,
    Agents and Infra and Devices and Consumer, *"replacing the prior three-segment
    framework"*, with the comparative table labelled *"Segment History as Restated"* and
    both presentations tying to the same $331,839m of revenue.

    *Business Overview, written by a different section call, describes the old structure as
    current.* It says the evidence *"covers two of the company's reporting segments in
    detail"* — a partitive, so there are more — and then narrates **"The More Personal
    Computing segment"** in the present tense, which is a *legacy* name, alongside the
    Dynamics line and a cloud-and-enterprise activity it has no detail for. Three, present
    tense, under the old framework. A reader meets it six sections before the restatement
    is explained.

    *Why ADR 0125's check passed it, and this is the finding.* The cross-section
    consistency check compares **figures**: a claim's number against the record, a
    section's figure against another section's. This contradiction contains no disagreeing
    figure at all — every number in both sections is right, and $331,839m is stated once
    and reconciles under both structures. What disagrees is a **structure and a set of
    names**. The check is not blind here by an oversight; it has no channel for it.

    *The class, and it is §3.19.37's one layer further out.* Item 37 found the check could
    not see the front page, because the front page is not a section; this finds it cannot
    see a claim that carries no figure. Both are the same shape: *the instrument measures
    the document it can address, and a reader reads all of it.*

    *Not fixed here.* A check over names and counts is new deterministic work in Phase 3's
    territory, wants its own measurement against the corpus before it is allowed to fail a
    run, and would be ADR 0125's second extension. Recorded with the location so whoever
    builds it has a real case to measure against, rather than a rule argued from prose.


48. **Phase 6a is estimated as eleven features to build, and five of them are built, 20
    September 2026.** Measured from the code before starting the phase, because §3.19.43
    and §3.19.45 both found the plan describing something nobody had re-read, and the
    handover's §7 says every error so far has been exactly that.

    | Feature | Measured state |
    |---|---|
    | F9 thesis editor | **Built.** `services/theses.py`, `/theses` and `/theses/{id}`, `tests/test_theses.py`, a browser screen test |
    | F10 decisions | **Built.** `services/decisions.py`, six routes including withdraw, carry-out and revise, a browser screen test |
    | F11 the monitor | **Built**, and diagnosed in item 45. Needs F15 to run unattended, and its price-move alert kind is the open half |
    | F13 the view | **Composed half built** (Phase 4.4); the authored half is out of scope twice over |
    | F14 review and analytics | **Built.** `services/post_trade.py`, a `reviews` table, `/review`, `/review/{id}`, `/analytics`, a browser screen test |
    | F12 risk | **Half.** `calc/risk.py`, `services/risk.py` and `calc/portfolio.py` are 2,109 lines with `/risk` and a browser screen test — and `web/decisions/pages.py` imports `portfolio` and `theses` and **not `risk`**. Its "done when" is *the same shock produces the same figure on both surfaces*, and the second surface does not exist |
    | F3 the closing section | **Absent.** No section definition, and `research_requests` carries none of the three fields |
    | F4 the refresh | **Absent.** One workflow in `workflow/workflows/` |
    | F6 Ask | **Absent.** No `services/ask.py`, no route |
    | F15 scheduling | **Absent.** No cron anywhere in `worker.py` or `queue.py` |

    *And the nineteen surfaces are fifteen.* Of `03-page-specifications.md`'s nineteen,
    routes exist for fourteen or fifteen; **Position detail, Companies, Company and Ask have
    no route at all**. Methods is `/skills` and Today is `/`, which is ADR 0112's tools menu
    rather than the spec's three bands — so those two are *present but not met*, and the
    distinction matters: **a route existing is not the specification satisfied**, and
    nothing here claims otherwise. What it claims is that five features and fifteen surfaces
    do not need building from nothing, which is a different phase from the one estimated.

    *What the remaining order actually is*, in the dependency order
    `04-feature-specifications.md` gives: **F12's second surface** (the pre-trade check on
    the decision form) → **F15 the scheduler**, which F11 needs → **F11's price-move alert**
    → **F3 the closing section**, which needs F12 → **F6 Ask** → **F4 the refresh** → the
    four absent surfaces. The five built features become verification against their own
    "done when" rather than construction.

    *The 25–35 session estimate is not corrected here*, deliberately. Verifying a built
    feature against a specification it was not written from is not free — §3.19.45 took a
    session to establish that a 1,303-line subsystem was complete and its seam was not — and
    four features and four surfaces remain to build outright. What is recorded is the
    measurement; re-estimating is the operator's, with it in hand.


### Before this leaves one machine

None of this is needed for a personal tool on a laptop, and all of it is needed before
anything else. Grouped because they stand or fall together.

- **A5 — no authentication.** `get_current_user` returns the first row of `users`.
- **A7 — no inbound rate limiting.** The token bucket protects outbound fetches only.
- **A8 — no production deployment story.** No production compose file, no TLS, no
  supervision.

Treat these as a single gate rather than three tickets. Shipping any one alone buys nothing.

**This is F17, and it now has a design**: ADR 0120 — an account owns a book, and a share is a
sealed, read-only evidence pack rather than a login. It is **deferred** out of V1.0 on the
operator's decision, and the ADR names the three constraints it places on work happening now
so that deferring it stays cheap: write `user_id` into new tables while one user exists, never
add a service function whose only scope is a company or a request, and keep ADR 0073's
attested-figure rule intact.

### Commercial and licence checks still outstanding

Carried forward from the original plan. Each is a verification against a primary source,
not a design task, and each should be done **before** money or a dependency is committed.

1. Verify Anthropic **web-search tool pricing** against the official pricing page — the
   figure in the cost model came from secondary aggregators. **Done, 2026-08-28**: the
   official pricing page states $10 per 1,000 searches plus standard token costs, one use
   per search whatever it returns, and no charge for an errored search. Recorded as
   `aer.providers.costs.WEB_SEARCH_USD_PER_CALL` and in ADR 0092.
2. Verify the **Companies House rate limit** (600 requests / 5 minutes) against the official
   developer documentation. **Done, 2026-09-04**: the developer specifications' rate-limiting
   guide (`developer-specs.company-information.service.gov.uk/guides/rateLimiting`) states
   "You can make up to 600 requests within a five-minute period", that requests beyond it
   receive `429 Too Many Requests` until the period ends and the limit "will reset back to
   its maximum value of 600 requests", that Companies House "reserve the right to ban
   without notice applications that regularly exceed or attempt to bypass the rate limits",
   and that a higher limit is available on request. The fetch policy's bucket for the host
   is 1.8 requests a second, under the 2 a second the limit sustains, and stays as it is.
3. Verify **EODHD's licence terms** for internal commercial use versus redistribution, in
   writing, before building further on it.
4. Verify **Langfuse's current self-host licence** before making it a dependency. The
   OpenTelemetry + Postgres + Grafana fallback has no licence risk, and the `costs` table is
   needed either way. **Done, 2026-09-04**: the repository's `LICENSE` (langfuse/langfuse,
   main, copyright 2023–2026 ClickHouse, Inc.) puts everything outside the `ee/`,
   `web/src/ee/` and `worker/src/ee/` directories under the MIT Expat licence, and those
   directories under `ee/LICENSE` — the Langfuse Enterprise License, which permits copying
   and modification "for development and testing purposes" only and otherwise requires a
   valid enterprise licence. The self-hosting documentation (`langfuse.com/docs/open-source`)
   says the core — tracing, evaluations, prompt management, experiments, annotation, the
   playground — is MIT-licensed without usage limits, and that the enterprise modules
   (SCIM, audit logging, data-retention policies) need a commercial licence when
   self-hosted. Self-hosting the MIT core for one operator's own metering carries no
   licence risk; nothing the platform would need is behind the enterprise key. Still not a
   dependency: the `costs` table is the record either way, and this check removes the
   licence reason for preferring the fallback, not the reason for waiting.
5. Validate **WeasyPrint's native dependencies** on the target Windows machine. It is the one
   tooling choice that can force late rework.
6. Verify a **GBP risk-free series** — its identifier, its frequency and its terms — against the
   primary source, before §3.17's sterling valuations depend on anything but an
   operator-confirmed assumption. The named candidate is the OECD long-term UK government bond
   yield republished by FRED, which is already a wired source with a cleared licence; it is
   **not** adopted until verified, because this repository does not adopt a data series on a
   recollection. Open question 19 in `../V1.0_Alpha/06-open-questions.md`.

---

## 4. Archived

Finished, with the date, or decided against. Kept in full: a diff records what changed and
these records are why, which is the half nobody can reconstruct afterwards.

**4.1 The replay report called a rounding error a divergence. Fixed 2026-08-25.**
`just replay-run` on the 2026-08-24 MSFT run reports 113 of 1,034 calculations as "does not
replay", while the same run's evaluation gate passed `numerical_consistency` on the same
rows. Both cannot be right, and the gate is the one that is.

`calculations.output_value` is `NUMERIC(38, 12)`, so a non-terminating quotient is stored
rounded to twelve places. `services.run_replay` then compares `observation.replayed !=
observation.expected` **exactly**, and a recomputed ratio carries the full context precision
— `gross_margin` on those figures stores `0.679546406541` and replays
`0.6795464065405211563438896573338275`, a relative difference of 7 × 10⁻¹³. Every ratio in
the run fails that comparison and every sum survives it, which is why `invested_capital` and
`working_capital` are the two rows per period that pass.

The gate already had the right rule and the replay service now reads it:
`ReplayObservation.delta` against the `numerical_consistency` threshold, with a unit mismatch
and a re-run error as failures in their own right. The old comparison also accepted a unit
mismatch silently, which is a second defect the same line carried. Each problem now names
what went wrong rather than saying "does not replay" and stopping there.

**4.2 "Reproduce this run" failed in the browser and worked from the shell. Fixed
2026-08-25.** The button returns `internal_error`; `just replay-run` on the same job
succeeds. The difference is the event loop. `just dev` passes `--reload`, uvicorn sets
`use_subprocess`, and on Windows that selects `SelectorEventLoop` — where
`asyncio.create_subprocess_exec` raises `NotImplementedError`. Replay is the only web route
that re-extracts a document, so it is the only one that trips it; the CLI gets the Proactor
loop from `asyncio.run` and never does.

The fix belongs in `extract.sandbox`, not in the instructions: the child is spawned through
a thread, so isolation no longer depends on which loop the server happened to choose.

**The page is deliberately left able to fail.** The first draft of this entry also proposed
catching whatever a leg raises and reporting it as a finding. That is wrong: an unreadable
artefact and a parser that will not start are already findings — the artefact leg catches
everything and the citation leg catches every `ExtractionError` — and the only thing a
broader catch would have added is swallowing the `NotImplementedError` that made this
diagnosable at all. A 500 with a request id in the log is what a code defect should look
like.

**4.3 Gate 3 — separate a fault from the system working. Done, 2026-08-25.** Three triggers
fired on the MSFT run and only two were faults. `MATERIAL_MISSING_SECTION` and `HIGH_MODEL_UNCERTAINTY` are
real: five sections did not exist and three rated themselves at 0.30. `THESIS_DISAGREEMENT`
is not — the red team's job is to contradict the draft, and a run where it found nothing
would be the one worth worrying about.

The red team is out of the trigger banner and has its own section — each challenge's
severity, its objection at reading width, its basis and its cited evidence — and it still
reaches the report's appendix. The banner now means one thing: something is wrong.
`escalation._thesis_disagreement` and `TriggerKind.THESIS_DISAGREEMENT` are gone; the
challenges were always rows and remain so. The calculations table is closed by default with
a filter over name, period and formula, and the coverage table says *not generated* across
the row for a section that never ran rather than reporting zero coverage for an absence.

**4.4 Settling a disagreement, on the record. Done, 2026-08-25.**
`services.disagreements.settle_by_hand`
existed from the first day of the ladder and nothing reached it, so the page showed two
positions and offered no way to prefer either — which reads as a question the operator is
failing to answer. It is wired: choose a side, give a rationale, and the choice is written
under the operator's name beside the rule that escalated it, which is not overwritten. A
disagreement nobody settles keeps publishing both sides, which stays the default. The labels
follow the kind — "keep the draft's position" and "accept the challenge" for a red-team row,
because asking somebody to choose between A and B on a thesis is asking an unanswerable
question.

**4.5 Gate — confirm the extracted financials. Done, 2026-08-25.** The page listed raw
taxonomy element names and nothing else, so the question it asks — does this gap matter? —
could not be answered from it.

Each unmapped tag now carries its label, the largest figure it held in this filing, the
period that figure belongs to, and what it is as a share of the biggest mapped line; the
rows are sorted biggest share first, so the one that decides the gate is the first on the
screen. Beside it, closed, is what the run *did* capture — because the question is a
comparison, and an operator asked it over element names alone was being asked to hold the
statements in their head. Both tables filter as you type, from markup that is hidden until
a script reveals it, so scripting off gets a complete table rather than a dead search box.

**The largest figure, not the latest**: a tag's most recent observation can be a quarter, a
restatement or a zero, and what is being decided is whether anything material hangs on the
element at all.

A run recorded before this date has no figures in its step output and falls back to the tag
list it always showed.

**4.6 Why a section failed. Done, 2026-08-25, at gate 3.** `sections.writing._failed` already
records
what a section was dealt and why it refused, on the row and in the step's own output (gap
A63), and nothing displayed it — so five failed sections read as five chips indistinguishable
from the twelve that worked, and diagnosing one meant reading a worker log. "Sections in this
draft" is now a record: outcome, the evidence tally by kind, the attempt count, the refusal
in the producer's own words, and the causes counted. **The run console still shows none of
this** and should; gate 3 was the surface an operator was actually on.

**4.7 One weak objection cost a whole run. Done, 2026-08-24.** Found by the first live run
on the merged trunk, which died at `red_team` — the second-to-last step — after £8 and forty
minutes. The adversary returned six challenges; the sixth cited no evidence; a schema
validator raised on it, which failed the parse of the whole `RedTeamReport`, which failed the
step, which failed the run. Five well-evidenced objections were discarded to punish the sixth.

`services.red_team` **already** dropped challenges citing ids the run does not hold, one at a
time, logging each. The schema was simply stricter than the service and fatal where the
service was graceful — so the rule moved to where the other drops happen. An objection
resting on nothing still gets no row; it now costs a challenge instead of a run.

A second attempt fires **only when every challenge was dropped**, which is the case where a
retry rescues the step rather than paying for a second adversary to recover an objection the
report did not need. That gate is one condition in `run_red_team` if it proves wrong.

**4.8 An empty series could not be replayed. Done, 2026-08-24.** From the same run:
`numerical_consistency` failed with 62 findings, every one reading `equity_value#N (did not
replay: TypeError: missing a required argument: 'adjustments')`. None was a real
inconsistency.

An empty sequence argument expands to no input rows, so a record holding none is
indistinguishable from one where the argument was never passed — and most companies have no
non-operating items, so this was the ordinary case rather than the edge. The recorder now
writes an empty series as a structural parameter, which is what it is: no number entered, and
that fact is the thing worth keeping. Replay needed no change; a list parameter already
passes through.

**Forward-only.** Calculations already stored keep the ambiguous shape, so a run recorded
before this date still reports those findings. Re-running the report is the cheaper remedy
than a backfill, and is what the failed run needs anyway.

**4.9 A seed-data downgrade on a used database. Done, 2026-08-24.** Found by a manual run
of the acceptance sheet. Six revisions seed a `section_definitions` row and delete it again
on the way down (0036, 0037, 0039, 0044, 0050, 0052); once a report has used that section
version, `report_sections` holds it and the delete is refused. What an operator got was a
bare `ForeignKeyViolationError` naming a constraint.

Each of the six now counts the citing rows first and **refuses with the remedy** — `N stored
report section(s) cite 'x' at version n … run just reset-research` — rather than letting
Postgres refuse with a constraint name. Deleting the report's own content or repointing it at
an older contract were both rejected: either would change what a stored report says it was
written under, and `ON DELETE RESTRICT` on that column is deliberate.

**The more useful half was the test gap.** `TestRoundTrip` downgrades a throwaway *empty*
database, so it proved the chain reverses on a fresh schema and could never have caught this.
`test_a_seed_downgrade_refuses_when_a_report_still_cites_it` now seeds a realistic run and
asserts the refusal names its remedy — verified by removing the guard and watching it
reproduce the original foreign-key error.

Seeding it realistically is the part worth knowing about: revision 0054's downgrade deletes
any job without a `request_id`, so a job carrying only a work order is swept away three
revisions before 0050 is reached and the guard is never exercised.

**4.10 A static asset's content type came from the operating system. Done, 2026-08-24.** Also
found by a manual run of the sheet, on Windows. `mimetypes` seeds itself from the host —
`/etc/mime.types` on Linux, the registry on Windows — and `.woff2` is in neither Python's own
hardcoded table nor the Windows registry, so the vendored Inter face was served as
`application/octet-stream` there and `font/woff2` on Linux.

Not cosmetic: `base.html` preloads the face as `type="font/woff2"`, and a preload whose
declared type does not match the response is discarded and fetched again — the head start
paid for twice, and slower than no preload at all. Nothing errors, which is why it survived.

`aer.api.app` now pins the types it serves rather than asking the host. The lasting part is
the drift guard: a fresh `MimeTypes()` is Python's hardcoded table alone, which is the one
baseline identical on every machine, and any suffix in the served tree that it cannot name
must be pinned. That fails on Linux — where the existing response assertion passes either
way — so this class cannot come back through CI unnoticed.

**The general lesson is worth more than the fix.** A green Linux suite says nothing about
behaviour that a host supplies. Two of the three defects this sheet has found were invisible
to CI by construction.

**4.11 The pre-commit hooks corrupted the tree they were checking. Done, 2026-08-24.** Three
faults, each independently minor and jointly enough that `just hooks` could not be run:

- The config **pinned ruff 0.14.2 while the project ran 0.16.0**. The two disagree about
  docstring formatting, so the hook rewrote `tests/test_phase5_acceptance.py` into a state
  that made `just lint` fail — a formatter and a linter undoing each other with the
  repository as the battlefield. The pin now follows the project.
- `end-of-file-fixer` appended a newline to `tests/fixtures/fx_report/golden.html`, which a
  golden test compares byte for byte. `tests/fixtures/` now sits in the same exclusion as the
  generated stylesheets and vendored libraries, for the reason all three share: they are
  committed *output*, and rewriting output means it stops matching what produced it.
- `.secrets.baseline` was five weeks stale and failed on 33 findings. All were checked and
  all are false positives — a stub `sk-test` key, SHA-256 digests in fixtures, the pinned
  font hashes, Jupyter cell IDs. The baseline records them as reviewed rather than unseen.

`no-commit-to-branch` also listed `main`, which had been the trunk since the merge, so the
hook forbade committing to the only branch anybody works on. It now guards `master` alone.

All fourteen hooks pass and the working tree is unchanged afterwards, which is the assertion
that matters and the one the sheet now makes.

**4.12 Guidance mode had a flag and no control. Done, 2026-08-25.** The flag, the route and
`data-guidance` on `<body>` shipped with the design tokens; nothing rendered a control for
any of it, so the only way to turn callouts on was to edit a cookie by hand.

The blocker was stated at the time and turned out to be the whole of it: **a form in the
shell needs a CSRF token in the shell**, which means `render()` minting one and setting the
cookie for any handler that did not supply its own. That is now what `render()` does, and
the menu carries both preference controls — guidance, and the light/dark/auto choice that
arrived with it (§4.13). A handler that mints its own token still wins; this only fills in
for the ones that never thought about it.

**4.13 There was no way to choose a colour scheme. Done, 2026-08-25.** Dark mode shipped with
the design tokens and followed `prefers-color-scheme` alone, so the only way to change it was
to change the operating system. Nobody found the control because there was not one.

Light / dark / auto now sits in the menu, remembered in a cookie and stamped on `<html>` by
the renderer. **Not a `<head>` script**, which is the usual way this is done: the cookie is
already in hand when the page is built, so there is no flash to beat, and buying a scripting
dependency to avoid one on an application whose navigation deliberately works without
scripting would be the wrong trade.

`dark:` was redefined as a custom variant answering `[data-theme]` as well as the media
query, and that is what makes the control work at all: without it the shell would have
flipped and forty panels written as `dark:bg-slate-900` would not — a control that works on
some pages is worse than none, because a reader cannot tell which half is broken. What
remains is consistency of the colours themselves, which is §2.5.


**4.14 The draft's figures contradicted the calculations they cited. Fixed 2026-08-25
(ADR 0086).** On the 2026-08-24 MSFT run the draft asserted a quick ratio of 0.93 and a
current ratio of 1.23; the recorded `quick_ratio` calculations were 1.567 and 1.536 and the
`current_ratio` values 1.785 and 1.769. Debt to equity was drafted at 0.09× against 0.299
and 0.229, interest cover at ~50.9× against 40.4 and 45.0, the cash conversion cycle at
−51.8 days against −7.41 and −2.56.

The direction mattered as much as the size: the section concluded liquidity was thin where
the run's own arithmetic says it is comfortable, so a reader taking it at face value would
have reached the opposite view of the balance sheet.

**Only the red team caught it**, for the second time in two live runs.
`numerical_consistency` re-executes stored rows and never reads the prose;
`citation_accuracy` re-reads the quoted excerpt, which was quoted correctly — what was wrong
was the number in the sentence beside it; `figure_plausibility` asks whether a figure is
*possible*, and 0.93 is a perfectly possible quick ratio.

`cited_figure_agreement` closes it at threshold zero: **a claim naming a calculation must
state that calculation's figure.** Structural rather than textual — `claims.calculation_id`
already exists and the writer already sets it — so there is no ratio vocabulary to maintain.
Agreement is the draft's own precision rather than a tolerance, which is what lets 0.09 over
a stored 0.0857 pass while 0.93 over 1.567 fails at every precision. The renderings the
platform actually produces are admitted (a percentage is the fraction times a hundred; money
reaches prose in millions or billions), and a claim resting on a calculation without printing
it is not a violation. It joins `VALIDATION_FAILURE`, so it reaches the banner rather than
only the table.

**4.16 Every form in the browser was refused. Fixed 2026-08-25.** Found by tranche 0 of the
interface overhaul, which baselined the browser suite for the first time since §4.12 landed:
**40 of 124 browser tests failed**, every one of them on a form submission, every one with
*"the anti-forgery token was missing or stale"*.

§4.12 gave `render()` a CSRF token for handlers that never thought about one, so a menu whose
preference controls are forms could not ship controls that silently do nothing. It also made
`render()` set the cookie from that token. Correct for a page; wrong for a fragment.

`GET /_shell/badges` is fetched by htmx on **every** page load and renders through the same
door. It carries no form, so it supplied no token, so `render()` minted one and set it — and
the cookie became the fragment's while every form already on the page still carried the
page's. The next submission failed a check that was never about that submission.

**The comment above the line predicted it and guarded the wrong thing.** *"Two `Set-Cookie`
headers for one name is a race over which token the browser keeps — the form would then carry
one and the cookie the other."* That is exactly the failure; the guard covered two handlers on
one response, not a later response clobbering an earlier one.

**A double-submit cookie is a secret for the session, not for the response.** A render now
adopts the token the request already carries and mints only when there is none, so a fragment
re-sets the same value and invalidates nothing.

**Only the scripting-on path was broken**, which is the wrong half to have working and is why
nothing caught it: the default suite drives the application in-process and an HTTP client does
not run htmx. Two tests now do — one in the default suite that fetches the fragment on the same
cookie jar and asserts the token survives, and 127 browser tests that pass again.

**4.15 Comps said "for want of usable data" over a deliberate choice. Fixed 2026-08-25.**
Eight peers were discovered on the 2026-08-24 MSFT run and all eight were excluded. Nothing
was broken: `services.comps.UNACQUIRED_PEER_REASON` is the true reason, and this workflow
acquires neither a peer's filings nor a peer's prices (ADR 0059), so a peer recorded by name
alone can never contribute a multiple.

What was wrong was the report. §17 read "every one of the eight proposed peers was excluded
**for want of usable data**", which reads as a failure to get hold of something on a run that
made a deliberate choice — a reader would go looking for a fault. The step already grouped
its exclusions by reason; `WithheldComps` now carries them and the disclosure names them.

The reason itself was rewritten to survive the report's register: the first draft cited the
architecture decision inside the sentence, which is exactly the process language
`presentation_integrity` refuses in a document that should be about a company. The decision
belongs in the code comment; the sentence belongs to the reader.

**The peer gate already said it**, and that is worth recording rather than re-fixing:
*"Confirming records the set; it fetches nothing. Computing a peer's multiple needs its
filings and its prices, and this run acquires neither."*

**The remnant — whether to keep buying the model's slate at all — was decided
2026-09-03** (ADR 0059, second amendment): the model is asked only when a price feed is
configured, because until then its slate can contribute no multiple. Without one the step
proposes the deterministic floor, spends nothing, and the gate page says why.

**What remains is a decision, not a defect.** `propose_peers` is a model step and a gate, and
on the present design its whole output is a list of names and rationales that contribute no
figure. That may be worth the money — a reasoned peer set is not nothing, and it is held for
the day a subscription makes it computable — but it should be a choice somebody made. The
options are to skip peer discovery when no price client is configured, or to acquire peer
filings and prices and make comps actually compute, which is an ADR 0059 amendment and
multiplies the data subscription across the set.

### Decided against

Not deferred. Not on this roadmap. Deciding otherwise needs an ADR, not a ticket.

- **Trade execution and any broker connection.**
- **A portfolio optimiser** — no efficient frontier, no allocation solver.
- **Multi-user deployment.**
- **Investment advice.** Every surface keeps its disclaimer.
- **A `positions` table** (ADR 0083). A position is a calculation.
- **A currency-exchange transaction kind**, until it has a row shape that cannot silently
  double-count a cash balance.
- **A Bank of England adapter.** Its documentation describes a CSV route its own
  `robots.txt` disallows, and reaching around that is circumvention. The consequence is
  real and stays visible: `risk_free_series_for("GBP")` refuses rather than substituting a
  US Treasury yield.
- **FCA National Storage Mechanism fetching** (ADR 0022).
- **An external tracing vendor.** OpenTelemetry spans exist behind a setting.

---

## 5. How to work on this

**Do not skip ahead, and do not fold a later item's work into an earlier one.** The
  dependency order in §3.5 onwards is real.
- **If a prerequisite is missing or an architectural choice is unclear, stop and ask.** A
  wrong foundational choice is expensive to undo here, and guessing has been the more
  expensive option every time it has been tried.
- **A decision that changes an invariant needs a new ADR**, not a code change. The eight
  invariants are in `CLAUDE.md`; what enforces each is in
  [`../developers/knowledge-map.md`](../developers/knowledge-map.md) §5.
- **Record the decision if it was a decision.** Eighty-five records exist because
  reconstructing *why* from a diff does not work.

---

**See also:** [what is built](../product/what-it-is.md) ·
[the decision records](../adr/) · [the archive](../archive/README.md)
