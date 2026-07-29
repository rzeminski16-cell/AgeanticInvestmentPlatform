# 15. The vendor contract is asserted, not assumed

- **Status:** Accepted
- **Date:** 2026-07-29
- **Amends:** ADR 0012 (model provider abstraction)

## Context

The first model call this platform ever made in earnest failed:

```
BadRequestError: 400 - output_config.schema: Extra inputs are not permitted
```

Behind it, unreached, sat a second 400 of the same kind: `thinking: {"type": "enabled",
"budget_tokens": 8000}`, a parameter removed from the API two model generations ago. The
planner routes at effort `high`, which mapped to 8,000 thinking tokens, so it would have
fired on the very next attempt.

Neither was a subtle mistake. Both were wrong against documentation that existed when the
code was written, and `docs/PLAN.md` §1.6 had named the correct mechanism —
`output_config.format` — all along. **The implementation drifted from its own plan and
nothing noticed**, because ADR 0012's central and still-correct decision has an edge that
had not been thought through.

That decision was: model calls go through a narrow protocol, and the test suite drives a
`FakeProvider` so that exercising the vertical slice costs nothing. It works. It also means
the fake is the *only* thing 1,300 tests ever spoke to. The fake accepts any arguments,
because it is an implementation of the protocol rather than of the vendor's API — so the
suite proved the platform used its own abstraction correctly, and proved nothing whatever
about what went on the wire.

**A fake provider tests the code above the seam. Nothing was testing the code below it.**
That is the gap this ADR closes, and it generalises: every adapter to an external contract
has an "under the seam" that a fake cannot reach.

Three further defects surfaced while checking the contract properly, all of the same
species — a fact about the vendor written from memory rather than from the specification.

## Decision

### The SDK owns the wire format

`complete_structured` calls `messages.parse(output_format=schema, ...)` and no longer builds
a schema block itself.

This is not deference for its own sake. The API's JSON-schema mode **rejects** most of what
Pydantic emits: `minimum`/`maximum` from `ge`/`le`, `minLength`/`maxLength` from
`min_length`/`max_length`, and it requires `additionalProperties: false` plus a `required`
key on every object. `ResearchPlanDraft` has a bound on nearly every field, so
`schema.model_json_schema()` sent raw is a 400 the moment it leaves. The SDK's translation
moves each stripped constraint into the schema's `description`, where the model still reads
it as guidance, and it validates the reply client-side on the way back.

Writing that translation here would mean re-deriving it, and re-deriving it wrongly is
exactly how this ADR came to be written.

### What is sent and what is archived are different, and the difference is stated

The payload handed to the SDK deliberately omits the schema. An archived request that
omitted it too would not describe the call well enough to reproduce it, which would breach
invariant 3 in spirit — a recorded model call whose output contract is unrecorded.

So `_archived` puts it back, as JSON Schema, under the `output_config.format` key the API
uses. What is stored is therefore **the schema as requested**, not byte-for-byte as
transmitted; the SDK strips constraints before sending. That is the right way round for an
audit record: the requested schema is what the platform asked for, and it is what the reply
was checked against on the way back in. Storing the stripped version would record the
vendor's translation of our intent rather than the intent.

### Capability is a table, and an unlisted model loses the feature rather than the run

`output_config.effort` arrived with the 4.6 model generation. It is a 400 on anything
earlier — including `claude-haiku-4-5`, which is where `source_triage` and `obsidian_linker`
route. So `_MODELS_ACCEPTING_EFFORT` gates the parameter, and a model absent from it is
called **without** effort, at the API's own default.

Failing the call instead was considered and rejected: it would trade a working triage call
for a dead one over a quality knob. The direction of the safe error differs from the cost
table's, and deliberately — `unknown_model_prices` **overstates** an unknown model's price,
because there the safe error is to pause a run for a decision. Here the safe error is to
run.

`client.models.retrieve(id).capabilities` is the live authority and the table says so. It is
not consulted at call time: a capability lookup before every call doubles the request count
and turns a slow Models endpoint into a stalled run.

Structured output needs no such gate — `output_config.format` is accepted by every current
model.

### `thinking` is omitted entirely

Adaptive thinking is on by default on every model this platform routes to, which is what is
wanted. The manual form was **removed**, not deprecated, and returns a 400. There is no
version of `_EFFORT_THINKING_TOKENS` worth keeping: effort *is* the thinking control now,
and it lives in `output_config`.

### An effort level that does not exist is refused before the call

A typo in `AER_MODEL_ROUTES` used to reach the API and come back as an opaque 400 a round
trip later. `_effort_for` now raises `ConfigError` naming the ladder and the env var.

