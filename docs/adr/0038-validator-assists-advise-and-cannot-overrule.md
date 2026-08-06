# 0038 — Validator assists advise; deterministic verdicts cannot be overruled

Date: 2026-08-06. Status: accepted.

## Context

Task 39 applies the §2.10 evaluation arithmetic to every live run: four validators —
citation, temporal, numerical, coverage — write eight `evaluations` rows per run, from the
run's own tables, through the same `aer/eval` functions the CI gate trusts. Two of those
validators meet genuine ambiguity that a rule answers badly: a citation that failed the
excerpt match may be a claim whose support sits *elsewhere* in the document, and a source
with no established publication date may state its date in its own prose. §2.10 assigns
both to LLM assistance. A new agent role requires an ADR (ADR 0035). This is the
`validator` role's.

## Decision

**One role, advice as its entire output.** The `validator` role answers one narrowly
scoped question per call — locate a candidate excerpt, or adjudicate an ambiguous date —
and returns a `ValidatorAdvisory`: a proposal, a rationale, a confidence. The advisory is
recorded in the evaluation row's `details` and nowhere else. It has no path to a verdict
by construction, three times over: `citations.excerpt_verified` is written by exactly one
function and this role is not it (the task 12 source-scan test enforces that); the
quarantine flag is written at acquisition and cleared by nothing; and the metric values
are computed from the deterministic rows *before* the advisories are attached. An LLM
"yes" on a failed excerpt match therefore stays failed — the test the task names.

**No tools, bounded evidence.** The role's allowlist is empty: an assist reads exactly
what the validator hands it — the claim and a bounded window of document text, quoted in
the untrusted channel like every other fetched byte — and a helper that could search or
fetch would be a validator with an input nobody reviewed.

**The batch path is a transport, not a different audit standard.** The provider protocol
gains `complete_structured_batch` — many items, one schema, one route, results in request
order, all or nothing — and the Anthropic implementation uses the Messages Batches API
with the SDK's own schema transformation, polling with backoff to a deadline. The fake
provider answers batch items from the same script as the sync path, which is what makes
"batch and sync produce identical rows" a testable statement. `Agent.run_batch` gives
every batch item what `run` gives a single call: the composed prompt, the input-cap
refusal before money moves, and its own archived, metered `agent_runs` row.

**Not exercised is a recorded state, not a pass.** The run-time metrics reuse the gate's
empty-corpus refusal; the evaluations writer catches it and records the row with a NULL
value and NULL verdict. A run with no post-dated source did not *pass* look-ahead recall
— it gave it nothing to catch, and the row says so.

## Consequences

Every completed run carries eight evaluation rows the gate 2 dashboard and the task 41
escalation triggers can read, written by arithmetic that CI independently proves against
labelled corpora. The assists add a small, metered, capped cost to the validate step and
can be batched at half rate where the provider prices batches lower; their advice makes a
failed citation or an undated source actionable by a person without ever acting by
itself. The cost of the all-or-nothing batch contract is that one poisoned item fails its
whole batch — accepted, because a partial result list whose positions have shifted is
wrong in a way nobody notices.
