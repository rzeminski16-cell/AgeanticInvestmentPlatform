# ADR 0070 — A bank is valued on the spread over its book value

**Status.** Accepted
**Date.** 2026-08-23
**Joins.** ADR 0029 (the sector block is a mandate, not a footnote), which said what a bank
may *not* have. This says what it gets instead. Decided while closing gap A64's remainder.

## Context

`aer.core.sectors` blocks `DCF_FCFF` for banks and insurers, for a reason that is not a
matter of taste: deposits and borrowings are a bank's raw material rather than its
financing, so enterprise value is not a quantity that exists and free cash flow to the firm
cannot be separated from the business of lending. The block works — ADR 0029 made it a
mandate the arithmetic requires rather than a warning printed under a number that was
computed anyway.

What the block left behind was a hole. A bank's profile permitted comparable multiples and a
dividend discount, and the dividend discount was named in the vocabulary but built nowhere.
The seeded warning said so in as many words: *"This build produces P/TBV and P/E comparables
only, and does not implement a specialist bank model."* So a run on a bank could reach a
valuation section with a relative judgement and nothing else — no model of the business, and
no figure a reader could argue with beyond the peer set somebody chose.

Three candidates were available.

* **A dividend discount.** The conventional equity model, and the one the vocabulary already
  named. Its weakness for a bank is where it puts the uncertainty: almost the whole answer
  ends up in a terminal value nobody can observe, and the near years — the ones a reader can
  actually check against a filing — contribute almost nothing.
* **P/TBV anchored on sustainable return on equity.** Cheap to build and genuinely how the
  sector trades, but it is a comparable multiple with a regression attached. It would have
  added a second relative judgement where the gap was the absence of an absolute one.
* **Residual income.** Takes the filed book value as the starting point and values only the
  excess of what the equity earns over what it costs.

## Decision

**Residual income is the bank model, and `ValuationModel.RESIDUAL_INCOME` is permitted for
banks and insurers.**

    equity value = opening book value + Σ PV(residual income) + PV(terminal residual income)

    residual income_t = (ROE_t - cost of equity) × book value_(t-1)

`aer.calc.residual_income` implements it on the same terms as `aer.calc.dcf`: pure,
`mypy --strict`, every step a `@traced` calculation, units carried through, and a
`ValuationMandate` as a required argument so the model cannot be run for a company nobody
permitted it for.

### Why residual income rather than the dividend discount

**They are the same model.** Under clean surplus the two give identical answers — the
identity is exact over any finite horizon, and `TestTheDividendIdentity` asserts it against
a dividend discount computed independently in the test:

    BV_0 + Σ PV(RI_t)  ==  Σ PV(dividend_t) + PV(BV_N)

So the choice is not about which number is right. It is about **where the uncertainty is
put**, and therefore about what a reader is asked to take on trust. A dividend discount asks
them to accept a terminal value that is most of the answer. Residual income asks them to
accept a book value the filer published, and puts the arguable part — the spread, and how
long it survives — in the explicit years where it can be examined. For a bank, whose balance
sheet is the most reliable statement it produces, that is the better place for the weight.

The dividend discount stays in the vocabulary and stays permitted. It is not built, and this
decision is why it is unlikely to be: it would be the same arithmetic presented worse.

### The terminal treatment has no default

`TerminalTreatment` is a required field with two members and no default:

* `FADE_TO_NOTHING` — competition removes the excess return at the end of the forecast, so
  there is no terminal value at all.
* `PERPETUAL_GROWTH` — the final year's residual income grows at a constant rate for ever.

On the worked example in the tests the two differ by 21% of the per-share answer, and the
excess over book value quadruples. **That difference is the claim about competition, stated
as a number.** A default would make one of those claims the silent case, which is precisely
the failure this repository exists to avoid — and the more optimistic reading is the one a
default would tend to smuggle in.

Under `FADE_TO_NOTHING` the terminal fields are `None`, not nil. A nil terminal value is an
arithmetic result; no terminal value is a refusal to make the claim, and a reader who sees
0.00 in a valuation table is entitled to ask which formula produced it.

### The discount rate is recorded as a cost of equity

`equity_discount_factor` duplicates arithmetic that `aer.calc.dcf.discount_factor` already
performs. The duplication is deliberate: the DCF records its rate under the name `wacc`, and
a ledger that calls a cost of equity a weighted average cost of capital gives the right
number under the wrong name. Reusing the function would have produced a correct valuation
with a misleading audit trail, which is the failure mode this platform is built to make
impossible.

### The per-share row is named for its model

`aer.calc.dcf` already records a calculation called `value_per_share`. The ledger stores the
name, so a second function claiming it would make every stored row of that name ambiguous —
and `aer.eval.replay.registry` refuses to build at all when two do. This model's step is
therefore recorded as `residual_income_per_share`.

The prefix is not a workaround for a naming clash. A per-share figure from a residual-income
model and one from a discounted cash flow are different claims about a company, and this is
the number a reader quotes; a report showing both should not have to guess which row
produced which.

### Insurers get it too, with their own limit stated

An insurer's balance sheet is unclassified for the same reason a bank's is, its free cash
flow to the firm is blocked for the same reason, and it trades on price to book against
return on equity — which is residual income's exact framing. The profile's warnings gain the
limit that matters: **this is not an embedded-value calculation**, so for life business it
omits the value of profits already written into policies in force.

## Consequences

* A bank or insurer can be valued absolutely, not only relatively. `undefined_concepts`
  (gap A64) stopped the platform asking a bank for accounts it does not keep; this stops it
  having nothing to offer once it has stopped asking.
* The clean-surplus assumption is now load-bearing and is carried on every result rather
  than footnoted. A bank whose available-for-sale book is underwater has a book value this
  model treats as fully earning, and the caveat says so on the figure.
* `perpetual_residual_value` refuses a negative final-year residual income. Capitalising a
  shortfall in perpetuity subtracts an unbounded amount from book value on one year's
  evidence; the operator is told to extend the forecast or to choose the fade treatment and
  say that the bank does not earn its cost of equity.
* Two profiles' seeded rows change, so migration 0053 moves the seed in step with the
  constants — the arrangement `test_the_seed_matches_the_constants` exists to enforce.
* **Not yet wired.** The calculation core and the permission exist; a bank driver set in the
  assumptions gate, dispatch in the valuation service, and the report surface do not. Until
  they do, this is a model the platform may run and does not yet run itself.
