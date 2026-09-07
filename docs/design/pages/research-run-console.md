# The run console

**`/runs/{job_id}`** — the hub of the research tool, and the page an operator sits on longest.

---

## At a glance

| | |
|---|---|
| **URL** | `/runs/{job_id}` |
| **Who arrives** | The operator, immediately after starting a run, and repeatedly for the next half hour |
| **From where** | Starting a run; the work list on `/`; a request's detail page; returning from every gate |
| **What they came for** | Three different questions, in this order of frequency: *is it still alive?* · *does it want something from me?* · *what has it cost?* |
| **Template** | `runs/console.html` (398 lines) + `static/js/console.js` (316 lines) |
| **Token state** | **119 raw ramp classes.** The second-worst page in the product |

---

## The job

**Distinguish a healthy run mid-thought from a dead worker, and say clearly when it wants
something.**

That is a harder problem than it looks, and it is the reason this page exists rather than a
spinner. **A step that calls a model routinely changes nothing visible for two to five
minutes.** Nothing is recorded until the model finishes reasoning. So the honest signals are:
which step, how long it has been in it, and when the server was last heard from — and the
page must convey "working" without pretending to more certainty than it has.

---

## What is on it

### The header block
- A back link to the request
- The company name, as `h1`
- Ticker · as-of date · workflow version, as a subtitle line
- **Status** — a chip, currently the raw enum in uppercase (`RUNNING`, `AWAITING_APPROVAL`,
  `FAILED`, `SUCCEEDED`, `CANCELLED`)
- **Spend so far**, in pounds

### Up to three banners, conditionally

**Waiting for you** — the run stopped at a gate. Says plainly: *"Nothing further happens, and
nothing further is spent, until you approve or reject it."* Carries a button to the gate that
is actually pending, plus "Review the plan" and "Review the draft" always.

**Stopped on budget** — two distinguishable scopes, and the distinction matters because the
remedies differ. A *per-run* cap: raise this run's ceiling, on this page, in the spend panel
below. A *monthly* cap: raising this request's cap will not release it; change the monthly
budget in settings, or wait for the month to turn. The framing is deliberate and worth
keeping: *"the next step would take this run past a spending cap, so it stopped before making
the call rather than after paying for it."*

**Stopping / Cancelled** — with the timestamp and, if given, the reason. While still running
it explains that the run stops at the *end* of the current step: a filing already being
fetched, or a model call already made, is not abandoned halfway.

### Spend against ceiling, and the raise

Spend and ceiling as one figure, then the sentence that the run stops *before* a step that
would cross the line rather than after paying for it.

**The raise lives here**, appearing from `budget_warn_ratio` of the ceiling — the same
fraction the engine already warns at, so the offer and the warning are one opinion — and on
any run stopped against its own cap whatever it has spent, because a run stopped by a
*projection* can be a long way short of its ceiling and is the case that needs it most.

It is here rather than on the request because the request page refuses every edit while a
run is live, correctly: a moved as-of date would falsify evidence already gathered. The cap
is the one field that does not, so it is the one field with its own operation. Before it
existed, both spend guards named a remedy — "raise the cap on this request" — that nothing
in the interface allowed.

Only upwards, never above the platform's own per-run budget, and never shown to a run
stopped on the monthly ceiling or to one that has finished. A request already at the
platform's budget is told where that lifts instead of being shown a form that would be
refused.

### The evidence links
Three links in a row: *Sources and provenance* · *Claims and their evidence* · *Valuation and
comparables*. On the console rather than only on a finished report, because "what has this run
actually gathered?" is worth asking while it is still running.

### The step list
**Every declared step, not only the ones that have started.** A run showing one line for its
first five minutes says nothing about how much is left — and "nothing is happening" and "step
one of nine is thinking" look identical.

Per step: a status dot, the step key in monospace, an elapsed clock, the status, the cost in
pounds, and — on failure — the error message and its stable code, each on its own line.

Above the list: *"4 of 19 done"*.

The dot pulses on the running step. It is a liveness cue and nothing more: it says the server
recorded this step as started and not finished, which is exactly what it looks like it says.

### The progress note
While not terminal, a block that says what "still working" looks like when nothing has changed
for four minutes:

- *"Working on `draft`."*
- The explainer: two to five minutes is normal; past about ten minutes on one step, look at
  the terminal running `just worker` — that is where this run is actually happening, and where
  a failure appears first.
- A "server last seen at…" line, filled by a heartbeat on the event stream.

### The cancel form
A reason field (placeholder: *"Wrong as-of date"*) and a button. Only while the run can still
be stopped.

---

## Inputs

| Control | Type | Notes |
|---|---|---|
| Cancellation reason | Optional free text | Recorded on the cancellation |
| Cancel this run | Submit | Records a *request* to stop. The run does not stop here — it stops at its next step boundary, which the page then shows |

**A page that reported the run as stopped the moment the button was pressed would be wrong for
as long as the current step took.** Design the feedback for that gap.

---

## States

