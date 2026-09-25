# ADR 0133 — The calculator strikes the report's own model over the operator's numbers, and records nothing

- **Status:** Accepted (25 September 2026)
- **Date:** 2026-09-25
- **Applies:** ADR 0132 §3 (the preview is computed and never recorded), whose last sentence
  names this page. Invariant 3 (no figure reaches a report unless it is a stored fact, a
  recorded calculation or an attestation) is untouched, because nothing here reaches a report.
- **Decided by:** the operator, 25 September 2026: *"For some of the big models such as DCF,
  we should have a page that is the calculator for that analysis, where the user can manually
  change the values and see how that affects the results."*

## Context

A report's valuation page reads the run's ledger and never recomputes. Its handler says why: a
page that re-ran the valuation would show today's answer beside yesterday's report, and both
would look authoritative. That rule is right for a record. It also means the one question a
reader of a discounted cash flow most wants to ask — *what if I believe something else?* — has
no answer anywhere in the product except Ask's tier 1. Tier 1 moves one input per question,
through a sentence the resolver has to parse, and records every answer.

The verdict round made the question concrete. On MSFT, the confirmed 3% perpetual growth and
14x exit multiple cannot both hold, and the operator's own view depends on which of them they
believe. ADR 0132 shows the conflict at the assumptions gate. This page is where the operator
tries the alternatives.

## Decision

### 1. A page per valued run, over the run's own model

`/runs/{id}/calculator` lists every confirmed assumption the discounted cash flow reads, each
with its confirmed value, and a box for the operator's own. It strikes the base case through
`aer.services.preview.strike`, the function ADR 0132's gate preview uses. That function uses
the value step's own assembly and arithmetic, so the calculator is the report's model, not a
model of it.

It shows the report's recorded figures beside the operator's:
- the value per share by each terminal method;
- how far apart the two finish;
- the implied growth and the implied multiple;
- the discount rate;
- the terminal value's share;
- the distance from the recorded price.

Nothing is averaged and nothing is ranged, as in the report.

### 2. It proves it is the same model before the operator uses it

Before it strikes the operator's numbers, the page strikes the confirmed values and compares the
result with the report's recorded base case, at the ledger's stored precision.

- **When they agree,** the page says so: the calculator reproduces the report.
- **When they do not,** the page says that too, and shows the confirmed values struck today
  beside the report's. The usual reason is that a later run has acquired filings the report
  predates, so the base year has moved.

A what-if built on a model that no longer reproduces its report would be a comparison between
two things, silently.

### 3. It records nothing, and says so

The operator's numbers travel in the page's address, never in a row. The ledger the arithmetic
writes to is discarded, as the gate preview's is. No figure on the page has a calculation id,
so none can be footnoted, exported, cited or carried into a report. The page says it is a
what-if in its first sentence. Ask's tier 1 remains the way to put a what-if on the record,
with its question.

### 4. Only what the model reads can be changed, and only within the gate's own bounds

The boxes are the confirmed rows the discounted cash flow reads. The cost of debt is left out
when the filings carry an interest expense, because the valuation then derives the rate and a
typed one would move nothing.

An entry outside `aer.core.assumption_scales.PLAUSIBLE_RANGE` is refused with the sentence the
assumptions form uses, minus the form's instruction to tick a box and submit again: a what-if
that reads as a typing mistake is typed again rather than overridden. The rest of the page still
strikes. Entries the arithmetic refuses, such as growth at or above the discount rate, are
refused too, with the arithmetic's own reason.

Building this found two faults in that shared sentence, and both are fixed at the source rather
than worked around here:
- It named the assumption by its key, as in *"outside the plausible range for
  risk_free_rate"*, so it now uses words.
- It raised on a value that parses but is not a number, such as `NaN`, rather than saying so.

## What is given up

**A what-if the operator can come back to.** Only the address keeps the numbers. That is
deliberate: a stored what-if is a record, and a record has to be a recorded calculation.

**Scenarios.** A scenario is a stored set of overrides with a name, and it feeds the report. The
calculator never feeds anything. They stay separate surfaces with separate rules.

## Consequences

- `aer.services.calculator` assembles the page from `aer.services.preview` and the run's
  ledger. `aer.web.pages` serves it, and the valuation page links to it.
- F5's workbook is the same model with its formulas written out. It is the calculator for a
  reader who wants to take the model away.
