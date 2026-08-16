# 0053 — A call is capped in pounds, not in tokens

Date: 2026-08-16
Status: Accepted

## Context

Every agent role carried a `max_input_tokens` allowance in the registry, checked against a
real token count before each call. The numbers were guesses — 20,000 for the planner,
30,000 for the research workers, 16,000 for the validator — chosen when the roles were
built, before any complete run existed to measure.

During acceptance testing, a live large-cap run failed at the analysis step:

> The analysis agent composed a call of 40367 input tokens against its registered cap of
> 30000. The call was refused before it was made — a composition this size means something
> was included that this role is not meant to carry.

The message's diagnosis was wrong. Nothing improper was included: a research worker's
evidence digests accumulate across its rounds by design, and a company with fourteen
thousand facts on file legitimately composes bigger turns than the fixture company the
number was sized against. The 40,367-token call would have cost roughly £0.20 against a
£12 run budget, and the guard that refused it was defending a number, not a ceiling anyone
cared about. This is A31 and A33's disease in its third appearance: a limit chosen before
measurement, failing on first contact with a real company.

The operator's direction was explicit: **there should be no token cap, only a spending
cap.** This ADR records how that direction is implemented and what of the old mechanism
survives, because one piece of it must.

## Decision

**The per-role input allowances are deleted** — the field is gone from `RoleDefinition`,
not set high. A generous guess is still a guess, and the next bigger company would find it.

**Every call is priced before it is made, and the price is checked against both real
ceilings.** At the provider boundary, the composed call's worst case — the counted input
tokens at the uncached rate, plus the full output ceiling at the output rate, since
`max_tokens` is the only hard bound on the expensive direction — is converted to pounds
and compared against what remains of the request's own budget and of the month's. A breach
raises the same `BudgetExceededError` the step-level guard raises, with the same scope
vocabulary, and the engine already turns that into a paused run awaiting a decision
whichever depth it comes from. A batch prices as one question for all its items, because
the items are submitted together.

This closes the gap ADR 0052 recorded as open: the step-level guard runs before a step, so
the many calls inside one — nineteen drafting calls, five research workers' loops — ran
unchecked between checks. Now the last call of a long step is guarded exactly as its
first, against spend that includes everything the step has already metered.

**One token-shaped check survives, because it is the vendor's, not ours.** A composition
whose input plus output ceiling cannot fit the routed model's context window is refused
before the call — past that limit the API answers 400, but only after the whole prompt has
been uploaded, so the early refusal is the same refusal for free. The windows live in
`providers/costs.py` beside the prices; an unlisted model gets the smallest current
window, conservative in the same direction as `unknown_model_prices`. This is the only
condition that still raises `TokenCapExceededError`, and it means what it now says: no
retry and no setting can fix it, the composition itself has to shrink.

**The projection deliberately ignores prompt caching.** A worst case that assumed cache
hits would under-guard exactly when the cache goes cold, which is the first call — the one
most likely to be the largest.

## Consequences

**What the old caps quietly provided is genuinely given up, and on purpose.** The
allowances doubled as tripwires: a caller that interpolated the whole evidence pack where
a digest belonged would trip 20,000 tokens long before it dented a budget. That bug now
surfaces as cost — visible on `/costs`, bounded by the run's cap, but no longer a named
refusal at the moment of the mistake. The trade is deliberate: the tripwires' false
positives were killing real runs, their true positives are bounded by £12, and the
operator owns both sides of that trade.

**A run can now pause mid-step with `budget_exceeded`.** Previously that status only
arrived between steps. The console banner needs no change — the scopes are the same two
strings — but an operator will now sometimes see a run pause at, say, the fourth research
worker rather than before the research step, which is the guard being precise rather than
a new failure mode.

**The count endpoint is consulted per call, as before.** The projection reuses the token
count the old check already paid for, so the boundary makes no additional requests.

**`max_output_tokens` stays in the registry.** The API requires a finite `max_tokens` on
every request; it is a wire parameter, not a policy allowance, and it is also the number
the spend projection multiplies by. Raising it for a role now visibly raises that role's
worst-case price per call, which is the coupling A33 was found by.
