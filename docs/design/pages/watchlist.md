# The Watchlist tool

**`/watchlist`** — companies you follow and have not commissioned research on, the queue
of what to research next and what it would cost, and the standing budget the queue spends.

---

## At a glance

| | |
|---|---|
| **URLs** | `/watchlist` · `/watchlist?withdrawn=1` · `POST /watchlist` · `POST /watchlist/{entry_id}/commission` · `POST /watchlist/{entry_id}/stop` · `POST /watchlist/commission-next` |
| **Who arrives** | The operator, with a company in mind and no report yet |
| **From where** | The launcher, the Watchlist nav item, a work-list row (*not started*) |
| **What they came for** | *What am I following, what should I research next, and can I afford it this month?* |
| **Templates** | `watchlist/index.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Keep the list of what is worth researching, with why, and turn the next one into an
ordinary research run at a stated date without the queue spending more than the operator
set aside for it.**

---

## Three ideas that shape everything on this screen

**1. Two clocks** (ADRs 0075, 0107). An entry is followed *from* a date the database
stamped; a commission is research *as at* a date the operator chose. The row shows both
and never merges them.

**2. A state is read from a run, not stored on the entry.** *Queued* with no commission,
*commissioned* while the run is alive, *researched* once a report exists, *stopped* if the
run died. A second commission after a report is the ordinary case.

**3. The standing budget bounds what the queue may start.** A pound figure for the month,
less what this month's commissioned runs spent, less what the live ones may still spend up
to their caps. Each run keeps its own cap and the month's cap applies on top.

---

## What is on it

**The verdict** leads with the counts: *"Two companies are followed and not yet researched;
one run is alive; one has been researched; the standing budget affords one more run this
month."* With nothing followed, a sentence pointing at the form.

**The standing budget** — four figures: the budget for the month, spent, reserved, room
(with how many runs at the cap it holds).

**Followed** — in the order followed, which is the order the queue runs in. Per row: the
company and its listing, the *why*, followed on, and where a commission exists the as-of
date and when it was commissioned, links to the request, the run (with its state and cost)
and the report. Once an entry has been commissioned more than once, a closed disclosure —
*Every commission, n so far* — opens to each commission newest first: researched as at,
commissioned on, and its request, run (with state and cost) and report; a commission whose
request was removed says so. A status chip. **Commission** with an *As at* date defaulting
to today (absent while a run is alive), and **Stop following because** with a required
reason. Beneath the list, **Commission the next the budget affords**, disabled with the
reason when the budget is spent, and beside it the cost guidance the request form shows
(what runs at the standard depth have cost, or an honest admission of no history) — beside
the button that is about to spend it. *Show the companies no longer followed* switches to
`?withdrawn=1`.

**No longer followed** (`?withdrawn=1`) — each entry put away: company and listing, the
*why*, followed on and stopped on, how many commissions it had, the reason it was stopped,
and its last report where one exists.

**Follow a company** — company, ticker, exchange (checked against the universe a request
is), and *what would make it worth researching*.

A commission sends the operator to the run it started; a commission the queue could not
reach because Redis was down leaves the run recorded and says so.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Company, ticker, exchange | not blank; inside the universe (UK or US listing, not a fund); not already followed | `follow`, through `check_universe` |
| Why | free text | — |
| As at | a date, else today | the handler |
| Stop following because | not blank | `stop_following` |
| Commission | a run's cap must fit the standing budget's room; no run alive | `commission` |
| Limit (commission next) | an integer, else unbounded | the handler |

Every refusal is a sentence on the problem page with the status the error carries. A
budget refusal names the room and the cap, and the three ways out.

---

## States

| State | What it shows |
|---|---|
| **Nothing followed** | The empty state above the form; the budget sheet still shows the room |
| **Queued** | A row with the commission form; a *not started* row on the work list naming the next in the queue |
| **Commissioned** | The row links the request and the run; no commission form; the budget reserves the cap |
| **Researched** | The row links the report; the commission form is back, for a later date |
| **Stopped** | The row says the last run died and is back in the queue |
| **Withdrawn** | Off the followed list; on `?withdrawn=1` with its reason and its commissions |
| **Budget spent** | The verdict and the button say so; a commission is refused by name |
| **Queue unreachable** | The run is recorded and the notice says to start it from its page |
| **Not yours, or no such entry** | 404, the same answer for both |

---

## What is wrong today

**No reordering.** The queue runs in the order followed; jumping it is *Commission* on a
row, which is the case that matters, but a long list cannot be reshuffled.

**The commission's cap is the per-run default.** A run that needs more is raised from its
own page afterwards, and the standing budget then reserves the original cap.

**A withdrawn entry cannot be followed again.** Following the same listing again is refused
as already followed; the way back is a new entry once the old one is put away, which the
service does not yet offer.

---

## What to improve

**1. A withdrawn list** — done, at `?withdrawn=1`, as the decisions journal has.

**2. The cost guidance beside the button** — done. It says what *Commission the next* is
about to spend, and leaves the budget sheet to its four figures.

**3. A "researched as at" history per entry** — done, as a closed disclosure once an entry
has more than one commission.

**4. Following again.** See above: a stopped entry that becomes interesting again needs a
door, and the record of why it was stopped should stay attached.

**5. Reordering**, if the list grows long enough for the order followed to stop being the
order wanted.

---

## What must not change

* **Nothing on this page is a figure a report rests on.** The budget is money and the
  states are read from runs.
* **A commission is an ordinary research request** with its gates; the queue automates
  commissioning, never approval (ADR 0107 §3).
* **The standing budget reserves the cap, not the spend**, for a live run (ADR 0107 §2).
* **The two clocks are never merged**: followed on is the database's, as at is the operator's.

---

## Done when

* A company followed on the form appears queued with its reason, and on the work list as
  not started.
* Commissioning from the row lands on the run it started, the row reads commissioned with
  the as-of date, and the budget shows the cap reserved.
* Stopping following with a reason takes the row away, and puts it on the list of what is no
  longer followed with that reason.
* An entry commissioned twice opens to both commissions, newest first, each dated as at.
* `aer queue` commissions the next entries the budget affords and stops short with the
  reason, exiting non-zero when it did.