Substituting a default silently was the alternative, and it is the same mistake ADR 0012
already names: *a silent default is how a run costs thirty times what was expected.*

### The token ceiling is diagnosed, not merely reported

`max_tokens` bounds thinking **and** visible output together. At effort `high`, a model can
spend a 4,096-token ceiling reasoning about a research plan and return nothing at all — and
the failure looks identical to a refusal, because both leave the content empty. Both were
previously reported as "returned no structured output", which sends the reader to look for a
parsing bug that does not exist.

The stop reason is now read *before* the content, and each of the two says what to do. The
planner's ceiling rises from 4,096 to 16,384: headroom, not an expectation, and it costs
nothing unless it is used.

The same distinction applies one layer up. When `messages.parse` cannot validate a reply it
raises before returning, so there is no stop reason to read — and there are two causes:

| Pydantic error | Cause | Fix |
|---|---|---|
| `json_invalid` at the root | JSON cut off mid-object | Raise `max_output_tokens` |
| anything else | A stripped constraint was broken | Loosen it, or say it in the prompt |

The second is real rather than theoretical: the API enforces the *shape* of a structured
reply but not the bounds, since the SDK moved them into descriptions. A model can return
`confidence: 1.7` from a structurally perfect reply. Blaming truncation would send someone to
raise a ceiling that was never the problem.

### Opus 5 costs $5/$25, not $15/$75

`DEFAULT_PRICES` carried Opus 4.7's rate, forward-copied when the model ID changed. It
overstated every planner call threefold.

Overstating is the safe direction and it is still a defect. Invariant 6 says cost is metered
**and capped** in code, and a cap that trips at a third of the real spend stops runs with
money left in the month — on a £100 ceiling, that is the difference between three reports
and one. The introductory Sonnet 5 rate ($2/$10 to 2026-08-31) is deliberately **not** used:
a ledger fed by a promotional rate starts under-reporting on the day it lapses, which is the
unsafe direction.

### Archiving a response must not warn

`ParsedMessage` annotates `content` with the unparameterised block union, so serialising a
perfectly good parsed response emits a dozen Pydantic "unexpected value" warnings. The suite
runs with `filterwarnings = ["error"]`, so archiving a real response would have *raised* — a
third failure queued behind the two 400s, and one that would have destroyed the artefact
trail rather than the call. `_DUMP_ATTEMPTS` passes `warnings=False`, and keeps the
untargeted attempts behind it for SDK shape drift.

## Consequences

**Good.**

- `tests/test_anthropic_provider.py` asserts the payload against the documented contract with
  the client stubbed. Every one of the six defects above was reproduced by reintroducing it
  and confirming a named test fails. That is the layer where a request-shape mistake is
  catchable for nothing, and it was empty.
- `TestTheSdkContract` checks the *installed SDK* for the surface the provider depends on —
  `messages.parse`'s parameters, `OutputConfigParam`'s two keys, `parsed_output` on the
  message and the block, the effort ladder. An SDK upgrade that moves any of them fails there
  rather than on the next live run.
- Failure messages name the fix. "It ran out of room at the 16,384-token ceiling, which
  bounds thinking and output together" is a sentence an operator can act on; "returned no
  structured output" is not.
- The budget ledger is threefold more accurate on the model the platform spends most of its
  money with.

**Costs, accepted.**

- The provider now knows two things about specific model IDs — which accept `effort`, and
  what they cost. Both tables go stale when Anthropic ships a model, both are named as
  offline copies of a live API, and both fail in a stated direction. The alternative is a
  network call on the hot path.
- The archived request is not byte-identical to the transmitted one. Justified above, and
  stated in `_archived`'s docstring so nobody discovers it by diffing an artefact against a
  packet capture.
- `messages.parse` validating client-side means a constraint violation raises inside the SDK,
  where the response object is out of reach. The cause is inferred from the Pydantic error
  types instead. It is a good inference and it is still an inference.

**Deliberately not built.**

- **A `live_llm` test of the real contract.** It is the only thing that can prove the contract
  has not moved, it belongs in the suite, and it bills — so it needs a decision about what an
  acceptable per-commit spend is, which is not this ADR's to make. Until then, the
  contract-with-the-SDK tests are the early-warning system and they are cheap.
- **Capability discovery via the Models API.** Correct, live, and a request per call. Revisit
  if the tables above ever cause a second outage; one is not a pattern.
- **A shared "model facts" module.** `effort` capability lives with the provider that speaks
  the protocol and prices live in `costs.py`, which the provider-agnostic meter needs. Merging
  them would drag vendor capability into a module that has no business knowing about it.
