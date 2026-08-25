# What this platform is

*Read this first. The screens are shaped by three or four ideas, and without them half the
interface looks like over-engineering.*

---

## In one paragraph

It produces **one institutional-style equity research report at a time** for a UK or US
listed company. A person commissions it, the platform plans the work and stops for approval,
fetches and hashes the filings, extracts and normalises the numbers, does every calculation
in Python with units carried through, drafts the prose with a language model, attacks its own
conclusion with a second model, and stops again for approval before producing a document in
which **every figure carries a footnote resolving either to the formula that produced it or
to the archived bytes it came from.**

Alongside it is a second tool that records what the operator actually holds, and computes the
book's value from the transactions rather than storing a position.

> **It is a personal research tool. It is not regulated investment advice.** Every surface
> says so, and the disclaimer lives in the page shell so a screen cannot ship without it.

## Who is on these screens

**One person. There is no second user, no team, no sharing and no permissions.** It runs on
their own machine, against their own database.

That single fact removes a great deal of what usually shapes an application interface — there
is no inbox, no assignment, no "shared with me", no presence, no notification centre. What
replaces all of it is a single question the front door has to answer: *is anything waiting for
me?*

Two things about them are worth designing for.

**They are financially literate and they are checking the platform's work.** They know what a
discounted cash flow is and they will not thank you for explaining it. What they do not know
is whether *this* run got it right, and the entire interface exists to let them find out. A
figure they cannot trace is a figure they have to ignore.

**They are spending real money, in small amounts, continuously.** A run costs a few pounds of
model calls. Every gate is a moment where approving costs money and rejecting wastes what has
been spent already. Cost is not a settings-page concern here; it belongs on the screens where
decisions are made.

## The one rule everything follows from

> **Deterministic Python owns every number and every fact. The language model owns planning,
> interpretation, comparison, adversarial challenge and writing.**

The model never does arithmetic. It never confirms a citation. It proposes; code disposes.

**Why a designer needs to know this:** it is the reason the interface is so full of
provenance. Chips saying where a number came from, footnotes that resolve to hashes, a
"grade" on every portfolio figure, a page that shows you the exact sentence in the original
filing — none of that is decoration or an audit feature bolted on. It is the product. A
research platform whose numbers cannot be checked is a chatbot with better typography, and
every one of these surfaces exists to be the difference.

**So: never design provenance away to reduce clutter.** Design it so that it can be *ignored
when skimming and reached in one click when doubted*. That tension is the single most
interesting design problem in this brief, and it recurs on almost every screen.

## Three kinds of figure, and why the screens distinguish them

Any number on any screen is exactly one of these. The distinction is enforced in the type
system, not by convention, and the interface has to carry it.

| Kind | What it is | Where it appears |
|---|---|---|
| **Source fact** | Filed by the company, extracted from a hashed document | Financials, the report, evidence surfaces |
| **Calculation** | Produced by Python from other figures, with its formula, its inputs and their units all stored | Valuation, ratios, the portfolio's every tile |
| **Attestation** | What the operator's own book says — a holding, a fill, a cash balance — carrying a **grade** of evidence | Portfolio only |

Two more classes exist for things that are *not* figures and must never be mistaken for one:
**Assumed** (a value nobody could derive, agreed by the operator) and **Judged** (a view
somebody holds). A judgement is never a source reference, because a thesis that can be cited
as evidence is a system that can launder an opinion into a fact.

**The grade is the portfolio's version of this.** A holding typed from memory and one parsed
from a contract note look identical on screen unless the screen says which — so it does.
`Typed` and `Documented`, and the grade of a total is the grade of the weakest thing beneath
it.

## Two clocks

This trips people up, so it is worth stating plainly.

**Research runs on an as-of date.** A run is a point-in-time selection over the record: it is
answering "what could have been known on 14 March", and anything published after that date is
refused rather than filtered. This is not a preference; it is what stops the analysis being
contaminated by hindsight.

**The portfolio runs on a continuous clock.** A book is followed. What it is worth today and
what it was worth last March are two different questions, both legitimate, and the screen
takes a date so you can ask either.

Conflating them is a known trap. It is why the two tools' date controls mean different things
and should not look like the same control doing the same job.

## What it refuses to do, and why that matters on screen

A great deal of this platform's value is in its refusals, and **a refusal is a design
surface**. Handled badly it reads as a bug; handled well it is the moment the operator trusts
the tool.

- A **bank** gets no discounted cash flow. Its accounts have no classified balance sheet, so
  the standard model is not thin — it is undefined. Confirm the sector and the model changes.
- A **figure with no source** does not appear. Not as zero, not as a dash with a hopeful
  footnote.
- A **total short one position** is not shown. If one holding cannot be valued, the net asset
  value is refused — because a subtotal presented as a total is the most dangerous number on
  a financial screen.
- A **GBP risk-free rate** is refused rather than substituted with a US Treasury yield,
  because the Bank of England's own `robots.txt` disallows the route its documentation
  describes, and reaching around that is circumvention.
- A **run over its cost ceiling** stops and asks, rather than spending.

Every one of these needs a screen that says *what happened, why, and what the operator can do
about it*. There are a lot of them. See [`04-content-and-voice.md`](04-content-and-voice.md).

## The nine tools, of which two exist

The platform is designed to hold several tools on one kernel. Two work; seven are planned and
appear on the launcher as placeholders that say what they would be and what they are waiting
on.

| Tool | State |
|---|---|
| **Equity Research** | Working, end to end |
| **Portfolio** | Working |
| Watchlist, Theses, Decisions, Monitor, Risk, Post-trade review, Decision analytics | Planned |

**A planned tool is a real page, not a dead link and not a lie.** That decision is worth
keeping and worth designing properly: the launcher is the one place the shape of the whole
product is visible, and seven honest placeholders communicate the ambition better than hiding
them would.

**Design for nine, build for two.** Whatever you do to the launcher, the work list and the
menu has to survive the third tool arriving without a redesign — because navigation is
already data rather than markup, and adding a tool adds a row rather than editing a template.

---

## The anatomy of a research run

The single most important flow in the product. Seven places can stop and wait for a person;
two of them always do.

```
plan → [GATE 1] → acquire → classify → [gate] → peers → [gate] → themes → [gate]
     → prices → extract → [gate] → calculate → research (×5, parallel) + comps
     → assumptions → [gate] → value → draft → validate → red team → [GATE 3] → render
```

- **Gate 1 — the plan.** Always. About £0.15 has been spent proposing what it intends to do.
  Approving commits you to the rest, and the rest is where the money is.
- **Five conditional gates** — unmapped concepts, sector, peers, themes, assumptions — appear
  only when the company makes them necessary.
- **Gate 3 — the final review.** Always. The drafted report, its validation results, its
  costs, its coverage, and what the adversary said about it.

**A run takes tens of minutes and a step that calls a model changes nothing visible for
several of them.** The console's whole job is to distinguish a healthy run mid-thought from a
dead worker, and it is a real design problem rather than a spinner.

---

**Next:** [the constraints](01-constraints.md) · or, for more depth, the
[users' guide](../users/running-a-report.md) and the [pipeline
diagram](../product/anatomy-of-a-research-run.html) (open in a browser)
