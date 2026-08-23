# 0042 — The section writer holds no tools

Date: 2026-08-06. Status: accepted. Task 45; admits the `report_writer` agent role that
ADR 0035 requires this document for.

## Context

Until now the eighteen built-in sections were filled by a Phase 1 placeholder
(`_content_for` in the vertical slice) whose own docstring promised replacement by a
section-writer agent. `docs/archive/PLAN.md` §1.8 commits to the role — `report_writer`, "18
sections from structured facts" — and its model route has been configured since task 2.
Task 44 widened the spine to eighteen seeded sections; prose that merely restates the
request is no longer a placeholder, it is sixteen sections of nothing.

The question this record settles is not whether the role exists but what it may reach:
does the writer get the research workers' tool set (`search_facts`, `search_sources`,
`fetch_known_url`), the custom-section grant, or nothing?

## Decision

**The `report_writer` role holds no tools.** Its evidence arrives as a pack assembled by
deterministic code from what the run has already recorded — facts, calculations,
admissible sources and their extractions, bounded by the section's own token budget — and
the role's registry allowlist is empty.

The writer shares the custom-section execution discipline exactly: one structured-output
call per section validated against the definition's `output_contract`, a schema violation
retried once and then the section fails with its reasons recorded, evidence short of the
policy floor generates under an insufficiency banner rather than being padded, and every
figure names the stored fact or recorded calculation it came from while claims and
citations go through the same recording services. The shared core lives in
`aer/sections/`, and the custom-section boundary in `aer/skills/execution.py` is a caller
of it — one implementation of "what may a section draft say", not two.

## Why no tools

1. **The design is "from structured facts".** §1.8's budget for the role assumes the
   research workers already investigated and the deterministic layer already computed.
   A writer that searches is a researcher with a second identity, and its searches would
   happen after the plan and the peer set were approved — evidence acquisition nobody
   gated.
2. **A section's evidence must not depend on a model thinking to ask.** Code enumerates
   what the run holds, the same argument that made custom-section gathering deterministic
   (§2.12: one call, evidence assembled for it). What differs per section is the budget
   and the policy floor, both data on the definition row.
3. **The containment story is short.** The prompt carries fetched-document text only in
   the labelled untrusted channel, and there is no tool a hostile excerpt could steer.
   The most an injection can do is propose content — which the contract validator, the
   numeral rule and the citation verifier already treat as unproven.

## Two rules the writer forced into their final shape

**A figure row is lineage.** §2.12's numeral rule demanded that every content numeral
appear in a numeric claim, and a numeric claim requires an excerpt citation — which no
calculation output can honestly have, since no document contains a DCF's result. The rule
now accepts the convention built-in sections have used since Phase 1: a numeral inside an
object naming its ``calculation_id`` or ``financial_fact_id`` carries its lineage in the
row, and the renderer footnotes it. This is not a loosening: the same change put content
ids under the closed world for the first time (previously only claim ids were checked
against the call's evidence), so a fabricated row id now fails validation where before it
would have sailed through unexamined. Prose assertions still require claims, and claims
still require citations the deterministic verifier confirms.

**Structured API responses are citable.** The slice's evidence is an XBRL facts response
— JSON — and a citation must be re-readable by hash, so a ``json`` extractor joined the
sandbox roster (the decoded source verbatim, locators as character ranges, the same
injection scan as prose) and the extract step now records one extraction per persisted
fact, located in the archived bytes. The verifier re-reads them exactly as it re-reads
HTML excerpts.

## Consequences

* The registry row is `allowed_tools=frozenset()`; a test holds it there.
* Gate 1's section listing gains per-section cost estimates (budgeted evidence tokens at
  the writer's routed model price), and the plan's estimated total now covers the spine —
  the estimate became honest the moment the spend became real.
* The evidence pack excludes quarantined sources and tiers above the section's own
  ceiling, exactly as for custom sections; the two paths cannot drift because they are
  one path.
* The Phase 1 placeholder is deleted. Its contract-walking behaviour survives only in the
  test fake that scripts the writer, which is where a placeholder belongs.
