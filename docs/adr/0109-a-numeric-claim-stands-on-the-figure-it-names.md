# ADR 0109 — A numeric claim stands on the figure it names

**Status.** Accepted
**Date.** 2026-09-05
**Extends.** ADR 0096 (a malformed claim costs the claim, not the section), ADR 0097 (a
numeral is checked against the figure, not its spelling), ADR 0057.
**Required by.** The first live run of the confirmation runbook (`docs/users/the-confirmation-run.md`),
whose `business_overview` section died twice on one rule, and whose `cash_flow_analysis` and
`capital_allocation` sections carried the same refusal beside the sign defect ADR 0097's
amendment records.

## Context

A numeric claim names exactly one figure — a stored financial fact or a recorded
calculation, by id — and the platform has required it to *also* carry at least one
proposed citation: the extraction id of a prose excerpt. `ProposedClaim._numeric_reason`
refused a numeric claim with no citation at draft time, and `CITATION_REQUIRING_CLAIMS`
made the final gate refuse the run if any numeric claim had no admissible one.

The live run shows what that rule does to a section about a company's own filings. The
`business_overview` pack dealt 34 iXBRL facts and 5 prose excerpts. Revenue of $331.8bn is
a fact row extracted by code from the 10-K's financial statements; it is not a sentence in
any of the five excerpts, which are passages of narrative. The writer had the fact's id and
nothing honest to cite for it, omitted the citation, and was refused — "Claim 1: A numeric
claim needs at least one proposed citation", nine times over, on both attempts. The
`schema` refusal counts on the three sections the run lost (10, 13 and 14) are this rule.
A writer that complied would have attached an excerpt that does not contain the figure,
which the citation verifier would then confirm — it checks that the excerpt is in the
document, not that the figure is in the excerpt. The citation was theatre, and the
refusal was for not performing it.

The same rule ran a second time at the final gate. `review_evidence` counted every
numeric claim without an admissible citation as unsupported, so a section that survived
drafting with a fact-backed claim and a citation that did not verify re-blocked the run
after the operator had approved it.

## Decision

**A numeric claim stands on the figure it names.** Naming a stored fact or a recorded
calculation by id *is* the claim's evidence: the fact carries its source document and was
extracted from it by code; the calculation carries its formula and inputs, each with a
unit and a source. That is a lineage the platform confirmed, not one the model proposed,
and it is exactly what invariant 2 asks for — the model may propose, only code confirms.

Concretely:

- `ProposedClaim._numeric_reason` requires exactly one figure id and nothing else. A
  citation on a numeric claim is admitted and verified as before; it is no longer
  required.
- `CITATION_REQUIRING_CLAIMS` is `{FACTUAL}`. A factual claim still has nothing but its
  excerpt to stand on, and the final gate still refuses a run with an unsupported one.
- `review_evidence` treats a numeric claim as supported by its figure. A citation it
  carries that fails to verify is still an unverified citation, and still blocks the gate
  until verified or overridden with a reason: the rule on citations is unchanged; only
  the rule that demanded one where none was owed is gone.
- The writer's prompt says so: a numeric claim names its figure; cite an excerpt as well
  only where one states the figure. It also says to quote a cited figure at a precision it
  rounds to — "50.9" or "51" over a stored 50.88, never "50" — because the agreement
  metric judges the draft's own precision, and the live run wrote "roughly 50" and was
  reported.

## Consequences

- The three sections the live run lost to this rule draft under it. The proof is a
  zero-spend replay of the archived replies against today's rules (`aer replay-draft`,
  the roadmap's next item), not another live run.
- `source_coverage` still reaches a fact-backed claim's source: the metric already added
  the fact's own document to the claim's sources beside any cited excerpt.
- A numeric claim in the report renders as it always did. Footnotes come from the content's
  figure rows (`calculation_id`, `source_document_id`), not from claims, so nothing a reader
  sees changes; what changes is that the section exists.
- The acceptance readout's `citations` row counts the citations the run *has*; a run whose
  numeric claims carry none has fewer rows, not unaccounted ones.

## What it does not decide

Whether a *factual* claim about a figure — "revenue grew" — should be admitted on a fact id
alone. It is not: a sentence that asserts a direction without a number is a factual claim
and owes its excerpt, and letting a fact id stand in for one would let the model narrate a
table without reading a word of the filing.
