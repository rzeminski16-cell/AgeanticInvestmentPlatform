# The tools, and what success means for each

*Fourteen surfaces across four stages, plus the two that sit above them all. Each entry says
what the tool is, what it must make possible, the measurable bar it has to clear, and what
exists today.*

**How to read the bar.** A success criterion here is a user outcome with a number attached,
not a feature. "Shows the concentration" is a feature; "the user knows what this trade does to
their book before they place it" is an outcome. Where a number is a guess it says so.

---

## Above the stages

### A. Today — the front door

**What it is.** One page answering one question: *what needs me today?*

**Surfaces.** A single ranked queue. No charts, no totals, no welcome.

**What it must make possible.**
- Four kinds of item, in this priority: a **gate waiting** on a live run; a **premise that
  broke** on a held position; a **decision with no thesis** behind it; a **report gone stale**
  against its refresh cadence.
- Each item is one line, says why it is there, and goes straight to the thing that resolves it.
- An empty queue says so plainly and offers the one thing worth starting: research a company.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A returning user knows what to do within **10 seconds** of the page loading | Timed on a first-time observer; no scrolling required to see the top item |
| **Zero** items require a second page to understand why they are queued | Every row carries its own reason |
| The queue is **empty when nothing needs doing** — it never manufactures work | No "suggested" or "you might like" rows, ever |

**Today.** Does not exist. The current home is an overview page of counts.

### B. The company page — the object view

**What it is.** Everything the system knows about one company, on one page. **This is the most
important new surface in the design**, and the one that makes the loop legible.

**Surfaces.**
- **Header** — name, ticker, last close, and the state: *researched · held · watched · closed*.
- **The view** — the thesis, its premises, and which of them currently hold.
- **The position** — size, cost, value, weight in the book, if held.
- **The record** — reports (current and superseded), decisions, and the monitor's last verdict.
- **The actions** — refresh the report, revise the thesis, record a decision, open the workbook.

**What it must make possible.**
- Arriving from anywhere — search, portfolio, watchlist, an alert — and finding the same page.
- Reading the current state of a belief without opening the thesis tool.
- Seeing that a report is stale, and refreshing it, without going to the research tool.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| Every question of the form *"what do I know about X, and what do I think?"* is answered **without leaving the page** | Walk the ten most common questions; none may require navigation |
| A user who has never seen the system can say what the company's status is within **20 seconds** | Observed |
| **Zero** orphans: no report, thesis, decision or position exists that this page does not show | Asserted in code |

**Today.** Does not exist. The record is scattered across five tools.

---

## Stage 1 — Research

### 1. Equity Research

**What it is.** The engine that turns a company and a brief into a cited, immutable report.

**Surfaces.**
| Sub-tool | What it is |
|---|---|
| **New request** | The commission form: company, brief, focus questions, and — new — the planned weight, horizon and purpose that the recommendation section reads |
| **Active run** | The live run: what stage it is at, what it has spent, and the gate waiting on the operator |
| **The gates** | Six or seven decision points, redesigned as one reviewable queue rather than seven interruptions |
| **Report library** | Every report, current and superseded, by company and by date |
| **Report reader** | The document, with the evidence drawer: any figure to its formula, any claim to its source |
| **Model workbook** | The spreadsheet export — live formulas, a provenance tab (§2.5 of the end-state plan) |
| **Refresh** | The update: recompute everything, re-draft what moved, lead with the change summary |

**What it must make possible.**
- Commissioning without reading documentation.
- Approving a costed plan understanding what is being bought.
- Answering each gate from the page itself, with no terminal, no dead end, and no code identifier on screen.
- Walking any figure to the bytes it came from, in the reader and in the export.
- Refreshing a company already researched without paying for a full run.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A first report is commissioned and approved **without documentation** | Observed on a new user |
| **Zero** dead ends: every stopped state has a labelled forward control | The journey harness, asserted in CI |
| A figure walks to its source in **under 30 seconds** | Timed, three figures, in the reader |
| A full run costs **under £8** and a refresh **under £2** | The cost ledger |
| Operator attention per run is **under 15 minutes** across all gates | Timed; today the unmapped-concepts page alone carries up to 304 rows |
| The workbook recomputes correctly when an input is changed | Fixture: change growth, assert the valuation moves as the model says |

