# ADR 0091 — The critic gets a second draft, and only a person writes the lesson down

**Status.** Accepted
**Date.** 2026-08-28
**Extends.** ADR 0039 (the red team attacks from a separate context), ADR 0042 (the
section writer holds no tools), ADR 0035 (a new agent role requires an ADR — this ADR
admits `plan_critic`).
**Required by.** Roadmap §3.13.
**Does not amend.** Invariant 7 (skill files are additive-only) or ADR 0040 (containment
is proved by a corpus that must all fail). The memory decision below exists to leave both
exactly where they are.

## Context

The red team already attacks the draft from a separate context, and it has twice caught a
section publishing a number that contradicts its own citation (4.14, ADR 0086) — the
platform's own log, not a hypothetical. What it does not do is loop back: a challenge
reaches the disagreement ladder for a human at `gate_final`, the section that provoked it
is never redrafted, and nothing about the challenge survives past that one run. The
machinery to critique exists; what is missing is a writer that gets a second attempt
before a human ever sees the draft, and a way for a recurring class of challenge to be
recognised as recurring.

The roadmap scopes this to where an open-ended, model-written step is both expensive and
wrong often enough to matter: `draft` first, `plan` second. It does not belong on the
deterministic steps — a model's opinion feeding back into `calculate` is the "calculation
drifting into a prompt" failure the architecture exists to prevent — nor on the gates,
`validate`, or `red_team` itself.

The hard half is the memory. A memory that changes what a future agent writes is exactly
the shape of thing invariant 7 governs: skill files may only add requirements, proved by a
corpus that must all fail. An auto-written "do not repeat this" lesson has no such proof
behind it, and a critic that was wrong once would get to entrench its own mistake,
unreviewed, in every future run.

## Decision

### Draft: one revision pass, on the record, before the human

A `revise` step runs after `red_team` and before `gate_final`.

- **What provokes a revision.** A material challenge — severity ≥ 4, the same line the
  §2.4 banner already draws. A quibble is recorded and shown; it does not buy a redraft.
- **How a challenge finds its section.** The red team names the claims it attacks
  (`claim_ids`), and code resolves claims to sections — attribution the service validates
  against the run's own rows, dropping ids the run does not hold without dropping the
  challenge. The adversary's isolation is untouched: it still sees claims and the evidence
  index, never prose.
- **What the writer sees.** The challenge's statement, passed to the ordinary section
  writer as a labelled *direction to address* — in the instruction block, never as
  evidence, because a challenge is an argument and not a source. The writer's contract,
  evidence policy, claim rules and validation are exactly the first draft's: a revision
  that could relax the evidence contract would be a second way to publish an unsupported
  sentence. The section's previous claims are replaced, as the claims model always said a
  redraft does.
- **Bounds.** Each section is revised at most once per run; at most four sections revise,
  most severe first; the step carries a cost estimate so the budget guard sees it
  (ADR 0052). One pass, no recursion: the red team is not re-run over the revision —
  recursing the loop onto the critic is the diminishing return the roadmap names, and the
  disagreement ladder already covers it.
- **Custom sections are not auto-revised.** A user-authored section executes under its
  pinned composed policy (ADR 0037); a platform-initiated redraft would execute content
  under that policy that gate 1 never displayed. Its challenges stay for the human, and
  the revise step records that it stood aside.
- **The record.** The challenge's `disagreements` row is never auto-resolved (ADR 0039
  unchanged): the revision happens *beside* the challenge, both reach gate 2, and the
  gate-2 payload gains a `revisions` block inside the hash — "approved with these
  revisions in view" is verifiable afterwards. The `revise` step therefore seals the
  payload hash, taking that duty from `red_team` as the new last step that can change
  what the operator is shown.

### Plan: a critic before gate 1, and one planner revision

A `critique_plan` step runs between `plan` and `gate_plan`, carried by a new agent role,
**`plan_critic`** — admitted by this ADR per ADR 0035. A separate context, like the red
team: it sees the request and the proposed plan — summary, per-section focus, planned
sources, named risks — and no findings, because none exist yet. No tools. It returns
scored challenges on a closed vocabulary of aspects (coverage, sources, risks,
feasibility, point-in-time, focus) and a coverage note.

A challenge at severity ≥ 3 sends the plan back to the planner for **one** revision with
the critique and its own previous proposal in front of it. The threshold is deliberately
lower than the draft's: a plan revision costs one planner call, a wrong plan costs the
whole run, and gate 1 still sees everything either way. The critique — challenges, note,
and whether the plan was revised — is stored inside the plan body, so it sits inside the
gate-1 hash: approving a plan is approving it with its critique in view. A critic call
that fails leaves the plan as proposed and says so; the failure discipline is the peer
proposer's, and a budget refusal is never absorbed.

### The memory: recorded by code, promoted only by a person

Every revision — and every challenge the loop deliberately declined to act on — lands in
a `revision_notes` table: job, scope (`plan` or `draft`), section, the challenge's class
(the scored dimension or plan aspect), severity, statement, disposition. `aer lessons`
groups the notes by class across runs and prints the classes that recur.

**That is the whole of the automation.** No lesson is ever written into a prompt, a skill
file, or any other input of a future run by the platform. A recurring class becomes
standing guidance in exactly one way: the operator authors a **methodology skill**
(§3.11's library — `SkillKind.METHODOLOGY`, versioned, pinned at gate 1, composed
additive-only, containment-proved by the ADR 0040 corpus). The platform surfaces the
recurrence; a person decides it is real; the existing skill machinery — with its existing
proof obligations — carries it forward. No new inheritance mechanism exists to need a new
proof.

## Consequences

- The failure 4.14 records — a section contradicting its own citation, caught by the red
  team and shipped to the gate anyway — now buys a redraft before anyone approves it, and
  the human sees the challenge, the revision, and both positions.
- A run's cost rises by at most one critic call, one planner revision and four section
  redrafts, each estimated and under the budget guard. Runs whose adversary finds nothing
  material pay for the critic call and nothing else.
- The red team's schema gains claim attribution and its prompt version bumps; challenges
  recorded by earlier runs lack `claims`/`sections` in their detail and simply provoke no
  revision on resume, which is the conservative direction.
- `gate_plan` verifies against the critique step's hash and `gate_final` against the
  revise step's. A run recorded before this change that resumes across the upgrade
  executes the new steps on its way back to the gate; if its content changes, the stale
  approval is refused, which is what approvals-by-hash are for.
- Recurrence is visible in one command, and invariant 7 is untouched: the only path from
  "the critic keeps saying this" to "future runs are told this" runs through a person and
  the proved skill boundary.
