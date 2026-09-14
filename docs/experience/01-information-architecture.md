# The information architecture

*How fourteen surfaces become six destinations. Decided 14 September 2026.*

---

## The decision

**Stages organise the menu. The company page is where the user actually lives.**

The alternative — a company-first menu with everything nested underneath — was rejected for one
reason: several surfaces are genuinely cross-company. A portfolio is not about a company. Risk
over a book is not about a company. Decision analytics is about *you*, across many companies. A
company-first tree would have to hang those somewhere awkward, and would make the book harder to
see than any single position in it.

So the menu is organised by what the operator is doing, and the company page is reached from
almost everywhere within it.

## The six destinations

| # | Destination | What lives there | Absorbs |
|---|---|---|---|
| 1 | **Today** | The action queue. The default landing page | — |
| 2 | **Companies** | Every company the system knows, why it is there, its state, its cadence → the company page | **Watchlist** |
| 3 | **Research** | New request, active runs, the report library, methods, knowledge | Equity Research, Skills, Knowledge |
| 4 | **Book** | Positions, transactions, exposure, concentration, shocks | Portfolio, Risk |
| 5 | **Review** | Closed positions to review, and what many decisions say about you | Post-trade review, Decision analytics |
| 6 | **Platform** | Settings, costs, health, backups, the API | — |

Plus one thing that is not a destination: **a command bar**, always present, that jumps to any
company by name or ticker and starts a request from anywhere. It is how an experienced operator
navigates; the menu is how a new one does.

## Three things that deliberately have no menu item

This is the substance of the design, and each removal is an argument.

### Watchlist is not a tool. It is the Companies list.

A watchlist answers *"which companies am I keeping an eye on, and why?"* — and if the system
holds a company at all, the operator is watching it at some cadence. They are the same set.
So **Companies is the watchlist**, with a *why it is here* column and a *cadence* column:

| Company | Why it is here | Last looked at | Cadence |
|---|---|---|---|
| Microsoft | Held — 4.2% of book | Report refreshed 2 days ago | Monthly |
| AstraZeneca | Researched, not owned — passed at $78 | Thesis reviewed 6 weeks ago | Quarterly |
| M&T Bank | Closed Jan 2026 — still watching | No change since close | Quarterly |

The "researched, not owned" population is the one nobody else has: **the ideas you rejected,
with your reasons, still being watched.**

### Decide has no menu item, because deciding always happens about a company

A thesis is about a company. A decision is about a company. Both belong on the company page,
in the moment the operator is looking at the thing they are deciding about — not in a separate
tool they must remember to visit afterwards, which is how records go unwritten.

The cross-company views that *do* exist — every decision, every thesis — are filters on
**Companies** and inputs to **Review**, not destinations of their own.

### Monitor has no menu item, because it is a service rather than a place

The monitor runs on a schedule and produces two things: **alerts**, which belong on Today, and
**a verdict per thesis**, which belongs on the company page. A page called "Monitor" would be a
list of things the operator has already been told, which is how a notification becomes noise.

Its settings — cadence and price threshold — sit on the company page, per company, where the
operator can see what they are setting them for, with a default in Platform.

## The company page

Six sections on one page. No tabs at the top level: the operator should see the shape of what
they know by scrolling once, and tabs hide exactly the thing that makes this page worth having.

```
┌──────────────────────────────────────────────────────────────┐
│  MICROSOFT  MSFT · NASDAQ            $493.16  ▾1.2%          │
│  HELD · 4.2% of book · monthly cadence                       │
├──────────────────────────────────────────────────────────────┤
│  WHAT YOU BELIEVE            │  WHAT YOU HOLD                │
│  Thesis, 4 premises          │  120 shares · $59,179         │
│  ✓ ✓ ✓ ✗  one broke          │  cost $412.40 · +19.6%        │
├──────────────────────────────┴───────────────────────────────┤
│  THE RECORD                                                  │
│  Report · refreshed 2 days ago · 3 superseded versions        │
│  Decisions · opened Mar 2026, added Jul 2026                  │
│  Monitor · checked yesterday · one premise broke 3 days ago   │
├──────────────────────────────────────────────────────────────┤
│  ASK                                                          │
│  [ what if the discount rate were half a point higher? ]      │
├──────────────────────────────────────────────────────────────┤
│  Refresh report · Revise thesis · Record a decision · Workbook│
└──────────────────────────────────────────────────────────────┘
```

**The rule that makes it work:** no report, thesis, decision, position or alert may exist that
this page does not show. Orphans are asserted against in code, not hoped against.

## Ask — structured, with a door in

Settled: **structured controls, with a text box that routes to them.** A bare chat box promises
open-ended conversation the system will then refuse, which is the worst of both. A panel of
sliders alone is honest but cold, and limits an operator who knows what they want.

So the box accepts a sentence, and resolves it into one of three outcomes:

| The question | What happens |
|---|---|
| Recomputable — *"what if the discount rate were 50bp higher?"* | The model re-runs. The answer appears with the same evidence drawer as the report. Instant, exact, free |
| Answerable from the record — *"how does this compare to last quarter?"* | The stored calculations are compared and the difference is shown |
| Outside the record — *"what are analysts saying?"* | **Refused with a price.** *"Not in this record. Researching it costs about £1.40 — shall I?"* |

The refusal is the feature. It is the sentence a chat can never say honestly, because a chat
does not know what it does not know.

## What this does to the count

**Fourteen surfaces, six destinations, three deliberate absences.** Against today's ten headings
over eighteen destinations, the menu shrinks by nearly half while the system grows — which is
the test ADR 0112 set when it made the shell own the headings: *a tenth tool should add a line,
not a heading.*
