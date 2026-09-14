# ADR 0114 — A bank's revenue is derived, and only for a bank

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
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
