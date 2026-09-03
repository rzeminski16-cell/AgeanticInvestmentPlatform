# Post-trade review and decision analytics

Scoring a closed position for the quality of the decision behind it — deliberately not for
whether it made money — and counting what the scores have in common.

> **A review is your own judgement, not evidence and not advice.** The reviewer proposes
> one; you confirm it, and what you confirm is yours. Nothing on these pages feeds a number
> anywhere else in the platform.

---

## The idea in one line

**Good process, bad outcome — and the other way round — are the two cells a P&L cannot
show you, and this tool exists to make them visible.**

A thesis says what you believed. A decision says what you chose to do about it, written
before the outcome. Once the position has closed, a review asks whether the decision was
made well: was it written down first, with a basis? Was the size stated? Was there an exit
plan, and did the exit follow it? Was the holding period near the one you intended? A
well-made decision that lost money is still sound. A trade with no decision behind it that
made money is still flawed.

## What counts as closed

A position is closed when the holding of a listing in a book returns to nothing. The
platform works that out from the trades you recorded, so there is nothing to mark. A listing
bought, sold out and bought again is two positions and two reviews. A holding still open is
not reviewed, and the tool will not offer it: an outcome over an open position is a mark
with an opinion attached.

## Running the reviewer

Open **Post-trade review** from the menu. Each closed position is listed with where its
review stands. Choose **Run the reviewer** on one.

The platform first computes what happened — the cost, the proceeds, the realised return in
the book's currency, and the holding period against the horizon your decisions stated —
and records each figure as a calculation you can open. Then the reviewer reads the
decisions as they were written, the thesis's premises, and whatever the monitor found while
the position was open, and proposes a review: a verdict on each premise, a process quality
with its basis, and lessons.

The pass runs while you wait, in the same way the skill dry run does, and spends against
your per-run cap. If it would cross the cap it stops with the reason and the position stays
unreviewed; you can run it again.

## Confirming it

The proposal appears on the work list as **waiting for you**, and on its own page beside
the outcome. The form is prefilled with what the reviewer proposed, and everything on it
can be changed:

- **A verdict per premise** — held, partially held, failed, untested (the position closed
  before anything could have answered it), or unobservable.
- **Process quality** — sound, questionable or flawed. About how the decision was made, not
  how it turned out.
- **On what basis** — which decisions, premises and findings the quality rests on. The
  reviewer's words if you agree with them; yours if you do not.
- **Lessons** — what to look at before making the same call again.

**Confirm as my review** records it as a judgement you hold, with the reviewer's proposal
kept beside it as it arrived. Where you amended a verdict or the quality, the review page
shows the two side by side — *you confirmed*, *the reviewer proposed* — rather than burying
the difference in a line. Whether you agreed with the reviewer is itself something the
analytics count.

## The analytics

Open **Decision analytics** from the menu. Every table carries its `n`, and below three
reviewed positions it is a tally rather than a percentage — three of four is an anecdote,
not a rate.

The first table is the one the tool exists for: process quality against the sign of the
realised return, laid out two by two — quality down, gain and loss across — so the two cells
a P&L cannot show, sound process with a loss and flawed process with a gain, are where the
eye lands. A review whose outcome could not be computed is counted in the `n` and shown in a
row of its own. Then the qualities, the premise verdicts, how the holding period compared
with the intended horizon, whether a decision is on record for the position, and whether
you confirmed the reviewer's quality or amended it.

## What this tool does not do

- It does not score the outcome. A gain is not a good decision and a loss is not a bad one;
  the review is about the process (ADR 0081).
- It does not recommend, size or set a stop, and it does not change a methodology. A lesson
  is displayed and compared, never cited, and the path from a lesson to a checklist stays
  yours to walk (ADR 0091).
- It does not read prices. A closed position's proceeds are its sales; no mark is consulted.
- It does not write a review on its own. The reviewer proposes; only you confirm (ADR 0105).
