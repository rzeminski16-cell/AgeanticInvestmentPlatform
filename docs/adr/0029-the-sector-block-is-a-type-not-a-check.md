# ADR 0029 — The sector block is a type, not a check

**Status.** Accepted
**Date.** 2026-08-05
**Implements.** `docs/PLAN.md` section 2.9 and `docs/phase-3-plan.md` task 28.

## Context

A discounted cash flow on a bank is not a rough answer. Deposits are a bank's raw material
rather than its financing, so enterprise value does not mean what the model assumes and free
cash flow to the firm is not a quantity the business has. The number the model produces is
arithmetic performed on a category error, and it looks exactly like a valuation.

`docs/PLAN.md` states the rule: **a blocked model produces a hard gate, not a footnote.** A
report that ran the standard model anyway and disclaimed it in small print is worse than one
that refused, because the number is what a reader remembers and the disclaimer is not.

Task 28's acceptance criterion sharpens that into something testable: *a bank ticker cannot
produce a DCF **by any route**, asserted at the calculation layer rather than at the page.*
The phrase "by any route" is the whole design constraint, and it rules out every obvious
implementation.

## Why the obvious implementations fail

**A check in the route.** Protects that route. The valuation surface arrives in task 31, the
API in task 31 as well, a CLI command eventually, and a Phase 4 agent tool after that. Each is
a new route and each has to remember.

**A check in the service.** Better — one place, and every caller who goes through the service
is protected. The failure mode is the caller who does not: `discounted_cash_flow` is a public
function in a pure module, and importing it directly is the natural thing for anybody writing
a script, a notebook or a test.

**A check at the start of `discounted_cash_flow`.** Closest, and still wrong in a specific
way: it needs to know *which company* it is computing for, which means the pure calculation
kernel has to take a company id and reach a database. `aer.calc` is pure and side-effect free
by design (ADR 0003), and giving the DCF a database session to enforce a sector rule would
undo that for every function downstream of it.

**A boolean argument** — `discounted_cash_flow(..., sector_permits=True)`. Enforces nothing.
The caller who would have forgotten the check passes `True`.

## Decision

**A capability token, validated at construction, required by the function.**

`aer.core.sectors.ValuationMandate` is permission to run one model on one company. Its
validation lives in `__post_init__`, so:

- `ValuationMandate(model=DCF_FCFF, sector_key="banks", ...)` raises. There is no constructor
  that produces one.
- `dataclasses.replace(comps_mandate, model=DCF_FCFF)` raises, because `replace` re-runs
  `__post_init__`.
- The dataclass is frozen, so a permitted mandate cannot be mutated into a forbidden one.
- A factory-only check would have missed the first of these; the tests assert all four.

`aer.calc.dcf.project`, `discounted_cash_flow` and `sensitivity_grid` take a mandate as a
**required keyword argument with no default**. The kernel stays pure — a mandate is a value,
not a session — and a caller cannot reach the arithmetic without one. A bank does not produce
a DCF that is then suppressed; the call does not type-check, and if it is made anyway the
mandate it would need cannot be constructed.

**The person, not the model, decides which models are permitted.** A mandate for a specialist
sector requires `confirmed_by`, which comes from an approval at the `SECTOR_SPECIALIST` gate.
A classifier is a model, and a model that classified Barclays as early-stage technology would
otherwise unlock a DCF on a bank.

**An unconfirmed specialist proposal stops the run.** It does *not* fall through to
"unclassified". This is the subtle half and the one worth writing down: unclassified is the
*permissive* state — most listed companies are ordinary and run the standard model — so a run
that reached it by forgetting rather than by deciding would be permissively wrong. The
asymmetry is the point: a wrong classification that blocks a model wastes an afternoon; one
that permits a model puts a meaningless number in a report.

## Consequences

**The mandate reaches every layer, and that is deliberate friction.** `run_valuation`,
`run_scenarios` and `run_sensitivity` all take one and hand it down. Threading an argument
through four call sites is a cost; it is the cost of the guarantee, and mypy found every site
that needed it the moment the argument was added — which is the argument for a type over a
convention in one line.

**The block is a block in the report too.** `SectorNote` renders immediately after the header
and before any analysis, naming the models that were not run and carrying the profile's seeded
warnings verbatim. A sector warning at the foot of a report is a footnote, which is what this
whole decision exists to avoid.

**Required metrics are disclosed both ways.** `MetricDisclosure` reports what the run produced
*and* what it owed and did not, because a list of what a report has says nothing about what it
was supposed to have.

**The classifier is a floor, not the finished article.** The proposal currently comes from the
filer's SIC code — free, deterministic, reproducible, and available before any model call.
`Company.sic` is populated by the adapters that parse it (Companies House, and the SEC
*submissions* endpoint); the vertical slice acquires *companyfacts*, which does not carry one,
so a run through that path classifies nothing and takes the standard model. Phase 4's
classifier agent replaces the proposal and nothing else: the confirmation, the mandate and the
block are all indifferent to who proposed.

**What this does not do.** It does not stop somebody classifying a bank as a utility and
confirming it. Nothing in code can: that is a judgement, it is recorded against a named person
with their notes, and the audit trail is the control. The rule enforced here is narrower and
worth having on its own — no model runs on a sector that blocks it, and no classification
takes effect that nobody agreed to.
