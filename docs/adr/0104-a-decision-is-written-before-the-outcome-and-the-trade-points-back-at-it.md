# ADR 0104 — A decision is written before the outcome, and the trade points back at it

**Status.** Accepted
**Date.** 2026-09-02
**Required by.** Roadmap §3.7, and by ADR 0081, whose reviewer reads "the decision as it was
recorded" and has had no decision to read.
**Extends.** ADR 0074, which made a judgement the fifth record class and forbade it a
`SourceRef`; ADR 0102, which gave the class its shape and left the second subtype to the
change that first stored one; ADR 0080, which reserves the six sizing names "in the commit
that introduces any sizing concept, never in a follow-up". This is that commit.

## Context

A thesis says what the operator believes. A position says what they hold. Between the two
is the thing that connects them and that the platform has never stored: **what they decided
to do, when, and on what basis** — before the outcome was known.

ADR 0081 depends on it entirely. Its reviewer scores the *process*, not the P&L, and a
process can only be scored against what was written down in advance: the action, the
intended holding period, the size the operator meant to take, and what they said would make
them reverse it. Written afterwards, every one of those is a rationalisation; written before,
each is a commitment the review can hold the operator to. The whole of 0081's argument —
*good process, bad outcome* is a cell that has to be reachable — rests on the entry
predating the outcome.

Three questions the build had to answer.

**What is a decision, structurally?** ADR 0102 answered this in advance: a judgement
subtype, "a decision or a post-trade verdict will each arrive with a table of their own",
keyed on the judgement's id. A decision *is* a judgement seen from its consequence — a named
person, at a time, on a stated basis — with columns for what only a decision has.

**How does a trade say which decision it carried out?** The journal is the decision *and*
the trade, and a review needs the pair. But a transaction is an attestation, which is a
`SourceRef` kind (ADR 0073), and a judgement may never enter a lineage (ADR 0074).

**Does this introduce a sizing concept?** A decision carries an action — buy, add, trim,
sell, hold, pass — and may say how much. `action` is one of ADR 0080's six reserved names,
and "how much" is what `position_size` denotes. It does.

## Decision

### 1. `decisions` is the second judgement subtype

`JudgementKind` gains `DECISION`, and `decisions` is keyed on the judgement's own id, in
`premises`' shape. It carries: the thesis it acts on (required — a decision is about a
thesis, and one with no thesis is a trade with no reason); the security and the book where
the decision names them (optional — a decision to buy may precede the listing's first
trade); an `action` from a closed enum of six; a `statement` of what was decided; and four
things the reviewer will hold the operator to, each optional because an honest journal
records what the operator actually committed to rather than forcing five boxes to be
filled: `size_statement`, `horizon_months`, `exit_plan` and `review_by`.

**The size is a sentence, not a number.** `size_statement` is text — "about two per cent of
the book", "half the position" — and the schema has no column a calculation could read. This
is ADR 0074's rule applied to the one figure a decision most wants to carry: a stored
intended weight would be a judgement wearing a `Quantity`'s clothes, and the day something
multiplied it by a net asset value the position would be sized by a view. The reviewer
compares the sentence with the transactions that followed, in prose, which is a comparison
with an outcome and the privilege 0074 grants.

**Corrections are supersessions.** `judgements.supersedes_id` already exists and is unique.
Revising a decision writes a new row that supersedes the old, and withdraws the old with the
reason "superseded"; withdrawing one records the reason on the row. Nothing is edited and
nothing is deleted, for the reason 0102 gives: a view held at a time is a fact about that
time, and a decision quietly rewritten after the outcome is the row the reviewer exists to
read.

### 2. The trade points at the decision, and no calculation looks

`transactions.decision_id` is a nullable foreign key to `decisions`, `SET NULL` on delete.
The transaction says which decision it carried out; the decision page lists the transactions
that carried it out; the reviewer gets the pair.

**The direction is what keeps ADR 0074 intact.** The column is on the attestation and points
*at* the judgement, so the judgement's own row is unchanged and still references nothing a
calculation reads. What a calculation reads off a transaction is its quantity, price, fees
and currency — the attestation's figures, sourced as the attestation — and `aer.calc` has no
symbol for a decision at all. A test scans the package for the word, so a later calculation
that reached for the link would be a red build rather than a laundered figure. The lineage
of a position is exactly what it was under ADR 0083; the link is context beside it.

The link is made from the transaction form — *carries out* — so the trade is recorded once,
with its decision, at the moment the operator is already saying what happened. A trade
recorded without one can name its decision later from the decision's page.

### 3. The six sizing names are reserved now, with this change

`RESERVED_OUTPUT_FIELDS` gains `position_size`, `weight`, `recommended_weight`, `action`,
`order_quantity` and `stop_loss`, each with an attack file in
`tests/fixtures/fx_skill_adversarial/`, and the adversarial corpus grows from thirteen to
nineteen. ADR 0080's reason stands unamended: a skill file that can declare a field named
`recommended_weight` is a skill file that sets position sizes, and invariant 7 makes that
unrepresentable by refusing the field. The refusal names its own reason, as `conviction`'s
does — a size is the operator's decision, and a section has no writable path to one.

## What was rejected

**A numeric intended size on the decision.** Useful for calibration, and refused for the
reason above; the sentence carries the intent and the reviewer reads it.

**A `decision_id` on `attestations` rather than `transactions`.** An attestation is any
kind of thing the book says; only a trade carries out a decision, and a column on the
supertype would let a dividend claim to.

**Requiring a security on every actionable decision.** The listing may not exist yet — the
portfolio's third door creates it at the first trade (ADR 0093) — and a decision to buy
something the platform has not priced is still a decision, written before the outcome.

**A `decisions.outcome` column.** That is ADR 0081's, platform-filled, once the position is
closed. Putting it here would invite writing it early.

## Consequences

The Decisions tool is the fifth working tool: a list, a detail with the thesis's premises
as they stood and the trades that followed, four forms, and an attention row for a decision
whose review date has passed or that was never carried out. The portfolio form gains one
optional control. The reviewer ADR 0081 describes now has both halves of its subject —
the decision as recorded and the trades as attested — and §3.8 can be built on them.

What this does not do is decide anything for the operator. There is no field for a
recommendation, a target, a weight or a stop, on the decision or anywhere a model writes;
the six names are now refused at the one place a skill could have declared them.
