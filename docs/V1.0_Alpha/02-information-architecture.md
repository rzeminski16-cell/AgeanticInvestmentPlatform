# The information architecture

*How the surfaces are grouped, named and reached. Revised 14 September 2026 after the first
draft put the company page at the centre — which was wrong, and §2 says why.*

---

## 1. The decision

**Stages organise the menu. The portfolio is where the user lives. A company page is where
they go to answer a question about one company.**

A company-first architecture was drafted and rejected. The correction matters enough to record:
a company page is a *destination for a question* — "what do I know about Microsoft, and what do
I think?" — and nobody opens a tool every morning to ask that about one name. What an investor
opens every morning is **the book**, and the question they are really asking is not "am I up?"
but **"are the reasons I bought still standing?"**

That is the product's whole difference, so it gets the front seat.

## 2. The portfolio is a validity dashboard, not a performance dashboard

Every other tool in this category shows value, weight and profit. Those numbers are necessary
and they are not the point: a broker already shows them, in real time, for nothing.

What nothing else can show is **whether the reasoning behind each position still holds** — and
this platform can, because it made the operator write the reasoning down as testable premises
and it has been checking them against every filing since.

So the portfolio's primary column is not profit. It is **conviction**:

| Column | Why it is there |
|---|---|
| Company and weight | What you hold, and how much of the book it is |
| **Thesis** | Holds · one premise broke · under review · **no thesis** |
| **Last checked** | When the monitor last read it, and when it reads next |
| **Risk** | Concentration, sector, and what a stated shock would do |
| Value and unrealised | Necessary, and deliberately not first |

**The default sort is by conviction risk, not by profit.** A position whose thesis broke sits
above a position that is down 8% with every premise intact, because the first needs a decision
and the second needs nothing. A position with no thesis at all sorts to the very top — it is
money committed for reasons nobody wrote down, which is the worst state in the system.

## 3. The six destinations

| # | Destination | What lives there | Absorbs |
|---|---|---|---|
| 1 | **Today** | The landing page: what needs you, and what is worth doing next | — |
| 2 | **Portfolio** | The book as a validity dashboard: conviction, monitoring, risk, then value | Positions, Risk |
| 3 | **Companies** | Every company the system knows, why it is there, its cadence → the company page | **Watchlist** |
| 4 | **Research** | New request, active runs, the report library, methods, knowledge | Equity Research, Skills, Knowledge |
| 5 | **Review** | Closed positions to review, and what many decisions say about you | Post-trade review, Decision analytics |
| 6 | **Platform** | Settings, costs, health, backups, the API | — |

Plus a **command bar**, always present, that jumps to any company by name or ticker and starts
a request from anywhere.

## 4. Today is a landing page, not a notification list

The first draft made Today a queue of four alerts, which is half a page. A queue answers *what
needs me*; a landing page must also answer **what is worth doing next**, because most days
nothing is broken and the operator still came here to work.

Today has three bands, in this order:

### Band 1 — Needs you

The queue: gates waiting, premises broken, price moves past a threshold, reports gone stale,
positions with no thesis, closed positions not yet reviewed. Each row carries its own reason and
one action. **When it is empty it says so and does not manufacture work.**

### Band 2 — Worth doing

Suggestions, and each one must be *earned by something in the record* rather than generated to
fill the band:

| Suggestion | What earns it |
|---|---|
| *Review the AstraZeneca refresh* | A refresh completed and the change summary has not been read |
| *Research a company you have been watching* | A watchlist entry with no report and no research in 30 days |
| *Write a thesis for M&T Bank* | A position held with no thesis behind it |
| *Three decisions are ready to review* | Positions closed more than 30 days ago and not reviewed |
| *Your concentration ceiling is close* | Top-five weight within 2 points of the operator's own limit |

**If nothing earns a row, the band is absent.** An empty band is honest; a padded one teaches
the operator to ignore the page.

### Band 3 — The state of things

Three or four quiet figures with no call to action: book value and the day's move, how many
companies are watched and when the next check runs, what has been spent this month against the
ceiling. This is the band that makes the page feel like a home rather than an inbox.

## 5. Ask has three tiers, and the third one improves the record

The first draft had Ask refuse anything outside the stored record. That was too narrow: the
question an investor actually asks — *"has anything changed at their main competitor?"* — is
answerable, it just costs something. So Ask resolves a question into one of three tiers, and
always says which tier it is using and what it costs **before** it runs.

| Tier | What it does | Cost | Example |
|---|---|---|---|
| **1 · Recompute** | Re-runs the stored model with a changed input | Free, instant | *"What if the discount rate were half a point higher?"* |
| **2 · Re-read** | A model pass over everything already fetched and hashed for this company — filings, exhibits, transcripts — without fetching anything new | Pennies | *"What does the 10-K actually say about their lease obligations?"* |
| **3 · Research** | Goes and gets new material: new filings, investor-relations pages, competitors' filings, the open web. Everything fetched is hashed and **added to this company's record** | Priced and approved first | *"Has anything changed at their main competitor?"* |

**Tier 3 is not a detour from the product; it is the product.** A question that sends the system
out to fetch, hash and cite new material leaves the company's record larger than it found it, so
the next report and the next question are better. Asking improves the asset. A chat session
cannot do that — its answers evaporate.

The tier is shown before the run, never after: *"This needs new material. About £1.40, and
eleven new documents will be added to Microsoft's record. Go ahead?"*

## 6. Three things that deliberately have no menu item

### Watchlist is the Companies list

If the system holds a company at all, the operator is watching it at some cadence. So Companies
*is* the watchlist, with a *why it is here* column and a *cadence* column, and three populations:
**held**, **researched but not owned**, and **closed but still watched**. The middle one is the
one nobody else has — the ideas you rejected, with your reasons, still being watched.

**Corrected 24 September 2026, on building it.** Companies is the menu item, at `/companies`,
under the watchlist tool; the Watchlist page stays beside it as the queue that commissions
research and the standing budget the queue spends — the form behind the list, not a second
list.

### Deciding has no menu item, because it always happens about a company

A thesis and a decision are written where the operator is looking at the thing being decided —
on the company page, or straight from an alert. A record written in a tool you must remember to
visit afterwards is a record that goes unwritten.

### Monitor has no menu item, because it is a service rather than a place

It produces alerts, which belong on Today, and a verdict per thesis, which belongs on the
portfolio row and the company page. A page listing things the operator has already been told is
how a notification becomes noise. Its settings — cadence and price threshold — sit on the
company page, with a default in Platform.

## 7. The full surface map

| Surface | Reached from | Specified in |
|---|---|---|
| Today | Menu · the default landing | [`02-page-specifications.md`](03-page-specifications.md) §1 |
| Portfolio | Menu | §2 |
| Position detail | A portfolio row | §3 |
| Companies | Menu | §4 |
| Company | A companies row · a portfolio row · the command bar · an alert | §5 |
| New request | Research · the command bar · a company with no report | §6 |
| Active run | Today · Research · a request | §7 |
| Report reader | A company · the library | §8 |
| Report library | Research | §9 |
| Thesis editor | A company · an alert · a portfolio row with no thesis | §10 |
| Decision | A company · an alert · a report | §11 |
| Monitor alert | Today | §12 |
| Ask | A company · a report | §13 |
| Risk | Portfolio | §14 |
| Post-trade review | Review · Today | §15 |
| Decision analytics | Review | §16 |
| Methods | Research | §17 |
| Knowledge | Research | §18 |
| Platform | Menu | §19 |

Nineteen surfaces, six destinations, three deliberate absences.
