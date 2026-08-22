# ADR 0068 — The ledger records derivations, not calls

**Status.** Accepted
**Date.** 2026-08-22
**Refines.** ADR 0011 (every figure is a recorded calculation) by settling what "a
calculation" counts as when the same arithmetic is asked for twice. Decided while closing
report-quality R14.

## Context

`@traced` appended a row per invocation. That is the obvious implementation and it was
wrong in a way that only showed up on a real note: the CHRW report's approval page listed
118 calculations for five periods, with `ebitda` appearing twice at the same value in the
same year, `days_outstanding` twice, and `depreciation_rate` twice.

The first two explanations were both wrong, and worth recording because they are the ones
anybody would reach for. The calculate step does **not** run per-statement as well as
per-period — the analysis loop strikes each period exactly once. A re-run does **not**
append instead of replacing — the recomputations that feed the valuation and comps
surfaces build their own throwaway contexts and are never persisted.

What actually happens is that several callers legitimately want the same figure. The ratio
suite computes EBITDA for its margin and again inside net debt to EBITDA. The cash
conversion cycle re-strikes all three days-outstanding ratios it is built from. A paired
earnings-quality signal recomputes its own base at each end of the comparison. None of
these is a bug; each is a caller asking honestly for a number, and each produced a second
row identical to the first in every field but the id.

Two identical rows are worse than redundant. A citation names a calculation id, so a
figure with two rows is a figure whose citation is arbitrary: the reader following it
learns nothing about which of the two the writer meant, and the two cannot be told apart
because there is nothing to tell apart.

## Decision

**Identical arithmetic on identical inputs is one derivation, and the ledger holds one row
for it however many callers ask.** `CalculationContext.add` memoises: a record whose every
field but the id matches one already struck is discarded, and the caller receives the
record that already exists.

**Identity is every field except the id** — name, formula, function reference, code
version, inputs, output value, output unit, parameters, assumptions and period. Anything
that differs makes a different derivation and gets its own row. In particular a figure's
**period is part of its identity**, so the same result struck on two years stays two rows;
collapsing those would be a claim about the run that is not true.

**The period a row carries is the period of the figure, not of the pass that struck it.**
A caller computing a figure *of another period* inside this one's pass scopes it with
`CalculationContext.stamped`. This fell out of the first half: the paired quality signals
were striking the prior year's base under the current year's label, which is a mislabelled
row rather than a duplicate one, and it was only visible once the duplicates stopped
hiding it.

## Consequences

**Lineage stays a tree and gets tighter.** The second caller's result is attributed to the
record the first one struck, so one figure has one id and a citation resolves to exactly
the arithmetic it names.

**Row count is no longer a measure of work done, and two tests had been using it as one.**
The DCF sensitivity grid asserted twenty-seven free-cash-flow rows to prove that nine cells
were nine complete valuations rather than one valuation and eight interpolations. The
forecast depends on neither axis of that grid, so those twenty-seven calls were always
three derivations, and the assertion was counting invocations. It now asserts what actually
varies per cell — nine distinct terminal values and nine distinct discount factors — which
is a stronger statement of the same property and one that does not depend on how the ledger
counts. The comparables band asserted two implied-value rows for its two ends; with a single
priced peer the lowest observed multiple *is* the highest, so both ends are one figure and
one row. It now asserts that each end of the band resolves to a recorded output, which is
the property the band actually needs.

**A run's calculation count will fall,** and a note re-run after this change will show fewer
rows than the same note before it, having done exactly the same arithmetic. That is the
intended effect and not a regression to investigate.

**What this does not do.** It does not deduplicate across contexts — the ledger is per pass,
and two runs computing the same figure each record it. It does not touch any value: the
memoised record is by construction identical to the one discarded, so no number anywhere
changes.

## Alternatives considered

**Deduplicate at persistence rather than in the context.** Rejected because the in-memory
quantity carries its source before anything is persisted, so the second caller would still
be handed an id that later vanished, and lineage would be correct in the database and wrong
in the object graph that built it.

**Let callers pass a flag to suppress recording.** Rejected as exactly the wrong default. It
makes correct provenance the caller's responsibility at every call site, and the call sites
are where this went wrong in the first place.

**Leave it and add a period column so the duplicates are at least visible.** Rejected: two
rows a reader can see are identical are two rows a citation still cannot choose between.
