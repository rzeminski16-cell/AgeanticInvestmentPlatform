# The page guide

*Every screen, in depth: what it is for, what to look at, and the design decision it embodies.
Read beside the canvas — this explains the drawings, it does not replace
[`../03-page-specifications.md`](../03-page-specifications.md), which is what you build from.*

---

# The hub

## Today

**What it is for.** The landing page. It answers *what needs me* and *what is worth doing next*,
in that order, and it is the page the operator opens every morning.

**What to look at.** The **conviction strip** — every position by weight, coloured by whether its
reasoning still holds. That single element is the two-second read, and it is the reason this page
is not an inbox.

**The decision it embodies.** Two earlier drafts of this page were rejected for the same reason:
their dominant element was a stack of alert rows with buttons, and that reads as notifications
whatever is placed around it. **A hub leads with state; an inbox leads with events.** So the page
now opens on a verdict — *"Your book needs attention. Nine of eleven positions still hold their
reasoning"* — and the strip beneath it makes the claim visible: 15.9% of the book has no standing
reason behind it.

**Why "Since Thursday" has no buttons.** It is a briefing on what the system did while the
operator was away, not a list of things to action. Giving each line a button would turn it back
into a queue. The one queue on the page is small, on the right, and numbered — because on most
days it is short and it must never dominate.

**Why the page ends pointing forward.** *Start something* and *Worth doing* sit at the bottom so
the last thing read is an invitation rather than a complaint. Every card in *Worth doing* must be
earned by a condition in the record; if nothing qualifies, the band is absent. A padded
suggestion band teaches the operator to ignore the page, which costs more than the band is worth.

---

# 1 · Research

## 1.1 New request

**What it is for.** Commissioning a report with the operator understanding what they are buying.

**What to look at.** The submit control reads **"Commission — about £7.90"**, and the panel beside
it prices the whole thing: sections, cost range, machine time, and *how many decisions it will
ask of you*. That last number is the one nobody else shows.

**The decision it embodies.** **Your context** — planned weight, horizon, purpose — is drawn as
its own tinted fieldset because it is doing something different from the rest of the form. The
brief tells the platform what to research; these three tell it what the answer is *for*, and they
are what the closing section reads (F3). A developer who treats them as ordinary optional fields
will produce a report with no closing section and no explanation of why.

## 1.2 Active run

**What it is for.** Watching a run, and answering the gates it stops at.

**What to look at.** The 304-wide **decision panel** in amber, and the line at the bottom of it:
*"Nothing is spent while this waits."* That sentence appears on every gate, and it is the whole
psychological contract of the gate system — a stop is not a cost.

**The decision it embodies.** The rail has five stages and **no percentage**. A percentage on a
process with variable-length steps is a guess presented as a measurement; the stages, each with
one line of detail, say more and promise less. Note also *what it has spent*, broken into
drafting and research against a ceiling — the ceiling is enforced in code, and the bar shows the
operator that rather than telling them.

## 1.3 Report reader

**What it is for.** Reading the document, and checking any figure in it.

**What to look at.** The **evidence drawer**, 448 wide, opened on a figure. It names what kind of
thing the figure is — *"a recorded calculation, not a stored fact, and not a number a model
wrote"* — then the formula, each input with its own source, the document with its dates and
tier, and the artefact digest.

**The decision it embodies.** This is the platform's strongest feature and the audit found it
lives only in the interface: judges reading the exported file alone found the console no slower
to check. So the drawer's contents are specified to survive export (F8), and the control at the
bottom — *"Re-run it"* — is the claim the whole system rests on, made available rather than
asserted.

## 1.4 Ask

**What it is for.** Answering a question about a company, from the record where it can and by
going and looking where it cannot.

**What to look at.** The three tier cards across the top, and then the **amber confirmation** for
a tier-3 question: *"About £1.40, and eleven new documents will be added to Microsoft's record."*

