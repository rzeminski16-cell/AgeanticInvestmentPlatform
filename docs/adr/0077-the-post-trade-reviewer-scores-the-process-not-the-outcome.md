# ADR 0077 — The post-trade reviewer scores the process, not the outcome

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** ADR 0035, which holds that a new agent role needs an ADR before it needs a
registry row.
**Extends.** ADR 0063, whose rule — a statement about the platform's own record is code's to
make — decides which half of this role's output a model is allowed to write.

## Context

A position closes. Two questions are worth asking about it, and they are not the same
question: **did it make money**, and **was the decision sound**.

They come apart constantly. A thesis researched properly, sized sensibly, with its
invalidation conditions written down in advance, loses money because a rate cycle turned:
good decision, bad outcome. A position taken on a tip, resting on no premise anybody could
falsify, trebles because the sector re-rated: bad decision, good outcome. Across a few dozen
trades the two are correlated enough to be tempting and independent enough to be dangerous.

**A system that conflates them teaches the operator to chase whatever recently worked**,
which is the single most expensive habit in discretionary investing — and it costs twice
over, abandoning methods that work and have not yet paid, entrenching methods that do not
work and happened to pay anyway.

The platform has refused this once already, from the other side. ADR 0075 denies the thesis
monitor any sight of a mark, on the argument that a monitor marking premises weakened because
the price fell "would tell an operator to abandon correct theses in drawdowns and hold broken
ones in rallies". That record named the legitimate use of price and deferred it to this one.

The deterministic half already exists, in one narrow place. `services/history.py` builds the
prior-research comparison section — `prior_research_comparison`, position 900,
`token_budget = 0` — by measuring a prior run's confirmed assumptions against the filings
that later arrived, over `calc/outcomes.py`'s `@traced` `assumption_delta` and a
`realised_driver` built from traced ratios, on the same line concepts the proposal derivation
used. Its statuses are `measured`, `not_yet_observable`, `not_measurable` and `skipped`, its
docstring opens "read, never re-judged", and it reaches no model at all. So the platform can
already say what a forecast did. Nothing in it has ever asked whether the decision was well
made.

## Decision

**A new agent role, `post_trade_reviewer`, which reads a closed position's full record and
returns a per-premise verdict, two independent quality fields and its lessons.** A registry
row in the shape ADR 0035 requires: an output contract named by reference,
`allowed_tools=frozenset()`, and this record in the `adr` field. It runs once per closed
position, over the premises as they were written, the decision as it was recorded, whatever
the monitor said while the position was open, and a deterministic comparison of expectation
against outcome.

**`process_quality` is a separate field from `outcome`.** Not a derived field. Not a
weighted blend. Two fields that are permitted to disagree.

The reason is the whole record. There are four cells — good process with a good outcome,
good process with a bad one, bad process with a good one, bad process with a bad one — and
the two that carry information are on the off-diagonal. If `process_quality` is computed
from `outcome`, or scored on one scale that mixes them, those two cells are unreachable and
every statistic built on the field measures the P&L twice under two names. **A schema in
which the two axes cannot disagree is a schema that has already decided they are the same
thing**, and no prompt asking politely for a fair process score recovers the distinction
afterwards.

This is the house move run in the opposite direction. ADR 0034's `WithheldComps` and ADR
0029's `ValuationMandate` forbid a thing by having no field that could hold it; here the
forbidden thing is a *collapse*, so the enforcement is two fields rather than none. The
mechanism is identical: the schema decides what is sayable, and a later prompt cannot argue
with it.

**`outcome` is not the model's field.** It is platform-filled under ADR 0063: content whose
subject is the platform's own record is "rendered from that record by deterministic code,
never written by a model". Realised return, holding period against intended, whether the
catalyst arrived by the date the thesis named — arithmetic over stored bars, stored dates and
stored premises, handed to the reviewer as figures it may read and may not restate. The model
writes `process_quality` and the prose, which is what stops a review producing a performance
number no calculation backs.

## The output contract

Four things, and nothing else.

**A per-premise verdict from a closed enum** — `held`, `partially_held`, `failed`,
`untested`, `unobservable`. `unobservable` carries ADR 0075's meaning and 0075's argument:
nothing could ever have answered the premise, and forcing it into a verdict asserts a reading
that did not happen.

**`untested` is the value this role adds, and it is load-bearing.** A position exited after
four months on valuation leaves a premise about FY2027 margins unanswered — not weakened, not
falsified, unanswered, because the year it was about has not been filed. Without `untested`,
every early exit records its unresolved premises as `held` or `failed` according to what the
price did, which is exactly the conflation this record exists to refuse, arriving through the
one field nobody would think to guard. `unobservable` says nothing could ever answer it;
`untested` says something could have, and the position closed first.

**`process_quality`**, from a closed enum with a required basis — the model's judgement, and
the only one it makes. **`outcome`**, platform-filled, which the model receives and cannot
write. And **free-text lessons**, which are prose and are handled as prose.

**There is no field recommending a methodology change.** Not one validated empty — absent.

