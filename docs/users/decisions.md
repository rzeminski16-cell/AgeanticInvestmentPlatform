# Decisions

Writing down what you decided to do about a thesis — before you do it.

> **A decision is your own record, not evidence and not advice.** Nothing you write here
> feeds a number anywhere in the platform. It is kept so that a later review can ask whether
> the decision was sound, separately from whether it made money.

---

## The idea in one line

**The entry is written before the outcome is known, and the trade points back at it.**

A thesis says what you believe. The portfolio says what you hold. A decision is the thing
between them: what you chose to do about the thesis, when, and why — recorded first, so that
"good decision, bad outcome" is something you can later see rather than something you have to
remember.

## Recording one

Open **Decisions** from the menu. The form asks for:

- **About the thesis** — the thesis this decision acts on. A decision with no thesis is a
  trade with no reason, so the form needs one.
- **What you decided** — one of six: open a position, add to it, trim it, close it, keep
  holding, or not act. Keeping holding and passing are decisions too, and often the ones a
  review most wants to see.
- **In a line** and **on what basis** — what you decided and what led to it. The basis is
  required; a decision with no grounds is the entry a review cannot score.
- **Listing** — `TICKER.EXCHANGE`, if the platform already holds the listing. Leave it empty
  if not; the trade form creates the listing at the first trade.
- **How much, in words** — *"about 2% of the book"*, *"half the position"*. A sentence, on
  purpose: the platform never stores a size it could multiply.
- **Intended holding period**, **what would make you reverse it**, **review by** — the
  commitments a later review holds you to. Fill in the ones you actually mean.

## Carrying it out

Record the trade on the **Portfolio** form as usual, and choose the decision under **Carries
out**. The trade then names the decision, and the decision's page lists the trade. A sale
cannot carry out a decision to buy; the form says so rather than recording it.

A decision that moves the book and has no trade behind it appears on the main menu's work
list as *not started*, until a trade names it or you withdraw it with a reason.

## Changing your mind

**Revise** a decision from its page. That writes a new entry that supersedes the old one; the
old one stays, marked as superseded, so what you decided when is on the record.

**Withdraw** a decision with a reason. It moves to the withdrawn list and keeps everything.

Both are on the audit trail, with the decision as their subject.

## What this tool does not do

- It does not recommend, size, rank or set a stop. Its output has no field for any of those,
  and a skill file cannot declare one either (ADRs 0080, 0104).
- It does not score the decision. That is the post-trade review's job, once the position
  is closed, and it scores the process rather than the result (ADR 0081).
- It does not execute anything. There is no broker connection; the trade is yours to place
  and yours to record.
