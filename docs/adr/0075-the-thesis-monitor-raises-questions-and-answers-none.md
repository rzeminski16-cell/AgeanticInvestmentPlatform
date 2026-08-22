# ADR 0075 — The thesis monitor raises questions and answers none

**Status.** Accepted
**Date.** 2026-08-22
**Required by.** ADR 0035, which holds that a new agent role needs an ADR before it needs a
registry row.
**Extends.** ADR 0064. This is the first role whose subject *is* the platform's own prior
conclusions, so the boundary that record drew has to be redrawn for it rather than assumed.

## Context

A thesis is a set of premises about a company: that Azure keeps compounding above 25%, that
the margin structure survives the capex cycle, that management allocates capital well. A
position is a bet that those premises hold.

Evidence keeps arriving after the bet is placed — a quarterly filing, a results
announcement, a trading update — and each piece bears on some premises and not others. The
question is narrow and mechanical to state: does this new evidence support, weaken or
contradict this premise? It is also exactly the work nobody keeps up. Across a book of
positions it is a few hundred small readings a year, each boring until the one that matters,
and the failure mode is not a wrong answer but no answer — a premise falsified two reporting
periods ago and a position still held on it.

The platform already measures the shape of that failure without addressing it.
`services/knowledge.py:75` sets `STALE_AFTER_DAYS = 180` so that "a thesis nobody has
revisited for two reporting periods is visible" — visible as *unrevisited*. Nothing has ever
asked whether an unrevisited thesis is still true. What a book of positions needs is a
standing reading of one premise against the evidence that arrived after it was written.

## Decision

**A new agent role, `thesis_monitor`, which reads one premise against new evidence and
returns a status and its reasons. It reaches no conclusion, changes nothing, and holds no
tools.** A registry row in the shape ADR 0035 requires: an output contract named by
reference, `allowed_tools=frozenset()`, and this record in the `adr` field.

Where the output lands is ADR 0074's — `contradicted` opens a gate, the other four statuses
accumulate as findings with no approval semantics. That record settles who has to read the
output; this one settles what the role may say.

## The thesis model

**A thesis item is a free-text statement plus an *optional* predicate** — the design
decision underneath the role, and the one that decides what the model is for.

**With a predicate** — "Azure revenue YoY >= 25%" — nothing about the comparison is a
reasoning task. The growth is `calc/basic.py:52`'s `growth_rate`, a traced calculation
carrying the formula `growth = (end - start) / |start|` over two stored facts; the crossing
is `Quantity.__ge__` (`calc/units.py:566`), which routes through `_require_same_unit` and
raises `UnitMismatchError` rather than coercing, so a threshold in per cent can never be
quietly compared against a figure in dollars. Under ADR 0003 the model does not do the
arithmetic and does not decide whether the threshold was crossed; code hands it a crossing
that has already happened, and it writes the interpretation of why that matters. The part
that can be wrong invisibly is the part code owns, which is the whole reason a predicate is
worth having.

**Without a predicate** — "management allocates capital well" — there is no trigger at all,
and the item gets a scheduled human review instead.

**The optionality is deliberate, and the tidier design is wrong.** The alternative — every
premise a threshold — fails for a reason already recorded here: ADR 0011 declined to demand
a `SourceRef` for a calculation's `years` parameter because satisfying the rule "would mean
minting a fake fact or a fake assumption", and "fake sources are worse than no sources,
because they defeat the check while appearing to pass it". A mandatory predicate is exactly
that: `capital_allocation_score >= 7` is a number nobody measured, wearing a `Quantity`'s
clothes and inheriting machinery built for measurements, and it would monitor
deterministically, render with a provenance badge, and mean nothing. **A system that
pretends "management is trustworthy" has a metric is worse than one that admits it does
not**, because the second tells the operator where the judgement is.

**Items without a predicate are not second-class, and no surface may treat them as such.**
The premises that decide whether a position works are disproportionately the unquantifiable
ones — the quality of the people, the durability of the advantage. If an unpredicated item
renders greyed out with no badge and no activity, operators will write predicates to make
the interface light up, and the platform will have taught them to fabricate the precision it
refused to demand.

## A thesis is not tested by price

**Nothing in this role reads a mark.** The output contract has no field for a price, a
return, a drawdown or a position, and the monitor's evidence scope excludes `price_bars`
outright — a refusal rather than an absence, since the bars are stored already (ADR 0032)
and the join would be two lines. Price is an **outcome** test, and a bad premise test on
three counts.

**It lags.** A premise about FY2027 margins is answered by FY2027's filings. Between now and
then the price will move several hundred times, and none of those moves is evidence about
the margin.

**It is confounded.** A correct thesis is routinely swamped by a rate move, a sector
de-rating or a broad drawdown, and a wrong one is routinely carried by the same forces. A
price change is the sum of every view every holder has about everything, with the one
premise under examination somewhere inside it and no way to extract it.

**And treating it as the test inverts the operator's behaviour exactly.** A monitor that
marked premises weakened because the price fell would tell an operator to abandon correct
theses in drawdowns and hold broken ones in rallies — selling the position whose evidence
still holds because it hurts, keeping the one whose evidence has failed because it does not.
That is the behavioural failure the post-trade surfaces exist to catch, so building it into
the monitor would put the disease inside the diagnosis. Outcome testing is separate — ADR
0077's, which scores the process rather than the outcome — and it never touches thesis
status.

## The output contract

Three fields. A `premise_id`, chosen by code and not by the model. A `status` from a closed
enum: `unchanged`, `weakened`, `strengthened`, `contradicted`, `unobservable`. And a
`justification`, prose naming `source_document` ids and nothing else.

