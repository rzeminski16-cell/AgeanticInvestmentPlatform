# The request: list, form, detail, removal

*Where a research run is commissioned. The form is the largest input surface in the product.*

---

## At a glance

| | |
|---|---|
| **URLs** | `/requests` · `/requests/new` · `/requests/{id}` · `/requests/{id}/edit` · `/requests/{id}/remove` |
| **Who arrives** | The operator, deciding to research a company |
| **From where** | "Start a research request" on the main menu; the Requests nav item; the work list |
| **What they came for** | To commission a report — or to find one they commissioned earlier |
| **Templates** | `requests/list.html`, `new.html`, `edit.html`, `_form.html` (157 lines), `_field.html`, `detail.html`, `remove.html`, `immutable.html`, `not_found.html` |
| **Token state** | 43 · 2 · 4 · 18 · 39 · 63 · 40 · 14 · 6 raw ramp classes respectively |

---

## The job

**Collect twenty fields of mandate without making the operator feel interrogated — and make
it obvious which four of them actually matter.**

Four fields are genuinely required and consequential: the company, the as-of date, the depth,
and the cost ceiling. The other sixteen are optional refinements that make the report speak to
this operator's actual situation rather than in the abstract. **The form currently presents
all twenty at roughly equal weight**, and that is the central design problem here.

---

## `/requests/new` — the commission form

### The fields, as grouped today

**Company** — 4 fields
| Field | Type | Required | Notes |
|---|---|---|---|
| Company name | text | ✓ | e.g. "Microsoft Corporation" |
| Ticker | text | ✓ | Letters, digits, `.` and `-`. MSFT, BRK.B, RIO.L |
| Exchange | select | ✓ | **US and UK main markets only.** OTC venues unsupported |
| ISIN | text | | Check digit validated if given |

**Timing and currency** — 3 fields
| Field | Type | Required | Notes |
|---|---|---|---|
| As-of date | date | ✓ | The analysis is performed as at this date. Cannot be in the future |
| Base currency | select | ✓ | What the valuation is expressed in |
| Reporting currency | select | | Blank means "same as base" |

**Mandate** — 4 fields
| Field | Type | Required | Notes |
|---|---|---|---|
| Investment horizon (months) | number | ✓ | 1–240 |
| Horizon label | text | | "Medium term, through the next capex cycle" |
| Depth | select | ✓ | Quick is a screen; full is the complete report |
| **Point-in-time mode** | checkbox | | **Default on.** Refuses any source published after the as-of date |

**Portfolio context** — 3 fields, all optional. Current weight %, maximum weight %, benchmark.
*"Given, it lets the analysis speak to your actual position rather than in the abstract."*

**Your priorities** — 5 fields, all optional. Risk tolerance, ESG weighting, liquidity
constraint, plus two textareas:
- **Questions you want answered** — one per line. *"These are carried into the plan, so the
  report addresses your question rather than a generic template."*
- **Excluded sources** — one domain per line. Never cited.

**Cost ceiling** — 1 field, required. *"Enforced in code, not merely reported. A run projected
to exceed this pauses for your decision rather than quietly spending."*

### How input is collected and validated

**Percentages are typed as percentages and stored as fractions.** The form asks for `2.5`
because that is how people say it. Asking an operator to type `0.025` invites somebody to type
`2.5` and silently commission research against a 250% position.

**Textareas are one item per line.** The natural HTML representation of a short list.

**Validation is entirely server-side.** The form carries `novalidate` deliberately — the same
code validates whether the submission came from a browser, from htmx or from the JSON API, so
the form and the API cannot reach different conclusions.

**On failure, everything typed is handed back.** Losing a carefully written set of focus
questions to a mistyped ticker is the kind of thing that stops people using a tool.

**Errors arrive in two rounds, not one, and this is a known limit rather than a bug.** Every
*schema* problem is reported together; every *service* problem — a future as-of date, an
over-budget ceiling, a universe exclusion — is reported together. But the service rules cannot
run on a payload that failed to construct, so a submission with both kinds shows the schema
problems first and the rest on resubmission. **The design should make a second round feel like
progress rather than like the form rejecting things one at a time.**

**With htmx**, only the error fragment is re-rendered, into a live region. **Without it**, the
identical POST returns the full page. The validation is the same either way.

---

## `/requests` — the list

A table: Company · Ticker · Exchange · As at · Depth · Status · Actions.

Per row: **Archive** (one click, no dialogue, because one more click undoes it) and **Remove**
(a link to a confirmation *page*, because the counts have to be read before the decision and a
`confirm()` box cannot hold them).

A link to the archive, showing its count, when there is one. A "New request" action.

---

## `/requests/{id}` — the detail

The mandate as saved, in the same groups as the form, plus the run situation:

- **Never run** — *"Saved as a draft. Nothing has been fetched and nothing has been spent."*
  With **Start the run**.
