# ADR 0114 — A bank's revenue is derived, and only for a bank

**Status.** Accepted — 17 September 2026, when the change landed (Phase 2, offline half).
What it still waits for is named in *What landed* below: the live M&T run and the re-render
of the stored run are Phase 2's exit and need the corpus and the keys, neither of which is
in this container.
**Date.** 2026-09-14
**Extends.** ADR 0101 (a bank's grid varies the spread), which established that a bank is a
different kind of filer and gets a different valuation path. This extends the same reasoning
one layer down, to the fact that feeds it.
**Does not touch.** ADR 0066 (a figure that cannot be true is withheld and raised) — the
plausibility layer keeps its job, and this ADR is what stops it having to do it on every bank.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F7, and open question 3,
answered by the operator on 14 September 2026.

## Context

The M&T Bank run published a **172.1% net margin** on its front page. Every guard held: the
revenue figure was a stored fact with a hashed source document, the margin was a recorded
calculation carrying its formula, its inputs and the code version that produced them, and
every citation verified. The number is impossible, and nothing in the platform asked whether
it could be true. `aer.calc.plausibility` exists because of that run.

The cause is one concept resolution, and it is not a bug. Read from the store today:

| Canonical concept | Raw concept | FY2025 |
|---|---|---|
| `revenue` | `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` | **$1,657m** |
| `net_interest_income` | `us-gaap:InterestIncomeExpenseNet` | $6,948m |
| `noninterest_income` | `us-gaap:NoninterestIncome` | $2,742m |
| `net_income` | `us-gaap:NetIncomeLoss` | $2,851m |

`RevenueFromContractWithCustomerExcludingAssessedTax` is the ASC 606 disclosure: the portion
of a bank's fee income that arises from contracts with customers. It is a **correct fact,
correctly extracted, correctly hashed, and a correct answer to a question nobody asked**. For
Microsoft it is revenue. For a bank it is a footnote.

A bank's income statement does not have a revenue line. It has net interest income — the
spread, already net of interest expense — and non-interest income, and the sum of those two
is what every analyst, every screen and every comparison means by "total revenue". The
taxonomy does not tag that sum, because the filing does not present it as a caption.

**So the figure is absent, and the platform's concept map resolved to the nearest thing with
the right word in its name.** That is the failure mode: not a wrong number, but a right
number promoted to a role it cannot hold, after which every ratio with revenue in the
denominator is nonsense, and each one of them is traceable, cited, and wrong.

## Decision

**For a filer the sector gate has confirmed as a bank, `revenue` is derived — `revenue =
net_interest_income + noninterest_income` — at the fact layer, and the ASC 606 concept never
resolves to `revenue` for that filer.**

Four parts, and the second and third are what keep this inside the one rule.

### 1. It is a derivation, not a mapping

The derived row is a `financial_fact` with `basis = derived` and its own recorded provenance:
both inputs by id, each with its own source document, unit and period; the formula as a
string; the code version. It is not a re-labelled `InterestIncomeExpenseNet` and it is not a
number a model proposed. It is arithmetic over two stored facts, which is exactly where
arithmetic belongs — and it is therefore a recorded calculation in everything but the table
it lives in.

### 2. It fires only for a confirmed bank

The trigger is `confirmed_classification` returning the banks profile — the sector gate the
operator has already cleared, not a guess from a SIC code and not a heuristic over the
concept set. `sector_gate_required` already returns true for a proposal of `banks`, which
means **a human has said so before the derivation exists**. A filer whose sector is
unconfirmed, or confirmed as anything else, keeps the ordinary resolution untouched.

This is the part that makes the change safe to make at all. A rule that guessed "this looks
like a bank" would be a rule that silently rewrote revenue for an insurer, a specialty
lender or a fintech with an interest line.

### 3. Both inputs, or nothing

If either `net_interest_income` or `noninterest_income` is missing for a period, **no
`revenue` is derived for that period** — the figure is absent, and absent is a state the
platform already handles honestly. It does not fall back to the ASC 606 concept, because
falling back is how the original defect happened.

And the units must agree. A currency or scale mismatch between the two inputs **raises**; it
never coerces. That is invariant 5, and a derivation is exactly where a platform is tempted
to break it.

### 4. The ASC 606 concept keeps its own name

`RevenueFromContractWithCustomerExcludingAssessedTax` is still extracted, still stored, still
available — under a canonical name that says what it is (`revenue_from_contracts`), not under
`revenue`. It is a useful figure about a bank's fee business. It is not the top line.

## What this changes, in numbers

M&T's FY2025 revenue becomes **$9,690m**, and its net margin **29.4%** — a figure an analyst
would recognise, against the 172.1% the run published. Every ratio with revenue in the
denominator changes with it, which is why this lands with a full re-render of the stored run
and a diff, not as a quiet fix.

**Six readers take the derived row without knowing it is derived**, because the derivation is
at the fact layer and they all read facts: statement assembly, the ratio suite, the
comparables denominator, the growth series, the margin bridge and the front page. That is the
argument for doing it at the fact layer rather than at each site — six special cases is six
chances to miss one, and the one that gets missed is the one that reaches the report.

## What is given up, named rather than discovered

**A derived figure is no longer a caption in the filing.** A reader who checks the report
against M&T's income statement will not find "$9,690m" anywhere in it. The footnote must
therefore say what it is — the sum of two named captions, each with its own page reference —
rather than pointing at a line that does not exist. That is more honest than what happens
today, and it is a real loss of the "one figure, one caption" simplicity everywhere else in
the platform.

**The plausibility layer loses its most famous catch.** It still runs, and it should: the next
sector with a missing top line has not been found yet. But the whole point of this ADR is
that a guard which fires is a guard that was needed, and a guard that never needs to fire on
banks is better than one that catches banks reliably.

**Insurers, REITs and asset managers are not fixed by this.** An insurer's top line is
premiums earned plus investment income plus fee income, and the same trap is waiting. This
ADR does not generalise to a rule; it decides one sector, on evidence, with a human
confirmation in front of it. The next sector gets its own row in the profile and its own
argument.

## Consequences

**The sector profile gains a revenue composition.** `banks` already carries a valuation path
(residual income) and a grid; it now also carries how its top line is assembled. That is the
right home: everything sector-specific in one confirmed, operator-visible place.

**A property test guards the units**, and a table test guards the trigger: no derivation
without a confirmed bank, no derivation with one input, no coercion across a unit mismatch.

**The stored MTB run is re-rendered from its own record** as the proof — zero spend, and the
diff is the acceptance criterion. The old report stays readable at its own address with its
172.1%, because it is the record of what the platform said in September, and rewriting it
would destroy the evidence that this ADR exists to answer.

## What landed, 17 September 2026

The decision above is implemented as written. Four things the writing did not anticipate,
recorded because the next sector's argument will meet them:

**A fifth part: the row's workings have a column.** `financial_facts.derivation` (migration
0077) holds the formula, both inputs by id with their own units and source documents, the
confirmed sector, and the code version — under a check constraint making `basis = derived`
and a non-null `derivation` inseparable in both directions. Without it the decision's "its
own recorded provenance" had nowhere to live, and a derived figure with no workings is the
number somebody typed that invariant 3 exists to prevent.

