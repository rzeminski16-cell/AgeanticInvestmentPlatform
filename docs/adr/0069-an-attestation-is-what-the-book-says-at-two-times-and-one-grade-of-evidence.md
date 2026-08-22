# ADR 0069 — An attestation is what the book says, at two times and one grade of evidence

**Status.** Accepted
**Date.** 2026-08-22
**Required by.** `docs/investment-os.md` §5, and by ADRs 0074 to 0077 — each of which
monitors, prices or reviews a position that the platform currently has no way to write down.
**Extends.** `SourceKind` (`src/aer/calc/units.py:136`), closed at three values since ADR
0011.

## Context

The record taxonomy is closed, and it says so in the code:

> Three kinds, and the list is deliberately closed. Every number in a report resolves,
> eventually, to a fact somebody filed, an assumption somebody made and justified, or a
> calculation over those two. A fourth kind would be a way in for a number with no story.

**That docstring is not an obstacle to route around. It is the standard the fourth kind has
to meet.** The question is not whether a fourth kind is permitted, but whether the one being
proposed arrives with a story of its own or as an exemption from having one.

A fill price is none of the three. Nobody filed it — it exists on a contract note addressed
to one person. Nobody chose it — the market did, and the operator found out afterwards. No
code computed it. The same is true of a position quantity, a cash balance and a cost basis:
they are the four numbers every portfolio figure eventually rests on, and the taxonomy has
no shelf for any of them.

There are two ways to force them onto an existing shelf. Both are worse than admitting a
fourth kind, and specifically so.

**Wrapping a trade as an `Assumption` destroys the assumptions register.** The table is
unique on `(request_id, name)` — `uq_assumptions_name_per_request`, whose comment reads "Two
different discount rates in one valuation is not a disagreement to be averaged, it is a
bug". Two fills of the same holding in one month are not a bug; they are an ordinary
Tuesday, and the constraint that makes the register trustworthy is exactly what a trade
ledger must violate. `justification` is `NOT NULL` with a non-empty `CHECK`, because "an
assumption without a stated reason is a guess wearing a label" — and a fill price has no
justification, because nobody reasoned their way to it. Worst of all, ADR 0046's
propose/confirm containment (`assumption_proposals` → `assumptions`, with `as_quantity`
refusing an unconfirmed row) exists so that a **judgement** a model made is agreed to by a
person before it can move money. A fill price is observed, not chosen. Asking an operator to
approve the price they were filled at devalues the word "approved" on every gate that uses
it. And `assumptions.request_id` is a `NOT NULL` foreign key to `research_requests`, so
every trade would need an equity research mandate to exist — the same coupling ADR 0068 is
prising apart one table up.

**Wrapping it in a synthetic `Calculation` is what ADR 0011 explicitly forbids.** A
zero-input calculation whose formula is `price = price` would satisfy the resolver, render a
lineage node and pass every check the platform owns, while asserting something untrue about
where the number came from. ADR 0011 settled this when it declined to mint a source for
`years`: "Fake sources are worse than no sources, because they defeat the check while
appearing to pass it."

## Decision

**A fourth record kind, `Attestation`: a value about the operator's own affairs, carrying
two times and one grade of evidence.**

Its guarantee, stated as the other three state theirs: *this is what the book says as at
`effective_at`, as known at `recorded_at`, and here is the grade of evidence behind it.*

The two times are not decoration and they are not the same clock the research tool runs on.
That argument is ADR 0071's; this record only requires that both are stored, and that
neither is derivable from the other.

**The grade is one of two values, and it is a property of the row, not of a rendering.**

- **`documented`** — extracted from a hashed `USER_SUPPLIED` artefact: a contract note, a
  custodian statement, a dividend advice. The full chain applies unchanged — artefact →
  extraction → locator → citation — because the entire spine below the locator is already
  subject-agnostic. A `Locator` addresses `text[char_start:char_end]` inside an extraction
  (ADR 0017), and `aer.verify.citations.verify` re-slices those offsets and compares (ADR
  0018). Neither knows nor needs to know whether the bytes came from a 20-F or a broker's
  PDF. `Provider.USER_SUPPLIED` has existed since `core/enums.py:260`; ADR 0022 named this
  precise route for operator-supplied documents and recorded that "building that ingestion
  path is not part of task 18 and is not done here". This is that path, arriving for a
  different reason than the FCA's terms of use.
- **`attested`** — typed by the operator, self-certified, no artefact behind it.

`SourceKind` gains `ATTESTATION`, and its docstring is rewritten to say four and to say why
the fourth arrives with a story. That edit is part of this decision rather than a
consequence of it: a comment that still says "deliberately closed" beside four values
teaches the next reader that the comments here are decoration.

## The grade propagates, and it propagates as a type

**A lineage containing any attested node cannot reach a shareable rendering.** Not the node
— the lineage. One attested exchange rate three levels down taints the NAV computed above
it, because the NAV is only as evidenced as its weakest input, and the whole point of
carrying units and sources through arithmetic is that properties travel the edges.

The enforcement is a return type with no field for the figure, exactly as ADR 0034's
`WithheldComps` has no `peers` and ADR 0029's `ValuationMandate` has no constructor for a
bank. Third use of the same move; at that point it is a house pattern rather than a
coincidence.

**A flag would not do, and the reason is about people rather than code.** A flag is an
argument waiting to happen: the figure is right there in the object, the boolean says not to
show it, and every future template, exporter, API serialiser and copy-paste is one
`if not internal_only` away from showing it anyway. Somebody under time pressure will reason
that *this* surface is fine. Nobody argues with a field that does not exist, because there is
nothing to argue about — the number is not in the object they were handed.