**Today.** The pipeline works end to end. The gates are seven separate interruptions, the
unmapped-concepts page is unusable at scale, the refresh mechanic and the workbook do not exist,
and the reader's evidence drawer is the platform's single strongest feature.

*Corrected 25 September 2026.* The refresh exists (ADR 0131) and so does the workbook (ADR 0134).
The workbook's bar is met: a LibreOffice recompute test changes the growth inputs and holds
the answer to where the report's own records say it goes. The unmapped-concepts page shows at
most twenty ranked rows (Phase 1.5). The three timed bars are the operator's own first run on
their machine, and [`../users/windows-bring-up.md`](../users/windows-bring-up.md) is the
checklist for it.

### 2. Methodology library

**What it is.** The operator's own report sections, written in plain language, and — later —
a shared library of them.

**Surfaces.** My methods · the library · the attack report for a method.

**What it must make possible.**
- Writing a section in plain language and seeing it appear in the next report.
- Understanding *why* a method was refused when it tries to relax a rule.
- Later: taking somebody else's method and knowing it was proved safe before it ran.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A method is written and running in the next report in **under 20 minutes**, with no syntax to learn | Observed |
| A method that tries to weaken a rule is refused with a **readable** reason, never silently ignored | The attack corpus, all of which must fail |
| The library's methods carry the evidence they were attack-tested | Every published method links its report |

**Today.** The skills system is built and safety-proven. There is no library and no attack
report a user can read.

### 3. Knowledge

**What it is.** The map between a filer's vocabulary and the platform's concepts — the thing
that decides whether a figure is understood or unmapped.

**Surfaces.** Unmapped concepts · the map · curation history.

**What it must make possible.**
- Teaching the system a tag it did not know, once, so that every later run knows it.
- Understanding what an unmapped concept costs before deciding whether to map it.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| The unmapped queue presents **under 20 rows at a time**, ranked by materiality | Today: 496, 312 and 852 rows on three subjects |
| A concept is mapped in **under 60 seconds**, with the filing's own words shown beside it | Timed |
| Mapping is **permanent** — no later run asks the same question twice | Asserted |

**Today.** The gate exists and is a wall of raw tags. Curation is roadmap §2.8.

---

## Stage 2 — Decide

### 4. Theses

**What it is.** What the operator believes about a company, written as premises — and, where a
premise can be tested, the test.

**Surfaces.** Thesis editor · thesis viewer · revision history.

**What it must make possible.**
- Writing a belief in a sentence, and attaching *what would defeat it* as a metric and a
  threshold — or, when nothing can test it, a date to review it by.
- Revising a belief without erasing what was believed before.
- Seeing, at a glance, which premises currently hold.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A thesis is written in **under 10 minutes** | Observed |
| **At least one premise per thesis carries a testable predicate** — otherwise the monitor has nothing to do | Asserted at save; the user is told why it matters |
| A withdrawn premise is **never deleted**, and the history reads as a story rather than a diff | Already true in the tables; must become true on the page |
| The user can answer *"what would change my mind?"* from the page | Observed |

**Today.** `theses.py` is built: premises, predicates as a metric against a threshold, withdrawal
without deletion. It has no interface a person has used.

### 5. Decisions

**What it is.** The record of what the operator decided, why, and how much — including deciding
**not** to act.

**Surfaces.** Decision form (pre-trade) · decision log · the pre-trade check.

