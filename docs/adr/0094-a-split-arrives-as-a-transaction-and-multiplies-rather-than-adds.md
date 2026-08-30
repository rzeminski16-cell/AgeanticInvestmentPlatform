# ADR 0094 — A split arrives as a transaction, and multiplies rather than adds

**Status.** Accepted
**Date.** 2026-08-30
**Extends.** ADR 0083 (a position is a calculation, not a row) and ADR 0085 (cost basis is
a pooled average). Honours the commitment 0083 recorded under "What this costs": deriving
a split from `corporate_actions` and writing the transaction it implies.
**Enables.** Roadmap §2.6: a book spanning a split stops being wrong.

## Context

`corporate_actions` has known about splits since migration 0018, and the price arithmetic
honours them: `adjusted_series_for` divides every bar before the ex-date by the ratio. The
portfolio arithmetic does not read the table at all, so a book spanning a split is wrong
in the direction that flatters or ruins depending on the ratio — a two-for-one shows half
the shares at twice the average cost, and every figure downstream inherits it. ADR 0083
accepted this for the first version and recorded the fix it expected: a split must arrive
**as a transaction**, never as a quantity that changed with nothing behind it, because "a
holding is a calculation over transaction rows" is only worth having if nothing else can
move a holding.

Two precedents constrain the shape before any code is written.

**The currency-exchange refusal.** `TransactionKind`'s docstring refuses a currency
exchange until it has a row shape that cannot silently double-count a cash balance —
the row shape question comes first, because the failure mode is a wrong figure that looks
entirely ordinary. A split has the same property in a different place:
`_cash_effects` routes any transaction with no price into the **cash balance**, so a
split row added carelessly would pour a share count into cash as if it were money. The
shape has to make that impossible, not merely avoided.

**The staleness trap.** The obvious quantity for a split transaction is the share delta —
`held at the ex-date × (ratio − 1)` — because the existing pool arithmetic would then
need no change at all: units arrive at zero cost and the pool's total is untouched. It is
also wrong, in the quiet way this platform exists to refuse. The delta depends on what
the book held at the ex-date, and the book is append-only history: an operator who later
records a forgotten buy dated *before* the ex-date changes what was held then, and the
frozen delta is now a wrong number that no constraint can see. A correction machinery —
re-derive, supersede, re-check on every backfill — would spend its whole life defending a
number that never needed to be stored.

## Decision

**A split is one derived transaction per portfolio per corporate action, its quantity is
the ratio, and the book's walk multiplies.** `TransactionKind` grows a seventh value,
`SPLIT`, with `CorporateActionKind`'s reasoning intact: each kind needs its own
arithmetic, and this one's is multiplication.

### The row shape

| Column | A split row holds | Why |
|---|---|---|
| `kind` | `'split'` | |
| `quantity` | the ratio the share count is multiplied by — `2` for a two-for-one, `0.1` for a one-for-ten consolidation | copied from `corporate_actions.split_ratio`, so the row derives from the action alone and holdings at the ex-date are never consulted |
| `trade_date` | the action's `ex_date` | the walk is in trade-date order, so the multiplication lands at its place in history |
| `security_id` | the listing that split | required for this kind by a new check |
| `price` | `NULL` | `transaction_price_is_for_dealing_only` already forces this |
| `fees` | `0` | a split costs nothing |
| `currency` | the listing's quote currency | the column is `NOT NULL`; no arithmetic reads it on a split row, and nothing may start to — a split touches no money |
| `corporate_action_id` | the action it derives from — a **new** nullable FK, `ON DELETE RESTRICT` | the "something behind it" made structural: machine-checkable provenance from the book row to the vendor-documented action |

**Chosen over a frozen delta** because the ratio is stable under everything history can
do. An earlier buy recorded later still precedes the split in the walk and is multiplied;
a supersession re-walks and gets the same answer; nothing ever needs re-deriving. The
delta's one advantage — an unchanged `pooled_cost` — buys a number that goes stale the
first time the operator backfills, which is the trade ADR 0085 already refused when it
made cost basis a walk through history rather than a stored aggregate.

### The constraints, rewritten and added

- `transaction_sign_matches_its_kind` adds `'split'` to the positive kinds: a ratio is
  positive whichever way it points — `0.1` is a consolidation, not a negative quantity.
- New: `transaction_split_multiplies` — `kind <> 'split' OR quantity <> 1`. A ratio-one
  split moves nothing and must not be written; `transaction_moves_something` cannot see
  this because a ratio of one is not a quantity of zero.
