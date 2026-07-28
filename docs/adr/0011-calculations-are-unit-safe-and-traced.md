# 11. Calculations are unit-safe, traced, and refuse unsourced inputs

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

`docs/adr/0003` established that deterministic Python owns every number. This ADR settles
*how*: what a number is represented as, what a calculation must record, and what happens
when either is incomplete.

Three failure modes drove the design. Each produces a wrong figure that **looks entirely
plausible**, which is what makes them worth building machinery against rather than relying
on review.

**The unit slip.** A function returns revenue in millions; its caller expects units. Every
figure downstream is off by a factor of a million and each one still looks like a number a
company might report. Nothing raises, because both values were `Decimal`.

**The binary float.** `0.1 + 0.2 != 0.3`. Above 2⁵³ a float cannot represent consecutive
integers, and revenue in raw dollars passes that routinely. A cash-flow model built on
floats is wrong in the last places from the first operation, and the error compounds.

**The unaccountable number.** A figure appears in a report and nobody can say where it came
from. This is not hypothetical in an LLM-assisted system: it is the default outcome unless
something prevents it, and a check applied at the end catches only the numbers somebody
remembered to check.

## Decision

### A quantity is a value, a unit, and a source

```python
Quantity(value: Decimal, unit: Unit, source: SourceRef | None)
```

`Decimal` is enforced at construction — a `float` raises, with a message. Units are
**dimensional vectors**, not strings: a mapping from base symbol to integer exponent, where
dollars is `{USD: 1}`, dollars per share is `{USD: 1, shares: -1}`, and a ratio is `{}`.

Multiplication adds exponents, division subtracts them. Every rule the specification asked
for then falls out of the arithmetic rather than needing to be written down separately:
`USD/USD` is dimensionless, `USD/shares` composes, `{} * USD` is `USD`. A string-based unit
system would need a table of every legal combination, and the first combination missing
from it would be a silent wrong answer.

### Adding incompatible units raises, always

There is no coercion path, no default, no "pick the left one". `USD + GBP` raises
`UnitMismatchError`. Currencies convert only through `Quantity.convert(target, rate=...)`,
which requires the rate's own unit to be `target/source` — so an upside-down rate is
refused rather than producing a number wrong by the square of the rate — and requires the
rate to **carry a source**.

### Decimal context: 34 digits, traps on

`CALC_CONTEXT` is a `Context` *value*, entered explicitly via `localcontext`. Importing
`aer.calc` does not change how `Decimal` behaves anywhere else, which is what keeps the
package free of side effects.

Precision 34 is IEEE decimal128. `InvalidOperation` and `DivisionByZero` are **trapped**:
by default `Decimal` returns `Infinity` and `NaN`, and both propagate silently through
every subsequent step to produce a report full of values nobody can explain.

Rounding happens in exactly one place, `Quantity.round_to`, at presentation. Half-to-even,
so that rounding a column of figures does not systematically inflate their total.

### A traced calculation refuses any input it cannot account for

`@traced(name=..., formula=...)` wraps a function. Before it runs:

- every `Quantity` argument must carry a `SourceRef` — a fact, an assumption, or another
  calculation — or `UnsourcedValueError` is raised;
- a bare `Decimal` or `float` argument raises;
- each element of a sequence argument is checked individually.

A refused call records nothing. The record, when one is written, holds the name, the
formula, the `module:qualname` function reference, the code version, every input with its
unit and source, the structural parameters, the declared assumptions, and the output with
its unit.

### The formula is declared, not derived

`formula="cagr = (end / start) ^ (1 / years) - 1"`, written next to the function.

### Inputs and parameters are different things

An **input** is a measured quantity from evidence and must be sourced. A **parameter** is a
structural choice — how many periods, which basis — recorded verbatim for reproducibility
but making no claim to be evidence. Both are persisted.

### A calculation's output is sourced to its own record

So feeding a result into another traced function is exactly as sourced as feeding in a
fact. This is what makes lineage a tree.

### Persisting a context is all-or-nothing