**There is no field for a rating, an action, a revised target, a position size or a
conviction.** Not fields left null or validated empty — absent, in ADR 0034's shape for
withheld comps: "not fields set to `None`, no fields at all". A rule enforced by what an
object can contain is one no later prompt, and no skill file, can talk its way around, which
is why ADR 0029 made the sector mandate a type rather than a check.

**`unobservable` is in the enum because its absence would be a lie.** Without it a model
that genuinely cannot tell must return `unchanged`, which asserts the evidence was read and
the premise stood. `calc/outcomes.py`'s module docstring (`:20`) made this argument for the
two drivers no filing can measure — the pair `UNMEASURABLE_JUDGEMENTS` (`:53`) names with a
reason each rather than a number: "a zero delta claims the forecast was exactly right, which
is the one thing an absence of data cannot show."

## Why no tools

ADR 0042 settled this for the section writer in one line — "a writer that searches is a
researcher with a second identity" — and the monitor is a sharper version of the same
problem. It is given the premise, the new facts and the scope, and returns a status and
reasons.

A monitor that could search would be choosing its own evidence for a premise it has already
read, after the scope was fixed, with nobody having gated the acquisition. That is not
merely ungated fetching; it is evidence-gathering aimed at a specific prior conclusion,
which is the confirmation-shaped failure the whole design exists to refuse. Code enumerates
the window, so what the monitor sees never depends on it thinking to ask — and with an empty
allowlist, as every registered role has but `analysis` and `custom_section`
(`agents/registry.py:170` and `:254`), there is no tool an injected paragraph could steer.

## The ADR 0064 question

This is the hard part, and it has to be answered rather than noted. ADR 0064 let prior
research reach the planner only, as `untrusted_sources` at `tier='not_evidence'`, and drew
the line explicitly: "The section writers get nothing … Extending feed-forward to the
writers would put prior conclusions one hop from the report's prose with no human in
between; that is a separate decision with a separate risk profile, and this ADR does not
make it."

**A thesis monitor reads the platform's own prior conclusions by construction.**
A premise *is* a prior conclusion; there is no version of this role that does not read one.
So the decision 0064 deferred falls due here, and the answer is that this role is admissible
where a section writer is not, for three reasons that are structural rather than differences
of degree.

**Its output is a question raised, never a conclusion reached.** A writer's output is prose
in a published report, which is why one hop mattered there. This role's output is a status
on an internal queue: it asserts nothing about the company and moves nothing about the
position.

**`contradicted` opens a gate, and nothing else updates anything** (ADR 0074). 0064's real
containment was never the wrapper — it was "the gate that already exists", a person reading
the planner's proposal before anything was spent. The same containment is present in the
same shape: the one status with a consequence goes to a human, and the four without one sit
there.

**And the justification may name `source_document` ids only.** A prior conclusion can
therefore decide *which* premise is examined and can never be *what* the answer rests on —
0064's own rule, "may shape the questions, never the answers", enforced at the field level
rather than in prompt text. The id also lands in a channel the platform already polices:
`verify/citations.py:237` hard-rejects any reference whose provider is `INTERNAL_PRIOR_RUN`,
because "a platform citing itself would launder yesterday's inference into today's evidence"
(`core/enums.py:262`). A free-form reference, or one that could name a prior finding, would
be a reference nothing looks at. **It is still a widening**: until now prior conclusions
entered exactly one prompt, once per run, ahead of a gate, and they now enter a second
routinely and unattended.

## What this role is not

**It is not the red team.** `RedTeamChallenge` (`agents/red_team.py:83`) scores a dimension
from one to five, must cite evidence or fail validation, and lands on the disagreement
ladder inside a run that ends. The monitor asks a narrower question on a standing basis and
returns no severity, because a severity is a claim about how much of the operator's
attention a finding deserves — about the operator rather than about the evidence.

**It does not revise a view.** Ratings, targets and valuation ranges belong to built-in
sections drawing on recorded figures, and `RESERVED_OUTPUT_FIELDS`
(`core/schemas/skill.py:67`) already refuses to let a user-authored contract declare one.
This role has no writable path to any of them, beginning with nowhere to put one.

**It does not decide whether to sell.** A contradicted premise raises the question; the
answer is a decision a person takes, and the record of it is a Judgement (ADR 0070), which
this role cannot write. Nor does it score what happened afterwards — that is ADR 0077's, and
the separation is load-bearing: a role that raised the question and graded the answer would
be marking its own homework.

## Consequences

A thesis stops being something the platform stores and becomes something it re-reads: a
premise falsified in March becomes a finding in March rather than a discovery in December,
bought at one model call per premise per evidence event against the monthly cap ADR 0074
makes the only real one.

**The residual risk is the loop, and this record does not close it.** An Investment OS is
circular by construction — thesis → monitor → decision → outcome → review → next thesis —
and `INTERNAL_PRIOR_RUN` blocks citation while doing nothing whatever about influence. ADR
0064 said those are two different problems, and it was right. The end state to fear is
concrete: a book of positions whose theses are mutually reinforcing because each was written
with the others in view, every premise individually well-sourced and every finding
individually defensible, the whole book resting on one unexamined view about the rate cycle
that nothing records as shared — and every control the platform has passes on each thesis
taken alone, because every one of them was designed for one report at a time.

**This ADR does not solve that. It declines to make it worse**: one premise at a time, one
scope, no field in which a position's neighbours can enter, and no path from a finding to
anything that moves without a person. The measure that would close it — how much of a book
rests on the same premise, put where the operator can see it — nobody has designed.
