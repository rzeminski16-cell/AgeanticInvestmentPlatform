# The interface specification

*Everything a designer needs to redesign this platform's screens, without reading the code.*

> **The design came back on 2026-08-25.** It is in [`../redesign/`](../redesign/README.md),
> reviewed in [`../redesign/05-review-and-corrections.md`](../redesign/05-review-and-corrections.md)
> and sequenced in [`../plan/interface-overhaul.md`](../plan/interface-overhaul.md). This folder
> remains the **requirements** — the thing the design is answerable to — and stays the first
> document in the authority order.

This folder is the input to roadmap [§3.12](../plan/ROADMAP.md), the interface overhaul. It
says what each surface is, who is on it, what it must make possible, what data it has, what
it asks the reader for, every state it can be in, what is wrong with it today, and what a
redesign must not break.

It is **a specification, not a design.** Nothing here says what anything should look like.
Where it constrains, it says why, and [`01-constraints.md`](01-constraints.md) keeps a
challenge appendix for the constraints worth arguing with.

---

## Read in this order

| # | Document | Why |
|---|---|---|
| 1 | [`00-the-product.md`](00-the-product.md) | What this platform is, who uses it, and the one rule that shapes every screen. **Twenty minutes, and nothing else makes sense without it.** |
| 2 | [`01-constraints.md`](01-constraints.md) | What the design must satisfy, what it may not do, and — in the challenge appendix — what to push back on. |
| 3 | [`02-information-architecture.md`](02-information-architecture.md) | Every surface in scope, how they connect, and the four journeys through them. |
| 4 | [`pages/`](pages/) | One specification per surface. The detail. |
| 5 | [`03-design-system.md`](03-design-system.md) | The tokens and components that already exist, and what is missing. |
| 6 | [`04-content-and-voice.md`](04-content-and-voice.md) | How this platform writes, and why an empty state is a design problem here rather than a copy one. |
| 7 | [`05-accessibility.md`](05-accessibility.md) | The floor. Not optional and not a later pass. |
| 8 | [`06-implementation-contract.md`](06-implementation-contract.md) | For turning a design into templates: what the mechanism can and cannot do. Read it before you finalise, not after. |

**If you read only two**, read `00-the-product.md` and `01-constraints.md`. The first is why
the screens are strange; the second is what will bounce your design if you skip it.

---

## What is in scope

Four surfaces, from roadmap §3.12:

| Surface | Pages | Spec |
|---|---|---|
| **The main menu** | The front door: launcher, work list, and the page that renders when the database is down | [`pages/overview.md`](pages/overview.md) |
| **The menu system and shell** | Header, menu panel, drawer, badges, footer, preference controls — the frame every page sits in | [`pages/shell-and-menu.md`](pages/shell-and-menu.md) |
| **Equity Research** | Requests, the run console, seven gates, the evidence surfaces, reports, skills, knowledge | [`pages/research-*.md`](pages/) — five documents |
| **Portfolio** | The book as at a date, its empty and broken states, the transaction form | [`pages/portfolio.md`](pages/portfolio.md) |

## What is deliberately out of scope

- **The rendered report document.** The PDF and its print stylesheet have their own known
  defects and their own roadmap entry ([§2.4](../plan/ROADMAP.md)). It is a document-layout
  problem, not a screen one, and mixing them would put page-break debugging in the middle of
  an interface design.
- **The seven planned tools** — Watchlist, Theses, Decisions, Monitor, Risk, Post-trade
  review, Decision analytics. Each is a placeholder page saying what it would be and what it
  is waiting on, and that is all it should be until its tables exist. Designing a screen for
  a tool with no data is how a specification becomes fiction. **The placeholder pattern
  itself is in scope** — it appears in the launcher and is specified in
  [`pages/overview.md`](pages/overview.md).
- **Settings, Costs, Health and the API docs.** They are in the menu and so appear in
  [`pages/shell-and-menu.md`](pages/shell-and-menu.md) as destinations, but their own screens
  are not being redesigned in this pass.

---

## The state of the thing you are redesigning

Honest numbers, measured on 2026-08-25, because the shape of the problem is not "it looks
dated".

**It is two designs sharing one shell.** In 2026 the platform gained design tokens, a
component set, a themable palette and a new front door — and they were applied to the shell
and the main menu, not to the tool underneath.

| | Templates | Raw Tailwind ramp classes |
|---|---|---|
| On the design tokens | 13 of 54 | 0 |
| On the stock ramps | 41 of 54 | 1,837 |

The clean thirteen are the shell, the main menu, the component macros and the portfolio's
empty state. The forty-one are the whole Equity Research tool. So the two most-used screens
in the product — the run console and the review gate — are the two furthest from the design,
at 119 and 226 raw colour classes each.

**Walk that boundary before you design anything.** Run the app, switch to dark mode, and go
`/` → `/portfolio` → `/requests` → any run console. You will cross it in four clicks. The
first two pages are what somebody meant; the last two are what accumulated.

**The good news is that the hard part is done.** The tokens exist and are correct in both
schemes. The component macros exist. Navigation is data rather than markup. The shell is
tool-agnostic. What does not exist is a design that uses any of it consistently, and a
reason for each screen to be shaped the way it is.

---

## How to use this with an AI design tool

Each page specification is self-contained and written to be pasted whole. A useful prompt is
one page specification, plus [`01-constraints.md`](01-constraints.md), plus
[`03-design-system.md`](03-design-system.md).

**Give it the constraints every time.** They are the part a general-purpose design model will
otherwise cheerfully violate — it will reach for a modal wizard that holds its answers in
browser memory, a client-side sortable table that recomputes a total, an optimistic
"Approved" state. Each of those is specifically forbidden here, each for a reason worth
reading, and each is in `01-constraints.md` with the reason attached.

---

**See also:** [the roadmap](../plan/ROADMAP.md) · [ADR
0006](../adr/0006-server-rendered-htmx-gui.md) · [ADR
0077](../adr/0077-javascript-may-own-chrome-and-never-a-figure.md) · [how it is
tested](../developers/testing.md)
