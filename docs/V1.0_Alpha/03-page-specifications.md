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

**Approved 28 September 2026.** The operator approved every page of this specification and its
nineteen drawings, and settled the four places where the two disagreed. Today is the drawn hub
(§1, corrected below). A concentration limit is the operator's own statement (ADR 0136). The
decision page's check shows what a typed size would do and records nothing (ADR 0137). The menu
is the drawn six destinations under the name *Ageantic* (02 §3; ADR 0112, amended). Where a
drawing breaks a rule above, the rule wins: a code name is written in words, and no figure
appears on a sample too small to support it (§16).

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

**Corrected 24 September 2026, on building it** (`index.html`, `overview/_attention.html`,
`web/overview/{companies,suggestions,state}.py`). Band 1 is the attention feed every tool
already contributed to (ADR 0078's cross-tool surface), ranked by this section's six kinds
rather than sorted by severity: a row's kind is read from the key its tool wrote, the six take
ranks one to six, and every other row a tool contributes — a run at its cost ceiling, a failed
run, a decision not carried out, a review proposal waiting — takes its place after them, worst
first, with the label its severity gives it. Two of the six were emitted by no tool and are read
from the company record: *held with no thesis* and *report gone stale*, the latter against the
row's cadence window. The row anatomy is as specified with the timestamp rendered as *waiting
{duration}*. Band 2's fifth suggestion, *look at your concentration*, cannot be earned: no
ceiling is stored anywhere in the platform (ADR 0104, §11's correction), so the band shows the
other four and never a fifth with an invented threshold. *Review a completed refresh* is earned
by `jobs.changes_read_at` being null (ADR 0131) and cleared by opening the report the refresh
produced. Band 3's *next check* is the daily pass's last run plus its cadence, and *No check
scheduled* links to the platform. The states: *everything quiet* keeps the heading *Nothing is
waiting* the shell's tests read by role, with *Nothing needs you today.* as the line beneath;
*first run* shows the one card titled *Research your first company* whose text still opens
*Start with two things*, which the same tests read. The tools launcher stays below the three
bands: the menu is ADR 0112's and the bands are this section's, and neither replaces the other.

**Corrected 28 September 2026, on the operator's approval: the page is the drawn hub.** The
page guide records why the drawing replaced a page that led with its queue — *a hub leads with
state; an inbox leads with events* — and the text had not followed it. Everything this section
requires stays; it is arranged as drawn:

- **The verdict**, first and full width: one sentence on the book (*"Your book needs attention.
  Nine of eleven positions still hold their reasoning"*, or that every position does), the
  figures — the book and its day's move, how many positions' reasoning is current, the next
  check, the month's spend against the cap — and **the conviction strip**: every position by
  weight, coloured by whether its reasoning holds, each colour named in a legend beside it and
  each segment named for a screen reader. The strip is a bar of weights, not a chart; §1's *show
  a chart* means a chart of prices or performance.
- **Since {day}**: what happened while the operator was away — findings, refreshes finished,
  moves past a threshold, the daily pass — one line each, dated, with no controls. A line's
  title may link to the record it describes. *{day}* is when the operator last opened Today,
  which the page records; a first visit shows the last seven days.
- **Needs you**: the six kinds of row in §1.1's fixed rank, numbered, each with its one action
  as a word at the right. Small, beside the briefing, because on most days it is short.
- **Start something**: research a company, or ask about one already held.
- **Worth doing**, at the foot: §1.2's suggestions, earned as before. The fifth is earned
  against the limit the operator states (ADR 0136), and never against a default.

Reports held and how many are current move to Research. The tools launcher leaves the page: the
drawn menu is the launcher. States: with no book, the verdict speaks of the research the
operator holds and draws no strip; with nothing at all, it says so and *Start something* offers
the first request; on a quiet day *Needs you* reads *Nothing needs you today.*

*Built 28 September 2026* (`index.html`, `overview/_attention.html`, `web/overview/{hub,briefing}.py`,
`core/visits.py`, revision 0092). The verdict's shares — the book resting on reasons not
standing, and the cash the strip leaves out — are struck by `share_of_the_book`, never added in a
template. *{day}* is the operator's last look before the current visit, so a reload does not
empty the briefing and never claims a look that did not happen; a visit ends after three hours
away. A premise read and found holding is counted into the daily pass's line rather than given
one of its own. The greeting names no time of day, because the platform does not know the
operator's; the footer says when the book was valued, as drawn. The fifth suggestion waits for
the operator's limits (ADR 0136, U5), and the reports held and current are stated on the Reports
library.

*Fifth suggestion built 29 September 2026 (U5).* *Look at your concentration* is earned when the
five largest are within two points of the top-five ceiling the operator stated on Platform, or
past it, and its line says which — *your five largest positions are 71.8% of the book, past the
50% ceiling you set* — with the risk page's exposure as its action. With no ceiling stated it is
never earned.

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

**Corrected 24 September 2026, on building it** (`portfolio/index.html`,
`web/portfolio/dashboard.py`). The header, the sentence, the seven columns, the conviction sort,
the filters and the five summary rows are as specified, with these readings: *one premise broke*
is a thesis with an open contradicted finding, *under review* one with an open finding that
opened a gate without contradicting, and the count of broken premises is shown beside the label
when more than one broke. *Checked* is the daily pass's last run and the next due, since the
pass reads every held listing at once; *Never* is the warning it says it is, and a holding whose
thesis holds but whose pass is missed or never run ranks as *not checked within cadence*.
*Risk* flags *concentration* for a holding among the five largest and *sector {name}* for one
in the largest sector cut — flags that something applies, not breaches, because no ceiling is
stored (ADR 0104): the *over ceiling* filter and the *positions over their individual ceiling*
row both say so rather than pretending to have looked, and the top-five row states its figure
without a ceiling to state it against. The sort control is remembered for the session in a
session cookie, so a fresh session is always conviction. The stated-shock row reads the first
scenario the operator stated through the risk page's own computation (F12: one implementation),
and *none stated* when there is none. The quantity and the pooled cost stay under the company
cell as one line, because this is the screen an operator reconciles against a statement. The
return and exposure sheets stay beneath the table; the empty-book card is the one that was
already here. *Prices stale* is shown when the page defaulted to a last close that is not
today's, and not when the operator asked for a date.

**Corrected 28 September 2026, against ADR 0136.** The ceilings are the operator's own limits,
stated on Platform. Where one is stated, the weight bar carries its line, *Risk* reads *over
ceiling* in `warning` for a holding past it, the *over ceiling* filter and the *positions over
their ceiling* row count against it, and the top-five row states its limit beside its figure.
Where none is stated, each says that none is set, and no default is ever drawn.


*Built 29 September 2026, as drawn* (`portfolio/index.html`). The page is titled *The book*,
with the sentence it exists to deliver under the title — how many theses still hold, then
what the rest have instead (*two broke, one is under review, and one has nothing written down
at all*) — and the value with the day's move on the right, cash and the count beneath it. The
as-at lens, the typed-entries notice and the stale-prices band sit under the header; the
table fits its seven columns in the working width, with the thesis and risk states as the
drawing's chips and *Checked* as the day read and the next; the filter and the sort are one
compact form. The risk card reads its rows label against figure, in the warning ink past a
ceiling the operator set, with the stated shock beneath — the latest one stated, its name and
what it does — and a *Needs a decision* card beneath that when a premise stands broken or a
holding has no thesis, leading to the monitor or the thesis form. Return, exposure and the
transaction form stay below, as the 24 September correction kept them.

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

**Corrected 28 September 2026, against ADR 0136.** *What it does to the book* draws the weight
against the operator's single-position limit where one is stated, and says *over the {n}%
ceiling you set* in `warning` when the holding is past it, linking to the decision that took it
there where one is recorded.


*Built 29 September 2026, as drawn* (`portfolio/position.html`). The name with what the
position was built on (*built across two decisions and one thesis, since 14 March 2026*), and
its value and unrealised figure on the right; the ledger on the left, its last column the
decision each trade carried out where the operator said which (ADR 0104), and the theses
beneath it; *What it is worth* and *What it does to the book* as two cards beside it, the
second with the weight, the five largest and the sector each against the operator's ceiling,
and past one the drawing's note with *Read that decision*.

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


*Built 29 September 2026, as drawn* (`companies/index.html`). One row a company and one control
on it, *Open*; the priced refresh and the cadence are the row's too, one press further in
under *More*, so the table reads as the drawing does. *Why it is here* is the population's tag
and the line that makes it that — the weight held, the pass and its reason, when it was
added and never researched, or when it closed. Thesis and report states are the drawing's
chips: *broke* where a premise stands contradicted, *no thesis* in the warning ink only on a
holding, a stale report with its age in days. The line under the title counts the record and
says why it is in this order; *Watch a company* is the header's control; the note on why the
second population matters closes the page.

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

**Corrected 28 September 2026, on the operator's approval.** §5.3's weight bar is drawn
against the operator's single-position limit where one is stated (ADR 0136), and §5.6's *Open
the workbook* is a control where a workbook was archived (§8's correction of the same date).

*Built 28 September 2026* (`companies/detail.html`, `web/companies/pages.py::_believe`,
`_actions`). §5.2 reads each held premise through the thesis editor's own functions
(`web/theses/pages.py::premise_state`, `defeat_words`, `measurement_words`), so the two pages
cannot call one premise two things: *holds*, *broke*, *by hand* or *not read yet*, and the test
in words — *Gross margin at least 40.0% · measured 35.0% for the year to 31 December 2025* —
never `gross_margin ≥ 0.4`. The state line is §5.2's own sentence, and the sheet names when the
thesis was written and how many revisions it has had. §5.6's *Open the workbook* downloads the
current report's workbook where one was archived, and otherwise says why there is none. The
verdict sentence above the columns left the page: the drawing has none, and the header's labels
already say it. The weight bar against the operator's limit waits for U5.

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

*Built 28 September 2026* (`requests/new.html`, `requests/_form.html`, `_form_errors.html`,
`web/routes.py::_commission_panel`). The form sits beside *What you are buying*: each depth with
its sections — counted from the section rows, 9, 18 and 18 — and its price, the machine time,
and the decisions a run asks for, counted from the gates' own vocabulary (two on every run, up
to five more). A depth's price is the midpoint of the operator's own finished runs at it; a
depth with none is scaled from the standard runs, or from the workflow's declared estimate, by
the factor the drafting budgets scale by (60 per cent for quick, 140 for full), and says so.
The form opens at Standard, and the button says the chosen depth's price. *Commission* saves
and starts the run in one press, asking the register first (ADR 0128); *Save as a draft*
stays beside it and spends nothing. A company with a current report is met with the refresh
at its price and *Commission a new report anyway*; nothing is saved until one is pressed. The
resolved name appears beneath the subject's fields as the ticker or the exchange changes —
*SEC EDGAR lists TSCO on NASDAQ as TRACTOR SUPPLY CO /DE/* — asked of the register the venue
names (`services.availability.resolve_subject`); whether the company has filed, and in a form
the platform reads, is asked on *Commission*. An unresolvable ticker gets the register's own
sentence in both places; offering the alternatives found waits on a register that returns
them. Full is listed beside the two the table names, because the product has three depths.

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
| Sector | The proposed classification, the valuation models it refuses, and why it was proposed | Confirm the classification |
| Unmapped concepts | **At most 20 rows at a time**, ranked by materiality, each showing the filer's own words and the reference line it would affect; a *"{n} below materiality — skip them"* control | Map these, or skip |
| Assumptions | Each outstanding value with its proposed figure, its source and a justification field | Accept and continue |
| Final | Checks with their results; unverified citations listed individually | Approve the report |

**The unmapped gate's 20-row cap is a hard requirement**, not a preference. Today it can present
several hundred rows, which is the single worst moment in the product.

**Corrected 28 September 2026, on the operator's approval.** A run stops at seven gates, not
six: the table lacked the sector specialist's, which asks the operator to confirm the
classification that decides which valuation models the run may use, and leads with the
models confirming it refuses. Its row is above; *Gate {n} of {m}* counts seven.

**States.** Failed step: `failure`, what failed, and a labelled retry. Stranded run (worker
died): `warning`, *"This run stopped without finishing"*, with **Continue** as a labelled
control — never an instruction to use a terminal. Refused at the final gate: `refusal`, naming
the check, with a **re-measure** action.

**Must not.** Show a percentage. Use a spinner as the only indication of progress. Name a step
by its code identifier.

*Built 28 September 2026* (`runs/console.html`, `web/stages.py`, `web/pages.py::_console_view`).
The rail is read from the step rows the ledger shows, so the two cannot disagree; a stage is a
contiguous stretch of the workflow, and a stage a run has no steps for is left out rather than
drawn as done or waiting. A stage stopped at a gate, a pause or a ceiling reads *Waiting for
you* in `warning` — the state the table above did not name. The ledger names three actors, not
two: *you*, *code*, and *a model*, because a draft a model wrote is not the code's, and a gate
the run passed without stopping is the code's. *What it has spent* is the bar against the
ceiling with the spend by stage beneath it. The panel reads *Gate {n} of up to {m}* while a
conditional gate may still fire, because a count that promised a stop the run never makes is
the overstatement §7.2 exists to refuse.

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

**Corrected 28 September 2026, against ADR 0135, which outranks this document.** The report
argues both sides and takes neither: the adversary writes the case for and the case against,
each point with the lever code struck for it, and the document carries no view of its own.
The section is *The case for and the case against*; a single bear or bull case is not built.

**Actions.** Open the workbook · refresh · print · export.

**Corrected 28 September 2026, on the operator's approval.** The workbook exists (F5, ADR 0134):
*Open the workbook* downloads the valuation as live formulas, archived when the report was
approved. Where none was archived — no discounted cash flow, or a report approved before the
workbook existed — the control is absent and a sentence says which. The same holds for §5.6.

**States.** Superseded report: a band reading *"This report was superseded on {date}"* with a
link to the current one and to the change summary. Withheld figure: `refusal` inline, naming the
rule and what is missing — never a blank or a zero.

**Must not.** Let a footnote resolve to an identifier a reader cannot use. Show a figure with no
marker.

*Built 28 September 2026* (`reports/detail.html`, `reports/_note.html`, `render/html.py::
render_reader`, `web/reader.py`, `static/js/reader.js`). The reader renders the document in
the order the stored report does, and every marker opens the drawer through the page's one
drawer; without scripting, the marker is a link to the footnote's own page. The drawer names
the figure's kind and the verifier's verdict — a recorded calculation with its formula, each
input's own origin and the code version; a stored fact with the filing it came from; a
citation with its excerpt, digest and dates; a derived figure; and an unresolved note as the
refusal it is. The spine counts each section's figures and citations and marks the one being
read. *Open the workbook*, refresh and print lead the actions; the archived files and the
technical record sit behind disclosures at the foot.

**Corrected 24 September 2026, on building ADR 0122 §2 and §3.** Two sections read the
operator's own judgements back, and both take §8.4's shape: shown in full on this page, which
is the operator's copy, and withheld with a sentence that says so from every copy that leaves
— export, print, the evidence pack. The prior research comparison gains a second half, the
premises you held against the prior report and what the record says became of each (the
monitor's latest reading, a withdrawal and its reason, the verdicts a review filed), joined to
the one table here. The change summary's *what broke* rows are yours alone: a copy that leaves
carries the count and not the premise. Under *Actions*, *refresh* now treats a figure a premise
you hold reads as material at one per cent, half the ordinary threshold (ADR 0131, amended).

---

## 9. Report library

**Purpose.** Every report, current and superseded.

**Layout.** Filter row; a table — Company · Date · State (`current` · `superseded` · `refused`) ·
Sections · Cost · Actions.

**Grouping.** By company, newest first, with superseded versions collapsed under the current one
and a count.

**Must not.** Delete anything. A superseded report remains readable for ever.

*Built 28 September 2026* (`reports/index.html`, `web/pages.py::reports_index`). One row per
company, the newest report leading and every earlier version folded under it with a count; a
filter chip per state, each a link, and a filtered view lists reports rather than companies.
**Corrected on building:** the states are the report's own — current, superseded, withdrawn
(ADR 0116) and draft — not *refused*: a run a gate refused leaves a draft, not a report of its
own kind. The cost column reads the run's cost rows, the figure every other page beside a run
prints (§3.19 item 91).

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

*Built 28 September 2026* (`theses/detail.html`, `web/theses/pages.py`,
`services/theses.py::revise_premise`). The premises are the form: each sentence and each test is
a control, and *Save the revision* — in the header, beside the reason under the premises —
revises every premise that changed, with one reason, as two rows each: the old wording withdrawn
with the reason, and a new judgement superseding it (`judgements.supersedes_id`) with the same
basis. A revised premise keeps its predecessor's place, and *How this premise has changed* reads
the chain as a narrative. A premise keeps its kind; changing kind is a withdrawal and an addition,
and the page says so. The metric is a select of exactly the names the monitor resolves, in words
and grouped (ratios, growth, lines as filed); the unit is a select of what the monitor can
compare. Two labels beyond the three specified, because neither of the three is true of them:
*not read yet* for a test no filing has been read against, and *cannot be measured*. §10.3's
panel appears for each premise with an open broken reading: *Revise it* goes to the premise,
*Withdraw it* and *Keep it — say why* each take a reason, and all three close the reading with
that reason — at the thesis gate, bound to the hash of the finding as shown, where its pass is
on record (ADR 0078), and resolved otherwise. *Keep it* records no date to look again: a
finding's resolution has no date, and the monitor reads the premise against the next annual
filing in any case. A refused revision redraws the page with what was typed. The panel beside the
premises states the rule, and warns — never refuses — when no premise carries a test.

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

**Corrected 28 September 2026, against ADR 0137 and ADR 0136.** The check takes a what-if: the
position after the trade as a share of the book, typed into the check itself. With one, the
check shows before and after for the holding's weight, the five largest, the sector's share and
cash, by the risk page's own arithmetic, for this page only — nothing is recorded, and the
decision's size stays the sentence the operator writes. Where an after-figure crosses a limit
the operator stated, a `warning` band says so and the submit control reads *Record it anyway*;
it is never disabled. With the field empty, the check is as the correction above describes.

**States.** No thesis: the form cannot be submitted, and offers the thesis editor inline. No
report: allowed, with a `warning` noting the decision rests on nothing recorded.

**Must not.** Let a decision be recorded without a reason. Auto-fill the reason. Block a trade.

*Built 28 September 2026* (`decisions/new.html`, `decisions/_check.html`,
`web/decisions/pages.py`, `services/risk.py::book_after`, ADR 0137). Recording a decision is its
own page, *What did you decide, and why?*, reached from the journal, a thesis, a company and a
finding with what each knows already chosen. The six verbs are one row of equal buttons with none
chosen; each thesis in the list names the report it was written against, which is the report the
decision rests on (F10, corrected 24 September). The check is its own panel beside the form, with
its own GET form, so it works with scripting off and never nests in the record form; htmx asks
for the panel alone when the listing changes. With a weight typed, the holding's weight, the five
largest, the sector's share and cash read before and after, funded from cash alone
(`calc/consequences`), each after-figure struck with `SourceRef.what_if` — the unrecorded table —
which `persist_context` refuses to save, so nothing typed there can reach the record. A cash
shortfall is said, not hidden. *Record it anyway* and the limit bands wait for U5 (ADR 0136).
*What would make this wrong* as a route to a premise is not built.

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

**Corrected 24 September 2026, on building ADR 0122 §2.** A reading gains a sheet the
specification did not have, *Load-bearing elsewhere*: the other positions you hold whose thesis
asserts a premise on the same metric, where the company shares a confirmed theme or sector
with this one — each with the monitor's latest reading of it and a link to its thesis.
Deliberately narrow, because two holdings sharing *revenue growth* share almost nothing; the
wider question is Ask's to answer when asked (§13). A price move carries no such sheet: a
price is not a premise.


*Built 29 September 2026, as drawn* (`monitor/finding.html`, the price shape). The move and
what the record said when it happened are the left card: the size beside a mark and the word
for its direction, never a minus sign alone; the two closes; and fourteen sessions drawn as
each close against the one before, up or down from a baseline, so the column's place says the
direction and its colour only repeats it. *What the record says* is three sentences, each
with its family's mark — nothing filed, the premises as the pass read them, the market's move
— from the pass's own record of that moment. *Your premises* are the right card, as they stand
now and in the thesis editor's words, each test beside what the monitor last read against it;
under them *Open* the company (the Company page now exists), *Record a decision*, and
*Dismiss — say why*, whose reason form opens in place. The threshold band says what raised the
alert, how many alerts the listing has raised in six months and how many were dismissed, and
carries *Change it*: the listing's own threshold and window, changed in place with the old
values kept in the audit trail. The same control now sits on each watchlist row, where this
section's 23 September correction said it was.

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

*Built 28 September 2026* (`ask/index.html`). The three tiers stand above the question as
cards — what each does, what it costs, and a question that lands in it — with the tier that
spends and adds to the record drawn in the decision colour. The single input, the resolved
tier shown before anything runs, and tier 3's priced confirmation were built in Phase 6a.

**Corrected 24 September 2026, on building ADR 0122 §2.** Tier 1 has a second shape beside the
recompute: a question about what else rests on a belief — *which of my positions rest on the
same premise?* — is answered from the theses and the book, for nothing, and the answer is the
positions as rows, each with the monitor's latest reading of its premise, under one sentence.
It resolves to tier 1 whatever the record holds, because the theses are the record it reads;
the indicator is tier 1's, and the rationale says which of the two shapes the question took.

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

**Corrected 28 September 2026, against ADR 0136.** Each concentration and sector row states
the operator's limit beside its figure where one is set, and *no limit set* where none is;
a row past its limit says so in `warning`, with the word.


*Built 29 September 2026, as drawn* (`risk/index.html`). Titled *What the book is exposed to*.
Concentration and the sectors are the left cards, each figure with its bar beside the
operator's ceiling or *no ceiling set*, the bar in the warning ink past or within two points
of one. The shock panel is the right card: a fall in per cent and a set picked from the book,
its largest holdings, a sector it holds or one holding — the largest N being a new target
under ADR 0106 as amended — stated in one press as a scenario named in its own words, with the
latest stated shock's result beneath: the loss, its share of the book, and what each position
takes. *What this page will not do* says no value-at-risk and no correlation matrix; the
drawing's *no beta-adjusted anything* is not printed, because ADR 0106 decided the page shows
each holding's beta to the book and its contribution, and a sentence the page contradicts
further down is a sentence nobody trusts. The working follows under its own heading: the
movement, every band, each holding, every scenario with the three-shock form, and the
analyst's reading.

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

**Corrected 24 September 2026, on verifying F14 against its "done when".** *Every closed
position is reviewed or explicitly deferred with a date* — and the page had no way to defer
one, so a position nobody was ready to review sat in the queue and on Today indefinitely,
looking the same as one nobody had decided about. Each unreviewed row now carries a second
control beside *Run the reviewer*: **Defer to a date**, taking the date a person will review it
by and a reason. A deferred position moves to a *Deferred* group with its date and reason,
leaves Today's *Not reviewed* rows and the *Review it* card until the date, and comes back the
day after it with the lapsed date beside it. The reviewer can still be run over a deferred
position; a proposal waiting to be confirmed cannot be deferred, because it is waiting for a
person rather than for a date. Nothing is deleted: deferring again appends, and the earlier
dates stay as the record of how long it was put off.

*Built 29 September 2026* (`review/proposal.html`, U6). The form is the drawing: *{ticker} — was
the reasoning sound?*, question one (each premise with its verdict and a line why, then the
reasoning as a whole — *sound*, *sound, with a gap*, *not sound* — and its basis and lessons)
beside question two (the realised return, cost, proceeds and holding period, each a recorded
calculation), and on the right **which of the four was it?** The four are never chosen: code's
outcome dims the pair it rules out, and the operator's answer picks between the other two,
followed by CSS alone as the answer changes. Only *sound* counts as sound, so *sound, with a
gap* that made money is *right anyway*, in amber. The confirmed review names the combination it
landed in. Not drawn, because not recorded: the realised gain in money (the review records the
return, cost and proceeds, not their difference as a calculation) and the return against an
index (no benchmark is read).

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

**Corrected 28 September 2026, on the operator's approval.** The drawing greys out previews of
the findings below the threshold, with counts in them. This section wins: below about twenty
reviewed decisions the page shows the sample and the link to the review queue, and names what
will appear at twenty in words, with no figure.

**Must not.** Show a statistic on a small sample. Rank decisions by return.

*Built 29 September 2026* (`review/analytics.html`, U6; ADR 0105 §4 amended). Below twenty
reviews the page is the band — *{n} / 20 reviewed decisions*, *not enough yet, and this page
will not pretend otherwise*, how many more it needs, and the way to the queue, labelled with
how many closed positions wait — and beneath it what will be here at twenty, in words and with
no figure. From twenty: the four as a two-by-two with their names, then the answers, the
premise verdicts, the holding against the horizon, whether a decision is on record, and whether
the reviewer's answer was confirmed — every table with its `n`, the parts in words. **Calibration
is not built**, and the page says so: a decision records no stated confidence, so there is
nothing to calibrate against the outcome until the decision form asks for one.

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

*Restyled 28 September 2026, and not yet what this section describes* (`skills/list.html`).
My methods list each method with its state in words and its record. **The attack report is not
built.** The corpus exists — twenty-six escalations a method file can attempt, each named with
the layer that must stop it (`tests/skill_corpus.py`) — but it runs in the test suite, against
the platform's defences, not against each method as it is added; a method is contained by
those layers structurally (invariant 7) rather than admitted by a per-method pass. Showing a
method's own attack report needs the corpus run per method, and its outcome stored, before
this page can draw it.

**Corrected 24 September 2026, on building ADR 0122 §2.** Each method's row carries its record
where it has one, in a sentence: the approved reports your runs pinned it in, how far their
confirmed drivers landed from what was later filed, and what became of the theses written
against those reports — the decisions, the reviews' process grades, the verdicts on the
premises. A method pinned only on runs that never reached an approved report has no record,
and the row says nothing rather than a zero. The record is read from your own runs and never
from anybody else's, and it is a reading of the record, not a source: nothing on this page can
be cited.

---

## 18. Knowledge

**Purpose.** Teach the system a filer's vocabulary, once.

**Layout.** The unmapped queue, **at most 20 rows at a time**, ranked by materiality; each row
shows the filer's own tag, the filer's own label for it, the value, and the reference line it
would affect, with the mapping control beside it.

**Below materiality**, a single collapsed row: *"{n} concepts below 5% of their reference line"*
with a control to review or skip them as a group.

**Must not.** Present an unranked wall of tags. Ask the same question on a later run.

*Restyled 28 September 2026, and not yet what this section describes* (`knowledge/index.html`).
The page is the knowledge map's measurements — size, shape, coverage, freshness and the vault's
health — in words. **The vocabulary queue is not built.** The unmapped gate ranks and caps a
run's concepts (Phase 1.5), and `services/curation.py` ranks them across every run into a
worksheet; but a mapping the operator makes is not stored for later runs, so the next run asks
again. The queue, and the store that stops the question being asked twice, are a feature of
their own.

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

**Corrected 28 September 2026, against ADR 0136.** *Book* holds the operator's limits — a single
position, the five largest, and any sector — each blank until stated, each superseded rather
than edited, with no default and no suggestion; and the stated shock, as the risk page keeps it.

*Book built 29 September 2026* (`platform/book.html`, `/platform/book`). The two whole-book
limits always, blank until stated, and each sector limit in force, with what each means as the
book stands beside it; a sector limit is added from the sectors the book holds; the stated
shocks are listed and stated on the risk page; the limits replaced or withdrawn are kept at the
foot with when and why.

*Built 29 September 2026* (`platform/index.html`, `/platform`, U6). One page, *Settings and
state*, where the Platform destination opens: Costs (this month against the ceiling, by the
work that spent it, and **Discarded** — the spend on replies the schema refused, summed from
the runs recorded with that stop reason), Your book's limits (each as Book states it, and the
latest stated shock), Monitoring (the default threshold and cadence, when the checks run and
last ran, the dismissals in six months), Data and evidence (each source *Ready* or *No key*,
and the documents held), Backups and Health (the worker, the daily pass, the queue, the last
run, the database). Each sheet links to its editor — Settings, Book, Costs — which stay tabs,
so nothing is reachable only here; the JSON probe left the tabs. **Backups are not recorded**:
a backup is taken and restored from the terminal into a folder the operator chooses, and
neither act writes anything the page can read, so the sheet says *Not recorded* and *Never
recorded* in amber rather than the drawing's *Today, 04:00*, and has no *Prove it restores*
control. Recording both, and a restore proved into a scratch database, is a feature of its own.