- **Has a run** — *"The last run <status> and…"*, with **Open the … run**, and **Start a new
  run** where superseding applies.
- **Edit this request** — only while no run exists.
- **Delete** — only for a draft that has never been run. Currently guarded by a JavaScript
  `confirm()`, which is the one place in the product that uses one.

---

## `/requests/{id}/remove` — the confirmation page

Lists exactly what would be destroyed, with counts. Irreversible. **The audit chain, the spend
ledger and the artefacts survive** — that is worth saying on the page, because it is the
difference between deleting a request and erasing history.

---

## States

| Surface | States |
|---|---|
| **List** | Populated · empty · archived view · empty archive |
| **Form** | Blank · prefilled (edit) · rejected with field errors · rejected with a summary · CSRF token expired |
| **Edit** | Available · **refused because a run exists** — `immutable.html`, with the reason on the page rather than a bare 409, because an operator who followed a stale bookmark needs to know the request is now a record of something that happened |
| **Detail** | Draft, never run · running · awaiting approval · failed · succeeded · archived |
| **Not found** | Its own page rather than a generic 404 |
| **Remove** | The confirmation, with counts · refused |

---

## What is wrong today

**Twenty fields at equal weight.** Five `fieldset`s stacked vertically, each a grid of inputs
with hints beneath. Sixteen of the twenty are optional, and nothing about the layout says so
until you read every label. A first-time operator cannot tell whether they are filling in a
form or answering an interrogation, and the answer is that four fields would do.

**The point-in-time checkbox is the most consequential control on the page and looks like the
least.** It is a checkbox in a bordered box in the middle of the third group. What it does —
refuse every source published after the as-of date, which is the thing that stops the analysis
being contaminated by hindsight — is explained in small grey text beneath it. Turning it off
"silently invalidates backward-looking analysis". That is not a checkbox.

**The cost ceiling is last.** The field that decides when the run stops spending the
operator's money is at the bottom, after ESG weighting.

**The two textareas are the highest-value optional fields and read as an afterthought.**
"Questions you want answered" is carried into the plan and shapes the whole report. It is a
4-row textarea below a three-column grid of dropdowns.

**Nothing estimates what this will cost before it is submitted.** The operator types a
ceiling with no anchor. The platform knows what a run of each depth typically costs; the form
does not say.

**The list has no dates and no cost.** No created-at, no last-run, no spend. Six months of
requests sorted by nothing the operator can see.

**Status is an uppercase enum in a grey pill**, in both the list and the detail.

**Delete uses a `confirm()` dialogue** while Remove uses a proper confirmation page. Two
destructive actions, two different interaction models, and the weaker one is on the button
that is available more often.

---

## What to improve

**1. Make the four required fields the form, and the rest progressive.** Company, as-of date,
depth, ceiling — then everything else behind a clearly-labelled "Refine this mandate" that is
plainly optional. **Watch the constraint:** it must work without JavaScript, so this is
`<details>`/`<summary>`, or a second page, not a scripted accordion.

**2. Promote point-in-time to a decision, not a checkbox.** It is a two-option choice with a
strong default and real consequences. Design it as such, and put it near the as-of date, which
is the field it acts on.

**3. Move the cost ceiling next to the depth control** and anchor it. "Full reports on this
setup have cost £4–£9" turns a blind number into a decision.

**4. Give the questions field the room it deserves.** It is the operator's own voice in the
report and it is currently a small box near the bottom.

**5. Add dates and spend to the list**, and make it sortable or filterable — filtering rows
already on the page is permitted and the mechanism exists.

**6. Unify the two destructive flows.** One confirmation model. The page-based one is right.

**7. Design the two-round error case explicitly.** It is going to happen, it is structural,
and the difference between "you fixed one thing and it found another" and "here is the next
thing" is entirely presentational.

---

## What must not change

**Validation stays on the server.** No client-side rules — they would be a second
implementation, and the copy that drifts is always the one attached to the form.

**The form works with scripting off.** It commissions spending.

**Everything typed comes back on a rejection.**

**Percentages in, fractions stored.** Never ask the operator for a fraction.

**Editing is refused once a run exists**, with the reason on the page. The request is then a
record of something that happened.

**Removal is a page that lists what will be destroyed**, and says what survives.

**The exchange list is US and UK main markets only.** Not a placeholder — the universe is
deliberately bounded, and the form should say so where somebody would otherwise look for their
venue.

---

## Done when

- A new operator can commission a sensible run having made four decisions.
- The point-in-time choice is impossible to make by accident.
- The cost ceiling is chosen with some idea of what a run costs.
- A rejected submission loses nothing and reads as one problem list, not a queue of them.
- The list answers "what have I researched, when, and what did it cost?" at a glance.
- Both destructive actions use the same, page-based, confirmation.
