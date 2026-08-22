# ADR 0076 — The risk analyst comments on numbers it cannot write

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** ADR 0035, which holds that a new agent role needs an ADR before it needs
code, and `docs/investment-os.md` §9, which calls this one "barely a model role".
**Extends.** ADR 0063, whose platform-filled contract fields are the whole mechanism here.

## Context

The risk surfaces want a familiar list: exposure by sector, geography, currency and factor;
concentration in the largest holdings; drawdown against the book's own peak; each position's
contribution to portfolio risk; scenario profit and loss; an expected shortfall in the tail.
That reads like analysis — it is what a risk analyst writes a note about, and the note is
prose, so the tempting shape is a role that produces the note and the numbers in it together.

**Almost none of it is analysis.** Volatility is a variance under a square root and an
annualisation factor. Exposure is a sum of position values grouped by an attribute.
Concentration is that sum's largest members over the total. Drawdown is a running maximum
over a mark series. Scenario P&L is a shock applied to a mark. A risk contribution is a
weight times a covariance row over portfolio variance. Expected shortfall is the mean of an
ordered tail. Each has a right answer, and none has an interesting one.

The repository has already built the hard half without a model in sight.
`aer/calc/prices.py` holds `variance` (line 785), `covariance` (824), `beta` (877),
`simple_returns`, `aligned_returns`, `total_return` and `market_capitalisation` — each a
`@traced` calculation, pure, `mypy --strict`, property-tested, refusing a window too short to
mean anything and a market proxy that did not move. They were written for the research tool.
The portfolio needs the same functions over a different collection of rows.

One genuine blocker is worth naming, because it is not a model problem. **Currency exposure
cannot be computed at all yet:** `MacroClient.fetch_reference_rates`
(`src/aer/sources/macro/client.py:207`) has no callers, there is no rate store, and
`Quantity.__add__` refuses to add dollars to pounds rather than coercing. That figure waits
on §6.2's rate store and ADR 0045's pivot arithmetic, not on anything a model could supply —
and a role that answered it in prose would be inventing the rate.

## Decision

**Most of this decision is a refusal.** Every figure named above is a `@traced` calculation
in ordinary Python, under ADR 0003 and ADR 0011, with unit and property tests, persisting its
formula, its inputs — each with a unit and a source — and the code version that produced it.
None is a reasoning task, and none goes in a prompt.

That is the repository's first rule and should not need a record. It gets one because **this
is the surface where the rule is hardest to keep.** The output is prose, which invites the
writer of the prose to own the figures inside it; and the arithmetic wears specialised
clothing — "expected shortfall" sounds like expertise in a way "revenue divided by shares"
does not — which makes deferring to a model feel like deference to a judgement. It is forty
lines of Python, and CLAUDE.md names this exact temptation as the single most common way
systems like this produce confidently wrong numbers.

**What remains is commentary over fields the model cannot represent.** The risk contract's
numeric fields carry `"platform_filled": true` and are rendered from the ledger by code;
`model_facing_contract` (`src/aer/sections/deterministic.py:383`) strips them from the schema
the model is bound by, and the reply envelope forbids unknown keys. So the model cannot
restate a figure, round one, or correct one — its reply has nowhere to put a number. This is
ADR 0063's shape unchanged, for 0063's reason: a paraphrase of a record can only be equal to
it or wrong, and when it is wrong it is wrong in the register a reader trusts most.

**The role says what the pattern means, not what the number is.** That three of the five
largest exposures are one end market under different sector codes; that a drawdown sits
almost entirely in one position and the rest of the book barely moved. Those are readings of
a table, and a reading is the one thing a table does not supply. Like the valuation
commentary it gets a deterministic edge — the augmenter's `check` slot, refusing a commentary
naming a quantity the rendered block does not hold, inside the drafting loop so a retry is
told which term to drop rather than losing the draft.

## What this role does not do

- **It does not size a position.** Given a sizing rule a size is arithmetic; given none it is
  the operator's judgement. Neither is a commentary role's.
- **It does not set or propose a limit.** A limit states how much loss the operator will
  accept, and no price series answers it. ADR 0046 admitted a proposal role for two numbers
  of that kind, but only because a discounted cash flow could not run without them. A book
  runs perfectly well with no limits at all, so that argument does not reach here.
