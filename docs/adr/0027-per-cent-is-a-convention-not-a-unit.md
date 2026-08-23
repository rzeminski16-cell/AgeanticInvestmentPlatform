# ADR 0027 — Per cent is a convention, not a unit

**Status.** Accepted
**Date.** 2026-08-05
**Extends.** ADR 0011, which made units dimensional vectors carried through all arithmetic.

## Context

The unit system built in task 9 has caught real mistakes throughout this build: dollars added
to pounds, a share count divided into a currency, a margin multiplied by a margin. Its
guarantee is stated in `docs/archive/PLAN.md` invariant 5 — *units are carried through all arithmetic,
a mismatch raises, it never coerces* — and until task 26 that guarantee held everywhere the
arithmetic went.

Task 26 is where it stops holding, and the reason is worth writing down because the same shape
will recur in task 27 (growth and fade rates), task 30 (dividend and buyback yields) and task
31 (implied returns).

The ten-year Treasury yield arrives from ALFRED as `4.36`. Its unit is `pure`, correctly: a
yield is dimensionless. Beta times an equity risk premium is `0.055`. Its unit is `pure`,
also correctly. Both are genuinely dimensionless, not dimensionless by omission — there is no
missing symbol to add, no `Unit.base("percent")` that would be honest. And so:

```python
risk_free + beta * equity_risk_premium  # 4.36 + 0.055 = 4.415
```

runs, raises nothing, and produces a cost of equity of 441.5%. Discounting at it drives every
valuation in the report to approximately nil, and the number that caused it looks like a
perfectly ordinary Treasury yield in every log line, every provenance record and every table.

This is the first failure in this codebase that the unit system is structurally unable to see.

## Options considered

**1. A `percent` base symbol.** `Unit.base("percent")` would make `4.36 percent` and `0.055`
different units, and the addition would raise. Rejected: it is not true. A percentage is a
pure number written a hundred times larger; giving it a dimension means `percent * USD` is a
unit, `percent^2` is a unit, and every ratio in the platform then has to decide which of two
dimensionless dimensions it is in. The unit algebra is correct precisely because it describes
physical dimensions and nothing else, and the first non-dimension admitted into it is the last
day it can be reasoned about.

**2. Normalise at the source adapter.** Divide by a hundred in `aer.sources.macro`, so
everything downstream sees fractions. Rejected: it makes the stored observation disagree with
the published figure. `macro_observations` is a point-in-time archive whose value is that a
figure can be checked against the source it came from; a reader comparing `0.0436` against
FRED's `4.36` has to know about a transformation nobody recorded. Worse, it would mean the
stored value is not the fact — and invariant 1 says every externally derived fact traces to
the artefact it came from, unchanged.

**3. Record the convention on the series, convert once, and refuse implausible rates.**
Chosen.

## Decision

Three parts, and none of them alone is sufficient.

**The registry records the convention.** `MacroSeries.quoted_in_percent` says whether a
series' published figures are percentages. It is a property of the series, established once
when the series is added, alongside its licence and its originator — the same place every
other thing that has to be true about a series is written down. A test asserts the flag agrees
with the label for every series in the registry, so a new rate series added without it fails.

**One function converts.** `aer.calc.wacc.rate_from_percent` is `@traced`, so the division by
a hundred appears in the calculation ledger as a step with an input, a formula and an output.
A conversion performed inline at a call site is invisible, and the second call site is the one
that forgets.

**The rates refuse to be implausible.** Every guard in `aer/calc/wacc.py` refuses a rate
outside ±100%, and the message names `rate_from_percent` as the likely cause. This is the part
that actually catches the mistake: the registry can be wrong and the conversion can be skipped,
but a cost of equity of 4.415 stops at the next function that touches it.

Layered deliberately. The first two make the error unlikely; the third makes it non-silent.

## Consequences

**What this does not do.** Converting twice — `4.36` to `0.0436` to `0.000436` — is not caught
by anything here. It produces a risk-free rate of 4.4 basis points, which is implausible to a
person and entirely acceptable to every guard in the module. The mitigation is that there is
one conversion function and it is called once, in one place, from the service layer that reads
the vintage; there is no general defence and this ADR does not claim one.

**A stated threshold is not a default.** `MAX_RATE`, `MAX_BETA` and `MAX_QUOTED_PERCENT` are
refusal points, not substitutions. Nothing in `aer/calc/wacc.py` ever replaces a missing or
out-of-range input with a value — the module's acceptance criterion is that it contains no
defaults at all, and a test asserts it by inspecting every signature. A threshold that
*clamped* would be a default wearing a different hat.

**Where this recurs.** Any figure whose convention is not its dimension: basis points, index
levels rebased to 100, per-share amounts quoted in pence against a pound-denominated market
capitalisation, annualised versus periodic rates. Each needs the same three parts. The unit
system will not help with any of them, and expecting it to is how the 441.5% cost of equity
gets into a report.
