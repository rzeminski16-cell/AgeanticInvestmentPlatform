# 18. Only code confirms a citation

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Invariant 2: *the model may propose a citation; only code may confirm one.* The threat model
calls the deterministic verifier "the strongest single control in the system" (T10), because it
is the only thing standing between a plausible sentence and a fabricated source.

A control described that way has to be more than a boolean somebody remembers to set correctly.
`excerpt_verified` defaulting to false is worth nothing if a service can pass
`excerpt_verified=True` when it feels confident — which is exactly what a caller acting on a
model's output will eventually do, with the best of intentions, on a Friday.

## Decision

### One function writes the flag, and a test reads the source tree to prove it

`aer.verify.citations.verify` is the only place `excerpt_verified` is assigned. The test walks
`src/`, parses each file, and looks for assignments to that name — by attribute or by keyword
argument. The model file is permitted because declaring a column is not writing to it; nothing
else is.

The AST is parsed rather than grepped so the name appearing in a docstring, a query filter or a
log line does not count. A companion test asserts the verifier *does* write it, which stops the
first test passing because nothing sets the flag at all — the same shape as the SDK
import-boundary check from Phase 1.

`record_citation` has no parameter that could confirm one, and a test asserts the absence.

### Verification slices; it does not search

The verifier re-derives the document's text from the artefact and takes the slice at the
recorded locator. It never looks for the excerpt elsewhere.

This is the difference between a verifier that works and one that looks like it works. A filing
contains the sentence "Total revenue was $168,088 million for fiscal year 2021" three paragraphs
below the 2022 figure. A verifier searching the document confirms a citation pointing at either
one, and would go on confirming citations that point at the wrong year, the wrong segment or the
wrong company in a comparison table. **The test fixture contains that second sentence for
exactly this reason**, and the test asserts it is still there — so the test cannot quietly stop
proving anything.

### Four failures, four different messages

| What happened | Why it needs its own message |
|---|---|
| The artefact's bytes changed | The document is not the document. Re-fetch; do not re-read the quote |
| The extractor now produces different text | Every locator from it moved. Re-extract; the quote may be fine |
| The locator runs past the end | The extraction and the citation disagree about the document's size |
| The text there does not match | The quote is wrong, and the ratio says how wrong |

All four look identical from a bare mismatch, and each sends an operator somewhere different.
The similarity ratio is recorded on failures too: 0.94 is a reflowed paragraph, 0.02 is a
fabrication, and someone deciding whether to override needs to see which.

### A ratio, not equality

Whitespace normalisation handles reflowing. It does not handle a typographic quote against a
straight one, a non-breaking space, or a soft hyphen at a line break — and demanding equality
would fail correct citations over characters no reader can see. False alarms are how a control
gets switched off, so the threshold is 0.95: high enough that a different number or a different
subject falls far below it, forgiving enough that invisible differences do not.

### An override is not a verification

An unverified citation can be accepted by a named person with a written reason. It does **not**
set `excerpt_verified`. Both facts survive into the report: the check failed, and somebody took
responsibility for it. Collapsing them into one boolean would make an override read as a
verification to everything downstream, which is the precise misrepresentation this whole
mechanism exists to prevent.

There is no bulk override, and the absence is the design — a reviewer waving through twelve
unverified citations in one click has not reviewed twelve citations. A test asserts the function
takes a citation and not a list.

Overriding a citation that *did* verify is refused with a 409: recording a doubt the evidence
does not support is its own kind of dishonesty in the record.

### The gate checks evidence before it checks approval

`_gate_final` verifies every citation and reviews the claims **before** looking for an approval.
An operator shown a draft to approve while the platform still holds unverified citations would
be approving something the platform cannot stand behind, without being told so. The pause on
evidence has its own message naming what is wrong and how many.

§2.9's different bars are honoured rather than flattened: numeric and factual claims need an
admissible citation; forward-looking statements and opinions do not. An opinion with a citation
attached is not better supported than one without, and holding it to the numeric rule would push
a writer to attach evidence that does not bear on it.

## Two defects this work surfaced

**`LocalArtefactStore.read` did not verify the digest.** Threat T8 says "SHA-256 verified on
every read", `verify()` existed as a separate opt-in method, and `read()` simply returned the
bytes. I had already written docstrings in two modules asserting the check happened. The
tampered-artefact test failed and said so: the altered filing was read successfully and only
caught downstream, by the content-hash comparison, which reported it as *"the extractor
changed"* — the wrong diagnosis entirely.

`read()` now verifies. It belongs in the store rather than at each call site, because a caller
who has to remember is a caller who one day does not. The cost is a SHA-256 pass over bytes
already in memory, against a run that parses the document and calls a model about it.
`open()` still cannot verify — the digest is only known after the last chunk, by which point the
caller has acted on the first — and now says so.

**Verification spawned one subprocess parse per citation.** Forty citations across two filings
meant forty parses of two documents, turning a gate check into a minute of work. `ReadOnce`
memoises per pass, which is sound rather than merely convenient: extraction is deterministic in
the bytes and the extractor, a property asserted directly in Phase 2 task 11, so caching one
cannot hide a change. The cache lives for a single pass and is discarded, so a document edited
between passes is still caught.

The migration drift guard also caught a third, smaller thing: the migration set a server default
on `excerpt_verified` and the model set a Python-side one. The server default is the right
answer — an INSERT written outside the ORM gets false too — and the guard is what noticed.

## Consequences

**Good.**

- The platform's strongest claim is structural. Breaking it requires editing the verifier, and
  a test names the file.
- A citation that cannot be checked says which of four things went wrong.
- Overrides are visible, attributed and individual, and cannot masquerade as verifications.
- Gate 2 cannot open on unsupported evidence, and the render step cannot run past it. The
  latter is tested directly, because a gate that paused and then let the render through would
  publish the unsupported sentence anyway.

**Costs, accepted.**

- Verification re-parses each cited document once per gate check. Memoised within a pass; not
  cached across them, deliberately.
- Every artefact read now hashes its bytes. Measured in milliseconds against operations
  measured in seconds.
- Nothing produces claims yet — the Phase 1 draft step writes section content, not claims. The
  gate check is live and correct for a draft that asserts nothing, and a test pins that too, so
  the machinery is enforced from the day the first claim is written rather than retrofitted
  around it.

**Deliberately not built.**

- **Citation relevance** — whether the excerpt actually *supports* the claim, as opposed to
  existing. That needs a model judge and a gold set, and it belongs with the validator in
  Phase 4. What is here answers "does this sentence exist in this document at this position",
  which is the half that can be answered deterministically.
- **Re-verification on a schedule.** Artefacts are immutable by construction, so drift can only
  come from an extractor change or tampering, both of which surface on the next gate check.
