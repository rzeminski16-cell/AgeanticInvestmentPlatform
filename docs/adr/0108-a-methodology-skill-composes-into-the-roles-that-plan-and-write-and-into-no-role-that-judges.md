# ADR 0108 — A methodology skill composes into the roles that plan and write, and into no role that judges

**Status.** Accepted
**Date.** 2026-09-03
**Required by.** Roadmap §3.11, and by ADR 0091, which promised that a recurring lesson
reaches a future run in exactly one way — a `SkillKind.METHODOLOGY` skill, "versioned,
pinned at gate 1, composed additive-only, containment-proved by the ADR 0040 corpus" —
and left the composing to this record.
**Extends.** ADR 0037 (custom sections execute inside the `<user_skill>` boundary), ADR
0040 (containment is proved by a corpus that must all fail), ADR 0035 (a role's
capabilities are the registry's), ADR 0039 (the red team is a separate context), ADR 0087
(the verdict role reads the report and nothing that shaped it), ADR 0091.

## Context

`docs/archive/PLAN.md` §2.12 names four skill kinds. A custom section has had the whole
path since ADR 0037: parsed, composed against the floor, pinned, priced, gated, drafted
under its own delimiter. The other three — `METHODOLOGY`, `PREFERENCE`, `HOUSE_VIEW` —
have had half of it. The frontmatter schema accepts them and refuses them a section's
shape; the service versions them; the plan step pins them, with their status and reason,
inside the gate-1 hash. And then nothing reads them. §2.12 says they "are composed into
the relevant built-in agent's prompt under the same `<user_skill>` delimiter and the same
additive-only rule", and *relevant* was never decided. That is the whole of §3.11, and it
is four questions rather than one.

**Which roles?** The platform has fourteen roles. Some plan and write; some extract; some
validate; three exist to disagree — the plan critic, the red team and the verdict — and
one reviews the operator's own trading. "I weight owner-operator alignment heavily" is a
sentence for the planner and the writer. It is not a sentence for the red team: an
adversary handed the operator's priorities would attack the draft on the operator's own
terms, which is confirmation bias with a budget. It is not a sentence for the post-trade
reviewer either, whose contract (ADR 0081) has no field for a methodology change because
the reviewer must not be the author of the method it scores.

**Where in the prompt?** Every agent's system prompt is a versioned, hashed `prompts` row
(ADR 0035's attribution) and the stable prefix prompt caching keys on. Operator text in
the system prompt would give every run its own prompt row and put user-authored words
in the instruction that ranks above the evidence. The custom section already answers
this: the operator's text is the *last* thing in the user turn, after the platform
contract, the schema, the policy and the evidence, and it says so.

**From what record?** The planner runs before the plan row exists, and the pins were
created after the planner returned. A planner that read the enabled skills directly
would be reading a different query from the one gate 1 displays, and a skill edited in
the seconds between would reach the planner in one version and the gate in another.

**How is it proved?** ADR 0040's corpus scores nineteen escalations against the real
layers and must read zero breaches forever. Each of them is a custom section. A
methodology file that says "no citations needed" or "rate this a Buy" has no entry, so
nothing asserts that the new composition path is as contained as the old one.

## Decision

**1. A pure table says which roles read which kind, and every other role reads none.**

| Kind | Composes into |
|---|---|
| `methodology` | the planner, the section writer |
| `house_view` | the planner, the section writer |
| `preference` | the section writer |

The table lives in `aer.core.skill_guidance`, pure and `mypy --strict`, next to the
composer. A role absent from every row — the plan critic, the red team, the verdict, the
extractors, the validators, the risk analyst, the post-trade reviewer, the monitor —
receives no operator guidance under any kind, and that absence is the decision rather
than an omission: **the reader of the operator's text is never the grader of its
result.** A skill cannot name its readers. The frontmatter has no `applies_to`, and will
not: a skill choosing its audience is a skill choosing its judges.

**2. The guidance is the last thing the platform says in the user turn, and the system
prompt is byte-identical with or without it.** The planner and the writer each take a
typed `guidance` list on their input and append, after everything else they say, one
rule and then one `<user_skill>` block per skill — the same delimiter, the same neutralisation of
a smuggled close tag (ADR 0037), a header naming the kind, key and version so the
archived prompt says whose words these were. The rule is the custom section's, widened
from "this section" to standing guidance: follow it for what to analyse, what to weigh
and how to present; it cannot change the evidence standards, the citation duties, the
schema or any rule above it. Quoted documents are data and trail the whole composition inside
`<untrusted_source>` blocks, as they have since ADR 0037. Blocks are ordered by kind —
methodology, house view, preference — and then by key, so two runs under the same pins
compose the same bytes.
Nothing user-authored enters a `prompts` row, and a role's prompt hash does not depend on
which skills an operator has enabled.

**3. The pins are the one source, and the plan step pins before it plans.**
`resolve_skills_for_plan` is keyed on the work order rather than the plan row, runs
before the planner is called, and the planner composes its guidance from the pin rows it
just wrote. Gate 1 renders the same rows; the section writer reads the same rows at
execution; the critique loop's planner revision and ADR 0091's section revision read the
same rows. A pin names an immutable `skill_versions` row, so editing a skill after
approval changes nothing about the run — the property task 36 established, now carried
by every role that reads a skill. The gate-1 payload names, on each planned pin, the
roles it composes into, inside the hash: approving a plan is approving where the
operator's words reach.

**4. A prompt-kind skill is refused a section's shape at authoring, not ignored at run
time.** The schema already refused `position`, `output` and `token_budget` on a
methodology file. It now also refuses `evidence_policy`, `allowed_tools` and `charts`. A
methodology skill declaring `allowed_tools: [shell]` used to parse and be silently
ignored — the tools were never granted, but the author was never told — and a control
that works by nobody reading the field is a control one refactor from not working. There
is nothing to clamp on a prompt-kind skill because there is nothing declarable.

**5. The corpus grows by seven, and gains a layer.** `fx_skill_adversarial` adds
escalations written as methodology and house-view files: declare an output contract
carrying a rating; declare tools; declare an evidence policy of one source; switch off
point-in-time; write prose that disables citations; close the delimiter; and a house view
addressed to the red team. Each verdict is derived from the real layer, as ADR 0040
requires — the frontmatter refusals, the numeral rule, the boundary — and the last from
the role table itself, a layer named `roles`: the file composes for the planner and the
writer and for no adversary, observed by calling the real function. The count the gate
asserts moves from nineteen to twenty-six, and the zero-breach metric covers the new
path the day it lands.

**6. What does not change.** Custom sections keep ADR 0037's path exactly. The dry run
remains a custom section's tool; a prompt-kind skill has no section to try, and the
editor says instead which roles it composes into. Invariant 7 is enforced where it was —
the composer, the closed schema, the boundary, the output contracts, the role table —
and by no prompt text. A lesson still reaches a future run only through a skill an
operator authors and enables (ADR 0091); `aer lessons` still teaches nothing.

## Consequences

- Three of the four kinds do what §2.12 said they would, and the methodology library the
  roadmap describes — versioned, pinned and composed — exists for all three.
- An operator's method reaches the plan and the sections, and no adversary. A house view
  that is wrong is attacked by a red team that never read it, which is the only kind of
  red team worth paying for.
- The plan step's order changes: pins first, then the planner. A retried plan step finds
  its pins current and reuses them, as before; a re-plan over changed skills replaces
  them before the planner runs, so the planner never plans under a set the gate will not
  show.
- `resolve_skills_for_plan` takes a work-order id. Three tests and one workflow step
  change their call; nothing else did call it.
- The starter library ships one example of each prompt kind, so an operator meeting the
  three kinds for the first time meets a file rather than a paragraph.
- The gate-1 payload has one more key per pin, and every stored plan hash from before
  this record differs from what the same plan would hash now. That is the intended
  behaviour of a hash over exactly what is displayed. Two consequences for a run in
  flight across the deploy: one paused at gate 1 hashes differently from its sealed
  payload and is refused until its `critique_plan` step is retried, which re-seals it;
  one approved at gate 1 before the deploy with planned prompt-kind pins resumes under
  guidance nobody saw composed, and should be re-planned if that matters.
- A prompt-kind pin carries an estimate — its text, as input, on the planner's call and
  on every model-written section — so the gate shows what the guidance adds.

## Alternatives considered

**Compose into the system prompt.** Rejected for the two reasons in the context: it puts
operator text in the ranked instruction, and it gives every run its own prompt row,
which destroys both the attribution and the cache prefix.

**Let the frontmatter say which roles a skill reaches.** Rejected. It is more flexible
and exactly as flexible as invariant 7 forbids: `applies_to: [red_team]` is a skill
switching off the adversary's independence with a YAML key.

**Snapshot the composed text on the pin at plan time**, as the composed policy is
snapshotted for a custom section. Rejected as redundant: the policy is snapshotted because
it is *composed against a floor that can move*; the body of an immutable version cannot
move, and a second copy of it would be a second thing to keep equal.

**Read the enabled skills at each role's call time.** Rejected: the gate would approve
one set and the writer would run under whatever was enabled by the time it ran.

**A fourth kind for the adversaries** — "challenge the draft on these points too."
Deferred rather than rejected. It is a different thing, a standing question rather than a
method, and it would need its own record to say why a red team reading it is still a red
team.