- New: `transaction_split_names_its_security` — `kind <> 'split' OR security_id IS NOT
  NULL`. Nothing today forces a non-priced row to name a security; a split without one
  would be a multiplication with no subject.
- New: `transaction_split_derives_from_an_action` — `(kind = 'split') = (corporate_action_id
  IS NOT NULL)`. Every split points at the action behind it, and nothing else may carry
  the FK. A split the vendor has not recorded is not enterable by hand; the remedy is to
  acquire the price series, which records the action, which derives the row.
- New unique index: `uq_transactions_split_per_action` on `(portfolio_id,
  corporate_action_id)`. One derived row per book per action, held by the database for
  the reason `uq_source_document_per_artefact` gives: the database is the only
  participant that sees both writers, so the database holds the rule. Re-running the
  derivation is `ON CONFLICT DO NOTHING`, in the `job_cancellations` idiom — asking twice
  is not an error and does not create a second row.

### The derivation

One function in deterministic service code, run from two places, both of which are
moments new information arrives:

1. **When `record_actions` stores a new split**, derive for every portfolio that has any
   transaction in that security. A portfolio flat at the ex-date gets a row whose
   multiplication of zero units is a no-op — harmless, and cheaper than a rule that
   consults holdings and therefore reintroduces the staleness the ratio shape exists to
   avoid.
2. **When a transaction is recorded** in a security that has split actions dated after
   the new row's trade date, ensure the derived rows exist for that portfolio. This is
   what makes a backfilled first-ever trade self-healing: recording it creates the split
   row the portfolio had no reason to carry before.

The derived attestation is `ATTESTED` grade, like every book entry: the grade ladder's
`documented` means the operator's own document, and the vendor's feed artefact is not
that. The provenance that matters is structural — `corporate_action_id` to the action,
the action's `source_document_id` to the hashed vendor artefact — which is a stronger
chain than the grade could assert. `recorded_by` names the deriving code path, not a
person, because pretending otherwise would be an attestation nobody made.

### The fold

`SPLIT` joins neither `CASH_KINDS` nor the dealt-quantity treatment: `_movement` gains a
third answer, *ratio*, and `pooled_cost` gains the step ADR 0085's "share
reorganisations" paragraph reserved space for — units multiply, the pool's cost is
untouched, so the average cost per unit divides by the ratio and the unrealised figure is
continuous across the ex-date. A disposal after the split takes cost out of the pool at
the new average, exactly as a Section 104 holding behaves through a share reorganisation.
`_cash_effects` never sees a split: the branch keys on kind, not on the absence of a
price, closing the route by which a share count could have reached a cash balance.

## What this deliberately does not do

- **Dividends stay unmodelled in the book.** A dividend corporate action implies cash,
  which implies the operator's account details and withholding — a different argument,
  and one this ADR does not smuggle in by analogy.
- **Rights issues, spin-offs, demergers.** ADR 0085's line stands: each apportions pool
  cost by rules of its own, none arrives as an ordinary buy — and none arrives as a
  multiplication either.
- **Vendor corrections.** `record_actions` identifies a split by `(security, ex_date)`;
  a corrected ratio on the same ex-date is currently skipped as a duplicate. When vendor
  corrections are modelled, the derived transaction is superseded through
  `attestations.supersedes_id` like any other correction — the machinery exists and is
  not built here.
- **A manual split kind.** Refused, per the check above. "Never as a quantity that
  changed with nothing behind it" is the whole point; an operator certain of a split the
  vendor lacks has a data-acquisition problem, not a bookkeeping one.

## Consequences

- A book spanning a split is right, and stays right under backfills, supersessions and
  point-in-time reads — the row derives from the action alone, so there is nothing to go
  stale.
- The transaction form does not grow a seventh option: a split is derived, never typed.
  The row still appears in the book's transaction listing with its kind and its
  provenance, because a row the operator cannot see is a row they cannot audit.
- `quantity` now has three meanings by kind — units, money, ratio — which is one more
  than the class docstring admits today; the docstring changes with the code, and
  `_movement` remains the single place the meaning is decided.
- The first schema-level "derived from this row" foreign key exists. Anything later that
  derives book rows from recorded facts (a dividend, one day) has its precedent: FK to
  the source row, RESTRICT, one derived row per book per source held by a unique index.
