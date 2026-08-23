# ADR 0046 — A role that proposes only the two numbers no filing can answer

**Status.** Accepted
**Date.** 2026-08-09
**Required by.** ADR 0035, which holds that a new agent role needs an ADR before it needs
code.

## Context

The valuation has never run. `aer/calc/dcf.py`, `aer/calc/wacc.py` and
`aer/services/valuation.py` were built through Phase 3 with unit and property tests, and no
workflow step calls any of them — so the valuation page is empty, the scenario and
sensitivity charts are placeholders, and gap B2 has stood open since the first live run.

The reason is `inputs_from`, and it is a good reason. It refuses to assemble a forecast
without a **confirmed** assumption for every driver and every scalar, and says why: a
terminal growth rate this platform chose "would be its opinion presented as the operator's".

Eight assumptions at a minimum — five driver paths, `tax_rate`, `terminal_growth` and
`exit_multiple` — and up to twenty-eight if every driver is given a per-year path over a
five-year forecast. Nothing proposes any of them, so the assumptions page has always shown
an empty list, and the honest state of the product is that a discounted cash flow requires
an operator to fill a form that offers no help at all.

**Six of the eight are not opinions.** Revenue growth, EBIT margin, capex intensity,
depreciation intensity, working-capital intensity and the effective tax rate all have a
history in the filings this run already acquired. Proposing "revenue grows at the compound
rate the filings show, which is 11.4%" is arithmetic with a stated basis. It is a starting
point somebody still has to agree with, and it is not a judgement — which is why it is
deterministic code and appears nowhere in this ADR beyond this paragraph.

Those six are proposed in their **flat** form, one value per driver, because a trailing
average *is* flat and twenty-five per-year rows all carrying the same number would be noise
dressed as detail. `_path_for` already prefers a per-year path when one is confirmed, so an
operator who wants a fade enters the years they want and the flat proposal steps aside.

**Two of them are opinions, and no amount of history makes them otherwise.**

* **`terminal_growth`** is a claim about the rate at which the business grows *for ever*
  after the explicit forecast ends. No series answers it. It is a view about the economy,
  the industry's maturity and the durability of whatever advantage the company has.
* **`exit_multiple`** is a claim about what somebody would pay for the business at the end
  of the forecast. It is a view about where the market will be, which is not in any filing.

Between them they usually decide most of the value. That is exactly why the platform has
refused to pick them, and exactly why leaving the operator to guess unaided has not served
them either.

## Decision

**A new agent role, `assumption_proposal`, whose entire job is to propose those two numbers
with a justification.**

It proposes. It never confirms. `aer.services.assumptions.propose` returns an unconfirmed
assumption whatever the caller says, and `as_quantity` refuses an unconfirmed one — so a
value this role produces cannot reach a calculation until a person has agreed to it at the
assumptions gate. That containment already existed; this role is admitted *because* it
existed.

### The confinement is a type, not an instruction

The output contract has a field for a terminal growth rate and a field for an exit multiple
and no other fields. It cannot propose a revenue-growth path, a margin, a tax rate or a
discount rate, because there is nowhere in the schema to put one. This is the argument ADR
0034 made for withheld comps and ADR 0029 made for the sector block: a rule enforced by what
an object can contain is one a later prompt cannot talk its way around.

That matters more here than usual. A role that could propose the whole forecast would be a
model setting every number in a valuation, with a human review step as the only thing
standing between it and a report — and review fatigue over a long list is a real failure
mode, where review of two is not.

### The bounds are code, and a breach is a refusal

Whatever comes back is checked deterministically before it becomes a proposal:

* **Terminal growth must be below the discount rate.** Above it, the Gordon terminal value
  is negative or infinite; at it, undefined. This is not a matter of taste.
* **Terminal growth must not exceed a stated long-run nominal ceiling.** A business growing
  faster than the economy for ever eventually becomes the economy.
* **The exit multiple must fall inside a stated band.** Outside it the number is not a view,
  it is a typo or a hallucination.

