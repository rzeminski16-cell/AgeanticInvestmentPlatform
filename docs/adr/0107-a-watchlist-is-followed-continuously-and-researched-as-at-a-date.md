# ADR 0107 — A watchlist is followed continuously and researched as at a date, and the queue spends a standing budget that is not one run's cap

**Status.** Accepted
**Date.** 2026-09-03
**Required by.** Roadmap §3.10, whose two prerequisites — the standing budget and the two
clocks — this record settles.
**Extends.** ADR 0075 (the portfolio clock is not the research clock), ADR 0072 (a work
order is the run root), ADR 0078 (an unattended run stops at a ceiling rather than pausing
for nobody), ADR 0014 (a budget is a control, not a report).

## Context

Every research run the platform has made was commissioned by hand: a form, a date, a cap,
a button. The roadmap has carried a watchlist since the Investment OS plan — companies the
operator follows and has not researched, with the queue of what to research next and what
it would cost — and held it back on two questions the plan named and did not answer.

**Which clock?** ADR 0075 separates when something became true of the book from when the
platform came to know it, and separates both from the date a report is dated as at.
Following a company is not a fact about the book and not a publication: it is a standing
intention. Researching it is a run, and a run is as at a date. Conflating the two —
"researched" as a property of the entry rather than of a dated run — is the mistake the
roadmap names.

**Which budget?** Every model call is capped twice, by the run's own ceiling and by the
month's (invariant 6, ADR 0052). Neither is a budget for a *queue*. A queue that spent to
the monthly cap would leave nothing for the report the operator wanted this afternoon; a
queue with no ceiling of its own is a list that spends. The plan called for "a standing
budget that is not one run's cap", and the tool registry has said so on the placeholder
since the launcher existed.

## Decision

### 1. An entry is a standing intention with one clock, and a commission is a run with the other

`watchlist_entries` is what the operator follows: a listing (name, ticker, exchange, checked
against the same universe rule a research request is), a sentence saying what would make it
worth researching, `followed_at` on the database clock (ADR 0075's `recorded_at`, under
its own name, because nothing outside can be trusted to say when the operator started
following), and a withdrawal with a reason. Nothing on the row says "researched"; that is
the other clock's word.

`watchlist_commissions` is each time the queue turned an entry into research: the entry,
the research request it created (ADR 0072's run root, with the request as its detail row),
the **as-of date** the run is dated, the cap it was given, who commissioned it and when.
The request is an ordinary research request — the same validation, the same gates, the
same report — and the commission is the link that says it came from the queue. Deleting
the request keeps the commission with its date and loses the link, like a cost row.

An entry's state on the page is read from its latest commission's run: *queued* with none,
*commissioned* while the run is alive, *researched* once a report exists, *stopped* if the
run died. Following again after a report is the ordinary case, and a second commission is
a second dated run.

### 2. The standing budget is the queue's own monthly ceiling, and a run's cap is unchanged

`watchlist_budget_gbp` is a setting: what the queue may commit in a calendar month, on the
UTC month `spend_this_month` uses. Its **room** is the budget less what this month's
commissioned runs have spent less what the ones still alive may yet spend up to their caps.
A commission is refused, by name, when the run's cap would not fit in the room. Each run
keeps its own per-run cap and the month's cap applies on top: the standing budget bounds
what the queue may *start*, and takes nothing from the two guards that bound what any run
may *spend*.

Reserving the cap rather than the spend is deliberate. A run at gate one has spent pence
and may spend pounds; a queue that counted only what was spent so far would commission the
whole month's budget in an afternoon and find out in a week.

### 3. The queue is the followed entries in the order they were followed, and it drains by hand or on a schedule

The queue is every entry with no live run and no report on its latest commission, in the
order followed. *Commission* on an entry starts its run now, as at today or a date the
operator states. *Commission the next* walks the queue and starts runs while the standing
budget affords them, stopping at the first it cannot. `aer queue` does the same from a
terminal, for a scheduler, and exits non-zero when it stopped short — the shape `aer
monitor` has.

Every run the queue starts stops at gate one for the operator, as every research run does.
The queue automates commissioning, not approval; ADR 0078's rule that an unattended run
stops rather than pauses for nobody is why the budget is reserved at the cap.

## What was rejected

**Marking an entry researched.** The word belongs to a dated run. An entry followed for two
years has been researched as at three dates or none, and a flag cannot say which.

**A queue budget as a share of the monthly cap.** A share is a second number the operator
has to reason about in relation to a first. A pound figure is what a budget is.

**Priority ordering.** A reorderable queue is a small feature with a form and a drag handle,
and the wrong first shape: *Commission* on any entry already jumps the queue for the case
that matters.

**Automatic re-research on a cadence.** A cadence is a claim that the last report is stale,
and staleness is what the monitor measures (ADR 0103) against a thesis, not a calendar.

## Consequences

The Watchlist is the ninth working tool, and the launcher's placeholder pattern has no row
left to show; the pattern stays for the next tool. The page shows the standing budget with
its room and what a run typically costs, the entries with their state and the research
each led to, the follow form, and the commission and stop-following forms. The work list
carries a queue the budget can afford and a queue it cannot until next month.

The cost is a second monthly figure beside the cap, and a queue that can only ever spend
what the operator set aside for it — which is the point.