A methodology is a skill file, and a skill edit is a human act under invariant 7. The
structure enforcing that invariant is `compose_policy` in `aer/core/skill_policy.py`, which
clamps a requested number up to the built-in evidence floor and *intersects* a requested tool
set against the role's allowlist — and which has nothing whatever to say about a paragraph of
prose. `SkillKind.METHODOLOGY`, `PREFERENCE` and `HOUSE_VIEW` (`core/enums.py:313`) are
validated, versioned and pinned, and no execution path reads them; only `custom_section` is
branched on. The day the methodology library is built is the day invariant 7 first meets
content its enforcement was not designed for, and admitting a role that writes such content
beforehand would open the gap in the least visible place available: a checklist edited by the
system that was graded against it.

**The reviewer may observe that an invalidation condition was ignored. It may not rewrite the
checklist that contained it.** The first is a finding about what happened. The second is a
change to how the next decision gets made.

## Outcome testing belongs here, not in the monitor

This comparison is right once and wrong continuously, and that is the whole distinction
between this role and ADR 0075's. Comparing what was expected against what happened — the
return, the holding period, the catalyst date, the premises themselves — is the **right** use
of price and time, for two reasons that are both properties of *when* it runs rather than of
what it computes.

**It happens once, after the fact.** One reading per closed position, so there is no sequence
of readings for noise to accumulate across and no daily number for an operator to watch — and
by then the holding period is known rather than elapsing, the catalyst either arrived or did
not, and the filings covering the holding are in the store.

**It happens with nothing left to influence.** The position is closed. A reading that cannot
change a holding cannot bias one — the property the monitor lacks, and the reason 0075's ban
was categorical there and is unnecessary here.

**The same comparison run continuously against an open position is not evidence about the
thesis. It is noise with a P&L attached.** ADR 0075 argued that in full and this record does
not repeat it.

The arithmetic stays where all arithmetic stays: `@traced` calculations over stored bars and
dates, in the shape `calc/outcomes.py` already uses for a forecast driver. A realised return
on an LSE holding is a chain of recorded conversions rather than a subtraction — GBX quotes,
and the rate store `docs/investment-os.md` §6.2 describes — which is a reason for it to be
code's and none at all for it to be prose.

## A post-trade conclusion is the platform's own output

**A lesson learned may never support a research claim.**

This role produces the purest internal artefact in the system: a conclusion drawn from the
platform's own record about the platform's own record. `Provider.INTERNAL_PRIOR_RUN`
(`core/enums.py:262`) exists for a *weaker* case — a prior run's exported report — and is
uncitable twice over: no tier mapping, so it resolves to the unverified tier, and
`verify/citations.py:237` hard-rejects it regardless, because "a platform citing itself would
launder yesterday's inference into today's evidence". This role's output inherits that and
needs it more; a prior report at least rests on filings, where a lesson rests on a prior
report and on an operator's reading of what happened next.

**A lesson is a Judgement (ADR 0070), with exactly the privileges that record grants.** It
may be displayed — on the position, in the journal, in full. It may be compared with
outcomes, which is what this role is for. It may be aggregated for the operator's own review.
It may never be a `SourceRef`, because `SourceKind` (`calc/units.py:136`) stays closed at
three values and the exclusive choice on `claims` has no column that could hold one.

The failure this forbids would look entirely respectable on the page: a report section
reading "the platform's post-trade review of eleven similar positions found that
quality-compounder theses fail on capital allocation rather than on growth", cited to the
review that said it. Every check would pass, because there would be nothing to check against
but the platform's own row. Whether this tightens the loop ADR 0075 describes is a fair
question, and the answer is yes; nothing here closes it.

## Sample size is part of the statistic

**The Decision Analytics surface shows `n` on every statistic, and the display type makes
that structural rather than remembered.**

Twelve closed trades support no conclusion about win rate by thesis type. Split by type they
are four apiece, and "67% of quality-compounder theses worked" over three closed positions is
not a finding — it is two trades and a rounding. Rendered as a percentage it reads precisely
like a finding, and the operator adjusts their method to fit it. **A surface presenting a
ratio without its denominator invites the exact overfitting to noise this role exists to
reduce**, which would make it the most counterproductive screen in the product.

The precedent is the comps table, which has carried its counts since it was built —
`MultipleBand.count`, and `peer_count` and `excluded_count` on `WithheldComps`. So the rule
takes the shape `docs/investment-os.md` §8 gives the provenance badge: the count is a required
field on the type the template renders, so a page has no way to show the ratio without it. A
rule that lives in a review convention survives until the first cramped layout.

## Consequences

The trade journal becomes a genuine feedback loop rather than a diary. A closed position
acquires a per-premise reading, a process score free to disagree with the P&L, and a lesson
somebody can look up before making the same call again.

**The path from a lesson to a methodology change stays deliberately manual**, and that is a
cost this record chooses rather than a limitation to be lifted later. An automatic route from
"this went badly" to "the checklist now says" is the shortest road to a methodology overfitted
to a dozen outcomes and written by the system that graded them — which is ADR 0075's objection
to a role marking its own homework, arriving one level up.

**This is the only role in the platform that sees a mark**, admitted on one condition — that
the position is closed — which is a property of when the role runs rather than of what its
schema permits. A future change offering this role an open position voids the argument above
and reopens this record.

**It cannot fix a miscalibrated operator.** `process_quality` is one person's judgement about
their own earlier judgement, held to a Judgement's standard and no higher. What guards it is
the `n` rule and the honesty of the four cells: the ability to record *good process, bad
outcome* and mean it, and to be shown how few times it has been recorded.