**What it must make possible.**
- Recording a decision in the moment it is taken, not reconstructed later.
- Recording a **pass** with its reasons, as a first-class outcome.
- Seeing, before acting, what the intended size does to the book — concentration, sector
  exposure, and the horizon against the model's own payback.
- Linking the decision to the thesis it expresses and the report it rests on.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A decision is recorded in **under 3 minutes** | Timed |
| **Every** decision links to a thesis and a report; neither can be blank | Asserted |
| The pre-trade check runs **before** the decision is saved, not after | By construction |
| A pass is as easy to record as a buy | Same form, one control |

**Today.** `decisions.py` exists with supersede-and-withdraw-with-a-reason. No interface, no
pre-trade check, and a pass is not modelled.

---

## Stage 3 — Hold

### 6. Portfolio

**What it is.** What is held, at what cost, worth what today.

**Surfaces.** Positions · transactions · position detail · valuation history.

**What it must make possible.**
- Entering a trade in under a minute, or importing a statement.
- Seeing the book valued on yesterday's close without asking.
- Reaching the company page from any position.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| The book **reconciles to the broker statement to the penny** | Manual check against a real statement |
| Valuation updates **daily without intervention** — inside the price subscription's ceiling, no budget approval | Observed over a week |
| A trade is entered in **under 60 seconds** | Timed |
| Every figure is **recomputed from transactions**, never stored | Already true; must stay true |

**Today.** Working. Valuation is not scheduled; there is no daily update.

### 7. Watchlist

**What it is.** The companies the system is keeping an eye on, and why each one is there.

**What it must make possible.**
- Three populations, automatically: **researched but not owned**, **held**, and **closed but
  still watched**.
- Adding a company before any research exists, as a placeholder for an idea.
- Setting the cadence at which each is refreshed and checked.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| Every row says **why it is on the list** and when it was last looked at | By construction |
| Nothing enters or leaves silently | Every change is an event the user can see |
| "Researched, not owned" is visible as its own population | It is the sleeper feature: the ideas you rejected, with your reasons |

**Today.** A placeholder page.

### 8. Monitor

**What it is.** The thing that watches on the operator's behalf. It tests their own premises
against what has been filed since, and it watches the price for moves large enough to be worth
knowing about.

**Surfaces.** The verdict per thesis · the alert · the schedule and thresholds.

**Two kinds of alert, and they are not the same thing.**

| Kind | Cadence | What it says | What it means |
|---|---|---|---|
| **A premise broke** | Monthly or quarterly, on new filings | *"You believed revenue growth would stay above 15%. The FY2027 filing puts it at 11%."* | Your reasoning has a hole in it. Act |
| **The price moved** | Daily, on the end-of-day close | *"Down 12% this week."* | Something happened. Go and look |

**A price move is never reported alone.** It always arrives with what the record says about it:
*"Down 12% this week. Nothing has been filed since your last check and all four premises still
hold."* That sentence is the product in miniature — it is the difference between an alert that
causes panic and one that prevents it, and no chat can produce it because no chat remembers
what you believed last quarter.

**What it must make possible.**
- A monthly or quarterly pass that reads each premise carrying a predicate, resolves the metric,
  measures it against what has been filed since the premise was last read, and decides.
- A daily pass over the end-of-day close that raises a move against a threshold the operator
  set — never a default nobody chose.
- Surfacing a break **on the front door**, not in a page the user must remember to visit.
- Handling the untestable premise honestly — a review date, not a fabricated test.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| **Every broken premise surfaces within one filing cycle** of the filing that broke it | Asserted against stored history |
| A price move above the operator's own threshold surfaces **the morning after the close** | Observed over a fortnight |
| **Zero false alarms** across a quarter — an alert the user dismisses as noise is worse than none | Counted; a dismissal is recorded with a reason. This bar bites hardest on price: a threshold set too low teaches the operator to ignore the queue, which costs more than the alert was worth |
| Every price alert carries **the state of the thesis beside it** | By construction; a bare price move is not shipped |
| The user acts on or dismisses **every** alert; none is ignored | Queue depth returns to zero |
| A check costs **nothing in model spend** where the predicate is a metric | It is arithmetic, not inference |