| State | What the page shows |
|---|---|
| **Queued** | *"Queued. The worker picks this up within a second or two."* Every step listed, none started |
| **Running** | A pulsing dot on one step, an elapsed clock, spend rising |
| **Awaiting approval** | The banner, the pending gate's button, and *"nothing further is spent"* |
| **Stopped on budget** | The budget banner, with per-run and monthly worded differently |
| **Stopping** | Cancellation acknowledged, run still finishing its step |
| **Cancelled / Failed / Succeeded** | Terminal. No live updates, no cancel form, no progress note |
| **Failed at a step** | The step carries its message and its code. The message, not the whole error dictionary |
| **No scripting** | A `<meta refresh>` inside `<noscript>` reloads the page on a timer. Slower, identical information, never a blank page for a run that is still spending money |
| **Event stream unavailable** | Same fallback |

**The `<noscript>` containment is load-bearing and worth knowing about:** a declarative refresh
is scheduled at parse time and removing the element afterwards does not cancel it. An earlier
version emitted it bare and had the script delete it, which still reloaded the page every few
seconds and threw away the event stream it had just opened.

---

## What is wrong today

**The step keys are raw identifiers.** `acquire`, `classify`, `red_team`, `render` — in a
monospace font, which announces "this is for developers". The operator knows what a peer set
is; they do not necessarily know that `classify` is the step that decides whether their
company is a bank. **Nineteen technical tokens is the main content of the main page of the
main tool.**

**The gate sequence is invisible.** Five of the seven gates are conditional and the run does
not know which will fire — but the page does not even show the two that *always* fire. An
operator on their first run has no way to learn the shape of what they are in. This is the
single largest missed opportunity on the page.

**The status chip is an uppercase enum.** `AWAITING_APPROVAL` is not a sentence and it is the
most prominent word on the screen.

**Cost has no context.** Per-step costs in pounds and a running total, with the ceiling
nowhere on the page — so "£6.40" answers nothing. The cap is on the request, one navigation
away. The run stops at that number, which makes it the most decision-relevant figure here.

**The evidence links are three underlined links in a row**, visually identical to the back
link and carrying no indication of whether there is anything behind them yet. Early in a run,
all three lead to empty pages.

**The workflow version sits in the header** beside the ticker, at the same weight, and means
nothing to the reader.

**A failed run offers no route forward.** The error appears on its step and the page stops
there. The recovery — supersede the request and run again — is not on the page, and *a failure
one step from the end costs the entire run again*, which is a known and expensive limitation
the operator should meet here rather than discover.

---

## What to improve

**1. Make the journey legible.** Show the gate sequence — the two that always fire, and the
conditional ones as conditional. Something like *"Plan ✓ · Sector — · Peers ✓ · Assumptions
now · Review to come"*. The operator should be able to learn the shape of a run by doing one,
and should always know how many decisions are probably left.

**2. Name the steps in English.** Keep the keys available — they are what a log line and the
worker terminal say, and the explainer sends the operator to that terminal — but they should
not be the primary label. `red_team` is "Challenging the thesis".

**3. Put the cost ceiling on the page, beside the spend.** Both figures exist. £6.40 of £8.00
is a different sentence from £6.40, and it is the sentence that decides whether to keep going.

**4. Rework the "is it alive?" affordance as the primary content when running.** Currently the
step list dominates and the liveness signal is a block underneath it. For most of the run, the
liveness signal *is* the page: current step, how long, last heard from, and what is normal.

**5. Give a failed run somewhere to go.** State plainly what recovery costs and offer it.

**6. Consider the two audiences on this page.** During a run the operator wants liveness; at a
gate they want the decision; afterwards they want the evidence. Three different pages'
worth of priority, one URL, and it currently uses one layout for all three.

**7. Make the evidence links say whether there is anything behind them.** A count would do it.

---

## What must not change

**Every declared step is listed, not only started ones.** Otherwise "nothing is happening" and
"step one is thinking" look identical.

**The page renders complete on the server first.** The event stream keeps it current; it never
builds it. When the stream is unavailable the page still shows the state at the moment it was
requested.

**The script may own a dot colour and an elapsed clock, and nothing else.** It reloads the
page rather than inventing a step row. Chrome the status implies — the approval banner, the
budget notice, the report link — is all server-rendered, and the script re-fetches the page
when the status diverges rather than rendering the same banners a second time in JavaScript.

**The spend figure's digits come from the server.** The script prepends a "£" glyph and that
is the outer limit of what it may do to a number.

**The `<noscript>` refresh stays inside `<noscript>`.**

**Cancellation is a request, not an event.** Do not design feedback that implies the run
stopped when it has not.

**The budget banner distinguishes per-run from monthly.** Different remedies; reporting one as
the other sends the operator to change the wrong number.

---

## Done when

- An operator on their first run can say what has happened, what is happening, and roughly how
  many decisions remain.
- "Is it alive or is it dead?" is answerable in under two seconds, from across a desk.
- The spend is legible against its ceiling without leaving the page.
- No raw step key, enum value or workflow version is the most prominent text on the screen.
- A failed run tells the operator what to do next, and what it will cost.
- With scripting off, the page still refreshes and still shows everything.
