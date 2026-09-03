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
   [`interface-overhaul.md`](interface-overhaul.md).
5. **§2.5 — the palette migration. Done 2026-08-30, inside §3.12.** The ramp ledger fell
   1,837 → 0 across tranches 2 and 4–9, the legacy aliases are gone from the stylesheet,
   and the ratchet is now the hard zero §2.5 asked for
   (`tests/test_palette_migration.py`).

*Finished 2026-08-25 and now in §4: the drafted-figure check (§4.14) and the comps
disclosure (§4.15), which were the two at the top of this list.*

Everything else sits in its bucket below. The running order across everything open — the
phases, what gates each, and the moves only the operator can make — is
[`remaining-work.md`](remaining-work.md); this list stays the authority on priority.

---

## 1. Where the platform actually is

**The chain is complete. The breadth is not.**

A research request becomes a costed plan you approve, filings fetched and hashed,
point-in-time facts, traced calculations, a drafted report you approve, and a frozen
document in which every figure carries a footnote resolving to either the formula that
produced it or the archived bytes it came from. That path has no gap in it, and the
evaluation gate re-derives every stored calculation from its own record on every run.

Seven of nine tools work:

| Tool | State | Waiting on |
|---|---|---|
| **Equity Research** | Working, end to end | — |
| **Portfolio** | Working | — |
| Watchlist | Planned | A standing budget that is not one run's cap; the two clocks |
| **Theses** | Working | — |
| **Decisions** | Working | — |
| **Monitor** | Working | — |
| Risk | Planned | A book to be about, and the rate store *(the rate store now exists)* |
| **Post-trade review** | Working | — |
| **Decision analytics** | Working | — |

A planned tool is a real page saying what it would be and what it needs — not a dead link
and not a lie.

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
one costs, worst first. **§2.1 is next.**

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
[`interface-overhaul.md`](interface-overhaul.md)**, with the census command and the standing
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

---

## 3. New additions

Nothing here is broken; it does not exist. §3.1 is the one an operator is currently blocked
by. §3.5 onwards is the judgement layer, and the order there is forced by dependency —
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

**3.9 Portfolio risk and scenarios** (ADR 0080). Commented on rather than scored. Its rate
prerequisite is now met.

**3.10 Watchlist and research queue.** Needs the standing budget and the two clocks — a
watchlist is followed continuously and researched as at a date, and conflating those is the
mistake ADR 0075 names.

**3.11 The methodology library.** Three `SkillKind`s that are versioned, pinned and
composed. Mostly does not exist yet.

**3.12 The interface overhaul. Done 2026-08-30: specified, designed and planned 2026-08-25;
all ten tranches built and verified green by 2026-08-30 — the record
is the top section of [`interface-overhaul.md`](interface-overhaul.md).** Four
surfaces were in scope and the rest of the product deliberately not: **the main menu, the menu
system and shell, the Equity Research tool, and the Portfolio tool.**

**Where it stands.** The requirements are in [`../design/`](../design/README.md); the design
came back as [`../redesign/`](../redesign/README.md) — a token system, page specifications, a
production handoff and a twelve-screen prototype. It was reviewed against the invariants and
the code, and **adopted with nine corrections**, one of which is a WCAG failure the design's own
validation reported as passing: the navigation rail keeps dark colours on a light page, its
tokens were never in the normative table, and the light-theme focus ring measures 2.04:1 on it.
The review is [`../redesign/05-review-and-corrections.md`](../redesign/05-review-and-corrections.md)
and it wins where it and the design system disagree.

**The work is sequenced in [`interface-overhaul.md`](interface-overhaul.md)** — ten tranches,
each independently releasable — with its testing in
[`interface-overhaul-testing.md`](interface-overhaul-testing.md).

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

So the deliverable is [`../design/`](../design/README.md): every surface in scope with its
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

### Before this leaves one machine

None of this is needed for a personal tool on a laptop, and all of it is needed before
anything else. Grouped because they stand or fall together.

- **A5 — no authentication.** `get_current_user` returns the first row of `users`.
- **A7 — no inbound rate limiting.** The token bucket protects outbound fetches only.
- **A8 — no production deployment story.** No production compose file, no TLS, no
  supervision.

Treat these as a single gate rather than three tickets. Shipping any one alone buys nothing.

### Commercial and licence checks still outstanding

Carried forward from the original plan. Each is a verification against a primary source,
not a design task, and each should be done **before** money or a dependency is committed.

1. Verify Anthropic **web-search tool pricing** against the official pricing page — the
   figure in the cost model came from secondary aggregators. **Done, 2026-08-28**: the
   official pricing page states $10 per 1,000 searches plus standard token costs, one use
   per search whatever it returns, and no charge for an errored search. Recorded as
   `aer.providers.costs.WEB_SEARCH_USD_PER_CALL` and in ADR 0092.
2. Verify the **Companies House rate limit** (600 requests / 5 minutes) against the official
   developer documentation.
3. Verify **EODHD's licence terms** for internal commercial use versus redistribution, in
   writing, before building further on it.
4. Verify **Langfuse's current self-host licence** before making it a dependency. The
   OpenTelemetry + Postgres + Grafana fallback has no licence risk, and the `costs` table is
   needed either way.
5. Validate **WeasyPrint's native dependencies** on the target Windows machine. It is the one
   tooling choice that can force late rework.

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
