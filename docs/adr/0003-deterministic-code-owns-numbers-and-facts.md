# 3. Deterministic code owns every number and every fact

- **Status:** Accepted
- **Date:** 2026-07-27

## Context

A language model asked to "research Microsoft" produces fluent, plausible, partly
fabricated prose: invented figures, citations that do not support the claim attached to
them, arithmetic that is confidently wrong, and financial data that silently postdates the
as-of date.

This is not a prompt-quality problem to be engineered away. It is the expected behaviour
of a system optimised to produce likely-looking text, and it is fatal to all three of this
project's goals at once. Research you cannot trust is useless to you personally,
embarrassing in front of an investment-management employer, and unsellable.

The mitigation cannot be "ask the model to be careful", "ask it to double-check", or "have
a second model review the first". Those reduce the error rate. They do not change the
class of failure, because nothing in that loop can distinguish a real citation from a
plausible one.

## Decision

**Deterministic Python owns every number and every fact. The language model owns
planning, interpretation, comparison, adversarial challenge and writing.**

Deterministic code — ordinary, typed, unit-tested Python — owns:

- HTTP fetching, robots and terms-of-use checks, SSRF guards, rate limiting, retries
- Hashing, content-addressed storage, caching, deduplication
- iXBRL, PDF and HTML parsing
- **All arithmetic**: ratios, growth, CAGR, WACC, discounted cash flow, comparables,
  multiples, scenarios, sensitivities
- Unit and currency normalisation, and foreign-exchange conversion
- Date arithmetic and point-in-time filtering
- Citation resolution and excerpt verification
- Schema validation, persistence, rendering, cost metering, budget enforcement

The language model owns:

- Research planning and search-query formulation
- Source relevance triage
- Assumption **proposal**, with justification and stated confidence
- Drafting sections from already-structured facts
- Red-teaming the thesis
- Natural-language writing

Four structural consequences follow, and they are the point of the decision:

1. **No figure reaches a report unless it is a stored fact or a recorded calculation.** A
   calculation persists its formula, its inputs (each with a unit and a source reference),
   and the code version that produced it.
2. **The model may propose a citation; only code may confirm one.** Verification re-reads
   the artefact by hash and checks the excerpt actually appears at the recorded locator.
3. **Point-in-time is enforced at acquisition, in code**, before any model sees anything.
4. **Units are carried through all arithmetic and a mismatch raises**, rather than
   coercing.

## Consequences

- Hallucinated numbers become impossible rather than unlikely — there is no code path by
  which model output becomes a figure in a report.
- Hallucinated citations are caught deterministically, which is the strongest single
  control in the system.
- Reports are reproducible: the same request against the same pinned artefacts yields the
  same numbers. Prose may vary; numbers may not.
- The provenance drill-down — hover any figure, see its formula, inputs, sources and
  hashes — falls out of this design rather than being bolted on. It is also the single
  most persuasive thing to demonstrate to an employer.
- **It is more work.** A DCF engine, a unit system, a citation verifier and an extraction
  pipeline all have to be built. Accepted deliberately: this is the product.
- It constrains what the model may be asked to do. Requests like "summarise the financials"
  must be decomposed into deterministic extraction plus model-written narrative. This
  friction is the mechanism working, not a defect.
- Deterministic extraction is also a roughly fourfold cost lever, because structured data
  never has to be re-read as tokens.

## Alternatives considered

**Trust the model, verify by sampling.** Cheapest, and how most "AI research" tools work.
Rejected: the residual error rate is unknowable, the failures are exactly the confident,
plausible ones a reviewer is least likely to catch, and one fabricated figure in front of
an employer destroys the credibility of the whole artefact.

**Model-generated code for calculations.** Fashionable, and superficially attractive since
the arithmetic then happens in Python. Rejected: it moves the failure from a wrong number
to a wrong *formula*, which is harder to spot and impossible to unit-test in advance. A
DCF is a known, fixed algorithm; there is no reason to generate it per run.

**A second model verifies the first.** Rejected as the primary control. Two models sharing
a context and a prior are correlated, not independent — they agree on plausible-sounding
errors. A red-team agent is still used, but in a *separate* context, adversarially, and as
a supplement to deterministic checks rather than a replacement.

**Retrieval-augmented generation with citation prompting.** Improves grounding and is used
here as an input. Rejected as sufficient: nothing in it verifies that a cited span exists
or that a number was computed correctly, which are precisely the two failures that matter.
