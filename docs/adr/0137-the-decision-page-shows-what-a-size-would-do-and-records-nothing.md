# ADR 0137 — The decision page shows what a size would do to the book, and records nothing

- **Status:** Accepted (28 September 2026)
- **Date:** 2026-09-28
- **Applies:** ADR 0104 (a decision's size is a sentence, never a number), unchanged; ADR 0133
  (the calculator strikes over the operator's numbers and records nothing), whose shape this
  follows; ADR 0129 (the request's planned weight), whose meaning the what-if's number shares;
  ADR 0136 (the operator's limits).
- **Decided by:** the operator, 28 September 2026, approving the V1.0 page specification: the
  pre-trade check may show what the book becomes, from a weight typed into the check, without
  saving it.

## Context

The decision drawing shows an intended size, *+1.5% of the book*, and a check beside it with
the position, the five largest, the sector and cash before and after, then a warning that the
position would breach the operator's ceiling and a control reading *Record it anyway*.

ADR 0104 rejected a numeric intended size on the decision, and outranks the drawing: *a stored
intended weight would be a judgement wearing a Quantity's clothes, and the day something
multiplied it by a net asset value the position would be sized by a view.* So the built check
shows the book as it stands and says it cannot show what the book becomes.

The objection is to storing the number, not to computing with one. ADR 0133 already strikes a
report's model over numbers the operator types and records nothing. The request form already
carries a planned weight for the report's closing section (ADR 0129), with the same meaning: the
whole position after the trade, as a share of the book.

## Decision

### 1. A what-if weight, typed into the check, for this page only

The check panel has one field: the position after the trade, as a share of the book. With a
value in it, the check shows before and after for the holding's weight, the five largest, the
sector's share and cash, using the risk page's own arithmetic (F12: one implementation).

### 2. Nothing is recorded

The what-if is struck in a ledger that is never persisted, as the calculator's is. It is not
written to the decision, to the calculations table or to the audit trail, and it does not fill
the decision's size, which stays a sentence the operator writes. Submitting the decision form
records what it recorded before, and nothing from the check.

### 3. A limit crossed is said, and nothing is blocked

Where the after-figure crosses a limit the operator stated (ADR 0136), the check says so in
`warning`, with the word beside the colour, and the submit control reads *Record it anyway*.
The control is never disabled.

### 4. Without a what-if, the check is as built

With the field empty, the check shows the book as it stands, as the 20 September correction
specified.

## What was rejected

**Storing the what-if on the decision.** It is the number ADR 0104 refuses.

**Reading the size sentence for a number.** It would turn prose into a stored intent by
another route, and a sentence like *about a third of a normal position* has no number to read.

## Consequences

The decision page gains the field and the before-and-after rows; the check gains the limit
comparison when ADR 0136's limits exist. Tests assert the what-if changes the figures shown and
leaves the decisions, calculations and audit tables exactly as they were.