- **It does not choose which scenarios to run.** A scenario set is a claim about what is
  worth worrying about; a model choosing it decides what the operator sees and, by omission,
  what they never see.
- **It does not rank positions for disposal.** That is a recommendation with the word taken
  off the front.
- **It does not produce a risk score.** One number compressing exposure, concentration and
  drawdown is a rating under another name, and `rating` has been reserved since the skill
  schema existed.

## The six sizing names are reserved in the change that first needs them

`RESERVED_OUTPUT_FIELDS` (`src/aer/core/schemas/skill.py:67`) is today exactly six names:
`rating`, `recommendation`, `target_price`, `price_target`, `valuation_range`, `fair_value`.
**It must gain six more — `position_size`, `weight`, `recommended_weight`, `action`,
`order_quantity`, `stop_loss` — with an attack file each in
`tests/fixtures/fx_skill_adversarial/`, in the commit that introduces any sizing concept,
never in a follow-up.**

**`conviction` is deliberately not on that list, and it does not wait for sizing.** ADR 0070
reserves it outright, as part of its own decision and on its own reason: a conviction is a
judgement, and a skill that can declare an output field named `conviction` is a skill that
puts a judgement where a number goes. Naming it here as well would give one rule two due
dates, and a reader would have to guess which governed. This section governs the six sizing
names and nothing else.

A skill file that can declare an output field named `recommended_weight` is a skill file that
sets position sizes. Invariant 7 exists to make that unrepresentable, by ADR 0034's method:
refuse the *field*, so no value survives for anything downstream to police.

The same-commit rule is ADR 0040's guarantee working backwards. That corpus is a blocking CI
metric that must read zero for ever, and it proves resistance to the escalations somebody
wrote a file for — so a reservable-but-unreserved name is not a failing test, it is silence.
The gap would open the day sizing arrives and close, if ever, on a day nobody could date.
Since `reserved_fields_in` (`src/aer/core/section_output.py:247`) is the single function both
the execution boundary and the corpus call, adding a name arms both at once, and doing it in
the same change costs one line.

## No tools, and a cheaper route

**No tools**, like every registered role but `analysis` and `custom_section`. The book, the
figures and their provenance are assembled by code before the call; a risk role that could
fetch would be choosing its own inputs for a commentary nobody gated.

**The mid-tier route at medium effort** — the one `validator`, `peer_proposal` and
`theme_proposal` take in `DEFAULT_MODEL_ROUTES` (`src/aer/config.py:157`) — not the planner's.
The planner chooses a research programme, the red team tries to break a finished thesis, and
the assumption proposer picks two numbers a valuation rests on; those earn the expensive
route because their output is upstream of everything. Naming the pattern in figures somebody
else computed is downstream of everything, and it runs per book on a schedule rather than
once per report, so the cheaper route is also the one the monthly cap notices.

## Consequences

**The cheapest and safest of the three new roles, and the one to build first.** Nothing about
it is novel but the domain: the contract shape is ADR 0063's, the containment ADR 0034's, the
registration ADR 0035's, and the arithmetic the same `@traced` machinery every ratio in a
report already goes through. If this role is hard to build, the framework is wrong rather
than the role — worth learning before ADRs 0075 and 0077 commit to the same shape.

**It is also the proof that ADR 0063's pattern generalises.** That record argued
platform-filled fields where the field's subject was the platform's own record, and leaned on
it. Here the subject is the book — a domain object, not a record about the platform — and the
mechanism holds unchanged. A pattern that works once is a fix; twice is architecture.

**The cost is duller prose**, and sometimes a point declined because it needed a number
nobody computed: ADR 0063's trade, accepted a second time.

**Two things this does not fix.** The role has nowhere to run yet — every model call passes
`Agent._refuse_what_cannot_be_afforded`, which reads `job.request_id` and refuses without an
equity research mandate behind it, and that generalisation is ADR 0068's. And containing the
commentary says nothing about whether the figures are *possible*: `aer/calc/plausibility.py`
holds no portfolio relation, so weights that do not sum to one and a NAV that moves with no
trade and no mark change are as publishable today as the 172.1% net margin was. A fully
traced risk block can still be impossible, which is ADR 0066's lesson unlearned at a larger
scale.
