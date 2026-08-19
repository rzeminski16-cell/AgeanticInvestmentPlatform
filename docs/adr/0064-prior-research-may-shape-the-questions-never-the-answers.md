# 0064 — Prior research may shape the questions, never the answers

Date: 2026-08-19
Status: accepted

## Context

The platform accumulates approved research and, until now, never read it forward: a
second run of the same company started as blind as the first, re-discovering what changed
since the last view instead of being asked about it. The knowledge-graph plan
(`docs/knowledge-graph.md`, K2) calls this the difference between an archive and a
memory.

Feeding prior conclusions to a model is the single easiest way to corrupt this platform.
A planner shown "we rated this a hold at 100–120" is one paraphrase away from a run whose
conclusion predates its evidence, and a section writer shown the same is one step from a
report that cites the platform to itself. The anti-contamination rule already exists at
the far end — `Provider.INTERNAL_PRIOR_RUN` has no source tier and the citation verifier
hard-rejects any claim resting on a prior run's artefact — but that guards citations, not
influence.

## Decision

**Prior research feeds the planner only, as labelled untrusted material, and the gate
records that it did.**

- `history.prior_digest_for` renders the last three approved reports before the run's
  as-of date into strings — view, confidence, valuation range, named risks, catalysts
  with their calendar status already judged. Strings, deliberately: a digest carrying a
  bare `Decimal` invites arithmetic on it, and every field here is a conclusion to be
  questioned, not a value to be used. No excerpt of evidence is ever included, because an
  excerpt is what a citation quotes.
- The planner receives the digests through `untrusted_sources`, so the base class does
  the wrapping and the delimiter neutralisation — the path that cannot forget. The blocks
  carry `tier="not_evidence"` where a filing would carry `regulatory`, and the system
  prompt gains a rule (only on calls that carry priors, in the same shape as the
  containment rule) stating the digest may shape which questions the plan asks and may
  never support a claim.
- The plan's stored body and the gate-1 payload carry a one-line note naming how many
  prior reports the planner saw. The note is inside the hash: a plan informed by history
  and one planned blind are different proposals, and approving one is not approving the
  other.

**The section writers get nothing.** The planner's output is a proposal a human reads at
gate 1 before anything is spent — the containment is not only the wrapper and the
verifier, it is the gate that already exists. Extending feed-forward to the writers would
put prior conclusions one hop from the report's prose with no human in between; that is a
separate decision with a separate risk profile, and this ADR does not make it.

## Consequences

- A repeat run's plan can ask the questions only history can pose — did the named risk
  materialise, did the catalyst window close, what moved outside the prior range — at the
  cost of one more block in one prompt.
- The planner's prompt now has two variants (with and without priors), which hash to
  different `prompts` rows. That is correct: they are different instructions, and a run
  is attributable to the one it used. `prompt_version` bumped to 4.
- `plan_gate_payload` gained a `prior_research` key. A plan stored before this change
  re-hashes differently on the review page, so a run paused at gate 1 across the upgrade
  must re-run its plan step — the standing cost of evolving a hashed payload, paid before
  under the same rule (a hash over less than the operator sees is an approval of less
  than the run does).
- The bound is structural, not textual: whatever a prompt says, a claim citing prior
  research dies in the verifier. The prompt rule exists to make the boundary legible, the
  code to make it real.