A `documented` attestation propagates nothing special. It is as citable as a filing, which
is the whole reason for making the grade a distinction rather than treating all
operator-supplied data as second class.

## A correction is a new row

Attestations are immutable. A mis-keyed quantity is corrected by writing a superseding row,
never by an `UPDATE` — the shape `assumption_proposals` already uses (`supersedes_id`, a
`sequence` starting at one, and a `CHECK` that a row cannot supersede itself) and that
`audit_events` uses at the other extreme (a `BIGINT` primary key as chain order, with
`prev_hash` and `this_hash`).

ADR 0014 drew the line at "what a run left behind", and a trade record is on the far side of
it from the instant it is written: a NAV was computed from it, a monitor may have read it, an
attention item may have been raised because of it. An `UPDATE` would silently rewrite an
input to arithmetic that has already happened and possibly already been approved. The
superseded row is also the interesting one — "I entered 1,000 and meant 100" is precisely
what the post-trade reviewer of ADR 0077 exists to see, and an update erases it.

## Why not "Observation"

The obvious name is spent. `RawFact.period_key` (`core/schemas/facts.py:170`) and the
`uq_financial_facts_observation` constraint (`db/models/financial_fact.py:132`) both use the
word in ADR 0058's sense — a dimensioned fact is a different *observation* of the same
concept — and `macro_observations` uses it again for one value of one series at one vintage.
Two of those three are baked into an index and a constraint name that a migration cannot
rename for free.

This repository is careful about vocabulary; a word that means three things in one schema
means none of them. `Attestation` is also the more honest word: somebody is asserting this,
and their name is on the assertion.

## The claim constraint is the seam, and widening it is not the whole repair

`claims` currently enforces:

```sql
(kind = 'numeric') = (
  (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int = 1
)
```

That must become a three-way exclusive choice admitting an `attestation_id`, or a report can
never make a numeric claim about a holding. The check is only half the seam. The columns are
the other half, and `financial_fact_id` is a foreign key to `financial_facts`
(`db/models/claim.py:62`) rather than to anything more general — so widening buys an
attestation an arm of its own and buys nothing else a home.

**Macro is a second seam, and this decision does not close it.** A `macro_observations` row is
neither a financial fact nor an attestation: nobody filed it as their accounts and nobody's
book says it. A gilt yield therefore still has no arm on a `NUMERIC` claim after
`attestation_id` lands, and saying otherwise would be claiming a fix this ADR has not
delivered. That is an open question with two honest answers and this record picks neither: a
fourth arm, `macro_observation_id`, admitting a published statistic as a figure a claim may
name directly; or a settled position that macro reaches a report only wrapped in a
`Calculation`, which is the only route it has now. The wrapper is not a fabrication — the
calculation is real — but under it the platform cannot state a published statistic as a
numeric claim without inventing arithmetic to hold it, and that is a cost to be chosen
deliberately in its own record rather than inherited from a constraint nobody revisited.

**Both seams are one mistake made in two places.** `claims.financial_fact_id` and `_load_fact`
(`services/calculations.py:409`, a bare `session.get(FinancialFact, …)`) each encode the
unstated assumption that *kind == fact* means `financial_facts` — one in a foreign key, one in
a lookup. ADR 0072 is where that assumption is dug out and named, and it explains why a reader
that guesses which table a `fact` lives in degrades quietly at whichever end it sits. The
resolver end is ADR 0072's subject; the claims end is nobody's subject yet, and needs its own
record before a macro figure can be the number a sentence asserts.

## Consequences

**An `attested` Attestation is a number backed only by the operator's word, and it will
become the path of least resistance for every awkward figure in the system.** An
unsourceable FX rate, a private mark on an unlisted holding, a fee estimate, a cash balance
nobody wants to export a statement for. The FX case is not hypothetical: ADR 0045 makes
every non-EUR pair a derived cross, and the ECB says its own reference rates are "not
intended to be used in any market transaction", so the temptation to type a rate rather than
source one recurs daily on a multi-currency book. ADR 0078 — *a rate is a dated observation
with a source, not a number in a column* — is what stands between that temptation and the
default, and this ADR leans on it: an operator-typed rate must be the fallback a rate store
leaves over, never the ordinary way a book gets converted.

**Invariant 3 is amended here, and an invariant amended silently is an invariant
abandoned.** CLAUDE.md states it as "No figure reaches a report unless it is a stored fact or
a recorded calculation", and `db/models/claim.py`'s module docstring repeats that sentence
verbatim as the reason for `ck_claims_numeric_claims_name_one_figure`. Both now read "a
stored fact, a recorded calculation, or an attestation", and the docstring moves in the same
change as the constraint — a docstring justifying a two-way check beside a three-way one
teaches the next reader that the prose in this repository trails the schema. **Invariant 1 is
untouched, and that is the point.** A `documented` attestation traces to a hashed artefact by
the same chain a filing does, and an `attested` one reaches no shareable surface at all,
because the type it propagates into has no field for the figure. The widening admits a third
kind of figure; it does not admit an unevidenced one.

**The containment is structural or it is nothing.** The grade is a column on every row, the
propagation is a type with no field for the figure, and no prompt, template or convenience
argument can reach past that. If a later change lets an attested figure reach a shareable
surface behind a boolean, this ADR has been reversed whether or not anybody writes the
successor.

The platform also begins holding documents about its operator rather than about public
companies. That is a new category in the artefact store — not licensed like ADR 0030's
market data and under no external purge obligation, but personal in a way nothing there is
today, and the retention question should be answered before the first custodian statement is
hashed rather than after.