**The decision it embodies.** An earlier draft refused anything outside the stored record, which
was too narrow — the question an investor actually asks is answerable, it just costs something.
The tier is resolved and shown **before** the answer, never after, and tier 3 cannot run without
an explicit approval carrying a price. The second half of that sentence is the point: **the
material joins the company's record**, so asking a question leaves the asset larger than it found
it. A chat's answers evaporate; these accumulate.

## 1.5 Reports

**What it is for.** Every report, current and superseded.

**What to look at.** The M&T Bank row, marked **REFUSED** in purple, and the panel beneath:
*"A refusal is not a failure."*

**The decision it embodies.** The design system gives refusal its own colour precisely so it can
never be mistaken for an error. The report was produced in full and then withheld because eleven
relations among its figures were impossible. The run is kept, the reason is named and the cost is
shown — because hiding a refusal would teach the operator that the guardrails are failures, and
they are the opposite.

## 1.6 Methods

**What it is for.** The operator's own report sections, written in plain language.

**What to look at.** The refused method, and *why*: it names **the two sentences** that broke the
rules — *"conclude with a rating"* and *"without citing every figure"* — rather than a rule
number.

**The decision it embodies.** Invariant 7 says skill files are additive-only, enforced
structurally rather than by prompt text. A structural refusal is worthless if the human cannot
tell what to change, so the refusal quotes the operator's own words back. The attack-report panel
beside it is the evidence that the rule is proved and not merely promised: 148 attacks
attempted, 0 succeeded.

## 1.7 Knowledge

**What it is for.** Teaching the system a filer's vocabulary, once.

**What to look at.** Four rows. Then the collapsed line: *"308 concepts below 5% of their
reference line."*

**The decision it embodies.** This gate used to present every unmapped tag at once — 496 on
Microsoft, 852 on a bank — and the audit named it the worst moment in the product. **Twenty rows
at a time, ranked by materiality, is a hard requirement**, not a preference. Each row shows the
filer's own label beside the tag, because the operator is being asked to recognise a word, not to
decode one.

---

# 2 · Decide

## 2.1 Company

**What it is for.** Everything known about one company, and every action that concerns it.

**What to look at.** The order: **what you believe comes before what you hold.**

**The decision it embodies.** An earlier draft made this the page the operator lives on; that was
wrong, and the correction is recorded in
[`../02-information-architecture.md`](../02-information-architecture.md) §1. A company page is a
destination for a question. Nobody opens a tool each morning to ask it about one name. What
survived the correction is the ordering — the thesis above the position — because this product's
claim is that the reasoning is the asset and the holding is its consequence.

## 2.2 Thesis

**What it is for.** Writing what you believe, and naming what would defeat it.

**What to look at.** The fourth premise, which has **no test** and carries a review date instead.

**The decision it embodies.** The service layer already refuses to invent a predicate the
operator did not choose — *"a premise nothing can test is a premise a person reviews by a date"*
— and the screen makes that visible rather than hiding it. The alternative, a fabricated metric
for an untestable belief, would produce a monitor that reports confidently on nothing.

Note also the three doors when a premise breaks — revise, withdraw, keep and say why — and that
**all three keep the old wording**. A belief you held is part of the record.

## 2.3 Decision

**What it is for.** Recording what was decided, why, and how much.

**What to look at.** **Pass** sits in the same row of controls as Open, Add, Trim and Exit —
identical weight, not a footnote. And the pre-trade check on the right, which says the position
would breach a ceiling and then offers **"Record it anyway."**

**The decision it embodies.** Two, and both are about respect. A pass is a decision: recording
*"I declined at 62× sales because X"* is the record nobody else keeps, and when the price halves
it is the most valuable thing the system owns. And the check never blocks — the operator's book
is their own, and a tool that refuses a trade has confused advice with control. It exists to make
sure they knew.

---

# 3 · Hold

## 3.1 Portfolio

**What it is for.** The book, as the place the operator actually lives.

**What to look at.** The column order — **Company · Weight · Thesis · Checked · Risk · Value ·
Unrealised** — and the header sentence: *"7 of 11 positions have a thesis that currently holds."*