One savepoint. Half a chain leaves rows whose inputs reference calculations that do not
exist.

## Consequences

### Why the formula is not derived from the AST

It was considered and rejected. An AST rendering of a real financial function is
unreadable: it exposes guard clauses, intermediate names and `Decimal` plumbing, none of
which is the formula. The audience for a formula string is a person checking whether the
arithmetic matches what they expected, and for them `(end / start) ^ (1 / years) - 1` is
the answer and a syntax tree is not.

A declared string can drift from the code it describes. That is a real cost, and it is
smaller than the alternative: it is wrong *visibly*, next to the implementation, where a
reviewer sees both at once. The test suite asserts the declared formula on every
calculation, and `test_compounding_the_rate_reproduces_the_endpoint` checks that the code
actually does what the string says.

### Why the context is an explicit first argument

A `ContextVar` would make the API tidier. It would also make a traced function's behaviour
depend on invisible ambient state, and would let two concurrent research runs share a
ledger. `aer.calc` is required to be pure; ambient mutable state is the opposite of that.
The cost is one extra argument at every call site, paid in exchange for a kernel that can
be tested by calling it.

### Why `years` is a parameter and not a sourced input

It changes the answer, so the argument for sourcing it is real. But it is not a
measurement: it is a property of *which two facts were chosen*, and demanding a `SourceRef`
for it would mean minting a fake fact or a fake assumption to satisfy the rule. Fake
sources are worse than no sources, because they defeat the check while appearing to pass
it. Recording it as a parameter keeps it auditable and reproducible without claiming it is
evidence.

The smuggling risk — passing a real measurement as an `int` parameter — is closed from the
other side: an `int` has no unit, so any arithmetic combining it with a `Quantity` raises
on units before it can reach a result.

### Why inputs are JSONB rather than a join table

An input points at a fact, an assumption, or another calculation — three different tables.
A join table would need three nullable foreign keys with a check that exactly one is set,
or a polymorphic association; both make the common operation, reading a calculation and
showing what went into it, a multi-way join that has to know every source kind that will
ever exist.

What is given up is a database-level guarantee that every source id resolves. What replaces
it is `lineage()`, which resolves them explicitly and **reports the ones that dangle**
rather than hiding them. That is arguably better than a foreign key: a dangling input is
surfaced with its expected kind, so "this figure rests on a fact that is no longer here" is
a visible statement about the report rather than an insert that was refused months earlier.

### Why the unit `symbol` must round-trip through `parse`

A calculation's output unit is stored as text and read back to be used as an input to the
next one. The first implementation rendered `USD^2` and could not parse it — a gap that
would have broken exactly the compound and squared units nobody checks by hand. The
round-trip is now a property test over every currency and every exponent from -6 to 6.

### The cost of all this

Every calculation site is more verbose than `a / b`. Every test input needs a source. A
function that used to take two numbers takes two quantities and a context.

That verbosity is the deliverable. The alternative is a codebase where `a / b` is easy and
the resulting figure cannot be defended — and the entire premise of this platform is that
its figures can be.

## Alternatives considered

**A units library (`pint`, `astropy.units`).** Both are excellent and neither is built for
this. `pint` is float-first, which is disqualifying on its own; making it exact requires
fighting it. Both also treat currencies as awkward special cases, whereas here currency
*is* the primary dimension and the "no implicit conversion" rule is the point rather than
an inconvenience. The implementation here is about 200 lines and does exactly what this
domain needs.

**Enforcing provenance at the report boundary instead.** Cheaper, and catches only what it
is pointed at. A figure that reached the report through an unchecked path passes, and the
check has no way to know what it did not see. Enforcing at the calculation makes the
unaccountable case impossible to express.

**Storing values as JSON numbers in the API response.** Rejected: JSON numbers are IEEE
doubles in every parser that will consume them, so an exact `Decimal` would be corrupted at
the boundary — the last place anybody would look for a rounding error. Values are strings.

**Implementing `float` support "for convenience".** There is no convenience that is worth
`0.1 + 0.2 != 0.3` in a cash-flow model.
