# Page specifications

*Every surface in the end-state system: what it is for, how it is laid out, every component,
every state it can be in, the data it needs and the actions it offers. Written so that a
developer can build a page without inventing anything.*

**Normative references.** Colour, type, spacing, radii and control sizing come from
[`../design-system.md`](../design-system.md) (Tracework) and are not
restated here. Where this document names a token — `warning-ink`, `type-data`, `--space-5` — it
means that token exactly. Where it names a width — 232, 304, 448 — it means the layout metric of
that value in §5.1 of the design system.

**Rules that apply to every page and are not repeated.**

1. Every page carries the disclaimer *"This is not investment advice."* once, in the shell.
2. Every figure is server-formatted. Templates never insert a currency symbol, separator, sign
   or unit, and never round.
3. Colour never carries meaning alone; every semantic colour is paired with a word.
4. A refusal and a failure never share a label or a colour. A refusal is `refusal-ink` and says
   which rule withheld the answer; a failure is `failure-ink` and says what went wrong.
5. No page shows a UUID, a module path, a metric identifier or a raw enum. If the operator
   should see an identifier, the server sends a human label for it.
6. Every page works without JavaScript: links navigate, forms submit, disclosures disclose.
7. Every stopped state has a labelled forward control. There is no page a run can reach from
   which the only exit is the terminal.
8. Loading states are skeletons of the real layout, never spinners over an empty page.
9. Every destructive or spending action states its consequence in the control's own label —
   *"Refresh — about £1.40"*, not *"Confirm"*.

---

## 1. Today

**Purpose.** The landing page. It answers two questions in order: *what needs me*, and *what is
worth doing next*. It is the default route after sign-in.

**Reached from.** The menu; the brand mark; the default route.

**Layout.** Single column, `--reading-measure` not applied (rows are full working width).
Three bands separated by `--space-10`.

### 1.1 Band 1 — Needs you

A ranked list of action rows. Rank order is fixed and not user-sortable:

| Rank | Kind | Semantic | Row wording |
|---|---|---|---|
| 1 | A gate is waiting | `warning` | *"A gate is waiting on {company}"* |
| 2 | A premise broke | `failure` | *"A premise broke on {company}"* |
| 3 | A position has no thesis | `warning` | *"{company} is held with no thesis behind it"* |
| 4 | A price moved past the threshold | `info` | *"{company} moved {pct} this week"* |
| 5 | A closed position is unreviewed | `muted` | *"{company} closed {n} days ago and has not been reviewed"* |
| 6 | A report has gone stale | `muted` | *"{company} was last researched {n} days ago"* |

**Row anatomy.** A sheet (`--radius-md`, `--color-line` boundary, no shadow) with a 3px leading
rule in the semantic ink. Inside: a status label (`--radius-xs`, semantic wash, semantic ink,
`type-eyebrow`), the title (`type-body-strong`), the reason (`type-body-sm`, `ink-muted`, max
`--reading-measure`), a right-aligned primary action and a `type-caption` timestamp.

**The reason is mandatory and self-contained.** A row that cannot say why it is there in one
sentence is a row that must not be shown.

### 1.2 Band 2 — Worth doing

Suggestion cards, each **earned by a condition in the record**. The condition is part of the
data contract; a suggestion with no condition is a defect, not a default.

| Suggestion | Condition that earns it |
|---|---|
| Review a completed refresh | `refresh.completed_at` set and `change_summary.read_at` null |
| Research a watched company | Watchlist entry with no current report and none commissioned in 30 days |
| Write a thesis | A held position with no thesis |
| Review closed decisions | Position closed more than 30 days ago, no review recorded |
| Look at your concentration | Top-five weight within 2 percentage points of the operator's stated ceiling |

Cards are `type-subheading` title, one `type-body-sm` line of justification naming the
condition, and one action. Maximum four; if more qualify, show the four with the oldest
condition and a `type-caption` line *"and {n} more"* linking to the relevant destination.