**The decision it embodies.** This is the most consequential design decision in V1.0. Every other
tool in the category shows value, weight and profit; a broker shows them in real time for
nothing. **So this page asks a different question first: are the reasons still standing?** The
default sort is conviction risk, and a position with no thesis sorts above everything — it is
money committed for reasons nobody wrote down, which is the worst state in the system. Profit is
the last column, deliberately, and never colours a row.

## 3.2 Position

**What it is for.** One holding: how it was built, what it is worth, what it does to the book.

**What to look at.** The transaction ledger, and the note beneath it: *"Every figure is
recomputed from these rows. Nothing is stored as a total."*

**The decision it embodies.** The same discipline as the research tool, applied to the book. A
stored total is a number with no lineage; a recomputed one can be checked. Note that the weight
panel names the breach *and* links to the decision where the operator recorded it anyway — the
system remembers that they knew.

## 3.3 Risk

**What it is for.** What the book is exposed to, and what a stated shock would do to it.

**What to look at.** The shock panel: the operator types the percentage and picks the set. And
the small panel at the bottom: *"What this page will not do."*

**The decision it embodies.** No value-at-risk, no correlation matrix, no beta-adjusted anything.
**If the method is not on the page, the number is not on the page.** Those figures require
assumptions the operator has not made and cannot inspect, which is precisely the kind of
confident opacity this product exists to refuse.

## 3.4 Companies

**What it is for.** Everything the system knows about, why it is there, and when it is next
looked at. This is the watchlist.

**What to look at.** The **three populations** — held, researched-not-owned, closed-but-watching
— and the Palantir row: *"you passed on 4 May: priced for perfection at 62× sales."*

**The decision it embodies.** There is no separate Watchlist tool, because if the system holds a
company at all the operator is watching it at some cadence. And the second population is the one
nobody else keeps. It is also the easiest thing to cut and the hardest to rebuild, which is why
the page carries an argument for it in prose — the only place in the design where a screen
editorialises.

## 3.5 Monitor alert

**What it is for.** Telling the operator what happened, with what the record says about it.

**What to look at.** The headline: **"The price moved. Your thesis did not."**

**The decision it embodies.** A price move on its own is a reason to panic; a price move in
context is a reason to think. So the page states the *relationship* rather than the event, and a
price alert is never shipped alone — it arrives with whether anything has been filed, whether the
premises hold, and whether the sector moved too. Note the threshold band at the foot, and that a
dismissal requires a reason: *"Too many dismissals mean the threshold is wrong, not the market."*

---

# 4 · Review and platform

## 4.1 Post-trade review

**What it is for.** After a position closes: was the reasoning sound, and did it work out.

**What to look at.** The four named combinations, and which one is selected.

**The decision it embodies.** The form makes conflating process and outcome impossible, because
that conflation is how investors learn the wrong lessons. **"Right anyway"** — weak reasoning
that paid — is drawn in amber rather than green for exactly that reason: it is the dangerous
outcome, not the good one.

## 4.2 Decision analytics

**What it is for.** What many decisions say that one cannot.

**What to look at.** The page is **greyed out on purpose**, behind a band reading *"11 / 20
reviewed decisions — not enough yet, and this page will not pretend otherwise."*

**The decision it embodies.** Drawing the honest empty state rather than the populated one is
deliberate. Calibration on eleven decisions is noise wearing a percentage sign, and a page that
showed it anyway would be the first place this product lied. The greyed panels say what is coming
without claiming it has arrived.

## 4.3 Platform

**What it is for.** Settings, costs, monitoring, limits, data, backups, health.

**What to look at.** Two rows. **"Discarded — £0.32 · replies paid for and not usable"**, because
nothing else in the system sums that. And, in amber, **"Last proved restorable: Never."**

**The decision it embodies.** A backup nobody has restored is a hope rather than a backup, and
invariant 1's entire guarantee — every fact traces to a hashed artefact — rests on a store that
lives on one machine. Putting *Never* on the settings page in amber, with a control beside it, is
the cheapest honest thing the design can do about that.