**Today.** `thesis_monitor.py` is built — `resolve_metric`, `predicate_holds`, measurement from
the fiscal years filed since a premise was last read. **It has no caller, no schedule and no
screen.** This is the single largest piece of finished, unused value in the system. The price
half does not exist at all, though the daily end-of-day valuation the portfolio needs is the
same feed and the same schedule, so the two ship together.

### 9. Risk

**What it is.** What the book as a whole is exposed to, and what a shock would do to it.

**What it must make possible.**
- Concentration and exposure over the weights actually held.
- An operator-stated shock, applied to the book.
- The same arithmetic the pre-trade check uses, so the answer before a trade and the answer
  after it agree.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| The pre-trade check and the risk page **never disagree** | One implementation, two surfaces |
| A shock is stated and applied in **under 2 minutes** | Timed |
| Exposure is explained, not merely displayed — every number reaches its holdings | The evidence drawer, again |

**Today.** A placeholder page. ADR 0106 settles the model.

---

## Stage 4 — Review

### 10. Post-trade review

**What it is.** After a position closes: was the thesis right, and was the decision good?

**What it must make possible.**
- Separating **process from outcome**. A profitable trade on a broken thesis is a bad decision
  that paid; a loss on sound reasoning is not a mistake.
- Reading what was known at the time — which is what the retained retrieval timestamps are for.
- Writing the lesson in the operator's own words, attached to the decision.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| **Every** closed position is reviewed, or explicitly deferred with a date | Queue depth |
| The review distinguishes *right for the right reasons* from *right anyway* | The form makes it impossible to conflate them |
| A review is completed in **under 10 minutes** | Timed |

**Today.** A placeholder page.

### 11. Decision analytics

**What it is.** What many decisions say about the operator that no single decision could.

**What it must make possible.**
- Calibration: when you said you were confident, were you?
- Which premises you get wrong most often, and in which direction.
- Whether your process is improving.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| After **20 reviewed decisions** it tells the operator something they did not already know | Observed, once; honestly reported if it does not |
| It says nothing at all until the sample supports it | No statistics on four data points |
| Every claim it makes walks back to the decisions behind it | The evidence drawer |

**Today.** A placeholder page, and honestly the furthest from earning its place — it needs a
history nobody has yet.

---

## Cross-cutting

### 12. Ask — the grounded follow-up

**What it is.** A question box over the finished record: *"what if the discount rate were half
a point higher?"*, *"how does this compare to last quarter?"*

**What it must make possible.**
- Answers that are **recomputed from the stored record** — instant, exact, free.
- An honest refusal when the answer is not in the record, with the cost of finding out and a
  control to authorise it.
- No answer that is generated rather than computed, ever.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| A recomputable question answers in **under 5 seconds** and costs nothing | Timed |
| A question outside the record is **refused with a price**, never guessed | Adversarial corpus |
| Every answer carries the same evidence drawer as the report | By construction |

**Today.** Does not exist. The scenario engine and margin bridge it would call are built and
have no callers.

### 13. Platform

Settings · costs · health · backups · the API.

**Success criteria.**
| The bar | How it is measured |
|---|---|
| The operator can see what they have spent this month **without arithmetic** | One number, one page |
| A backup is taken and **proved restorable** | Exercised, once, and recorded |
| Nothing on these pages is the only way to do something a user needs | No terminal, no exceptions |

---

## What this inventory changes

Nine tools become **fourteen surfaces across four stages**, and the two most important — the
front door and the company page — do not exist today. Three of the fourteen are built and
unused (theses, monitor, the scenario engine behind Ask), which is the same pattern the audit
found in the research tool: **the system holds more than it shows.**