**If nothing qualifies, the band is absent entirely** — no heading, no empty state.

### 1.3 Band 3 — The state of things

Four quiet figures, `type-data` on `surface-sunken`, no actions, no colour beyond `ink`:

- Book value, and the day's move.
- Companies watched, and when the next check runs.
- Spent this month, against the ceiling.
- Reports held, and how many are current.

**States.**

| State | Behaviour |
|---|---|
| Everything quiet | Band 1 renders *"Nothing needs you today."* as a single `type-body-lg` line on `surface-sunken`. Bands 2 and 3 render normally |
| First run, no data at all | Bands 1 and 3 absent. Band 2 shows one card: *"Research your first company"* with the request form as its action |
| Monitor has never run | Band 3's "next check" reads *"No check scheduled"* and links to Platform |

**Data contract.** `queue[]` (kind, company, reason, action_href, occurred_at, semantic),
`suggestions[]` (kind, title, justification, condition_met_at, action_href), `state` (four
preformatted figures). All wording for `reason` and `justification` is server-supplied.

**Must not.** Manufacture a suggestion to fill the band. Show a chart. Show profit as the
headline figure. Rank by recency instead of the fixed rank order.

---

## 2. Portfolio

**Purpose.** The book as a **validity dashboard**. It answers *"are the reasons I bought still
standing?"* before it answers *"what is it worth?"*

**Reached from.** The menu.

**Layout.** Header band; the positions table (full working width); a right-hand risk summary at
Wide and above (304), which moves below the table at Workbench and narrower.

### 2.1 Header

Total value (`type-data-xl`), the day's move, cash, and the count of positions. One line of
`type-body` beneath: *"{n} of {m} positions have a thesis that currently holds."* — which is the
sentence the page exists to deliver.

### 2.2 The positions table

Columns, in this order, left to right:

| Column | Type | Notes |
|---|---|---|
| Company | `type-body-strong` + `type-data` ticker | Links to the company page |
| Weight | `type-data`, right | With a 4px bar beneath in `verification`, and `warning` when over the operator's ceiling |
| **Thesis** | Status label | `holds` (success) · `one premise broke` (failure) · `under review` (warning) · **`no thesis`** (warning, and the strongest wording on the page) |
| **Checked** | `type-data-sm` | Last monitor pass, and next scheduled. `Never` in `warning-ink` if none |
| **Risk** | Status label or blank | `concentration` · `sector {name}` · blank when nothing applies |
| Value | `type-data`, right | |
| Unrealised | `type-data`, right | `success-ink` positive, `failure-ink` negative, and never larger than `type-data` |

**Default sort is conviction risk, descending**, defined as: no thesis (0) → premise broke (1) →
under review (2) → not checked within cadence (3) → holds (4). Ties break by weight descending.

The sort control offers the other orders (weight, value, unrealised, name) and **remembers the
operator's choice**, but the default on a fresh session is always conviction.

A filter row offers: *all · thesis in doubt · no thesis · checked overdue · over ceiling*.

### 2.3 Risk summary

Not a chart. Five rows of `type-data` against labels:

- Top-five concentration, against the stated ceiling.
- Largest sector exposure, named.
- Positions over their individual ceiling, counted.
- The stated shock and what it does to the book — *"a 20% fall in the largest three: −£18,240, 7.1% of the book"*.
- Cash as a share of the book.

Each row links to Risk (§14) for the working.

**States.**

| State | Behaviour |
|---|---|
| Empty book | The table is replaced by one card: *"Nothing is held yet."* with two actions — record a transaction, or research a company |
| Prices stale | A `warning` band above the table: *"Valued at the close of {date}. Today's prices have not arrived."* The table renders in full |
| A position with no company record | The row renders; Thesis reads `no thesis`; the company cell links to a request form pre-filled |

**Data contract.** `positions[]` (company, ticker, weight, weight_ceiling, thesis_state,
thesis_broken_premise_count, last_checked_at, next_check_at, risk_flags[], value, unrealised,
conviction_rank), `totals`, `risk_summary`, `priced_at`.

