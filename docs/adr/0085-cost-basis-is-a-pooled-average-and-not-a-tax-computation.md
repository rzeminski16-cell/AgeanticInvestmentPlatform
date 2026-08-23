# ADR 0085 — Cost basis is a pooled average, and not a tax computation

**Status.** Accepted
**Date.** 2026-08-23
**Required by.** `aer/calc/portfolio.py`, which cannot compute "what did I pay for what I
still hold" without a convention for which pounds were spent on which shares.
**Extends.** ADR 0083, which made a position a calculation and named cost basis as one of
the six figures, without saying how it is worked out.

## Context

ADR 0083 lists cost basis as "the consideration paid for what is still held, with fees" and
stops there. That sentence has no arithmetic in it, because the arithmetic depends on a
choice nobody had made.

Buy 100 shares at £10, buy 100 more at £20, sell 100. What did the remaining 100 cost?

- **£1,500** under a pooled average: every share cost the same £15, so the sale took £1,500
  of cost out and £1,500 stayed.
- **£2,000** under first-in-first-out: the sale disposed of the cheap ones.
- **Anything from £1,000 to £2,000** under specific identification, depending on which
  certificate the operator says they sold.

Three defensible answers, a 33% spread, and no way to tell from the transactions alone which
one a reader is being shown. ADR 0083's own warning applies directly: *a wrong revenue figure
in a research note is embarrassing; a wrong cost basis is a tax return.*

The order matters too, which is why this cannot be a property of a sum. Buy at £10, sell,
then buy at £20 leaves £2,000 under pooling; the same three trades in the other order leave
£1,500. A cost basis is a walk through history, not an aggregate over it.

## Decision

**One convention: a pooled average, per portfolio and per security, walked in trade-date
order.** An acquisition adds its consideration and its dealing costs to the pool. A disposal
removes cost in proportion to the units it takes, at the pool's average at that moment, and
its own dealing costs do not touch the pool.

This is the shape of the UK's Section 104 holding, and it is the right default for a
platform whose operator is a UK investor: it is what HMRC requires for ordinary share
disposals, and it is what a UK broker's own statements show.

**Chosen over first-in-first-out** because FIFO is a US convention, and producing a US answer
for a UK holder would be wrong in a way that looks entirely ordinary — the number is
plausible, the units are right, and nothing in the output says which rule made it.

**Chosen over specific identification** because identifying a lot requires the operator to
say which one they sold, at the moment they sell, every time. That is a data-entry burden
this tool has not earned, and a partially-identified book silently falls back to some other
rule anyway.

## What this is not

**This is not a tax computation, and the platform must never present it as one.**

The pooled average is the *shape* of a Section 104 holding, and a real one has three rules on
top that this does not implement:

- **The same-day rule.** Disposals match acquisitions made on the same day first, before the
  pool is touched at all.
- **The thirty-day rule.** Disposals then match acquisitions in the following thirty days —
  the "bed and breakfasting" counter — again ahead of the pool.
- **Share reorganisations.** Rights issues, takeovers and demergers apportion pool cost by
  rules of their own, and none of them arrives as an ordinary buy.

A book that never trades the same security twice within thirty days and holds nothing through
a reorganisation gets the same answer either way. Any other book does not, and the difference
is real money.

So the figure this platform computes answers **"what did I pay for what I still hold?"** It
does not answer "what is my chargeable gain?", and every surface that shows it says so. That
is not a hedge: it is the difference between a research tool and an accounting package, and
pretending otherwise would be the most consequential wrong number this platform could
produce.

## The convention is one function, and that is deliberate

`pooled_cost` is a single traced calculation. A second convention arrives as a second
function and a setting on the portfolio, not as a branch inside this one — so "which rule
made this number?" is answered by the calculation's own name in the ledger rather than by
reading the code that produced it.

That also bounds the cost of this record being wrong for somebody. A US holder needing FIFO
gets a second function; they do not get a rewrite.

## Consequences

**A disposal's dealing costs leave the pool alone.** They reduce the cash balance and they
would reduce a chargeable gain, but they are not part of what the remaining shares cost.
Folding them in would inflate the cost basis of a holding by the cost of selling a different
part of it.

**Disposing of more than is held is an error, not a short position.** The pool would go
negative in units and the average would become meaningless. Shorting is not modelled, and a
book that appears to have sold what it never bought has transactions missing — which is a
thing to tell the operator about, not to compute through.

**A pool is per currency by construction.** Every acquisition of one security settles in that
security's dealing currency, and mixing two into one pool would average pounds with dollars.
The calculation refuses it rather than converting, because the conversion date would be a
choice nobody made.

**Every figure downstream inherits this.** Unrealised profit is market value less pooled cost,
so the convention decides that too — which is the argument for it being a written record
rather than a line in a docstring.