**A breach is refused, never clamped.** Clamping would substitute this platform's number for
the model's and then present it under the model's justification — a figure attributed to
reasoning that did not produce it, which is worse than no figure. A refused proposal leaves
the assumption unproposed and the operator types it, which is exactly where this started and
is a safe place to end up.

### No tools

Like the planner. It is given the computed history, the deterministic proposals and the
run's research findings, and it returns two numbers and its reasons. It fetches nothing,
searches nothing and cannot reach the network, so there is no path by which fetched text
could steer it beyond the containment `aer.agents.untrusted` already applies to evidence.

### Routing

`claude-opus-5` at high effort, alongside the planner and the red team. This is judgement
work whose output the whole valuation rests on, and it runs twice per report at most.

## What this role is not

**It is not `valuation_interpretation`.** That name is already reserved in the model routes
and in the router's role list, from `docs/archive/PLAN.md` §1.8, and it has never had a
`RoleDefinition`. Its intended job is to write *about* a finished valuation. Interpreting an
output and choosing an input are different jobs at opposite ends of the pipeline, and
merging them would put a writing role in the position of deciding numbers. `assumption_proposal`
is registered separately and `valuation_interpretation` stays unbuilt.

**It does not choose the discount rate.** The WACC is decomposed by `aer/calc/wacc.py` from
a risk-free rate, a beta and a premium, each with its own provenance.
`aer.services.valuation.SCALAR_NAMES` deliberately excludes it, and this role inherits that
exclusion: taking the discount rate as a proposed scalar would let one unexplained number
stand in for the whole cost-of-capital chain.

**It does not decide whether a valuation happens.** The sector mandate does that, and a
blocked model means no gate and no proposal — a bank never reaches this role.

## Consequences

A discounted cash flow becomes reachable in a live run for the first time: six assumptions
arrive derived from the filings with their derivation stated, two arrive proposed with
reasons, and the operator confirms or overrides all of them at one gate.

**The gate is now load-bearing in a way it was not before.** Until now every gate guarded
work that had already happened. This one guards work that is about to, and the numbers behind
it came partly from a model — so the gate payload states, for every assumption, what the
value is, what unit it is in, who or what proposed it, and the justification. An operator
approving a list they cannot interrogate is not a control, and this is the gate where that
would matter most.

**A model's judgement is now upstream of a headline number**, which is a real widening of
what the platform lets a model influence, and it is why this record exists. The mitigations
are the ones above: two fields and no more, bounds enforced in code, refusal rather than
clamping, no tools, and a confirmation the model cannot perform.

## Amendment — 2026-08-20: the gate verifies the rows, not the step's record

The first live run exposed a gap between what this gate displayed and what the valuation
read (gap A52). The gate page rendered the assumptions step's frozen output, and the
workflow verified the approval hash against that same record — but the valuation reads the
*rows*, which the operator can amend or add while the run waits. Two failures followed. A
value the operator saved stayed invisible on the gate page, which reads as a save that
failed — the run's operator typed the missing cost-of-capital inputs and watched the page
keep calling them outstanding. And an approval could verify against figures the forecast
would never use, because the rows had moved since the step assembled.

So this gate — alone among the gates, because it alone approves inputs to work that has
not happened yet — assembles, displays and verifies its payload from the rows as they
stand. `assumptions` and `outstanding` are re-read; `refused` and `skipped` stay the
step's own, because they describe what the run did and no row edit rewrites history.
Unchanged rows reproduce the step's payload byte for byte, so nothing changes for a run
nobody edited; a row changed after an approval pauses the run for a fresh decision, which
is what the original hash discipline always promised.

The same amendment put the entry forms on the gate page itself — supply an outstanding
value, amend or confirm a proposed one, each through the per-request surface's own routes
— and made the page state the consequence of approving with gaps: no discounted cash
flow, no scenario bridge, no sensitivity grid. An operator standing at a gate they cannot
act on is not operating a control; they are watching one.