**Must not.** Lead with performance. Draw a pie chart of allocation. Colour a row by profit.
Hide the `no thesis` state behind a filter.

---

## 3. Position detail

**Purpose.** One holding: how it was built, what it is worth, and what it is doing to the book.

**Reached from.** A portfolio row's value cell (the company cell goes to the company page).

**Layout.** Header (company, weight, value); three sheets — *How it was built* (the transaction
ledger), *What it is worth* (cost, value, unrealised, with the valuation date), *What it does to
the book* (weight, concentration contribution, sector contribution).

**Actions.** Record a transaction · open the company page · revise the thesis.

**States.** Partially closed position shows realised and unrealised separately. A position built
across more than one thesis shows each with its date range.

**Data contract.** `transactions[]`, `cost_basis`, `valuation`, `book_contribution`.

**Must not.** Recompute anything client-side. Every figure arrives computed from the
transactions.

**Corrected 24 September 2026, on building it** (`portfolio/position.html`,
`services/positions.py`). The three sheets are as specified, at `/portfolio/positions/{id}`
from the row's value cell — and from its *Closed* cell, since a closed position is the one
with the most to say about what it made. *What it is worth* adds the pool's cost per share
(`average_cost`, a traced calculation, never a division in a template) and, for a partly
closed or closed position, the realised figure from `realised_gain`, which walks the same pool
`pooled_cost` does so the two halves account for every unit that entered it. *What it does to
the book* names the largest-holdings share and the sector cut, each from the exposure the risk
page shows, and links there for the working; no position ceiling is drawn because none is
stored anywhere (§11's correction, ADR 0104). *A position built across more than one thesis*
lists every thesis on the company with its dates, and the decisions on them that moved the
book. *Revise the thesis* opens the first thesis, or the form with the company chosen where
none exists.

---

## 4. Companies

**Purpose.** Everything the system knows about, why it is there, and when it is next looked at.
This is the watchlist.

**Reached from.** The menu.

**Layout.** Filter row; one table.

**Columns.** Company · **Why it is here** (`held` · `researched, not owned` · `closed, watching`)
· Last looked at · Cadence · Thesis state · Report state (`current` · `stale` · `none`).

**Filters.** All · held · researched not owned · closed · no report · overdue.

**The "researched, not owned" population is never hidden by the default filter.** It is the
record of ideas declined with reasons, and it is the population most likely to become useful.

**Actions per row.** Open the company · refresh the report (priced) · change cadence.

**Page actions.** Add a company to watch (accepts a name or ticker; creates an entry with no
report, which then earns a Band 2 suggestion on Today).

**States.** Empty: one card, *"Nothing is being watched yet"*, with the request form as its
action.

**Data contract.** `companies[]` (name, ticker, population, last_looked_at, cadence,
thesis_state, report_state, next_check_at).

**Must not.** Merge the three populations into an undifferentiated list. Sort by name by
default — sort by *last looked at*, oldest first, because the point of the page is what has
been neglected.

**Corrected 24 September 2026, on building it** (`companies/index.html`,
`services/company_record.py`). The populations, columns, filters, sort and empty card are as
specified, and every state is read on the way to the page rather than stored. *Report state*
reads `stale` when the current report is older than the row's cadence window (31 days monthly,
92 quarterly, and 92 for a company with no cadence), because how often the operator asked to
look is the honest measure of too old. *Last looked at* is the newest of: the report's
approval, the last run, the watch's last check or follow, the open findings, the decisions, the
questions — and *Never* sorts first. A followed listing the platform has never resolved is a
row known by its listing alone, opening on the watchlist. *Refresh the report* is offered on a
row only where the current report has no run going; the route refuses anything else. *Change
cadence* moves the next check from now. The page action *Add a company to watch* is the
watchlist's own follow form, reached by link, rather than a second form that would drift from
it; the empty card's action is the request form, with the follow form as the sentence beneath.
In the menu, Companies sits beside Watchlist under the same tool (02's §6): the one is the
other's list, and the queue and the standing budget stay where they were.

---

## 5. Company

**Purpose.** Everything known about one company, and every action that concerns it. A
destination for a question, not a daily home.

**Reached from.** A companies row; a portfolio row; the command bar; an alert; a report.

**Layout.** Header; two columns (*What you believe* | *What you hold*); *The record*; *Ask*; an
action bar. Single column below Workbench.

### 5.1 Header

Company name (`type-display`), ticker and exchange (`type-data`), state labels (`held` ·
`researched, not owned` · `closed, watching`), weight if held, cadence. Right: last close
(`type-data-xl`), the day's move, and the valuation date. **If no price is held, the block reads
*"No price series"* in `ink-subtle`** — it does not render a blank or a zero.

### 5.2 What you believe

The thesis: each premise as a sentence with its test beneath (`type-data-sm`), and a status
label. Below: one line of what the state means, e.g. *"One premise broke on 11 September. Until
you revise or withdraw it, this thesis is marked under review."*

If no thesis exists, the sheet is replaced by a `warning` card: *"Nothing is written down about
why you hold this."* with *Write a thesis* as its action.

### 5.3 What you hold

Position, value, average cost, unrealised, and the weight bar against the ceiling. If not held,
the sheet reads *"Not held. Researched {date}; you declined on {date} because: {reason}."* —
pulling the recorded pass, which is the whole reason passes are recorded.

### 5.4 The record

Three ledger rows — Report, Decisions, Monitor — each with a summary, a detail line and a
right-aligned figure (cost, count, cadence). Superseded reports are counted here and reachable.

### 5.5 Ask

See §13. On this page it renders as a single input with the tier indicator.

### 5.6 Action bar

Refresh the report (priced) · Revise the thesis · Record a decision · Open the workbook.

**States.**

| State | Behaviour |
|---|---|
| No report | The record's Report row reads *"Never researched"* with a priced action |
| Report running | The Report row shows the stage and links to the active run |
| Refused at a gate | The Report row is `refusal` and names the check that refused it, with the re-measure action |

**Must not.** Show a chart of the price by default. Put the position above the thesis. Show a
recommendation.

**Corrected 24 September 2026, on building it** (`companies/detail.html`,
`web/companies/pages.py`). The header, the two columns in that order, the three record rows,
the Ask input and the action bar are as specified. §5.1's *No price series* is the block's
text where no listing or no bar exists. §5.2 shows each premise's status as the monitor last
read it — *not yet read* or *reviewed by a person* where it has not — and never re-measures
one here (ADR 0079); the state line counts the premises that broke and dates the latest. §5.3
draws the weight bar with no ceiling, because none is stored (ADR 0104); *not held* pulls the
recorded pass, and says plainly when no pass is recorded. §5.4's Report row: *Never
researched* links to the request form, which is where the price is; *running* shows the run's
own state and links to the console, which names the stage; *refused* names the run's stated
reason and links to the console, whose re-measure control it is (ADR 0123) — the row does not
carry a second one. §5.5 posts to Ask with the company chosen; a company with no record says
so instead of offering an input. §5.6's *Open the workbook* is a sentence that it is not built
(F5), never a control that opens nothing. The page answers for any door into the record — a
holding, a thesis, a watch or a report — where before it answered for the research history
alone, and it keeps the approved-report timeline, the valuation history and the catalyst
outcomes it carried before this section existed.

---

## 6. New request

**Purpose.** Commission a report, with the operator understanding what they are buying.

**Layout.** One form, `--reading-measure`, in four fieldsets.

| Fieldset | Fields |
|---|---|
| The subject | Company or ticker (with resolution against the source's own list, showing the resolved name before submit) |
| The brief | Free text; up to three focus questions, each its own input |
| **Your context** | **Planned weight** · **horizon** · **purpose** (new position · add · review · watching only). These feed the report's closing section (§8.4) |
| How deep | Standard (18 sections) · Quick (9 sections, ~60% of the cost). The estimate updates with the choice |

**Below the form**: the estimate — sections, expected cost as a range, expected machine time,
and the number of decisions it will ask of the operator. **The submit control reads
*"Commission — about £7.90"*.**

**States.** Unresolvable ticker: `refusal`, naming the source searched and offering the
alternatives found. A company with a current report: a `warning` band offering *refresh* instead,
at its lower price.

**Must not.** Submit without showing a cost. Offer a setting whose effect on the estimate is not
shown.

---

## 7. Active run

**Purpose.** Watch a run, and answer the gates it stops at.

**Layout.** Header (company, elapsed, state label); a five-stage rail; two columns — *What it has
done* (a ledger of completed steps, each with actor `you` or `code`) and *What it has spent* (a
bar against the ceiling, broken down); and, when paused, a **304 decision panel** in `warning`.

### 7.1 The stage rail

Five stages: Plan · Acquire · Compute · Write · Approve. Each carries a state (`done` success ·
`running` info · `waiting` muted) and one line of detail. The rail is the only progress
indicator; there is no percentage bar.

### 7.2 The decision panel

When a gate is waiting, the panel is the visual focus of the page: `warning` boundary, wash
header, gate name and *"Gate {n} of {m}"*.

Each gate specifies its own body, and every one of them ends with the same line: **"Nothing is
spent while this waits."**

| Gate | Body | Primary action |
|---|---|---|
| Plan | Sections, sources, estimate, risks | Approve the plan |
| Peer set | Up to eight peers with their rationale, each removable; an input to add | Confirm the peers |
| Themes | Proposed themes as labels, removable | Confirm the themes |
| Unmapped concepts | **At most 20 rows at a time**, ranked by materiality, each showing the filer's own words and the reference line it would affect; a *"{n} below materiality — skip them"* control | Map these, or skip |
| Assumptions | Each outstanding value with its proposed figure, its source and a justification field | Accept and continue |
| Final | Checks with their results; unverified citations listed individually | Approve the report |

**The unmapped gate's 20-row cap is a hard requirement**, not a preference. Today it can present
several hundred rows, which is the single worst moment in the product.

**States.** Failed step: `failure`, what failed, and a labelled retry. Stranded run (worker
died): `warning`, *"This run stopped without finishing"*, with **Continue** as a labelled
control — never an instruction to use a terminal. Refused at the final gate: `refusal`, naming
the check, with a **re-measure** action.

**Must not.** Show a percentage. Use a spinner as the only indication of progress. Name a step
by its code identifier.

---

## 8. Report reader

**Purpose.** Read the document, and check any figure in it.

**Layout.** A 152 evidence spine on the left at Wide and above; the report body at
`--report-measure`; a 448 evidence drawer that opens over the right.

### 8.1 The body

Sections in order, each with its heading and its confidence. Every figure carries a footnote
marker; every marker is a control.

### 8.2 The evidence drawer

Opening a marker opens the drawer with: the figure; **what kind of thing it is** (a stored fact ·
a recorded calculation · an attestation); for a fact, the excerpt with its locator, the
document, publisher, publication and retrieval dates, and the artefact digest; for a
calculation, the formula, each input with its unit and its own source, and the code version.
The drawer's header names the verifier's verdict.

### 8.3 The spine

A vertical sequence of the sections, showing which are read, and the count of figures and
citations in each.

### 8.4 The closing section

**Portfolio consequences** — not a recommendation. Computed from the operator's own book and the
request's context fields: what the planned weight does to concentration and sector exposure, and
how the stated horizon compares to the model's payback. Every figure is a calculation with its
own footnote. **No rating, no target price, no expected return.**

### 8.5 Disagreements

The adversary's surviving case, presented as a **bear or bull case** with a number attached —
never a list of unresolved accusations. Each carries its resolution: accepted, rejected with a
reason, or carried as an open question the operator acknowledged.

**Actions.** Open the workbook · refresh · print · export.

**States.** Superseded report: a band reading *"This report was superseded on {date}"* with a
link to the current one and to the change summary. Withheld figure: `refusal` inline, naming the
rule and what is missing — never a blank or a zero.

**Must not.** Let a footnote resolve to an identifier a reader cannot use. Show a figure with no
marker.

---

## 9. Report library

**Purpose.** Every report, current and superseded.

**Layout.** Filter row; a table — Company · Date · State (`current` · `superseded` · `refused`) ·
Sections · Cost · Actions.

**Grouping.** By company, newest first, with superseded versions collapsed under the current one
and a count.

**Must not.** Delete anything. A superseded report remains readable for ever.

---

## 10. Thesis editor

**Purpose.** Write what you believe, and name what would defeat it.

**Layout.** Header; a lead paragraph stating the rule; premise rows; a state panel.

### 10.1 A premise row

The sentence (`type-body`, editable). Beneath it, the test as three controls: **metric**
(a select of the measurable metrics the platform can resolve), **comparator** (at least · at
most · equal to), **threshold** (a value with its unit). Where nothing can test it, a single
**review date** instead, and a line saying so plainly.

A status label on the right: `holds` · `broke` · `by hand`.

### 10.2 The rule, stated on the page

*"At least one premise must carry a test, or the monitor has nothing to watch on your behalf."*
Saving a thesis with no testable premise is allowed but warns, and the warning names the
consequence rather than scolding.

### 10.3 When a premise has broken

A `warning` panel with three actions, and the panel says what each does:

- **Revise it** — write what you now believe; the old wording is kept.
- **Withdraw it** — with a reason; it stops being tested and stays in the history.
- **Keep it** — record that you think the miss is temporary, with a date to look again.

**Nothing deletes a premise, ever.** The history reads as a narrative — *"On 3 March you
believed… On 14 September you revised it to…"* — not as a diff.

**Data contract.** `premises[]` (id, sentence, metric, comparator, threshold, unit, state,
last_measured, last_measured_at, review_date, history[]), `measurable_metrics[]`.

**Must not.** Offer a metric the monitor cannot resolve. Let a threshold be saved without a unit.

---

## 11. Decision

**Purpose.** Record what was decided, why, and how much — including deciding not to act.

**Layout.** One form, `--reading-measure`.

| Field | Notes |
|---|---|
| What you decided | **Open · Add · Trim · Exit · Pass**. Pass is the same weight as the others, not a footnote |
| The thesis | Required. A select of this company's theses, or *write one first* |
| The report | Required. The current report, or a superseded one if that is what was read |
| Intended size | Weight or units. Absent for Pass |
| Why | Free text, required, no minimum length |
| What would make this wrong | Optional, and offered as *"the fastest way to a premise"* — text here becomes a premise on the thesis |

### 11.1 The pre-trade check

Renders **before** the form can be submitted.

~~Computed from the intended size:~~ ~~Weight after the trade, against the ceiling. Top-five
concentration before and after. Sector exposure before and after.~~ ~~If a ceiling would be
breached, a `warning` band says so and the submit control reads *"Record it anyway"*.~~

**Corrected 20 September 2026, against ADR 0104, which outranks this document.** There is no
*after* to compute and no ceiling to breach, and neither is a gap:

- **`decisions.size_statement` is text, and that is a decision.** ADR 0104 rejected a numeric
  intended size in as many words — *"a stored intended weight would be a judgement wearing a
  `Quantity`'s clothes, and the day something multiplied it by a net asset value the position
  would be sized by a view"*. Nothing can compute a weight after the trade because nothing
  may store the weight the trade intends.
- **No ceiling exists anywhere in the platform** — not on `portfolios`, not in settings, not
  in the risk service. A ceiling the platform invented would be the platform stating the
  operator's risk policy for them, which is the thing ADR 0080 refuses one layer along.

So the check states **the book as it stands**, cut to what this decision is about, and says in
words that it cannot state what the book becomes:

- What the book is worth, so a weight is a weight *of* something the reader can see.
- What is already held in this listing, or that none is.
- Top-five concentration.
- The share already in this listing's sector.
- The operator's own stated shocks that reach this listing, with what each does to the book.

Every figure is the same recorded calculation the risk page footnotes, from
`aer.services.risk.check_before_recording` and that page's own row formatter — F12's *one
implementation, surfaced twice*, made structural rather than tested for.

**It never blocks**, and now there is nothing it could block with. The operator's book is
their own. The stated horizon against the model's payback is not built and needs F13's
composed view on the same page; it is listed here as the check's open half.

**States.** No thesis: the form cannot be submitted, and offers the thesis editor inline. No
report: allowed, with a `warning` noting the decision rests on nothing recorded.

**Must not.** Let a decision be recorded without a reason. Auto-fill the reason. Block a trade.

---

## 12. Monitor alert

**Purpose.** Tell the operator what happened, with what the record says about it, so that a
price move is a reason to think rather than to panic.

**Layout.** Header stating both halves; the move and its context; the premises; the actions.

### 12.1 The headline

The page's title states the relationship, not the event: *"The price moved. Your thesis did
not."* — or, when both moved, *"The price moved, and so did a premise."*

### 12.2 The move

The change, the two prices, the period, and a 14-session end-of-day series as a small column
chart. **This is the only chart sanctioned outside Risk**, and it carries no axis furniture
beyond a baseline.

### 12.3 What the record says

Two or three sentences, each with its own icon and semantic:

- Whether anything has been filed since the last check.
- Whether the premises hold, and which if not.
- Whether the sector moved too — context that stops a market-wide move reading as company news.

### 12.4 The premises

Each premise with its measured value and its threshold, so the operator sees the margin rather
than a verdict.

### 12.5 The threshold band

At the foot: what threshold produced this alert, how many alerts it has produced in six months,
and a control to change it. **A dismissal requires a reason**, and the page says why: *"Too many
dismissals mean the threshold is wrong, not the market."*

**Actions.** Open the company · record a decision · dismiss with a reason.

**Must not.** Alert on a price move without the thesis state beside it. Use red for a fall and
green for a rise as the only signal.

**Corrected 23 September 2026, on building it** (`monitor/finding.html`, the price shape).
§12.3's third sentence says *the market*, not *the sector*: the platform holds one market proxy
per exchange and no sector series, so the page names the index — *the S&P 500 over the same
period* — and never calls an index a sector. §12.4 shows each premise as the monitor last read
it — value against threshold where a reading exists, *not yet read* or *reviewed by a person*
where none does — and never re-measures one here (ADR 0079). §12.5's count is alerts *and
dismissals* over six months, because the dismissals are the number the sentence is about; the
control to change the threshold is where the listing is followed, and the account's default is
on Settings. Of the three actions, *record a decision* carries the listing into the decision
form and *dismiss with a reason* is the finding's ordinary resolution; *open the company* waits
for the Company page (§5), which does not exist yet, and the finding shows the listing instead.

---

## 13. Ask

**Purpose.** Answer a question about a company, from the record where it can and by going and
looking where it cannot.

**Layout.** A single input with a tier indicator; answers accumulate below it in the session.

### 13.1 The three tiers

| Tier | Indicator | Before it runs | Cost |
|---|---|---|---|
| 1 · Recompute | `info` label *"from the record"* | Nothing; it runs | Free |
| 2 · Re-read | `info` label *"re-reading {n} documents"* | Nothing; it runs | Shown after, in pennies |
| 3 · Research | `warning` label *"needs new material"* | **A priced confirmation**: what it will fetch, roughly what it costs, and that the material joins the company's record | Approved first |

The tier is resolved and shown **before** the answer. A tier-3 question never runs silently.

### 13.2 An answer

Every answer carries the same evidence drawer as the report. A tier-1 answer names the inputs it
changed and the stored model it re-ran. A tier-3 answer lists the documents it added, and those
documents appear in the company's record afterwards.

### 13.3 The line that makes the tier honest

On tier 3, before running: *"This needs new material. About £1.40, and {n} new documents will be
added to {company}'s record. Go ahead?"*

**Must not.** Answer from the model's own knowledge. Guess at a tier. Hide the cost until after.

---

## 14. Risk

**Purpose.** What the book is exposed to, and what a stated shock would do to it.

**Layout.** Concentration; exposure by sector; the shock panel; the working behind each.

**The shock panel** takes an operator-stated shock — a percentage fall applied to a named set
(the largest three, a sector, everything) — and shows the result on the book. The set and the
percentage are the operator's; the platform proposes nothing.

**Every figure links to its working**, down to the positions that compose it. The pre-trade check
in §11.1 and this page use one implementation and can never disagree.

**Must not.** Compute a value-at-risk or any figure whose method is not on the page. Draw a
correlation matrix.

---

## 15. Post-trade review

**Purpose.** After a position closes: was the thesis right, and was the decision good?

**Layout.** The position's history; the thesis as it stood at each decision; the review form.

### 15.1 The form separates process from outcome

Two distinct questions, and the form makes conflating them impossible:

- **Was the reasoning sound at the time?** — answered against what was knowable, using the
  retained retrieval timestamps.
- **Did it work out?** — the realised result.

The four combinations are named on the page: *right for the right reasons · right anyway ·
wrong for good reasons · wrong for bad reasons*. **The second and third are the ones worth
learning from**, and the page says so.

**Must not.** Score a decision by its outcome alone.

---

## 16. Decision analytics

**Purpose.** What many decisions say that one cannot.

**Layout.** A sample-size band; then, only if the sample supports it, the findings.

**The sample-size band is the page's first element**: *"{n} reviewed decisions. Calibration needs
about 20 before it says anything."* Below that threshold, **the page shows nothing else** beyond
a link to the review queue.

**Findings, when earned.** Calibration (stated confidence against realised outcome); which
premise kinds break most often; whether process quality is improving. Every finding links to the
decisions behind it.

**Must not.** Show a statistic on a small sample. Rank decisions by return.

---

## 17. Methods

**Purpose.** The operator's own report sections, and later a shared library.

**Layout.** My methods (a list, each with its state and the reports it has run in); the
library; the attack report for a method.

**The attack report** shows the corpus of attacks the method was tested against and that all of
them failed — and, when a method is refused, **which requirement it tried to relax** in plain
words.

**Must not.** Run a method that has not passed the corpus. Show the corpus as a pass/fail count
without naming what was attempted.

---

## 18. Knowledge

**Purpose.** Teach the system a filer's vocabulary, once.

**Layout.** The unmapped queue, **at most 20 rows at a time**, ranked by materiality; each row
shows the filer's own tag, the filer's own label for it, the value, and the reference line it
would affect, with the mapping control beside it.

**Below materiality**, a single collapsed row: *"{n} concepts below 5% of their reference line"*
with a control to review or skip them as a group.

**Must not.** Present an unranked wall of tags. Ask the same question on a later run.

---

## 19. Platform

**Purpose.** Settings, costs, health, backups, the API.

**Sections.**

| Section | Contents |
|---|---|
| Costs | Spent this month against the ceiling, by run; discarded spend (replies paid for and not used) shown as its own line |
| Monitoring | Default cadence; default price threshold; when checks run |
| Book | Concentration ceiling; sector ceilings; the stated shock |
| Data | Sources and their state; the price subscription and its terms |
| Backups | When the last backup ran, **and when it was last proved restorable** |
| Health | Services, the queue, the last run |

**Must not.** Be the only route to anything an operator needs. Show a credential, even masked,
anywhere it could be copied.
