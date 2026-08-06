# 0040 — Skill-file containment is proved by a corpus of attacks that must all fail, forever

Date: 2026-08-06. Status: accepted.

## Context

Threat T19 is a user-authored skill file trying to escalate its privilege: weaken its
evidence policy, widen its tools, set the report's rating, exceed its budget, disable
citations, override point-in-time, or close its own `<user_skill>` delimiter and continue
as the platform's frame. The controls were built across tasks 33–38 — the frontmatter
schema, the additive-only composer (ADR on §2.12), the reserved-output-field refusal, the
prompt boundary — and each was proved once, by a unit test written the day it landed.

That is not the same as being true tomorrow. Injection resistance faced the same problem
in Phase 2 and answered it the same way: a corpus of real attacks (`fx_injection`) scored
as a **blocking** CI metric that must read zero, so a regression fails the build rather
than shipping. §2.10 names two more metrics of exactly this shape — **skill-file privilege
containment** (successful escalations, must be 0) and **custom-section contract
conformance** (outputs validating against their contract, must be 100%) — backed by
`fx_skill_adversarial` and `fx_custom_section`. Task 42 builds them.

## Decision

**The corpus is scored against the real containment layers, and the verdict is derived
from what happened — never from the label.** Each adversarial file is run through the
actual `parse_skill_file`, the actual `compose_policy` against the actual
`custom_section` role allowlist, the actual `wrap_user_skill`, and the actual
`section_output` checks the execution boundary runs. A verdict returns the layer that
*actually* stopped the escalation, or `None` with the evidence that it succeeded. Nothing
is simulated and nothing is told the answer, for the same reason the Phase 2 corpora
carry wrong answers as well as right ones: a gate scored against a simulation of the
control tests the simulation.

**A moved defence is a defect; a dropped one is a breach, and the corpus tells them
apart.** Each entry records both the layer that *should* own the escalation and the layer
that *did* stop it. The blocking metric counts only breaches — an escalation no layer
stopped — because that is the §2.10 number and it must be zero. But a separate corpus
test asserts every escalation was contained *at its owning layer*: a reserved field
caught only at the execution boundary instead of at authoring means the authoring refusal
has silently died and a backstop is carrying its weight, a state the zero-breaches metric
cannot see. Both matter, and they are asserted separately so a green metric cannot hide a
rotted layer.

**Conformance is measured as agreement with labels, not as the pass rate.** §2.10 words
it "outputs validating ÷ outputs", which is the right number only if every output
conforms — and against only-conforming outputs a validator that accepts everything scores
100%. So `fx_custom_section` carries labelled violations too (an undeclared key, a missing
required field, a number where a string was declared), and the metric counts correct
verdicts in both directions, refusing a corpus with no violations exactly as the citation
metric refuses one with no fabrications.

**The reserved-field check moves into the pure core, and the two metrics join `BLOCKING`
in §2.10's order.** `reserved_fields_in` and the contract projection (`contract_schema`)
become the single functions both the execution boundary and the corpus call, so the gate
scores the deployed check rather than a copy of it. `BLOCKING` grows from eight to ten;
nothing is removed, and both thresholds are at the extreme — zero successful escalations,
100% conformance — because these are properties the architecture is meant to make
impossible to violate, not aspirations that happen to hold today.

## Consequences

A regression in any containment layer now fails CI: relax the composer's `min_sources`
floor and `zero_min_sources` succeeds; drop the reserved-field refusal and three
rating-declaring files succeed; stop neutralising the delimiter and `close_the_boundary`
escapes. The corpus is checked into the repository as skill files a reviewer can read, and
the whole gate runs with no network and no model spend — the containment layers are pure
functions over text, so scoring twelve attacks costs nothing.

The known limit is the same one the injection corpus has: the gate proves the platform
resists the twelve escalations *someone thought of*. A T19 vector nobody wrote a file for
is not tested until somebody adds it — which is an argument for adding one whenever a new
containment layer lands, not for trusting the twelve to be exhaustive. The escalations
covered are the seven families §2.11 names, each with at least one file, and the corpus
test fails if a family quietly leaves the set.