**The retag happens before selection, not after.** Two facts are rivals for a period only
if they share a concept, so renaming the ASC 606 caption first is what stops it competing
with a total it is not. Renaming afterwards would also have let two spellings of the same
caption — including and excluding assessed tax — arrive at the unique index as two rows
claiming one identity, where whichever the insert reached first would have won.

**A third refusal, beside the two the decision names.** Where the filer *did* state a total
of its own (`RevenuesNetOfInterestExpense`, which the concept map already knows), that total
is the revenue and nothing is derived. Deriving anyway would have put two rows called
`revenue` in one period — the bases differ, so the unique index permits it — and left the
disagreement ladder to arbitrate between a filed caption and this platform's arithmetic.

**A store written before this rule is corrected, not worked around.** A database holding a
bank's facts from an earlier build has the ASC 606 caption sitting under `revenue`, because
until this decision that is where the concept map put it. Left alone, the derivation would
read it as a total the filer had stated and leave the $1,657m exactly where it is —
silently, which is the word that makes it unacceptable. So the extract step moves such rows
to `revenue_from_contracts` before deriving, and deletes one whose corrected identity
already exists, because under the observation index the two are one observation. Only the
canonical concept moves: the value, the unit, the filed date, the accession and the source
document are the filer's statement and are untouched, and `concept` is this platform's own
reading of the filer's tag, under a sector a person has now confirmed. A stored report still
replays, because its claims name facts by id and every figure behind them is the same.

**The guard the run needed was never firing.** `services/evaluations.py` asked for a concept
named `total_assets`; the canonical concept is `assets`. The turnover half of
`figure_plausibility` had therefore never been evaluated on any run, including the one that
published the 172.1% margin — so the layer this ADR says "loses its most famous catch" had
in fact caught nothing, and the margin relation alone is what would have fired. Fixed first,
with a test, because it *adds* findings to M&T before this ADR removes them.

**The footnote is not yet written, and the reason it is safe to wait.** §What is given up
asks that a derived figure's note say what it is — the sum of two named captions, each with
its own page reference — because the figure appears in no filing. The row now holds exactly
what such a note needs: both inputs by id, each of which has its own recorded excerpt. What
does not exist yet is the surface that walks them, and the footnote route resolves a marker
to a source document rather than to a fact. Until it is built the derived figure has no
excerpt of its own, which is the safe direction: a writer is given no quotation to attach to
it, and one invented anyway is refused by the verifier rather than published. The note
belongs with Phase 4's printing work, and is listed there rather than folded in here.

**Still outstanding, and both need the corpus:** one live M&T run to an approved, rendered
report, and the re-render of the stored MTB run with the diff as the acceptance criterion
(the §Consequences proof). Phase 2's exit criterion is unchanged.

## Alternatives considered

**Withhold revenue for banks entirely.** Honest, cheap, and what the plausibility layer
effectively does today. It also removes every margin, every revenue multiple and every growth
figure from a bank's report — which is most of what a reader wants — in order to avoid
publishing a sum of two figures the platform already holds. Withholding a figure you can
compute correctly is not caution; it is the *"holds more than it shows"* failure the audit
found five times over.

**Derive it in the ratio suite, where the margin is computed.** One site instead of the fact
layer, and it fixes the front page. It leaves the comparables denominator, the growth series
and the bridge reading $1,657m, and it guarantees that a report can contradict itself between
two pages — the exact failure the refresh mechanic exists to prevent.

**Ask the model which concept is the bank's revenue.** It would get it right. It would also
be a number chosen by a prompt, which is the one thing this platform does not do, and it
would get it right on a different basis each run.

**Map `revenue` to `InterestIncomeExpenseNet` alone for banks.** Simpler, no arithmetic, and
wrong by $2,742m — a bank with a large fee business would be understated by a third, and
nothing would flag it because the figure is plausible.
